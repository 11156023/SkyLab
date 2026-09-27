"""虛擬教室互動系統：VNC fan-out session 管理與教室信令。

各模組請直接從子模組 import（``presence``、``vnc_session_manager``、
``classroom_service``、``broadcast_hub``——後者是教室信令與課程進度兩個
推播 hub 共用的骨架）。

注意：下面這行把套件屬性 ``vnc_session_manager`` 綁成 *manager 實例*，
遮住同名子模組；目前只剩 ``api/websocket/vnc.py`` 靠
``from app.services.classroom import vnc_session_manager`` 拿實例。
那邊改成 ``from app.services.classroom.vnc_session_manager import
vnc_session_manager`` 之後，這行就可以刪掉。
"""

from app.services.classroom.vnc_session_manager import vnc_session_manager

__all__ = ["vnc_session_manager"]
