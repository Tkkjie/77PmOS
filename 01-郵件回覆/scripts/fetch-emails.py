#!/usr/bin/env python3
# 請在初始化時將 {{}} 佔位符替換為你的實際資訊
"""
郵件取信腳本：從 Gmail 抓取未讀信件，產出結構化 md 檔 + JSON。
用法：python3 scripts/fetch-emails.py [output_dir]
  output_dir 預設為 回信紀錄/
"""

import json
import sys
import os
import subprocess
import base64
import re
import html as html_module
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
MODULE_DIR = SCRIPT_DIR.parent
RECORD_DIR = MODULE_DIR / "回信紀錄"

# 轉信者判斷
MA_WEIMING_EMAIL = "{{轉信者email}}"

# 內部團隊 domain（用來判斷內部寄出信件）
INTERNAL_DOMAIN = "{{你的domain}}"

# CC-only 判斷：你的信箱 / 團隊信箱
USER_EMAILS = ["{{你的email}}"]
TEAM_EMAILS = ["{{團隊email}}"]

# 「AI read」標籤 ID（首次使用時自動建立）
AI_READ_LABEL_NAME = "AI read"

# 預過濾：已知電子報/系統通知的 domain 或寄件人 pattern
NEWSLETTER_DOMAINS = [
    "bnext.com.tw",
    "ikala.ai",
    "googlemail.com",  # mailer-daemon
    "cw.com.tw",
    "mail.hbrtaiwan.com",
    "trello.com",
    "read.ai",
    "ebilling.com.tw",
    "cathaylife.com.tw",
    "cathayholdings.com.tw",
    "sendgrid.net",
    "mailchimp.com",
]

MARKETING_SUBDOMAIN_PATTERNS = [
    "mailhunter.",
    "e.read.ai",
    "mail.ebilling.",
]

NEWSLETTER_SENDERS = [
    "mailer-daemon@",
    "noreply@",
    "no-reply@",
    "do-not-reply@",
    "donotreply@",
    "notification@",
    "notifications@",
    "alert@",
    "alerts@",
    "billing@",
    "invoice@",
    "receipt@",
    "calendar-notification@google.com",
]

NEWSLETTER_SUBJECT_PATTERNS = [
    r"電子報",
    r"newsletter",
    r"週報",
    r"早鳥",
    r"開課",
    r"Delivery Status Notification",
    r"spam report",
    r"^Accepted:|^Declined:|^Tentatively accepted:",  # Google Calendar RSVPs
    r"電子發票",
    r"發票開立",
    r"Meeting Report",
    r"帳戶.*(?:刪除|停用|到期)",
    r"(?:下載|登入).*(?:抽獎|優惠|點數)",
]


