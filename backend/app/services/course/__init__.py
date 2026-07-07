"""Course Lab（互動式實作教學）服務層。

- flag_service: 純函式 — 答案正規化、hash 比對、進度百分比
- course_service: 路徑/房間/任務/題目 CRUD 與發布狀態機
- deployment_service: 秒開部署編排（委派 VMRequest 快速通道）
- progress_service: 答題提交、學生/全班進度統計
- progress_hub: 老師端進度 WebSocket 推播 hub
"""
