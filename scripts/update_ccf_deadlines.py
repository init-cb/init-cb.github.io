#!/usr/bin/env python3
"""
从 ccfddl/ccf-deadlines 拉取指定会议的 ddl，生成 _includes/ccf_deadlines.html
"""

import datetime as dt
from pathlib import Path

import requests
import yaml

# sub 和 name 要和 ccf-deadlines 里的 conference/<sub>/<name>.yml 一致
# label 是显示你主页上的名字
TARGET_CONFS = [
    {"sub": "AI", "name": "aaai",   "label": "AAAI"},
    {"sub": "AI", "name": "nips",   "label": "NeuIPS"},
    {"sub": "AI", "name": "cvpr",   "label": "CVPR"},
    {"sub": "AI", "name": "emnlp",   "label": "EMNLP"},
    {"sub": "AI", "name": "iccv",   "label": "ICCV"},
    {"sub": "AI", "name": "eccv",   "label": "ECCV"},
    {"sub": "AI", "name": "ijcai",   "label": "IJCAI"},
    {"sub": "MX", "name": "www",    "label": "WWW"},   # 有可能是 DB 或 MX，请到仓库确认
    {"sub": "AI", "name": "bmvc",   "label": "BMVC"},
    {"sub": "MX", "name": "miccai", "label": "MICCAI"},
    {"sub": "MX", "name": "isbi",   "label": "ISBI"},
]

# ===================== 这里开始是通用逻辑 =====================

RAW_BASE = "https://raw.githubusercontent.com/ccfddl/ccf-deadlines/main"
OUT_FILE = Path("_includes/ccf_deadlines.html")


def fetch_conf_yaml(conf):
    """
    从 ccfddl 仓库拉对应的 yml 文件
    """
    url = f"{RAW_BASE}/conference/{conf['sub']}/{conf['name']}.yml"
    print(f"Fetching {url}")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = yaml.safe_load(resp.text)
    # 有的 yml 顶层是 list，有的是单个 dict，这里统一转成 list
    if isinstance(data, dict):
        data = [data]
    return data


def find_latest_year_entry(entries):
    """
    找到最近一届会议的信息（按 year 最大的那条）
    """
    if not entries:
        return None, None

    # 通常 entries[-1] 就是最新系列，但稳妥一点：把所有 confs 扁平化再找最大 year
    latest_entry = None
    latest_conf = None
    latest_year = -1

    for entry in entries:
        confs = entry.get("confs") or []
        for conf in confs:
            year = conf.get("year")
            if isinstance(year, int) and year > latest_year:
                latest_year = year
                latest_entry = entry
                latest_conf = conf

    return latest_entry, latest_conf


def _parse_deadline_str(dstr: str):
    """
    把 deadline 字符串尽量解析成 datetime，失败则返回 None。
    例如 '2025-03-01 23:59:59' 或 '2025-03-01 23:59:59 AoE'
    """
    if not dstr or dstr == "TBD":
        return None

    # 去掉可能跟在后面的时区标记（AoE, UTC+8 等）
    parts = dstr.split()
    core = " ".join(parts[:2]) if len(parts) >= 2 else parts[0]
    try:
        # 'YYYY-mm-dd HH:MM:SS'
        return dt.datetime.fromisoformat(core.replace(" ", "T"))
    except Exception:
        return None


def pick_future_or_last_deadline(timeline):
    """
    从 timeline 中选出“下一条未过期的 ddl”；若都过期，就选最后一条。
    """
    now = dt.datetime.utcnow()
    best_item = None
    best_dt = None

    # 先找未来的
    for item in timeline:
        dstr = item.get("deadline", "")
        ddl_dt = _parse_deadline_str(dstr)
        if ddl_dt is None:
            continue
        if ddl_dt >= now and (best_dt is None or ddl_dt < best_dt):
            best_dt = ddl_dt
            best_item = item

    # 没有未来的，就选最后一条（按时间）
    if best_item is None:
        for item in timeline:
            dstr = item.get("deadline", "")
            ddl_dt = _parse_deadline_str(dstr)
            if ddl_dt is None:
                continue
            if best_dt is None or ddl_dt > best_dt:
                best_dt = ddl_dt
                best_item = item

    return best_item, best_dt


def generate_html(rows):
    """
    rows: 每个元素形如
    {
        "label": "CVPR",
        "title": "CVPR",
        "year": 2026,
        "deadline": "2025-11-01 23:59:59 AoE",
        "deadline_dt": datetime or None,
        "comment": "main track",
        "link": "https://..."
    }
    """
    lines = []
    lines.append('<table class="table table-sm">')
    lines.append("  <thead>")
    lines.append(
        "    <tr>"
        "<th>Conference</th>"
        "<th>Year</th>"
        "<th>Deadline</th>"
        "<th>Note</th>"
        "</tr>"
    )
    lines.append("  </thead>")
    lines.append("  <tbody>")

    # 按时间排序：最近的 ddl 在上面；没有时间的排在最后
    def sort_key(r):
        return (r["deadline_dt"] is None, r["deadline_dt"] or dt.datetime.max)

    for r in sorted(rows, key=sort_key):
        ddl_str = r["deadline"]
        lines.append(
            "    <tr>"
            f"<td><a href=\"{r['link']}\" target=\"_blank\" rel=\"noopener noreferrer\">{r['label']}</a></td>"
            f"<td>{r['year']}</td>"
            f"<td>{ddl_str}</td>"
            f"<td>{r['comment']}</td>"
            "</tr>"
        )

    lines.append("  </tbody>")
    lines.append("</table>")

    updated = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines.append(
        f'<p><small>Last updated: {updated} (data from '
        '<a href="https://github.com/ccfddl/ccf-deadlines" target="_blank" rel="noopener noreferrer">ccfddl</a>)</small></p>'
    )

    return "\n".join(lines) + "\n"


def main():
    rows = []

    for conf in TARGET_CONFS:
        try:
            data = fetch_conf_yaml(conf)
        except Exception as e:
            print(f"[WARN] Failed to fetch {conf['name']}: {e}")
            continue

        entry, latest = find_latest_year_entry(data)
        if not entry or not latest:
            print(f"[WARN] No valid 'confs' for {conf['name']}")
            continue

        title = entry.get("title", conf["label"])
        year = latest.get("year", "")
        link = latest.get("link", "#")
        timeline = latest.get("timeline") or []

        if not timeline:
            print(f"[WARN] No timeline for {conf['name']} {year}")
            deadline_item = {}
            deadline_dt = None
        else:
            deadline_item, deadline_dt = pick_future_or_last_deadline(timeline)
            if deadline_item is None:
                deadline_item = {}
                deadline_dt = None

        ddl_str = deadline_item.get("deadline", "TBD")
        comment = deadline_item.get("comment", "")

        rows.append(
            {
                "label": conf["label"],
                "title": title,
                "year": year,
                "deadline": ddl_str,
                "deadline_dt": deadline_dt,
                "comment": comment,
                "link": link,
            }
        )

    html = generate_html(rows)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
