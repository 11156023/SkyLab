"""Course Lab（互動式實作教學）服務層。

- flag_service: 純函式 — 答案正規化、hash 比對、進度百分比
- course_service: 路徑/房間/任務/題目 CRUD 與發布狀態機
- progress_service: 答題提交、學生/全班進度統計
- progress_hub: 老師端進度 WebSocket 推播 hub
- ai_assignment_service: 學生端 AI 評分作業投影（僅核准版本、只看本人結果）
- weekly_task_service: 班級每週任務、檢查項目與教材 PDF
- reminder_service: 學生首頁提醒（到期、審核結果、近期課堂任務）
"""