def run_gws(args):
    """執行 gws 命令，回傳 stdout。失敗時拋出 RuntimeError。"""
    cmd = ["gws"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"gws 命令失敗: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def get_label_id_by_name(label_name):
    """根據標籤名稱取得 label ID（大小寫不敏感比對）。找不到回傳 None。"""
    try:
        raw = run_gws([
            "gmail", "users", "labels", "list",
            "--params", '{"userId":"me"}',
            "--format", "json",
        ])
        data = json.loads(raw)
        for label in data.get("labels", []):
            if label.get("name", "").lower() == label_name.lower():
                return label["id"]
    except Exception:
        pass
    return None


def get_unread_ids(max_results=30):
    """取得未讀信件的 id 列表。回傳 [{"id": ..., "threadId": ...}, ...]

    注意：Gmail q 參數的 -label: 對大小寫敏感，但用戶建立標籤時大小寫可能不一致。
    因此 query 加上常見大小寫變體，並在 main() 中用 labelIds 做二次精確排除。
    """
    raw = run_gws([
        "gmail", "users", "messages", "list",
        "--params", json.dumps({
            "userId": "me",
            "q": "is:unread -label:AI-read -label:reference -label:Reference -label:AI-done",
            "maxResults": max_results,
        }),
        "--format", "json",
    ])
    data = json.loads(raw)
    return data.get("messages", [])


def get_message(msg_id):
    """讀取單封信件完整內容，回傳 parsed dict。"""
    raw = run_gws([
        "gmail", "users", "messages", "get",
        "--params", json.dumps({
            "userId": "me",
            "id": msg_id,
            "format": "full",
        }),
        "--format", "json",
    ])
    return json.loads(raw)


def extract_headers(msg):
    """從 Gmail API payload 提取常用 headers。"""
    headers = {}
    for h in msg.get("payload", {}).get("headers", []):
        headers[h["name"]] = h["value"]
    return {
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "message_id": headers.get("Message-Id", headers.get("Message-ID", "")),
        "thread_id": msg.get("threadId", ""),
        "gmail_id": msg.get("id", ""),
        "labels": msg.get("labelIds", []),
    }


def get_body(payload):
    """Recursively parse MIME payload, get text body. Falls back to HTML with stripping.
    Priority: direct body (text/plain) > parts (text/plain) > direct body (text/html) > parts (text/html)."""
    # 1. Direct body - preserve original priority (check direct body first)
    if "body" in payload and payload["body"].get("data"):
        raw = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        if not payload.get("mimeType", "").startswith("text/html"):
            return raw
    # 2. Parts - look for text/plain first
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            result = get_body(part)
            if result and result != "[No parseable body]":
                return result
    # 3. Direct body - HTML fallback (strip tags)
    if "body" in payload and payload["body"].get("data"):
        raw = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        if payload.get("mimeType", "").startswith("text/html"):
            return strip_html(raw)
    # 4. Parts - look for text/html and strip
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType", "").startswith("text/html") and part.get("body", {}).get("data"):
                raw = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                return strip_html(raw)
    return "[No parseable body]"


def trim_quoted_reply(body):
    """移除 email body 中的引用回覆歷史，只保留最新一封的內容。
    偵測常見的引用分隔模式（On ... wrote:、寄件者：、---------- Forwarded message 除外）。
    轉寄信件保留完整內容（因為轉寄的本文就是原始來信）。"""
    lines = body.strip().split("\n")

    # 先判斷是否為轉寄信件（保留完整內容，但仍移除轉寄鏈中更早的引用）
    is_forward = any("Forwarded message" in line for line in lines[:10])

    # 引用回覆的分隔模式
    reply_patterns = [
        r"^On .+ wrote:$",                          # English: On Mon, ... wrote:
        r"^>\s*On .+ wrote:$",                       # Quoted: > On Mon, ... wrote:
        r"^.+於\s*\d{4}年.+寫道：",                   # 中文：xxx 於 2026年... 寫道：
        r"^>\s*.+於\s*\d{4}年.+寫道：",               # 引用中文
        r"^From:.+Sent:.+",                          # Outlook style
        r"^寄件者:.+寄件日期:.+",                      # Outlook 中文
        r"^_{10,}",                                   # Outlook separator (______)
        r"^-{10,}$",                                  # Long dash separator
    ]

    if is_forward:
        # 轉寄信件：找到轉寄內容後，再找更深層的引用並截斷
        last_fwd_idx = 0
        for i, line in enumerate(lines):
            if "Forwarded message" in line:
                last_fwd_idx = i
        for i in range(last_fwd_idx + 1, len(lines)):
            for pattern in reply_patterns:
                if re.search(pattern, lines[i].strip()):
                    return "\n".join(lines[:i]).rstrip()
        return body.strip()
    else:
        # 一般回覆信件：找到第一個引用分隔就截斷
        for i, line in enumerate(lines):
            stripped = line.strip()
            for pattern in reply_patterns:
                if re.search(pattern, stripped):
                    return "\n".join(lines[:i]).rstrip()
        return body.strip()


def extract_sender_name(from_str):
    """從 'Name <email>' 格式提取名稱。"""
    match = re.match(r'"?([^"<]+)"?\s*<', from_str)
    if match:
        return match.group(1).strip()
    return from_str.split("@")[0] if "@" in from_str else from_str


def extract_sender_email(from_str):
    """從 'Name <email>' 格式提取 email。"""
    match = re.search(r"<([^>]+)>", from_str)
    if match:
        return match.group(1)
    return from_str


def strip_html(text):
    """Roughly strip HTML tags, keep readable text."""
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_module.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


MAX_BODY_LINES = 50
MAX_BODY_CHARS = 3000

def truncate_body(body_text):
    """Truncate overly long email bodies in MD output."""
    lines = body_text.split("\n")
    if len(lines) > MAX_BODY_LINES or len(body_text) > MAX_BODY_CHARS:
        truncated = "\n".join(lines[:MAX_BODY_LINES])
        if len(truncated) > MAX_BODY_CHARS:
            truncated = truncated[:MAX_BODY_CHARS]
        return truncated + "\n\n(...truncated, full content in JSON file)"
    return body_text


def is_newsletter(headers, body):
    """判斷是否為電子報/系統通知。回傳 (is_newsletter, reason)。"""
    sender_email = extract_sender_email(headers["from"]).lower()

    for domain in NEWSLETTER_DOMAINS:
        if domain in sender_email:
            if "mailer-daemon" in sender_email or "googlemail.com" in sender_email:
                return True, "系統通知"
            return True, "電子報"

    # Marketing subdomain check
    for pattern in MARKETING_SUBDOMAIN_PATTERNS:
        if pattern in sender_email:
            return True, "marketing platform"

    for pattern in NEWSLETTER_SENDERS:
        if pattern in sender_email:
            return True, "系統通知"

    subject = headers["subject"]
    for pattern in NEWSLETTER_SUBJECT_PATTERNS:
        if re.search(pattern, subject, re.IGNORECASE):
            return True, "電子報"

    return False, ""


def is_internal_outgoing(headers):
    """判斷是否為內部團隊寄出的信（寄件人是內部 domain 且不是轉信者轉信）。"""
    sender_email = extract_sender_email(headers["from"]).lower()
    # 轉信者轉信不算「內部寄出」，那是需要處理的外部信
    if sender_email == MA_WEIMING_EMAIL:
        return False
    return INTERNAL_DOMAIN in sender_email


def is_cc_only(headers):
    """判斷用戶是否只在 CC 而不在 To。
    若用戶和團隊信箱都不在 To，但用戶在 CC → CC-only（可能僅供參考）。
    """
    to_lower = headers["to"].lower()
    for email in USER_EMAILS + TEAM_EMAILS:
        if email in to_lower:
            return False
    cc_lower = headers["cc"].lower()
    for email in USER_EMAILS:
        if email in cc_lower:
            return True
    return False


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

    # 建立新標籤
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


def add_label_to_messages(gmail_ids, label_id):
    """批量為信件加上標籤。"""
    if not label_id:
        return
    success = 0
    for msg_id in gmail_ids:
        try:
            run_gws([
                "gmail", "users", "messages", "modify",
                "--params", json.dumps({"userId": "me", "id": msg_id}),
                "--json", json.dumps({"addLabelIds": [label_id]}),
                "--format", "json",
            ])
            success += 1
        except Exception:
            pass
    print(f"[fetch-emails] 已為 {success}/{len(gmail_ids)} 封信加上「{AI_READ_LABEL_NAME}」標籤")


def generate_md(emails_external, emails_cc_only, emails_internal, emails_skipped, today):
    """產出郵件處理 md 檔內容。
    外部信在前（含全文），CC副本在中間（摘要表），內部信和電子報在最後。"""
    lines = []
    lines.append(f"# {today} 郵件處理\n")
    lines.append(f"> 狀態：待審核 | 信件數：{len(emails_external)}（需處理）| 已處理：0")
    cc_note = f" | CC副本：{len(emails_cc_only)}" if emails_cc_only else ""
    internal_note = f" | 內部信：{len(emails_internal)}" if emails_internal else ""
    skip_note = f" | 電子報/系統：{len(emails_skipped)}" if emails_skipped else ""
    lines.append(f"> {cc_note}{internal_note}{skip_note}（見文末）\n")
    lines.append("---\n")

    # Summary table for quick orientation
    if emails_external:
        lines.append("## 速覽\n")
        lines.append("| # | 寄件人 | 主旨 | 日期 |")
        lines.append("|---|--------|------|------|")
        for idx, email in enumerate(emails_external, 1):
            h = email["headers"]
            sender_name = extract_sender_name(h["from"])
            subject = h["subject"][:50] + ("..." if len(h["subject"]) > 50 else "")
            date_str = h["date"][:16] if h["date"] else ""
            lines.append(f"| {idx} | {sender_name} | {subject} | {date_str} |")
        lines.append("")
        lines.append("---\n")

    # === 外部信件（需處理，含全文）===
    counter = 0
    for email in emails_external:
        counter += 1
        h = email["headers"]
        sender_name = extract_sender_name(h["from"])
        body_text = email["body"]

        lines.append(f'## #{counter} | ⬜ 待分類 — {sender_name}')
        lines.append(f'- **寄件人**: {h["from"]}')
        lines.append(f'- **主旨**: {h["subject"]}')
        lines.append(f'- **分類**: （AI 填寫）')
        lines.append(f'- **Message ID**: {h["message_id"]}')
        lines.append(f'- **Thread ID**: {h["thread_id"]}')
        lines.append(f'- **Gmail ID**: {h["gmail_id"]}')
        if h["cc"]:
            lines.append(f'- **CC**: {h["cc"]}')
        # 轉信者轉信：多一個「回覆對象」欄位供 AI 填寫
        if extract_sender_email(h["from"]).lower() == MA_WEIMING_EMAIL:
            lines.append(f'- **回覆對象**: （AI 從轉寄內容辨識原始寄件者，格式：名稱 <email>）')
        lines.append("")

        lines.append("### 原始來信\n")
        trimmed_body = trim_quoted_reply(body_text)
        trimmed_body = truncate_body(trimmed_body)
        for line in trimmed_body.split("\n"):
            lines.append(f"> {line}")
        lines.append("")

        lines.append("### 來信摘要")
        lines.append("（AI 填寫）\n")

        lines.append("### 建議動作")
        lines.append("（AI 填寫）\n")

        lines.append("### 草稿回覆")
        lines.append("（AI 填寫）\n")

        lines.append("### 狀態：⬜ 待審核")
        lines.append("<!-- 改成 ✅ OK / ✏️ 需修改：你的意見 / ⏳ 待決策 / ❌ 不回 -->\n")
        lines.append("---\n")

    # === CC 副本（用戶僅在 CC，可能僅供參考）===
    if emails_cc_only:
        lines.append("## 可能僅供參考（CC 副本）\n")
        lines.append("| # | 寄件人 | 主旨 | 內容預覽 | Gmail ID |")
        lines.append("|---|--------|------|---------|----------|")
        for email in emails_cc_only:
            h = email["headers"]
            counter += 1
            sender_name = extract_sender_name(h["from"])
            subject = h["subject"][:50] + ("..." if len(h["subject"]) > 50 else "")
            body_preview = email["body"][:80].replace("\n", " ").replace("|", "｜").strip()
            if len(email["body"]) > 80:
                body_preview += "..."
            lines.append(f"| {counter} | {sender_name} | {subject} | {body_preview} | {h['gmail_id']} |")
        lines.append("")

    # === 內部寄出信件（僅摘要，不含全文）===
    if emails_internal:
        lines.append("## 內部寄出信件（僅供參考，不需回覆）\n")
        lines.append("| # | 寄件人 | 主旨 | 收件人/CC | 日期 |")
        lines.append("|---|--------|------|----------|------|")
        for email in emails_internal:
            h = email["headers"]
            counter += 1
            sender_name = extract_sender_name(h["from"])
            subject = h["subject"][:50] + ("..." if len(h["subject"]) > 50 else "")
            to_cc = h["to"][:30]
            if h["cc"]:
                to_cc += f' (CC: {h["cc"][:30]})'
            date = h["date"][:16] if h["date"] else ""
            lines.append(f"| {counter} | {sender_name} | {subject} | {to_cc} | {date} |")
        lines.append("")

    # === 電子報/系統通知備查表 ===
    if emails_skipped:
        lines.append("## 不需處理的信件（備查）\n")
        lines.append("| # | 寄件人 | 主旨 | 原因 |")
        lines.append("|---|--------|------|------|")
        for email in emails_skipped:
            h = email["headers"]
            counter += 1
            sender_name = extract_sender_name(h["from"])
            reason = email.get("skip_reason", "—")
            subject = h["subject"][:50] + ("..." if len(h["subject"]) > 50 else "")
            lines.append(f"| {counter} | {sender_name} | {subject} | {reason} |")
        lines.append("")

    return "\n".join(lines)


def generate_json(all_emails):
    """產出 JSON 格式的信件資料（供 create-drafts.py 使用）。"""
    result = []
    for email in all_emails:
        h = email["headers"]
        result.append({
            "gmail_id": h["gmail_id"],
            "thread_id": h["thread_id"],
            "message_id": h["message_id"],
            "from": h["from"],
            "from_email": extract_sender_email(h["from"]),
            "from_name": extract_sender_name(h["from"]),
            "to": h["to"],
            "cc": h["cc"],
            "subject": h["subject"],
            "date": h["date"],
            "body": email["body"],
            "is_newsletter": email.get("is_newsletter", False),
            "is_internal": email.get("is_internal", False),
            "skip_reason": email.get("skip_reason", ""),
        })
    return result


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else RECORD_DIR

    print(f"[fetch-emails] 開始取信... ({today})")

    # Step 1: 取得未讀信件 ID
    try:
        msg_list = get_unread_ids(max_results=30)
    except RuntimeError as e:
        print(f"[錯誤] 無法取得信件列表: {e}")
        sys.exit(1)

    if not msg_list:
        print("[fetch-emails] 沒有未讀信件。")
        sys.exit(0)

    print(f"[fetch-emails] 找到 {len(msg_list)} 封未讀信件，開始逐封讀取...")

    # 預先取得要排除的標籤 ID（解決標籤大小寫不一致問題）
    exclude_label_ids = set()
    for name in ["reference", "AI-read", "AI-done", "AI read"]:
        lid = get_label_id_by_name(name)
        if lid:
            exclude_label_ids.add(lid)
    if exclude_label_ids:
        print(f"[fetch-emails] 已取得排除標籤 ID: {len(exclude_label_ids)} 個")

    # Step 2: 逐封讀取完整內容
    all_emails = []
    skipped_by_label = 0
    for idx, msg_ref in enumerate(msg_list, 1):
        msg_id = msg_ref["id"]
        try:
            msg = get_message(msg_id)

            # 二次過濾：用 labelIds 精確排除（解決 Gmail q 大小寫問題）
            msg_labels = set(msg.get("labelIds", []))
            if exclude_label_ids and (msg_labels & exclude_label_ids):
                skipped_by_label += 1
                print(f"  [{idx}/{len(msg_list)}] ⏭ 已標記信件，跳過 (id={msg_id})")
                continue

            headers = extract_headers(msg)
            body = get_body(msg["payload"])

            email_data = {"headers": headers, "body": body}

            # 預過濾：電子報/系統通知
            is_nl, reason = is_newsletter(headers, body)
            if is_nl:
                email_data["is_newsletter"] = True
                email_data["skip_reason"] = reason
            # 判斷：內部寄出信件
            elif is_internal_outgoing(headers):
                email_data["is_internal"] = True

            all_emails.append(email_data)
            print(f"  [{idx}/{len(msg_list)}] {headers['subject'][:40]}...")
        except Exception as e:
            print(f"  [{idx}/{len(msg_list)}] 讀取失敗 (id={msg_id}): {e}")

    if skipped_by_label:
        print(f"[fetch-emails] 已排除 {skipped_by_label} 封已標記信件（reference/AI-read/AI-done）")

    # Step 3: 四分類：外部需處理 / CC副本 / 內部寄出 / 電子報系統
    emails_external = [e for e in all_emails if not e.get("is_newsletter") and not e.get("is_internal")]
    emails_cc_only = [e for e in emails_external if is_cc_only(e["headers"])]
    emails_external = [e for e in emails_external if not is_cc_only(e["headers"])]
    emails_internal = [e for e in all_emails if e.get("is_internal")]
    emails_skipped = [e for e in all_emails if e.get("is_newsletter")]

    cc_note = f" | CC副本: {len(emails_cc_only)}" if emails_cc_only else ""
    print(f"[fetch-emails] 需處理: {len(emails_external)}{cc_note} | 內部信: {len(emails_internal)} | 電子報/系統: {len(emails_skipped)}")

    # Step 4: 產出 md 檔（同天重跑時 append，不覆寫已審核內容）
    os.makedirs(output_dir, exist_ok=True)

    md_path = output_dir / f"{today}-郵件處理.md"
    if md_path.exists():
        print(f"[fetch-emails] ⚠️ 今日 MD 檔已存在，將 append 新信到尾部（不覆寫）")
        md_content = generate_md(emails_external, emails_cc_only, emails_internal, emails_skipped, today)
        # 只 append 信件區塊（跳過 header），用分隔線隔開
        with open(md_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n---\n\n## ⬇️ 追加取信（{datetime.now().strftime('%H:%M')}）\n\n")
            # 去掉 generate_md 產出的 header（取第一個 --- 之後的內容）
            parts = md_content.split("---\n", 1)
            if len(parts) > 1:
                f.write(parts[1])
            else:
                f.write(md_content)
    else:
        md_content = generate_md(emails_external, emails_cc_only, emails_internal, emails_skipped, today)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
    print(f"[fetch-emails] 已產出: {md_path}")

    # Step 5: 產出 JSON（同天重跑時 merge，不覆寫）
    json_path = output_dir / f"{today}-emails.json"
    json_data = generate_json(all_emails)
    if json_path.exists():
        print(f"[fetch-emails] ⚠️ 今日 JSON 檔已存在，將 merge 新信（不覆寫）")
        with open(json_path, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
        existing_ids = {e["gmail_id"] for e in existing_data}
        new_entries = [e for e in json_data if e["gmail_id"] not in existing_ids]
        merged_data = existing_data + new_entries
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(merged_data, f, ensure_ascii=False, indent=2)
        print(f"[fetch-emails] 新增 {len(new_entries)} 封，合計 {len(merged_data)} 封")
    else:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"[fetch-emails] 已產出: {json_path}")

    # Step 6: 加上「AI read」標籤
    all_gmail_ids = [e["headers"]["gmail_id"] for e in all_emails]
    if all_gmail_ids:
        label_id = get_or_create_label(AI_READ_LABEL_NAME)
        if label_id:
            add_label_to_messages(all_gmail_ids, label_id)

    print(f"[fetch-emails] 完成！請用 AI 填寫分類和草稿回覆。")


if __name__ == "__main__":
    main()
