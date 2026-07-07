"""純 RFB 協議基礎：VNC-DES 認證、訊息切框器、上下游握手。

不含任何 PVE / 業務邏輯 — 供 services/classroom 的 fan-out 使用。
"""

from app.infrastructure.vnc.des import vnc_auth_response
from app.infrastructure.vnc.handshake import (
    PIXEL_FORMAT_32BPP,
    ByteStream,
    ServerInitInfo,
    downstream_handshake,
    full_update_request,
    upstream_handshake,
)
from app.infrastructure.vnc.messages import (
    CLIENT_INPUT_TYPES,
    PIXEL_BYTES,
    ClientMessageSplitter,
    FramebufferSize,
    RfbStreamError,
    ServerMessageSplitter,
)

__all__ = [
    "CLIENT_INPUT_TYPES",
    "PIXEL_BYTES",
    "PIXEL_FORMAT_32BPP",
    "ByteStream",
    "ClientMessageSplitter",
    "FramebufferSize",
    "RfbStreamError",
    "ServerInitInfo",
    "ServerMessageSplitter",
    "downstream_handshake",
    "full_update_request",
    "upstream_handshake",
    "vnc_auth_response",
]
