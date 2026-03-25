#!/usr/bin/env python3
# 請在初始化時將 {{}} 佔位符替換為你的實際資訊
"""
草稿建立腳本：讀取審核後的 md 檔 + emails.json，建立 Gmail 草稿。
用法：python3 scripts/create-drafts.py YYYY-MM-DD-郵件處理.md

安全規則：
- 只建立草稿（users.drafts.create）
- 可加標籤（users.messages.modify，僅限 addLabelIds）
- 絕不發送（禁止 +send、users.messages.send）
- 絕不刪除（禁止 users.messages.delete、users.messages.trash）
"""

import json
import sys
import os
import re
import subprocess
import base64
from email.mime.text import MIMEText
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
MODULE_DIR = SCRIPT_DIR.parent
RECORD_DIR = MODULE_DIR / "回信紀錄"

# 轉信者特殊處理
MA_WEIMING_EMAIL = "{{轉信者email}}"
MA_WEIMING_CC = ["{{轉信者email}}", "{{副本email}}"]
SIGNATURE = """
{{你的名字}}
{{品牌名稱}}企劃｜部門協調人
{{你的email}}
{{公司地址}}（{{最近交通}}）
Tel：{{公司電話}} #867
Mobile : {{手機號碼}}"""

# 安全黑名單：這些 gws 子命令絕對禁止
FORBIDDEN_COMMANDS = [
    "+send",
    "users messages send",
    "users messages delete",
    "users messages trash",
]

# 標籤名稱
LABEL_AI_DONE = "AI done"
LABEL_REFERENCE = "reference"


