#!/usr/bin/env python3
"""
从 ccfddl/ccf-deadlines 拉取指定会议的信息，生成 _includes/ccf_deadlines.html

满足需求：
1. 对于同一会议，只展示“最近的一届”（最大 year），无论是 open / on the way / finished。
2. 截稿日期为 TBD 且会议日期尚未来临的会议，视为 open（绿色）。
3. 表格支持前端排序和筛选：
   - 列名：Conference / Deadline / Days / Date & Place / Status
   - 点击表头可排序（按字母、deadline 时间、days、status 等）。
   - 顶部有 status 下拉框和搜索框。
   - 默认顺序：按 status（open → on the way → finished），然后按会议名首字母。
"""

import datetime as dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

# ------------ 你关注的会议：按 ccfddl 仓库实际路径修改这里 ------------

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
    {"sub": "MX", "name": "bibm", "label": "BIBM"},
    {"sub": "MX", "name": "isbi",   "label": "ISBI"},
    {"sub": "AI", "name": "icaps",   "label": "ICAPS"},
    {"sub": "CG", "name": "icassp",   "label": "ICASSP"},
]

RAW_BASE = "https://raw.githubusercontent.com/ccfddl/ccf-deadlines/main"
OUT_FILE = Path("_includes/ccf_deadlines.html")


# ===================== 工具函数 =====================

def fetch_conf_yaml(conf_def: Dict[str, str]) -> List[Dict[str, Any]]:
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
    提取 CCF / CORE / TH-CPL 等级，兼容旧格式：
      rank: A
    和新格式：
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
        if ccf_rank is None:
            ccf_rank = rank
    elif isinstance(rank, dict):
        core_rank = rank.get("core")
        thcpl_rank = rank.get("thcpl")
        if ccf_rank is None:
            ccf_rank = rank.get("ccf")

    return ccf_rank, core_rank, thcpl_rank


def parse_timezone_offset(tz_str: str) -> dt.timezone:
    if not tz_str:
        return dt.timezone.utc

    tz = tz_str.strip().upper()
    if tz == "AOE":
        return dt.timezone(dt.timedelta(hours=-12))

    m = re.match(r"UTC([+-])(\d{1,2})(?::(\d{2}))?$", tz)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hours = int(m.group(2))
        minutes = int(m.group(3) or "0")
        offset_min = sign * (hours * 60 + minutes)
        return dt.timezone(dt.timedelta(minutes=offset_min))

    return dt.timezone.utc


def parse_deadline_local(ddl_str: str) -> Optional[dt.datetime]:
    """
    把 deadline 字符串尽量解析成本地 naive datetime，支持：
      '2025-03-01 23:59:59'（后面带 AoE / UTC+8 会在外层处理）
    """
    if not ddl_str or ddl_str == "TBD":
        return None
    parts = ddl_str.split()
    core = " ".join(parts[:2]) if len(parts) >= 2 else parts[0]
    try:
        return dt.datetime.fromisoformat(core.replace(" ", "T"))
    except Exception:
        return None


def to_utc(local_dt: dt.datetime, tz_str: str) -> dt.datetime:
    tz = parse_timezone_offset(tz_str)
    return local_dt.replace(tzinfo=tz).astimezone(dt.timezone.utc)


def pick_deadline_and_status(
    timeline: List[Dict[str, Any]],
    timezone_str: str,
    now_utc: dt.datetime,
) -> Tuple[Optional[Dict[str, Any]], Optional[dt.datetime], Optional[int], str]:
    """
    timeline → 选一个代表性的 deadline：
      - 优先：未来最近的；
      - 否则：最后一个（最晚的）。
    返回：
      ddl_item, ddl_utc, days_left, ddl_status(open/passed/unknown)
    """
    if not timeline:
        return None, None, None, "unknown"

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

        if last_utc is None or ddl_utc > last_utc:
            last_utc = ddl_utc
            last_item = item

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

    days_left = (ddl_utc.date() - now_utc.date()).days
    status = "open" if ddl_utc >= now_utc else "passed"
    return ddl_item, ddl_utc, days_left, status


MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_conf_end_date(date_str: str, year: Optional[int]) -> Optional[dt.date]:
    """
    尝试解析会议结束日期；典型：'Mar 12-16, 2025'
    解析失败则退化成该年的 12-31（如果有 year），用于区分 finished / on_the_way。
    """
    if not date_str:
        if isinstance(year, int):
            return dt.date(year, 12, 31)
        return None

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

    if isinstance(year, int):
        return dt.date(year, 12, 31)
    return None


