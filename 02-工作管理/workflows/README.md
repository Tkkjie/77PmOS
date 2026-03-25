# Workflow 模板

任務完成後的「下一步」模板。當任務被標記 done 時，系統會對照這裡的模板，自動建議下一步任務。

## 模板格式

每個模板一個 `.md` 檔，用 YAML frontmatter 描述：

---
name: 流程名稱
trigger_project: P02          # 觸發的專案 ID（必填）
trigger_title_contains: ""    # 任務標題包含的關鍵字（選填，空 = 該專案所有任務）
---

## 步驟

1. **步驟名稱**
   - status: waiting
   - waiting_on: 客戶
   - effort: 15m
   - 說明：等客戶回覆確認

2. **步驟名稱**
   - status: next
   - effort: 1h
   - 說明：根據回覆撰寫提案

## 使用規則

- 模板只是建議，永遠需要用戶確認才建立任務
- 一個專案可以有多個模板（用 trigger_title_contains 區分）
- 如果沒有匹配的模板，系統會問「這個有下一步嗎？」
- 未來可擴展 `trigger_sub_project` 欄位，目前用 project + title 即可覆蓋大部分場景