def run_gws_draft_create(raw_email: str, thread_id: str = "") -> dict:
    """呼叫 gws 建立 Gmail 草稿。thread_id 為空時建立獨立新信。"""
    message = {"raw": raw_email}
    if thread_id:
        message["threadId"] = thread_id
    payload = json.dumps({"message": message})

    cmd = [
        "gws", "gmail", "users", "drafts", "create",
        "--params", '{"userId":"me"}',
        "--json", payload,
        "--format", "json",
    ]

    # 安全檢查
    cmd_str = " ".join(cmd)
    for forbidden in FORBIDDEN_COMMANDS:
        if forbidden in cmd_str:
            raise RuntimeError(f"安全違規：禁止使用 {forbidden}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"草稿建立失敗: {result.stderr}")
    return json.loads(result.stdout)


def build_mime_email(to: str, subject: str, body: str,
                     cc=None,
                     in_reply_to: str = "", references: str = "") -> str:
    """建構 MIME 信件，回傳 base64url 編碼字串。支援新信和回覆。"""
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def parse_md_entries(md_content: str) -> list[dict]:
    """解析 md 檔，提取每封信的編號、狀態、草稿回覆。"""
    entries = []

    # 用 ## # 分割各封信
    sections = re.split(r"^## #(\d+)", md_content, flags=re.MULTILINE)

    # sections[0] 是 header，之後是 [num, content, num, content, ...]
    for i in range(1, len(sections) - 1, 2):
        num = int(sections[i])
        content = sections[i + 1]

        entry = {"num": num}

        # 提取 Thread ID
        thread_match = re.search(r"\*\*Thread ID\*\*:\s*(\S+)", content)
        entry["thread_id"] = thread_match.group(1) if thread_match else ""

        # 提取 Gmail ID
        gmail_id_match = re.search(r"\*\*Gmail ID\*\*:\s*(\S+)", content)
        entry["gmail_id"] = gmail_id_match.group(1) if gmail_id_match else ""

        # 提取 Message ID
        msgid_match = re.search(r"\*\*Message ID\*\*:\s*(.+?)$", content, re.MULTILINE)
        entry["message_id"] = msgid_match.group(1).strip() if msgid_match else ""

        # 提取 Draft ID（冪等性：已建草稿不重建）
        draft_id_match = re.search(r"\*\*Draft ID\*\*:\s*(\S+)", content)
        entry["draft_id"] = draft_id_match.group(1) if draft_id_match else ""

        # 提取主旨
        subject_match = re.search(r"\*\*主旨\*\*:\s*(.+?)$", content, re.MULTILINE)
        entry["subject"] = subject_match.group(1).strip() if subject_match else ""

        # 提取寄件人
        sender_match = re.search(r"\*\*寄件人\*\*:\s*(.+?)$", content, re.MULTILINE)
        entry["from"] = sender_match.group(1).strip() if sender_match else ""

        # 提取回覆對象（轉信者轉信時使用）
        reply_to_match = re.search(r"\*\*回覆對象\*\*:\s*(.+?)$", content, re.MULTILINE)
        entry["reply_to"] = reply_to_match.group(1).strip() if reply_to_match else ""

        # 判斷狀態：掃描 ### 狀態 行及其後所有行，排除 HTML comment
        status_block = re.search(
            r"### 狀態：(.+?)(?=\n---|\n## |\Z)",
            content,
            re.DOTALL
        )
        if status_block:
            # 去掉 HTML comment 再判斷
            status_text = re.sub(r"<!--.*?-->", "", status_block.group(1), flags=re.DOTALL)
        else:
            status_text = ""
        entry["is_ok"] = "✅" in status_text
        entry["is_rejected"] = "❌" in status_text

        # 提取草稿回覆
        draft_match = re.search(
            r"### 草稿回覆\n(.*?)(?=### 狀態：|---|\Z)",
            content,
            re.DOTALL
        )
        entry["draft"] = draft_match.group(1).strip() if draft_match else ""

        entries.append(entry)

    return entries


def load_emails_json(md_path: Path) -> list[dict]:
    """載入對應日期的 emails.json。"""
    # 從 md 檔名推導 json 檔名
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", md_path.name)
    if not date_match:
        raise RuntimeError(f"無法從檔名推導日期: {md_path.name}")

    json_path = md_path.parent / f"{date_match.group(1)}-emails.json"
    if not json_path.exists():
        return []

    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_reply_to_email(from_str: str) -> str:
    """從 From 欄位提取 email。"""
    match = re.search(r"<([^>]+)>", from_str)
    return match.group(1) if match else from_str


def clean_forward_subject(subject: str) -> str:
    """清理轉寄主旨：移除 Fwd:/FW:/Re: 前綴和【】括號內容。"""
    s = subject.strip()
    # 移除 Fwd: / FW: / Fw: / Re: / RE: 前綴（可能多層）
    s = re.sub(r"^(Fwd?:|FW:|RE:|Re:)\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(Fwd?:|FW:|RE:|Re:)\s*", "", s, flags=re.IGNORECASE)
    # 移除【】括號但保留內容
    s = s.replace("【", "").replace("】", "")
    return s.strip()


def clean_forwarded_body(body: str) -> str:
    """從轉寄信件 body 中提取原始寄件者的內容，移除中間轉寄 header 和來回紀錄。"""
    # 找所有 forwarded message 分隔線的位置
    forward_markers = list(re.finditer(
        r"-{5,}\s*Forwarded message\s*-{5,}",
        body,
        re.IGNORECASE,
    ))

    if forward_markers:
        last_marker = forward_markers[-1]
        after_marker = body[last_marker.end():]

        lines = after_marker.split("\n")
        content_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"^(寄件者|From|Date|Subject|To|Cc|收件者|日期|主旨)[:：]", stripped, re.IGNORECASE):
                content_start = i + 1
                continue
            content_start = i
            break

        content_lines = lines[content_start:]
        result = "\n".join(content_lines).strip()
        return result if result else body.strip()

    # 也處理「轉寄的郵件」中文格式
    chinese_markers = list(re.finditer(
        r"-{5,}\s*轉寄的郵件\s*-{5,}",
        body,
    ))

    if chinese_markers:
        last_marker = chinese_markers[-1]
        after_marker = body[last_marker.end():]

        inner_forwards = list(re.finditer(
            r"-{5,}\s*Forwarded message\s*-{5,}",
            after_marker,
            re.IGNORECASE,
        ))
        if inner_forwards:
            last_inner = inner_forwards[-1]
            after_inner = after_marker[last_inner.end():]
            lines = after_inner.split("\n")
        else:
            lines = after_marker.split("\n")

        content_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"^(寄件者|From|Date|Subject|To|Cc|收件者|日期|主旨)[:：]", stripped, re.IGNORECASE):
                content_start = i + 1
                continue
            content_start = i
            break

        content_lines = lines[content_start:]
        result = "\n".join(content_lines).strip()
        return result if result else body.strip()

    return body.strip()


