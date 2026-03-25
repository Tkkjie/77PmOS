---
name: gmail-cli-operations
description: 透過 gws CLI 和自動化腳本讀取與操作 Gmail 的操作指南
---

# Gmail CLI 操作指南

> 透過 gws CLI 讀取和操作 Gmail。進入郵件處理流程時按需讀取。

## 自動化腳本（優先使用）

日常郵件處理請優先使用腳本，比手動 CLI 更穩定、更省 token：

### 取信 + 產出 md 檔
```bash
python3 scripts/fetch-emails.py
```
- 自動取得未讀信件、解析全文、預過濾電子報/系統通知
- 產出 `回信紀錄/YYYY-MM-DD-郵件處理.md`（含完整原始來信 blockquote）
- 產出 `回信紀錄/YYYY-MM-DD-emails.json`（結構化資料）

### 建立 Gmail 草稿
```bash
python3 scripts/create-drafts.py 回信紀錄/YYYY-MM-DD-郵件處理.md
```
- 讀取審核後的 md 檔，對 ✅ OK 的信件自動建立 Gmail 草稿
- 全程 Python 內處理（不經 shell pipe，無 base64 長度問題）

## 前置條件

gws CLI 已安裝且已認證。
如果任何 gws 命令失敗，退回手動模式（用戶直接貼上郵件內容）。

## 手動 CLI 命令（腳本不可用時的 fallback）

### 1. 查看未讀收件匣摘要

```bash
gws gmail +triage
```

輸出表格包含：date、from、id、subject。用 id 讀取個別信件。

### 2. 搜尋信件

```bash
# 搜尋未讀信件
gws gmail users messages list --params '{"userId":"me","q":"is:unread","maxResults":20}' --format json

# 搜尋特定寄件人
gws gmail users messages list --params '{"userId":"me","q":"from:example@email.com","maxResults":10}' --format json

# 搜尋特定主旨關鍵字
gws gmail users messages list --params '{"userId":"me","q":"subject:邀約","maxResults":10}' --format json
```

回傳 message id 列表，需逐一用 get 取得內容。

### 3. 讀取單封信件

```bash
gws gmail users messages get --params '{"userId":"me","id":"MESSAGE_ID","format":"full"}' --format json
```

用以下 Python 解析回傳的 JSON：

```bash
gws gmail users messages get --params '{"userId":"me","id":"MESSAGE_ID","format":"full"}' --format json 2>&1 | python3 -c "
import sys, json, base64

msg = json.load(sys.stdin)
headers = {h['name']: h['value'] for h in msg.get('payload',{}).get('headers',[])}
print('From:', headers.get('From',''))
print('To:', headers.get('To',''))
print('Subject:', headers.get('Subject',''))
print('Date:', headers.get('Date',''))
print('Message-ID:', headers.get('Message-Id', headers.get('Message-ID','')))
print('Thread-ID:', msg.get('threadId',''))
print('Labels:', ', '.join(msg.get('labelIds',[])))
print('---')

def get_body(payload):
    if 'body' in payload and payload['body'].get('data'):
        return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and part.get('body',{}).get('data'):
                return base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
            result = get_body(part)
            if result:
                return result
    return '[無法解析正文]'

print(get_body(msg['payload']))
"
```

### 4. 建立草稿（回覆信件）

```bash
gws gmail users drafts create --params '{"userId":"me"}' --json '{
  "message": {
    "threadId": "THREAD_ID",
    "raw": "BASE64_ENCODED_EMAIL"
  }
}' --format json
```

用 Python 產生 base64 編碼的 email：

```bash
python3 -c "
import base64
from email.mime.text import MIMEText

msg = MIMEText('回信正文內容', 'plain', 'utf-8')
msg['To'] = 'recipient@example.com'
msg['Subject'] = '【{{品牌名稱}}】回覆：原始主旨'
msg['In-Reply-To'] = '<原始 Message-ID>'
msg['References'] = '<原始 Message-ID>'

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
print(raw)
"
```

然後將輸出填入上面的 BASE64_ENCODED_EMAIL。

### 5. 建立草稿（新信件）

```bash
python3 -c "
import base64
from email.mime.text import MIMEText

msg = MIMEText('信件正文', 'plain', 'utf-8')
msg['To'] = 'recipient@example.com'
msg['Subject'] = '【{{品牌名稱}}】主旨'

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
print(raw)
" | xargs -I {} gws gmail users drafts create --params '{"userId":"me"}' --json '{"message":{"raw":"{}"}}'
```

### 6. 批次讀取多封信件

```bash
# 先列出 id，再逐一讀取
gws gmail users messages list --params '{"userId":"me","q":"is:unread","maxResults":10}' --format json 2>&1 | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('messages', []):
    print(m['id'])
"
```

對每個 id 執行上面的「讀取單封信件」命令。

## 安全規則

1. **絕不使用 `+send` 或 `users messages send`** — 只建草稿
2. **絕不刪除信件** — 不使用 `users messages delete` 或 `users messages trash`
3. **標籤操作限定**：`users messages modify` 僅限用於加上「AI read」標籤，不得用於其他標籤修改
4. 草稿建立後提醒用戶到 Gmail 審核並手動發送

## 降級模式

當 gws 命令失敗時（認證過期、網路問題等）：
1. 告知用戶 gws 目前不可用
2. 提示重新認證指令：`gws auth login --scopes https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/calendar`
3. 退回手動模式：請用戶直接貼上郵件內容，回信以純文字輸出

## JSON 輸出欄位對照

| 欄位 | 說明 |
|------|------|
| `id` | 郵件唯一 ID（用於 get/reply） |
| `threadId` | 對話串 ID（回覆時必須帶上） |
| `labelIds` | 標籤（UNREAD、INBOX、CATEGORY_UPDATES 等） |
| `payload.headers` | From、To、Subject、Date、Message-Id 等 |
| `snippet` | 郵件摘要（前 200 字左右） |
