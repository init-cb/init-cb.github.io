#!/usr/bin/env python3
"""
从 ccfddl/ccf-deadlines 拉取指定会议的信息，生成 _includes/ccf_deadlines.html

功能：
1. 展示：会议名+年份（如 MICCAI 2025）、截稿时间、会议日期、地点、CCF 等级、时区、comment。
2. 计算：离截稿还剩多少天。
3. 按状态着色：
   - 截稿未到：浅绿色（Open）
   - 截稿已过但会议尚未开始/结束：浅蓝色（On the way）
   - 会议也结束了：浅灰色（Finished）
4. 页脚显示：更新时间（含 UTC 时区声明）。
"""

import datetime as dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

# 你关心的会议列表：根据 ccfddl 仓库里的路径来填
TARGET_CONFS = [
    {"sub": "AI", "name": "aaai",   "label": "AAAI"},
    {"sub": "AI", "name": "nips",   "label": "NeuIPS"},
    {"sub": "AI", "name": "cvpr",   "label": "CVPR"},
    {"sub": "AI", "name": "emnlp",   "label": "EMNLP"},
    {"sub": "AI", "name": "iccv",   "label": "ICCV"},
    {"sub": "AI", "name": "eccv",   "label": "ECCV"},
    {"sub": "AI", "name": "ijcai",   "label": "IJCAI"},
    {"sub": "MX", "name": "www",    "label": "WWW"},
    {"sub": "AI", "name": "bmvc",   "label": "BMVC"},
    {"sub": "MX", "name": "miccai", "label": "MICCAI"},
    {"sub": "MX", "name": "isbi",   "label": "ISBI"},
]

RAW_BASE = "https://raw.githubusercontent.com/ccfddl/ccf-deadlines/main"
OUT_FILE = Path("_includes/ccf_deadlines.html")


# ===================== 工具函数 =====================

