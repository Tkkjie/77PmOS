# PersonalOS Starter — 用 Claude Code 打造你的個人工作系統

> 不需要會寫程式。你只需要會打字。

## 這是什麼

這是一套用 AI 幫你處理日常工作的系統。分兩階段上手：

**階段一（今天就能用）**：郵件回覆 + 任務管理
**階段二（進階自動化）**：晨間流程、Gmail 自動掃描、行事曆整合、Slack 掃描

先把階段一用熟，再往階段二走。

---

## 階段一：郵件回覆 + 任務管理

### 安裝（5 分鐘）

**Step 1：安裝 VSCode**
1. 前往 https://code.visualstudio.com/ 下載安裝
2. 開啟 VSCode

**Step 2：安裝 Claude Code**
1. 在 VSCode 左側點擊「Extensions」（方塊圖示）
2. 搜尋「Claude Code」
3. 點擊「Install」
4. 安裝完成後，左側會出現 Claude Code 的圖示

**Step 3：開始使用**
1. 開啟 VSCode
2. 點擊左側的 Claude Code 圖示
3. 在對話框貼上以下指令：

```
幫我把這個 repo clone 下來，然後開啟資料夾：
https://github.com/___/PersonalOS-Starter
```

Claude 會幫你下載並開啟。

**Step 4：安裝 Superpowers 技能包**

在 Claude Code 對話框依序貼上這兩行指令：

```
/plugin marketplace add obra/superpowers-marketplace
```

等它完成後再輸入：

```
/plugin install superpowers@superpowers-marketplace
```

這會讓你的 Claude 多出 20+ 種工作技能，包括：
- 動手前先釐清需求（brainstorming）
- 把大任務拆成步驟（writing-plans）
- 按步驟執行不跳步（executing-plans）
- 出問題時有條理排查（systematic-debugging）

安裝完成後，輸入：「幫我初始化設定」

### 初始化

Claude 會問你幾個問題：
- 你的名字（用在回信署名）
- 你的團隊/品牌名稱
- 你的角色（PM/AM/企劃等）
- 你的 email 語氣偏好
- **你平常收到哪些類型的信**（Claude 會幫你調整分類和模板）

系統內建的信件分類（合作邀約、觀眾提案等）是範例。Claude 會引導你改成適合你工作的分類，例如「客戶來信」「廠商邀約」「媒體詢問」等。模板也可以邊用邊改。

### 日常使用

**回信**：把收到的信貼到 Claude Code，說「幫我回這封信」
1. 自動判斷信件類型
2. 從模板庫找到最適合的回信範本
3. 產出你可以直接複製貼上的回信草稿

**任務管理**：
- 「記一下：下週要交提案」→ 自動記進收件匣
- 「今天做什麼」→ 整理出今日待辦
- 「做完了 XXX」→ 標記完成 + 建議下一步

### 讓系統越來越聰明

- Claude 做對了 → 它會自動記住好的做法
- Claude 做錯了 → 告訴它哪裡不對，它會更新記憶
- 回信品質好 → 跟 Claude 說「把這個存成模板」
- 遇到新的信件類型 → Claude 會幫你建新的分類 + 模板
- 想改 AI 行為 → 改 CLAUDE.md 或跟 Claude 說

---

## 階段二：進階自動化

用熟階段一後，可以解鎖更強的自動化功能。每個功能都是跟 Claude 說一句話，它會一步步帶你設定。

### Gmail 自動掃描
跟 Claude 說：「幫我設定 Gmail 連接」

Claude 會引導你：
1. 安裝 gws CLI 工具
2. 建立 Google Cloud 專案 + 啟用 Gmail API
3. 完成 OAuth 認證

設定完成後，`/morning` 晨間流程會自動掃描你的收件匣，不用再手動貼信。

### Google Calendar 整合
跟 Claude 說：「幫我設定 Google Calendar」

設定完成後，`/morning` 會自動讀取今天的行事曆，根據會議空檔幫你排任務。

### Slack 掃描
跟 Claude 說：「幫我設定 Slack 掃描」

Claude 會引導你：
1. 建立 Slack App + 取得 API Token
2. 設定要掃描的頻道

設定完成後，`/morning` 會自動掃描 Slack 有沒有 mention 你的訊息。

### 晨間流程（/morning）
以上三個 API 設定完成後，每天早上輸入 `/morning`，Claude 會：
1. 自動掃描 Gmail、行事曆、Slack、任務庫
2. 產出一份審核表讓你快速決策
3. 根據你的決定產出今日排程

沒有設定 API 的部分會自動跳過，不影響其他功能。

---

## 系統結構（參考用）

```
PersonalOS-Starter/
├── CLAUDE.md              ← AI 的規則書（最重要的檔案）
├── 00-共享層/              ← 你的個人設定
├── 01-郵件回覆/            ← 回信系統
│   ├── skills/            ← AI 的回信技能
│   ├── 模板庫/            ← 回信範本
│   └── scripts/           ← 自動化腳本（階段二）
├── 02-工作管理/            ← 任務系統
│   ├── 收件匣.md          ← 快速記事
│   ├── 任務庫/            ← 所有任務
│   └── 每日待辦/          ← 每天一份
└── .claude/               ← Claude Code 設定
    └── commands/          ← 快捷指令（/morning 等）
```

## 常見問題

**Q: 我完全不會程式，真的能用嗎？**
A: 可以。所有操作都是跟 Claude 對話，用中文就好。系統裡的程式碼是給 Claude 看的，不是給你看的。

**Q: 我的資料安全嗎？**
A: 你的資料都存在你自己的電腦上。Claude Code 處理時會傳送到 Anthropic 的伺服器，但不會永久儲存。

**Q: 回信會自動發送嗎？**
A: 不會。系統只會建立草稿，你審核確認後才手動發送。

**Q: 怎麼客製化模板？**
A: 直接編輯 `01-郵件回覆/模板庫/` 裡的 .md 檔案，或跟 Claude 說「幫我改 XX 模板」。

**Q: 階段二的 API 設定很難嗎？**
A: 不難，Claude 會一步步帶你做。大約需要 10-15 分鐘，只需要做一次。

**Q: 遇到問題怎麼辦？**
A: 直接跟 Claude 說你遇到什麼問題，它通常能幫你解決。
