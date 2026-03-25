#!/usr/bin/env python3
"""
每日任務分類器
掃描任務庫所有任務 → 按 triage 規則分類 → 輸出 YYYY-MM-DD-tasks.md
"""

import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent
WORK_MGMT_DIR = SCRIPT_DIR.parent
TASK_DIR = WORK_MGMT_DIR / "任務庫"
PROJECT_LIST_FILE = WORK_MGMT_DIR / "專案總表.md"
DAILY_DIR = WORK_MGMT_DIR / "每日待辦"
BIRDSEYE_SCRIPT = SCRIPT_DIR / "generate-birdseye.py"

WEEKDAY_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
DEADLINE_LOOKAHEAD_DAYS = 7
WAITING_STALE_DAYS = 7


def parse_frontmatter(filepath: Path) -> Optional[dict]:
    """解析 markdown 檔案的 YAML frontmatter（不依賴 PyYAML）"""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    # 找 --- 包裹的 frontmatter
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None

    data = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 簡單 key: value 解析
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1 :].strip()
        # 去掉引號
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        # 去掉行內註解
        comment_idx = value.find("#")
        if comment_idx > 0:
            # 確保不是在引號內的 #
            value = value[:comment_idx].strip()
        data[key] = value
    return data


def parse_project_list(filepath: Path) -> dict:
    """解析專案總表，回傳 {project_id: {name, priority_order}}"""
    projects = {}
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return projects

    for line in text.splitlines():
        # 表格行格式: | # | ID | 專案名稱 | ...
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] 是空的（| 前面），cells[1] 是 #，cells[2] 是 ID...
        if len(cells) < 4:
            continue
        order = cells[1]
        pid = cells[2]
        name = cells[3]
        if not pid.startswith("P") or not order.isdigit():
            continue
        projects[pid] = {"name": name, "order": int(order)}
    return projects


def parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_effort(value: str) -> int:
    if not value:
        return 0
    v = value.strip().lower()
    if v == "half-day":
        return 240
    m = re.match(r"(\d+)\s*(m|min|h|hr)", v)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit in ("h", "hr"):
            return num * 60
        return num
    return 0


def format_effort(minutes: int) -> str:
    if minutes <= 0:
        return "未估時"
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def classify_task(task: dict, target_date: date) -> str:
    status = task.get("status", "")
    deadline = parse_date(task.get("deadline", ""))
    schedule = parse_date(task.get("schedule", ""))

    if status in ("done", "cancelled", "someday"):
        return "hidden"

    if deadline:
        if deadline == target_date:
            return "must-do-deadline"
        if deadline < target_date:
            return "must-do-overdue"

    if status == "must-do":
        return "must-do-status"

    if schedule and schedule == target_date:
        return "must-do-schedule"

    if status == "in-progress":
        return "in-progress"

    if status == "next":
        return "if-time-allows"

    if status == "waiting":
        return "waiting"

    return "hidden"


def get_deadline_warnings(tasks: list, target_date: date) -> list:
    warnings = []
    for t in tasks:
        dl = parse_date(t.get("deadline", ""))
        if not dl:
            continue
        days_until = (dl - target_date).days
        if 1 <= days_until <= DEADLINE_LOOKAHEAD_DAYS:
            warnings.append({**t, "_days_until": days_until})
    return sorted(warnings, key=lambda x: x["_days_until"])


def check_data_quality(tasks: list, target_date: date) -> list:
    issues = []

    seen_ids = {}
    for t in tasks:
        tid = t.get("id", "")
        if tid in seen_ids:
            issues.append(f"`{tid}`：重複 ID（也出現在 {seen_ids[tid]}）")
        seen_ids[tid] = t.get("_filename", "")

    for t in tasks:
        tid = t.get("id", "?")
        status = t.get("status", "")

        if not t.get("effort", ""):
            issues.append(f"`{tid}`：缺 effort")

        dl_raw = t.get("deadline", "")
        if dl_raw and not parse_date(dl_raw):
            issues.append(f"`{tid}`：deadline 格式錯誤「{dl_raw}」")

        schedule = parse_date(t.get("schedule", ""))
        if schedule and schedule < target_date and status in ("next", "must-do"):
            issues.append(f"`{tid}`：schedule 已過（{t['schedule']}）但 status 仍為 {status}")

    return issues