def fetch_conf_yaml(conf_def: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    从 ccfddl 仓库拉对应的 yml 文件
    """
    url = f"{RAW_BASE}/conference/{conf_def['sub']}/{conf_def['name']}.yml"
    print(f"[INFO] Fetching {url}")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = yaml.safe_load(resp.text)
    if isinstance(data, dict):
        data = [data]
    return data or []


def extract_ranks(entry: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    兼容新旧两种写法，提取：
    - CCF 等级
    - CORE 等级
    - TH-CPL 等级
    可能的结构：
    1) 旧：
       rank: A
    2) 新：
       ccf: A
       rank:
         core: A*
         thcpl: A
    """
    ccf_rank = entry.get("ccf")
    core_rank = None
    thcpl_rank = None

    rank = entry.get("rank")

    if isinstance(rank, str):
        # 旧格式：把 rank 当作 CCF 等级
        if ccf_rank is None:
            ccf_rank = rank
    elif isinstance(rank, dict):
        # 新格式
        core_rank = rank.get("core")
        thcpl_rank = rank.get("thcpl")
        if ccf_rank is None:
            ccf_rank = rank.get("ccf")

    return ccf_rank, core_rank, thcpl_rank


def parse_timezone_offset(tz_str: str) -> dt.timezone:
    """
    把 ccfddl 里的 timezone 字符串（UTC+8, UTC-5, AoE 等）转成 Python 的 timezone 对象
    """
    if not tz_str:
        return dt.timezone.utc

    tz = tz_str.strip().upper()
    if tz == "AOE":
        # AoE ≈ UTC-12
        return dt.timezone(dt.timedelta(hours=-12))

    m = re.match(r"UTC([+-])(\d{1,2})(?::(\d{2}))?$", tz)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hours = int(m.group(2))
        minutes = int(m.group(3) or "0")
        offset_min = sign * (hours * 60 + minutes)
        return dt.timezone(dt.timedelta(minutes=offset_min))

    # 兜底：解析失败就按 UTC 处理
    return dt.timezone.utc


def parse_deadline_local(ddl_str: str) -> Optional[dt.datetime]:
    """
    尝试把 deadline 字符串解析成「本地时间的 naive datetime」。
    支持形如 '2025-03-01 23:59:59'，后面如果多了 AoE / UTC+8 等，我们在外层处理。
    """
    if not ddl_str or ddl_str == "TBD":
        return None

    # 只取前两个 token：日期+时间
    parts = ddl_str.split()
    if len(parts) >= 2:
        core = " ".join(parts[:2])
    else:
        core = parts[0]

    try:
        return dt.datetime.fromisoformat(core.replace(" ", "T"))
    except Exception:
        return None


def to_utc(local_dt: dt.datetime, tz_str: str) -> dt.datetime:
    """
    把 本地时间 + tz_str 转成 UTC 时间
    """
    tz = parse_timezone_offset(tz_str)
    return local_dt.replace(tzinfo=tz).astimezone(dt.timezone.utc)


def pick_deadline_and_status(
    timeline: List[Dict[str, Any]],
    timezone_str: str,
    now_utc: dt.datetime,
) -> Tuple[Optional[Dict[str, Any]], Optional[dt.datetime], Optional[int], str]:
    """
    从 timeline 中选一个「代表性」的 deadline，并返回：
    - 对应的 item
    - deadline 的 UTC 时间（可能为 None）
    - 剩余天数（可能为 None）
    - deadline 状态：'open' / 'passed' / 'unknown'
    """
    if not timeline:
        return None, None, None, "unknown"

    # 找「未来最近」的 deadline；如果没有，就找最后一个
    best_future_item = None
    best_future_utc = None
    last_item = None
    last_utc = None

    for item in timeline:
        ddl_str = item.get("deadline", "")
        local_dt = parse_deadline_local(ddl_str)
        if local_dt is None:
            continue
        ddl_utc = to_utc(local_dt, timezone_str)

        # 记录「最后一条」以防都在过去
        if last_utc is None or ddl_utc > last_utc:
            last_utc = ddl_utc
            last_item = item

        # 找未来最近的
        if ddl_utc >= now_utc:
            if best_future_utc is None or ddl_utc < best_future_utc:
                best_future_utc = ddl_utc
                best_future_item = item

    if best_future_item is not None:
        ddl_item = best_future_item
        ddl_utc = best_future_utc
    elif last_item is not None:
        ddl_item = last_item
        ddl_utc = last_utc
    else:
        return None, None, None, "unknown"

    # 计算剩余天数
    days_left = (ddl_utc.date() - now_utc.date()).days

    # 状态
    if ddl_utc >= now_utc:
        status = "open"
    else:
        status = "passed"

    return ddl_item, ddl_utc, days_left, status


MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "SEPT": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def parse_conf_end_date(date_str: str, year: Optional[int]) -> Optional[dt.date]:
    """
    尝试从 ccfddl 的 date 字段里解析 conference 的「结束日期」，用于区分：
    - 截稿已过但会议还没开始/结束（"on the way"）
    - 会议也结束了（"finished"）

    典型格式示例：
      "Mar 12-16, 2025"
      "Sep 29 - Oct 5, 2025"（这种就比较难，简单起见只看最后的年份那一段）

    解析失败就返回 None。
    """
    if not date_str:
        return None

    # 优先匹配类似 "Mar 12-16, 2025"
    m = re.search(
        r"([A-Za-z]+)\s+(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?,\s*(\d{4})",
        date_str,
    )
    if m:
        mon_name = m.group(1).upper()
        start_day = int(m.group(2))
        end_day = int(m.group(3) or start_day)
        year_val = int(m.group(4))
        mon = MONTHS.get(mon_name[:3])
        if mon:
            return dt.date(year_val, mon, end_day)

    # 如果没匹配到，就退而求其次：只用 year 字段，设为当年 12-31
    if isinstance(year, int):
        return dt.date(year, 12, 31)

    return None


def build_year_candidates(
    entry: Dict[str, Any],
    now_utc: dt.datetime,
) -> List[Dict[str, Any]]:
    """
    把一个 entry 里的多届 confs 展开成多个 candidate，供后面选择「当前最相关的一届」。
    """
    title = entry.get("title", "").strip()
    description = entry.get("description", "").strip()
    ccf_rank, core_rank, thcpl_rank = extract_ranks(entry)

    confs = entry.get("confs") or []
    candidates = []

    for c in confs:
        year = c.get("year")
        if not isinstance(year, int):
            continue

        link = c.get("link", "#")
        timezone_str = c.get("timezone", "") or entry.get("timezone", "") or ""
        date_str = c.get("date", "") or entry.get("date", "") or ""
        place = c.get("place", "") or entry.get("place", "") or ""

        timeline = c.get("timeline") or []
        ddl_item, ddl_utc, days_left, ddl_status = pick_deadline_and_status(
            timeline, timezone_str, now_utc
        )

        # 根据 deadline & conference date 判定整体状态
        today = now_utc.date()
        conf_end_date = parse_conf_end_date(date_str, year)

        if ddl_status == "open":
            overall_status = "open"
        else:
            if conf_end_date and conf_end_date >= today:
                overall_status = "on_the_way"
            else:
                overall_status = "finished"

        deadline_str = ddl_item.get("deadline", "TBD") if ddl_item else "TBD"
        ddl_comment = ddl_item.get("comment", "") if ddl_item else ""

        candidates.append(
            {
                "title": title,
                "description": description,
                "year": year,
                "link": link,
                "timezone": timezone_str,
                "date_str": date_str,
                "place": place,
                "ccf_rank": ccf_rank,
                "core_rank": core_rank,
                "thcpl_rank": thcpl_rank,
                "deadline_str": deadline_str,
                "deadline_comment": ddl_comment,
                "deadline_utc": ddl_utc,
                "days_left": days_left,
                "deadline_status": ddl_status,
                "overall_status": overall_status,
            }
        )

    return candidates


def choose_best_candidate(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    在多届会议中，选择「当前最相关的一届」：
    优先级：
    1. overall_status: open > on_the_way > finished
    2. 在同一状态下：
       - 有 deadline_utc 的，按 deadline 早的在前
       - 没有 deadline_utc 的，按 year 小的在前
    """
    if not candidates:
        return None

    def key(c: Dict[str, Any]):
        status_rank = {"open": 0, "on_the_way": 1, "finished": 2}.get(
            c["overall_status"], 3
        )
        ddl_utc = c.get("deadline_utc")
        if ddl_utc is None:
            # 没有具体 ddl 的，用当年 12-31 作为近似
            year = c.get("year") or 9999
            ddl_utc = dt.datetime(year, 12, 31, tzinfo=dt.timezone.utc)
        return (status_rank, ddl_utc)

    return min(candidates, key=key)


def format_days_left(days_left: Optional[int]) -> str:
    """
    把剩余天数格式化成人类可读文本
    """
    if days_left is None:
        return "TBD"
    if days_left > 0:
        return f"{days_left} days left"
    if days_left == 0:
        return "Due today"
    return f"{-days_left} days ago"


def status_style_and_label(overall_status: str) -> Tuple[str, str]:
    """
    根据整体状态，返回：
    - 行的 inline style
    - 状态文本
    """
    if overall_status == "open":
        # 浅绿色
        return 'background-color:#e9f7ef;', "Open"
    if overall_status == "on_the_way":
        # 浅蓝色
        return 'background-color:#e7f1fb;', "On the way"
    # finished
    return 'background-color:#f2f2f2;color:#777;', "Finished"


def generate_html(rows: List[Dict[str, Any]], now_utc: dt.datetime) -> str:
    """
    rows: 每个元素包含：
      label, title, description, year, link,
      deadline_str, deadline_comment, timezone,
      days_left_str, date_str, place,
      ccf_rank, core_rank, thcpl_rank,
      overall_status, row_style, status_label
    """
    lines: List[str] = []

    lines.append('<table class="table table-sm">')
    lines.append("  <thead>")
    lines.append(
        "    <tr>"
        "<th>Conference</th>"
        "<th>Deadline</th>"
        "<th>Days</th>"
        "<th>Date &amp; Place</th>"
        "<th>Status</th>"
        "</tr>"
    )
    lines.append("  </thead>")
    lines.append("  <tbody>")

    # 按 deadline_utc 排序（最紧迫在上）；没有 ddl 的排在后面
    def sort_key(r: Dict[str, Any]):
        ddl_utc = r.get("deadline_utc")
        if ddl_utc is None:
            ddl_utc = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
        return ddl_utc

    for r in sorted(rows, key=sort_key):
        style = r["row_style"]
        conf_html = (
            f'<strong><a href="{r["link"]}" target="_blank" '
            f'rel="noopener noreferrer">{r["label"]} {r["year"]}</a></strong>'
        )

        # description + ranks
        meta_parts = []
        if r["description"]:
            meta_parts.append(r["description"])
        ranks_txt = []
        if r["ccf_rank"]:
            ranks_txt.append(f"CCF {r['ccf_rank']}")
        if r["core_rank"]:
            ranks_txt.append(f"CORE {r['core_rank']}")
        if r["thcpl_rank"]:
            ranks_txt.append(f"TH-CPL {r['thcpl_rank']}")
        if ranks_txt:
            meta_parts.append(" / ".join(ranks_txt))

        if meta_parts:
            conf_html += "<br><small>" + " | ".join(meta_parts) + "</small>"

        # deadline + comment + timezone
        ddl_cell = r["deadline_str"]
        if r["deadline_comment"]:
            ddl_cell += f'<br><small>{r["deadline_comment"]}</small>'
        if r["timezone"]:
            ddl_cell += f'<br><small>Timezone: {r["timezone"]}</small>'

        # date & place
        date_place = ""
        if r["date_str"]:
            date_place += r["date_str"]
        if r["place"]:
            if date_place:
                date_place += "<br>"
            date_place += r["place"]

        lines.append(
            f'    <tr style="{style}">'
            f"<td>{conf_html}</td>"
            f"<td>{ddl_cell}</td>"
            f"<td>{r['days_left_str']}</td>"
            f"<td>{date_place}</td>"
            f"<td>{r['status_label']}</td>"
            "</tr>"
        )

    lines.append("  </tbody>")
    lines.append("</table>")

    updated = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    lines.append(
        "<p><small>"
        f"Last updated: {updated}. "
        "Deadlines are taken from "
        '<a href="https://github.com/ccfddl/ccf-deadlines" target="_blank" '
        'rel="noopener noreferrer">ccfddl</a>. '
        "All countdowns are computed in UTC."
        "</small></p>"
    )

    return "\n".join(lines) + "\n"


# ===================== 主流程 =====================

def main():
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    rows: List[Dict[str, Any]] = []

    for conf_def in TARGET_CONFS:
        try:
            data = fetch_conf_yaml(conf_def)
        except Exception as e:
            print(f"[WARN] Failed to fetch {conf_def['name']}: {e}")
            continue

        all_candidates: List[Dict[str, Any]] = []
        for entry in data:
            all_candidates.extend(build_year_candidates(entry, now_utc))

        best = choose_best_candidate(all_candidates)
        if not best:
            print(f"[WARN] No valid confs for {conf_def['name']}")
            continue

        days_left_str = format_days_left(best.get("days_left"))
        row_style, status_label = status_style_and_label(best["overall_status"])

        row = {
            "label": conf_def["label"],
            "title": best["title"],
            "description": best["description"],
            "year": best["year"],
            "link": best["link"],
            "deadline_str": best["deadline_str"],
            "deadline_comment": best["deadline_comment"],
            "timezone": best["timezone"],
            "deadline_utc": best["deadline_utc"],
            "days_left_str": days_left_str,
            "date_str": best["date_str"],
            "place": best["place"],
            "ccf_rank": best["ccf_rank"],
            "core_rank": best["core_rank"],
            "thcpl_rank": best["thcpl_rank"],
            "overall_status": best["overall_status"],
            "row_style": row_style,
            "status_label": status_label,
        }

        rows.append(row)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    html = generate_html(rows, now_utc)
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"[INFO] Wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
