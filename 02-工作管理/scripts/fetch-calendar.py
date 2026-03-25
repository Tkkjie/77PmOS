#!/usr/bin/env python3
"""
行事曆讀取腳本：從 Google Calendar 拉取指定日期的事件，計算空閒時段。
用法：python3 scripts/fetch-calendar.py [YYYY-MM-DD]
  日期預設為今天。輸出 JSON 到 stdout。
"""

import json
import sys
import subprocess
from datetime import datetime, timedelta

# === 可調整常數 ===
WORK_START = "09:00"
WORK_END = "21:00"
LUNCH_START = "12:00"
LUNCH_END = "13:00"
TIMEZONE = "+08:00"


def run_gws(args):
    """執行 gws 命令，回傳 stdout。失敗時拋出 RuntimeError。"""
    cmd = ["gws"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"gws 命令失敗: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def parse_time(time_str):
    """從 ISO datetime 字串提取 HH:MM。"""
    # 格式：2026-03-11T10:00:00+08:00
    if "T" in time_str:
        return time_str.split("T")[1][:5]
    return time_str[:5]


def time_to_minutes(hhmm):
    """HH:MM 轉為分鐘數（從 00:00 起算）。"""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def minutes_to_time(mins):
    """分鐘數轉為 HH:MM。"""
    return f"{mins // 60:02d}:{mins % 60:02d}"


def fetch_events(date_str):
    """從 Google Calendar 拉取指定日期的事件。"""
    time_min = f"{date_str}T00:00:00{TIMEZONE}"
    time_max = f"{date_str}T23:59:59{TIMEZONE}"

    raw = run_gws([
        "calendar", "events", "list",
        "--params", json.dumps({
            "calendarId": "primary",
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
        }),
        "--format", "json",
    ])

    data = json.loads(raw)
    return data.get("items", [])


def parse_events(raw_events, date_str):
    """解析事件，回傳結構化列表。"""
    events = []
    for item in raw_events:
        start = item.get("start", {})
        end = item.get("end", {})

        # 全天事件
        if "date" in start:
            events.append({
                "title": item.get("summary", "(無標題)"),
                "start": None,
                "end": None,
                "all_day": True,
                "status": item.get("status", "confirmed"),
            })
            continue

        start_dt = start.get("dateTime", "")
        end_dt = end.get("dateTime", "")

        if not start_dt or not end_dt:
            continue

        # 跳過已拒絕的事件
        if item.get("status") == "cancelled":
            continue

        # 檢查自己的出席狀態
        my_status = "accepted"
        for attendee in item.get("attendees", []):
            if attendee.get("self"):
                my_status = attendee.get("responseStatus", "accepted")
                break

        if my_status == "declined":
            continue

        events.append({
            "title": item.get("summary", "(無標題)"),
            "start": parse_time(start_dt),
            "end": parse_time(end_dt),
            "all_day": False,
            "status": my_status,
        })

    return events


def calculate_free_slots(events):
    """根據事件計算空閒時段。"""
    work_start = time_to_minutes(WORK_START)
    work_end = time_to_minutes(WORK_END)
    lunch_start = time_to_minutes(LUNCH_START)
    lunch_end = time_to_minutes(LUNCH_END)

    # 收集所有忙碌時段（含午休）
    busy = [(lunch_start, lunch_end)]

    for event in events:
        if event["all_day"] or not event["start"] or not event["end"]:
            continue
        start = time_to_minutes(event["start"])
        end = time_to_minutes(event["end"])
        # 只算工作時間內的部分
        start = max(start, work_start)
        end = min(end, work_end)
        if start < end:
            busy.append((start, end))

    # 合併重疊時段
    busy.sort()
    merged = []
    for start, end in busy:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # 計算空閒時段
    free_slots = []
    current = work_start

    for busy_start, busy_end in merged:
        if current < busy_start:
            duration = busy_start - current
            free_slots.append({
                "start": minutes_to_time(current),
                "end": minutes_to_time(busy_start),
                "duration_min": duration,
            })
        current = max(current, busy_end)

    if current < work_end:
        duration = work_end - current
        free_slots.append({
            "start": minutes_to_time(current),
            "end": minutes_to_time(work_end),
            "duration_min": duration,
        })

    return free_slots


def main():
    # 日期參數
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    # 取得事件
    try:
        raw_events = fetch_events(date_str)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    # 解析事件
    events = parse_events(raw_events, date_str)

    # 計算空閒
    timed_events = [e for e in events if not e["all_day"]]
    free_slots = calculate_free_slots(timed_events)
    total_free = sum(s["duration_min"] for s in free_slots)

    # 輸出
    result = {
        "date": date_str,
        "events": events,
        "free_slots": free_slots,
        "total_free_hours": round(total_free / 60, 1),
        "work_hours": f"{WORK_START}-{WORK_END}",
        "lunch": f"{LUNCH_START}-{LUNCH_END}",
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
