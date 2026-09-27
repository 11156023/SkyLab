"""統一 Jobs 服務模組。

聚合多個來源的「需要等待的任務」，正規化為單一介面提供 API/WebSocket 使用。
實作在 ``jobs_service``，呼叫端一律 ``from app.services.jobs import jobs_service``。
"""