def run_gws(args):
    """執行 gws 命令，回傳 stdout。"""
    cmd = ["gws"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"gws 命令失敗: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def get_or_create_label(label_name):
    """取得或建立 Gmail 標籤，回傳 label ID。"""
    try:
        raw = run_gws([
            "gmail", "users", "labels", "list",
            "--params", '{"userId":"me"}',
            "--format", "json",
        ])
        data = json.loads(raw)
        for label in data.get("labels", []):
            if label.get("name") == label_name:
                return label["id"]
    except Exception:
        pass

    try:
        raw = run_gws([
            "gmail", "users", "labels", "create",
            "--params", '{"userId":"me"}',
            "--json", json.dumps({
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            }),
            "--format", "json",
        ])
        data = json.loads(raw)
        return data.get("id", "")
    except Exception as e:
        print(f"[警告] 無法建立標籤 '{label_name}': {e}")
        return ""


def add_label_to_message(gmail_id, label_id):
    """為單封信加上標籤。僅使用 addLabelIds（不移除任何標籤）。"""
    if not label_id or not gmail_id:
        return False
    try:
        run_gws([
            "gmail", "users", "messages", "modify",
            "--params", json.dumps({"userId": "me", "id": gmail_id}),
            "--json", json.dumps({"addLabelIds": [label_id]}),
            "--format", "json",
        ])
        return True
    except Exception:
        return False


def write_draft_id_to_md(md_path, entry_num, draft_id):
    """在 MD 檔對應 entry 的 Thread ID / Gmail ID 區塊後插入 Draft ID。"""
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = rf"(## #{entry_num}\b.*?)(- \*\*Gmail ID\*\*:[^\n]*\n|(?=- \*\*CC\*\*:|(?:\n\n|\n- \*\*回覆對象)))"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        insert_pos = match.end()
        draft_line = f"- **Draft ID**: {draft_id}\n"
        if f"**Draft ID**" not in content[match.start():match.start() + 500]:
            content = content[:insert_pos] + draft_line + content[insert_pos:]
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(content)


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/create-drafts.py <md檔路徑>")
        print("範例: python3 scripts/create-drafts.py 回信紀錄/2026-03-10-郵件處理.md")
        sys.exit(1)

    md_path = Path(sys.argv[1])
    if not md_path.is_absolute():
        md_path = MODULE_DIR / md_path

    if not md_path.exists():
        print(f"[錯誤] 找不到檔案: {md_path}")
        sys.exit(1)

    print(f"[create-drafts] 讀取: {md_path}")

    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    entries = parse_md_entries(md_content)
    emails_json = load_emails_json(md_path)

    # 改用 gmail_id 做 lookup，fallback thread_id（向後相容舊 MD）
    email_by_gmail_id = {e["gmail_id"]: e for e in emails_json if e.get("gmail_id")}
    email_by_thread_id = {e["thread_id"]: e for e in emails_json}

    def lookup_email(entry):
        """用 gmail_id 查找，fallback thread_id。"""
        if entry.get("gmail_id") and entry["gmail_id"] in email_by_gmail_id:
            return email_by_gmail_id[entry["gmail_id"]]
        if entry.get("thread_id") and entry["thread_id"] in email_by_thread_id:
            return email_by_thread_id[entry["thread_id"]]
        return None

    ok_entries = [e for e in entries if e["is_ok"] and not e.get("is_rejected") and e["draft"]]
    rejected_entries = [e for e in entries if e.get("is_rejected")]

    if not ok_entries and not rejected_entries:
        print("[create-drafts] 沒有需要處理的信件。")
        sys.exit(0)

    print(f"[create-drafts] ✅ OK: {len(ok_entries)} 封 | ❌ 不回: {len(rejected_entries)} 封")

    success_count = 0
    fail_count = 0
    skip_count = 0

    for entry in ok_entries:
        num = entry["num"]
        subject = entry["subject"]
        thread_id = entry["thread_id"]
        message_id = entry["message_id"]
        draft_body = entry["draft"]

        # 冪等性 — 已有 Draft ID 的跳過
        if entry.get("draft_id"):
            print(f"  [#{num}] ⏭️ 已有草稿 (draft_id: {entry['draft_id']})，跳過")
            skip_count += 1
            continue

        # 判斷是否為轉信者轉信
        sender_email = extract_reply_to_email(entry["from"]).lower()
        is_ma_forward = (sender_email == MA_WEIMING_EMAIL)

        json_email = lookup_email(entry)

        if is_ma_forward:
            # 轉信者轉信：建立新信給原始寄件者
            to_email = extract_reply_to_email(entry["reply_to"]) if entry["reply_to"] else ""
            if not to_email:
                print(f"  [#{num}] ⚠️ 轉信者轉信但缺少「回覆對象」欄位，跳過")
                fail_count += 1
                continue

            cleaned_subject = clean_forward_subject(subject)
            reply_subject = f"【{{品牌名稱}}】回覆：{cleaned_subject}"

            # 組合 body：回覆 + 簽名
            full_body = f"{draft_body}\n{SIGNATURE}"

            print(f"  [#{num}] 建立新信（轉信者轉信）→ {to_email} ({reply_subject[:40]}...)")
            print(f"    CC: {', '.join(MA_WEIMING_CC)}")

            try:
                raw = build_mime_email(
                    to=to_email,
                    subject=reply_subject,
                    body=full_body,
                    cc=MA_WEIMING_CC,
                )
                result = run_gws_draft_create(raw)
                draft_id = result.get("id", "unknown")
                print(f"    ✅ 草稿已建立 (draft_id: {draft_id})")
                write_draft_id_to_md(md_path, num, draft_id)
                success_count += 1
            except Exception as e:
                print(f"    ❌ 失敗: {e}")
                fail_count += 1
        else:
            # 一般回覆
            to_email = extract_reply_to_email(entry["from"])
            if json_email:
                to_email = json_email.get("from_email", to_email)

            reply_subject = subject
            if not reply_subject.startswith("Re:") and not reply_subject.startswith("RE:"):
                reply_subject = f"Re: {subject}"

            print(f"  [#{num}] 建立草稿 → {to_email} ({reply_subject[:40]}...)")

            try:
                raw = build_mime_email(
                    to=to_email,
                    subject=reply_subject,
                    body=draft_body,
                    in_reply_to=message_id,
                    references=message_id,
                )
                result = run_gws_draft_create(raw, thread_id)
                draft_id = result.get("id", "unknown")
                print(f"    ✅ 草稿已建立 (draft_id: {draft_id})")
                write_draft_id_to_md(md_path, num, draft_id)
                success_count += 1
            except Exception as e:
                print(f"    ❌ 失敗: {e}")
                fail_count += 1

    # 自動標籤管理
    print("\n[create-drafts] 標籤管理...")
    label_ai_done_id = ""
    label_reference_id = ""

    # 收集需要加標籤的 gmail_id
    done_gmail_ids = []
    ref_gmail_ids = []

    # ✅ OK 且有草稿（已建或剛建）→ AI done
    for entry in ok_entries:
        gmail_id = entry.get("gmail_id") or ""
        if gmail_id and (entry.get("draft_id") or entry["draft"]):
            done_gmail_ids.append(gmail_id)

    # ❌ 不回 → AI done
    for entry in rejected_entries:
        gmail_id = entry.get("gmail_id") or ""
        if gmail_id:
            done_gmail_ids.append(gmail_id)

    if done_gmail_ids:
        label_ai_done_id = get_or_create_label(LABEL_AI_DONE)
        done_success = 0
        for gid in done_gmail_ids:
            if add_label_to_message(gid, label_ai_done_id):
                done_success += 1
        print(f"  已為 {done_success}/{len(done_gmail_ids)} 封信加上「{LABEL_AI_DONE}」標籤")

    if ref_gmail_ids:
        label_reference_id = get_or_create_label(LABEL_REFERENCE)
        ref_success = 0
        for gid in ref_gmail_ids:
            if add_label_to_message(gid, label_reference_id):
                ref_success += 1
        print(f"  已為 {ref_success}/{len(ref_gmail_ids)} 封信加上「{LABEL_REFERENCE}」標籤")

    if not done_gmail_ids and not ref_gmail_ids:
        print("  無需加標籤的信件")

    print(f"\n[create-drafts] 完成！成功: {success_count} | 跳過: {skip_count} | 失敗: {fail_count}")
    if success_count > 0:
        print("[create-drafts] 請到 Gmail 草稿匣審核後手動發送。")


if __name__ == "__main__":
    main()
