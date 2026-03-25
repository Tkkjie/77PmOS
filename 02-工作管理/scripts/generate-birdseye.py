#!/usr/bin/env python3
"""
專案鳥瞰圖生成器
掃描任務庫所有任務 → 按 project + sub_project 分組 → 輸出 專案鳥瞰.md
"""

import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from typing import Optional

# 路徑設定
SCRIPT_DIR = Path(__file__).parent
WORK_MGMT_DIR = SCRIPT_DIR.parent
TASK_DIR = WORK_MGMT_DIR / "任務庫"
PROJECT_LIST_FILE = WORK_MGMT_DIR / "專案總表.md"
OUTPUT_FILE = WORK_MGMT_DIR / "專案鳥瞰.md"

TODAY = date.today()


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


def normalize_sub_project(value: str) -> str:
    """正規化 sub_project 名稱：去前後空白、統一全形/半形空格"""
    if not value:
        return ""
    # 統一空格
    value = re.sub(r"\s+", " ", value.strip())
    return value


def is_overdue(deadline_str: str) -> bool:
    """判斷是否逾期"""
    if not deadline_str:
        return False
    try:
        dl = datetime.strptime(deadline_str, "%Y-%m-%d").date()
        return dl < TODAY
    except ValueError:
        return False


def main():
    # 1. 解析專案總表
    projects = parse_project_list(PROJECT_LIST_FILE)

    # 2. 掃描所有任務
    tasks = []
    unparseable_files = []
    if not TASK_DIR.exists():
        print(f"任務庫不存在：{TASK_DIR}", file=sys.stderr)
        sys.exit(1)

    for f in sorted(TASK_DIR.glob("*.md")):
        fm = parse_frontmatter(f)
        if not fm:
            unparseable_files.append(f.name)
            print(f"無法解析 frontmatter：{f.name}", file=sys.stderr)
            continue
        status = fm.get("status", "")
        if status in ("done", "cancelled"):
            continue
        tasks.append(
            {
                "id": fm.get("id", f.stem),
                "title": fm.get("title", f.stem),
                "project": fm.get("project", ""),
                "sub_project": normalize_sub_project(fm.get("sub_project", "")),
                "status": status,
                "deadline": fm.get("deadline", ""),
                "priority": fm.get("priority", "normal"),
            }
        )

    # 3. 按 project 分組
    by_project = defaultdict(list)
    for t in tasks:
        by_project[t["project"]].append(t)

    # 4. 統計
    total = len(tasks)
    total_overdue = sum(1 for t in tasks if is_overdue(t["deadline"]))
    total_waiting = sum(1 for t in tasks if t["status"] == "waiting")
    total_in_progress = sum(1 for t in tasks if t["status"] == "in-progress")

    # 5. 按專案總表的優先序排序
    def project_sort_key(pid):
        if pid in projects:
            return projects[pid]["order"]
        return 999

    sorted_pids = sorted(by_project.keys(), key=project_sort_key)

    # 6. 生成 markdown
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("# 專案鳥瞰")
    lines.append("")
    lines.append(
        f"> 更新時間：{now_str} | 活躍：{total} | 進行中：{total_in_progress} | 逾期：{total_overdue} | 等待中：{total_waiting}"
    )
    lines.append("")

    status_order = {"must-do": 0, "in-progress": 1, "next": 2, "waiting": 3, "someday": 4}

    for pid in sorted_pids:
        ptasks = by_project[pid]
        pname = projects.get(pid, {}).get("name", pid)

        # 專案統計
        p_active = len(ptasks)
        p_in_progress = sum(1 for t in ptasks if t["status"] == "in-progress")
        p_overdue = sum(1 for t in ptasks if is_overdue(t["deadline"]))
        p_waiting = sum(1 for t in ptasks if t["status"] == "waiting")

        stats_parts = [f"{p_active} 活躍"]
        if p_in_progress:
            stats_parts.append(f"{p_in_progress} 進行中")
        if p_overdue:
            stats_parts.append(f"{p_overdue} 逾期")
        if p_waiting:
            stats_parts.append(f"{p_waiting} 等待中")

        lines.append(f"## {pid} {pname} [{' · '.join(stats_parts)}]")
        lines.append("")

        # 按 sub_project 分組
        by_sub = defaultdict(list)
        for t in ptasks:
            sub = t["sub_project"] if t["sub_project"] else ""
            by_sub[sub].append(t)

        # 先輸出有 sub_project 的組（按名稱排序）
        named_subs = sorted(
            [(k, v) for k, v in by_sub.items() if k], key=lambda x: x[0]
        )
        ungrouped = by_sub.get("", [])

        for sub_name, sub_tasks in named_subs:
            sub_tasks.sort(key=lambda t: status_order.get(t["status"], 99))
            lines.append(f"### {sub_name} ({len(sub_tasks)})")
            for t in sub_tasks:
                overdue_mark = " !" if is_overdue(t["deadline"]) else ""
                dl_info = f" (deadline {t['deadline']})" if t["deadline"] else ""
                lines.append(
                    f"- `{t['id']}` {t['title']} — {t['status']}{dl_info}{overdue_mark}"
                )
            lines.append("")

        # 再輸出未分組的
        if ungrouped:
            # 如果有 named subs，標示「未分組」
            if named_subs:
                lines.append(f"### 未分組 ({len(ungrouped)})")
            ungrouped.sort(key=lambda t: status_order.get(t["status"], 99))
            for t in ungrouped:
                overdue_mark = " !" if is_overdue(t["deadline"]) else ""
                dl_info = f" (deadline {t['deadline']})" if t["deadline"] else ""
                lines.append(
                    f"- `{t['id']}` {t['title']} — {t['status']}{dl_info}{overdue_mark}"
                )
            lines.append("")

        lines.append("---")
        lines.append("")

    # 附加無法解析的檔案警告
    if unparseable_files:
        lines.append("## 無法解析的檔案")
        lines.append("")
        lines.append("以下任務檔的 frontmatter 格式有誤，未納入鳥瞰圖：")
        lines.append("")
        for uf in unparseable_files:
            lines.append(f"- `{uf}`")
        lines.append("")

    # 寫入檔案
    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")

    # 輸出摘要
    warn_suffix = f" | {len(unparseable_files)} 個檔案無法解析" if unparseable_files else ""
    print(
        f"專案鳥瞰已更新 | {len(projects)} 專案 · {total} 任務 · {total_overdue} 逾期 | {now_str}{warn_suffix}"
    )


if __name__ == "__main__":
    main()
