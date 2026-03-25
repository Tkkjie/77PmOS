#!/usr/bin/env python3
"""掃描 waiting 任務，對照 workflow 模板，產出摘要報告。"""

import re
from datetime import datetime
from pathlib import Path

TASK_DIR = Path(__file__).parent.parent / "任務庫"
WORKFLOW_DIR = Path(__file__).parent.parent / "workflows"


def parse_frontmatter(filepath):
    """解析 markdown 檔案的 YAML frontmatter，回傳 dict。"""
    text = filepath.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def get_waiting_tasks():
    """取得所有 status=waiting 的任務。"""
    tasks = []
    if not TASK_DIR.exists():
        return tasks
    for f in TASK_DIR.glob("T-*.md"):
        fm = parse_frontmatter(f)
        if fm.get("status") == "waiting":
            tasks.append({**fm, "file": f.name})
    return tasks


def get_workflows():
    """取得所有 workflow 模板。"""
    workflows = []
    if not WORKFLOW_DIR.exists():
        return workflows
    for f in WORKFLOW_DIR.glob("*.md"):
        if f.name == "README.md":
            continue
        fm = parse_frontmatter(f)
        if fm.get("name"):
            workflows.append({**fm, "file": f.name})
    return workflows


def check_overdue_waiting(tasks, threshold_days=7):
    """找出等待超過 threshold 天的任務。"""
    today = datetime.now().date()
    overdue = []
    for t in tasks:
        updated = t.get("updated", "")
        if not updated:
            continue
        try:
            updated_date = datetime.strptime(updated, "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (today - updated_date).days
        if days >= threshold_days:
            overdue.append({
                "id": t.get("id", "?"),
                "title": t.get("title", "?"),
                "waiting_on": t.get("waiting_on", "?"),
                "days": days,
                "project": t.get("project", "?"),
            })
    return sorted(overdue, key=lambda x: x["days"], reverse=True)


def match_workflows(tasks, workflows):
    """比對 waiting 任務是否有對應的 workflow 模板。"""
    matched = []
    for t in tasks:
        for w in workflows:
            project_match = t.get("project") == w.get("trigger_project")
            title_kw = w.get("trigger_title_contains", "")
            title_match = not title_kw or title_kw in t.get("title", "")
            if project_match and title_match:
                matched.append({
                    "task_id": t.get("id", "?"),
                    "task_title": t.get("title", "?"),
                    "workflow": w.get("name", "?"),
                    "waiting_on": t.get("waiting_on", "?"),
                })
    return matched


def main():
    tasks = get_waiting_tasks()
    workflows = get_workflows()

    if not tasks:
        print("無 waiting 任務")
        return

    print(f"Waiting 任務：共 {len(tasks)} 項\n")

    # 超時等待
    overdue = check_overdue_waiting(tasks)
    if overdue:
        print("等待超過 7 天：")
        for o in overdue:
            print(f"  - [{o['id']}] {o['title']}（等 {o['waiting_on']}，已 {o['days']} 天）")
        print()

    # Workflow 匹配
    matched = match_workflows(tasks, workflows)
    if matched:
        print("有 Workflow 模板的 waiting 任務：")
        for m in matched:
            print(f"  - [{m['task_id']}] {m['task_title']} → 模板：{m['workflow']}")
        print()

    # 無匹配
    matched_ids = {m["task_id"] for m in matched}
    unmatched = [t for t in tasks if t.get("id") not in matched_ids]
    if unmatched:
        print(f"無模板的 waiting 任務：{len(unmatched)} 項")
        for t in unmatched:
            print(f"  - [{t.get('id', '?')}] {t.get('title', '?')}（等 {t.get('waiting_on', '?')}）")


if __name__ == "__main__":
    main()