def build_year_candidates(
    entry: Dict[str, Any],
    now_utc: dt.datetime,
) -> List[Dict[str, Any]]:
    """
    把一个 entry 里的多届 confs 展开成多个 candidate。
    """
    title = entry.get("title", "").strip()
    description = entry.get("description", "").strip()
    ccf_rank, core_rank, thcpl_rank = extract_ranks(entry)

    confs = entry.get("confs") or []
    candidates = []
    today = now_utc.date()

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

        deadline_str = ddl_item.get("deadline", "TBD") if ddl_item else "TBD"
        ddl_comment = ddl_item.get("comment", "") if ddl_item else ""

        conf_end_date = parse_conf_end_date(date_str, year)

        # === 需求 2：TBD 且会议尚未开始/结束 → 视为 open ===
        if (not ddl_item or deadline_str == "TBD") and conf_end_date and conf_end_date >= today:
            ddl_status = "open"
            days_left = None  # 没有具体 ddl，天数设为 None

        # 根据 ddl_status + conf_end_date 判 overall_status
        if ddl_status == "open":
            overall_status = "open"
        else:
            if conf_end_date and conf_end_date >= today:
                overall_status = "on_the_way"
            else:
                overall_status = "finished"

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


def choose_latest_candidate(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    需求 1：无论状态如何，总是选择“最近的一届”展示（最大 year）。
    如果同一年有多条，就选 deadline 最靠前的一条（大部分会议一年只有一个 conf）。
    """
    if not candidates:
        return None

    max_year = max(c["year"] for c in candidates if isinstance(c.get("year"), int))
    latest = [c for c in candidates if c.get("year") == max_year]
    if len(latest) == 1:
        return latest[0]

    # 同一年多条：按 deadline_utc 最早的在前；没有 ddl 的排后
    def key(c: Dict[str, Any]):
        ddl_utc = c.get("deadline_utc")
        if ddl_utc is None:
            ddl_utc = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
        return ddl_utc

    return min(latest, key=key)


def format_days_left(days_left: Optional[int]) -> str:
    if days_left is None:
        return "TBD"
    if days_left > 0:
        return f"{days_left} days left"
    if days_left == 0:
        return "Due today"
    return f"{-days_left} days ago"


def status_style_and_label(overall_status: str) -> Tuple[str, str]:
    if overall_status == "open":
        return 'background-color:#e9f7ef;', "Open"
    if overall_status == "on_the_way":
        return 'background-color:#e7f1fb;', "On the way"
    return 'background-color:#f2f2f2;color:#777;', "Finished"


def generate_html(rows: List[Dict[str, Any]], now_utc: dt.datetime) -> str:
    """
    rows 每个元素包含：
      label, year, link,
      description, ranks, deadline_str, deadline_comment, timezone,
      days_left_str, date_str, place,
      overall_status, deadline_utc, 等。
    """
    lines: List[str] = []

    # ---- 控件：过滤 + 搜索 ----
    lines.append('<div id="ccf-deadlines-controls" style="margin-bottom:0.5rem;">')
    lines.append(
        '  <label style="margin-right:0.75rem;">'
        'Status: '
        '<select id="ccf-status-filter">'
        '<option value="all">All</option>'
        '<option value="open">Open</option>'
        '<option value="on_the_way">On the way</option>'
        '<option value="finished">Finished</option>'
        '</select>'
        '</label>'
    )
    lines.append(
        '  <label>'
        'Search: '
        '<input type="text" id="ccf-search" '
        'placeholder="Type to filter..." '
        'style="max-width:200px;">'
        '</label>'
    )
    lines.append("</div>")

    # ---- 表格 ----
    lines.append('<table class="table table-sm" id="ccf-deadlines-table">')
    lines.append("  <thead>")
    lines.append(
        "    <tr>"
        '<th data-sort="conf">Conference</th>'
        '<th data-sort="deadline">Deadline</th>'
        '<th data-sort="days">Days</th>'
        '<th data-sort="text">Date &amp; Place</th>'
        '<th data-sort="status">Status</th>'
        "</tr>"
    )
    lines.append("  </thead>")
    lines.append("  <tbody>")

    # 默认顺序：status(open→on_the_way→finished) 然后按会议名(label) 排
    def default_sort_key(r: Dict[str, Any]):
        status_rank = {"open": 0, "on_the_way": 1, "finished": 2}.get(
            r["overall_status"], 3
        )
        return (status_rank, r["label"], r["year"])

    for r in sorted(rows, key=default_sort_key):
        style = r["row_style"]
        status = r["overall_status"]
        label = r["label"]
        year = r["year"]
        ddl_utc = r["deadline_utc"]
        days_numeric = r["days_left"] if isinstance(r["days_left"], int) else ""

        ddl_iso = (
            ddl_utc.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if ddl_utc
            else ""
        )

        # <tr> 上写 data-*，供 JS 排序/筛选使用
        tr_open = (
            f'<tr style="{style}" '
            f'data-status="{status}" '
            f'data-label="{label}" '
            f'data-year="{year}" '
            f'data-deadline-utc="{ddl_iso}" '
            f'data-days="{days_numeric}">'
        )
        lines.append("    " + tr_open)

        # Conference 列
        conf_html = (
            f'<strong><a href="{r["link"]}" target="_blank" '
            f'rel="noopener noreferrer">{label} {year}</a></strong>'
        )
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

        # Deadline 列
        ddl_cell = r["deadline_str"]
        if r["deadline_comment"]:
            ddl_cell += f'<br><small>{r["deadline_comment"]}</small>'
        if r["timezone"]:
            ddl_cell += f'<br><small>Timezone: {r["timezone"]}</small>'

        # Date & Place
        date_place = ""
        if r["date_str"]:
            date_place += r["date_str"]
        if r["place"]:
            if date_place:
                date_place += "<br>"
            date_place += r["place"]

        lines.append(f"<td>{conf_html}</td>")
        lines.append(f"<td>{ddl_cell}</td>")
        lines.append(f"<td>{r['days_left_str']}</td>")
        lines.append(f"<td>{date_place}</td>")
        lines.append(f"<td>{r['status_label']}</td>")
        lines.append("    </tr>")

    lines.append("  </tbody>")
    lines.append("</table>")

    # 页脚（含更新时间和时区）
    updated = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    lines.append(
        "<p><small>"
        f"Last updated: {updated}. "
        "Data source: "
        '<a href="https://github.com/ccfddl/ccf-deadlines" target="_blank" '
        'rel="noopener noreferrer">ccfddl</a>. '
        "All countdowns are computed in UTC."
        "</small></p>"
    )

    # ---- 前端排序 & 筛选 JS ----
    lines.append("<script>")
    lines.append("(function(){")
    lines.append('  var table = document.getElementById("ccf-deadlines-table");')
    lines.append("  if (!table) return;")
    lines.append("  var tbody = table.tBodies[0];")
    lines.append("  var headers = table.querySelectorAll('th[data-sort]');")
    lines.append(
        "  function compareRows(a,b,type,asc){"
        "    var da=a.dataset, db=b.dataset, cmp=0;"
        "    if(type==='conf'){"
        "      cmp = da.label.localeCompare(db.label);"
        "    }else if(type==='deadline'){"
        "      cmp = (da.deadlineUtc||'').localeCompare(db.deadlineUtc||'');"
        "    }else if(type==='days'){"
        "      var va=parseInt(da.days||'999999',10), vb=parseInt(db.days||'999999',10);"
        "      cmp = va - vb;"
        "    }else if(type==='status'){"
        "      var order={open:0,on_the_way:1,finished:2};"
        "      cmp = (order[da.status]||3) - (order[db.status]||3);"
        "    }else{"
        "      cmp = a.innerText.localeCompare(b.innerText);"
        "    }"
        "    return asc?cmp:-cmp;"
        "  }"
    )
    lines.append(
        "  headers.forEach(function(th,idx){"
        "    th.style.cursor='pointer';"
        "    th.title='Click to sort';"
        "    th.addEventListener('click',function(){"
        "      var type=th.getAttribute('data-sort');"
        "      var asc=th.getAttribute('data-asc')!=='true';"
        "      th.setAttribute('data-asc',asc?'true':'false');"
        "      var rows=Array.prototype.slice.call(tbody.querySelectorAll('tr'));"
        "      rows.sort(function(a,b){return compareRows(a,b,type,asc);});"
        "      rows.forEach(function(r){tbody.appendChild(r);});"
        "    });"
        "  });"
    )
    # 筛选：status + 文本搜索
    lines.append(
        "  var statusSel=document.getElementById('ccf-status-filter');"
        "  var searchInput=document.getElementById('ccf-search');"
        "  function applyFilters(){"
        "    var st=statusSel.value;"
        "    var q=(searchInput.value||'').toLowerCase();"
        "    var rows=tbody.querySelectorAll('tr');"
        "    rows.forEach(function(r){"
        "      var ok=true;"
        "      if(st!=='all' && r.dataset.status!==st) ok=false;"
        "      if(q && r.innerText.toLowerCase().indexOf(q)===-1) ok=false;"
        "      r.style.display=ok?'':'none';"
        "    });"
        "  }"
    )
    lines.append(
        "  if(statusSel){statusSel.addEventListener('change',applyFilters);}"
        "  if(searchInput){searchInput.addEventListener('input',applyFilters);}"
        "})();"
    )
    lines.append("</script>")

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

        best = choose_latest_candidate(all_candidates)
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
            "days_left": best["days_left"],
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
