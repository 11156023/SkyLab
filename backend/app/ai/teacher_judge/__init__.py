"""AI 評分表（teacher judge）套件。

刻意不在套件層級 re-export 任何名稱：呼叫端一律直接 import 子模組
（``service``、``session_service``、``config``…），避免 ``import`` 任一子模組
時連帶載入整個 ``service.py`` 與 vLLM client，也避免與
``app.infrastructure.ai.teacher_judge`` 形成循環 import。
"""