def build_output(target_date, all_tasks, buckets, deadline_warnings, projects):
    weekday = WEEKDAY_ZH[target_date.weekday()]
    now_str = datetime.now().strftime("%H:%M")
    active_count = len(all_tasks)

    def proj_name(pid):
        return projects.get(pid, {}).get("name", pid)

    def task_line_checkbox(t, extra=""):
        effort_min = parse_effort(t.get("effort", ""))
        effort_str = f" 預估 {format_effort(effort_min)}" if effort_min else ""
        p = proj_name(t["project"])
        sub = f"/{t['sub_project']}" if t.get("sub_project") else ""
        return f"- [ ] **{t['title']}**（{t['id']}）{p}{sub}{effort_str}{extra}"

    lines = []
    lines.append(f"# 任務分類 — {target_date.isoformat()}（{weekday}）")
    lines.append("")
    lines.append(f"> 自動產出：{now_str} | 任務庫：{active_count} 個活躍任務")
    lines.append("")

    # Must Do
    must_do_cats = ["must-do-deadline", "must-do-schedule", "must-do-status", "must-do-overdue"]
    has_must_do = any(buckets[c] for c in must_do_cats)

    lines.append("## Must Do")
    lines.append("")

    if buckets["must-do-deadline"]:
        lines.append("### 今日 Deadline")
        for t in buckets["must-do-deadline"]:
            lines.append(task_line_checkbox(t))
        lines.append("")

    if buckets["must-do-schedule"]:
        lines.append("### 今日排程")
        for t in buckets["must-do-schedule"]:
            lines.append(task_line_checkbox(t))
        lines.append("")

    if buckets["must-do-status"]:
        for t in buckets["must-do-status"]:
            lines.append(task_line_checkbox(t))
        lines.append("")

    if buckets["must-do-overdue"]:
        lines.append("### 逾期未處理")
        for t in buckets["must-do-overdue"]:
            dl = t.get("deadline", "")
            dl_date = parse_date(dl)
            days = (target_date - dl_date).days if dl_date else "?"
            dl_short = dl_date.strftime("%m-%d") if dl_date else dl
            lines.append(task_line_checkbox(t, f" — 原定 {dl_short}（逾 {days} 天）"))
        lines.append("")

    if not has_must_do:
        lines.append("（無）")
        lines.append("")

    must_do_tasks = []
    for c in must_do_cats:
        must_do_tasks.extend(buckets[c])
    efforts = [parse_effort(t.get("effort", "")) for t in must_do_tasks]
    total_min = sum(efforts)
    estimated = sum(1 for e in efforts if e > 0)
    unestimated = len(must_do_tasks) - estimated
    lines.append(f"> Must Do 小計：{format_effort(total_min)}（{estimated} 項有估時, {unestimated} 項未估時）")
    lines.append("")

    # Deadline Warnings
    if deadline_warnings:
        lines.append("## 近期 Deadline（7 天內）")
        for t in deadline_warnings:
            days = t["_days_until"]
            dl_date = parse_date(t['deadline'])
            dl_short = dl_date.strftime("%m-%d") if dl_date else t['deadline']
            lines.append(f"- **{t['title']}**（{t['id']}）— deadline {dl_short}（剩 {days} 天）")
        lines.append("")

    # In Progress
    if buckets["in-progress"]:
        lines.append("## In Progress")
        for t in buckets["in-progress"]:
            p = proj_name(t["project"])
            lines.append(f"- **{t['title']}**（{t['id']}）{p}")
        lines.append("")

    # If Time Allows (filtered: 7-day deadline OR effort <= 15m, capped at 10)
    ita_raw = buckets["if-time-allows"]
    ita = []
    for t in ita_raw:
        dl = parse_date(t.get("deadline", ""))
        effort_min = parse_effort(t.get("effort", ""))
        days_until = (dl - target_date).days if dl else 999
        if days_until <= 7 or (effort_min > 0 and effort_min <= 15):
            ita.append(t)
    # Sort: deadline tasks first (by proximity), then by effort (smallest first)
    ita.sort(key=lambda t: (
        parse_date(t.get("deadline", "")) or date.max,
        parse_effort(t.get("effort", "")) or 999,
    ))
    ita = ita[:10]  # Hard cap

    if ita:
        lines.append("## If Time Allows")
        lines.append("")
        lines.append("| 任務 | 專案 | 預估 | 備註 |")
        lines.append("|------|------|------|------|")
        for t in ita:
            p = proj_name(t["project"])
            effort_min = parse_effort(t.get("effort", ""))
            effort_str = format_effort(effort_min)
            dl = t.get("deadline", "")
            dl_date = parse_date(dl)
            dl_short = dl_date.strftime("%m-%d") if dl_date else dl
            note = f"deadline {dl_short}" if dl else "—"
            lines.append(f"| {t['title']}（{t['id']}） | {p} | {effort_str} | {note} |")
        lines.append("")

    # Waiting
    wt = buckets["waiting"]
    if wt:
        lines.append("## Waiting")
        lines.append("")
        lines.append("| 任務 | 等誰 | 天數 | 備註 |")
        lines.append("|------|------|------|------|")
        for t in wt:
            who = t.get("waiting_on", "—") or "—"
            updated = parse_date(t.get("updated", ""))
            days = (target_date - updated).days if updated else "?"
            stale = " > 7天" if isinstance(days, int) and days > WAITING_STALE_DAYS else ""
            dl = t.get("deadline", "")
            dl_date_w = parse_date(dl)
            dl_short = dl_date_w.strftime("%m-%d") if dl_date_w else dl
            note = f"deadline {dl_short}{stale}" if dl else stale.strip() if stale else "—"
            lines.append(f"| {t['title']}（{t['id']}） | {who} | {days} 天 | {note} |")
        lines.append("")

    # Stats
    must_do_count = len(must_do_tasks)
    waiting_count = len(wt)
    stale_waiting = sum(
        1 for t in wt
        if (parse_date(t.get("updated", "")) and
            (target_date - parse_date(t.get("updated", ""))).days > WAITING_STALE_DAYS)
    )
    lines.append("## 統計")
    lines.append(f"- 活躍任務：{active_count} | Must Do：{must_do_count}（{format_effort(total_min)}）| Waiting：{waiting_count}（{stale_waiting} 項 > 7 天）")
    lines.append("")

    return "\n".join(lines)


def main():
    target_date = date.today()
    write_to_file = True

    args = sys.argv[1:]
    for arg in args:
        if arg == "--stdout":
            write_to_file = False
        else:
            try:
                target_date = datetime.strptime(arg, "%Y-%m-%d").date()
            except ValueError:
                print(f"無法解析日期：{arg}（格式：YYYY-MM-DD）", file=sys.stderr)
                sys.exit(1)

    projects = parse_project_list(PROJECT_LIST_FILE)

    if not TASK_DIR.exists():
        print(f"任務庫不存在：{TASK_DIR}", file=sys.stderr)
        sys.exit(1)

    all_tasks = []
    unparseable = []

    for f in sorted(TASK_DIR.glob("*.md")):
        fm = parse_frontmatter(f)
        if not fm:
            unparseable.append(f.name)
            continue
        status = fm.get("status", "")
        if status in ("done", "cancelled"):
            continue
        task = {
            "id": fm.get("id", f.stem),
            "title": fm.get("title", f.stem),
            "project": fm.get("project", ""),
            "sub_project": fm.get("sub_project", ""),
            "status": status,
            "priority": fm.get("priority", "normal"),
            "deadline": fm.get("deadline", ""),
            "schedule": fm.get("schedule", ""),
            "effort": fm.get("effort", ""),
            "waiting_on": fm.get("waiting_on", ""),
            "updated": fm.get("updated", ""),
            "_filename": f.name,
        }
        all_tasks.append(task)

    # Classify
    priority_order = {"urgent": 0, "normal": 1, "low": 2}
    buckets = defaultdict(list)
    for t in all_tasks:
        cat = classify_task(t, target_date)
        if cat != "hidden":
            buckets[cat].append(t)

    for cat in ("must-do-deadline", "must-do-status", "must-do-schedule"):
        buckets[cat].sort(key=lambda t: priority_order.get(t["priority"], 9))

    buckets["must-do-overdue"].sort(
        key=lambda t: parse_date(t["deadline"]) or date.min
    )

    buckets["if-time-allows"].sort(
        key=lambda t: (
            priority_order.get(t["priority"], 9),
            parse_date(t["deadline"]) or date.max,
        )
    )

    buckets["waiting"].sort(
        key=lambda t: parse_date(t["updated"]) or date.min
    )

    deadline_warnings = get_deadline_warnings(all_tasks, target_date)

    quality_issues = check_data_quality(all_tasks, target_date)
    for uf in unparseable:
        quality_issues.append(f"`{uf}`：frontmatter 解析失敗")

    output = build_output(
        target_date, all_tasks, buckets, deadline_warnings, projects
    )

    if write_to_file:
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DAILY_DIR / f"{target_date.isoformat()}-tasks.md"
        out_path.write_text(output, encoding="utf-8")
        now_str = datetime.now().strftime("%H:%M")
        must_do_count = sum(len(buckets[k]) for k in buckets if k.startswith("must-do"))
        print(f"任務分類完成 | {len(all_tasks)} 活躍 · {must_do_count} must-do · {len(quality_issues)} 品質問題 | {now_str}")

        # 順便重新生成專案鳥瞰（確保鳥瞰與任務庫同步）
        if BIRDSEYE_SCRIPT.exists():
            result = subprocess.run(
                [sys.executable, str(BIRDSEYE_SCRIPT)],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(result.stdout.strip())
            else:
                print(f"鳥瞰生成失敗：{result.stderr.strip()}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
