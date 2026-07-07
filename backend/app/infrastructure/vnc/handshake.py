"""RFB 3.8 握手：上游（我們是 client，VNC auth）與下游（我們是 server，None auth）。

上游握手完成後像素格式固定為 32bpp true color little-endian、
編碼固定協商 Hextile/CopyRect/Raw/DesktopSize，讓所有下游訂閱者
拿到一致的資料流（fan-out 不需重新編碼）。
"""

import struct
from dataclasses import dataclass
from typing import Protocol

from app.infrastructure.vnc.des import vnc_auth_response
from app.infrastructure.vnc.messages import RfbStreamError

RFB_VERSION = b"RFB 003.008\n"

# bpp=32, depth=24, big_endian=0, true_colour=1,
# max r/g/b=255, shift r=16 g=8 b=0, 3 bytes padding
PIXEL_FORMAT_32BPP = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)

# Hextile, CopyRect, Raw, DesktopSize（優先順序）
ENCODINGS = (5, 1, 0, -223)

_SECURITY_VNC_AUTH = 2
_SECURITY_NONE = 1


class UpstreamSocket(Protocol):
    """websockets client connection 需要的最小介面。"""

    async def recv(self) -> str | bytes: ...

    async def send(self, data: bytes, /) -> None: ...


class DownstreamSocket(Protocol):
    """已 accept 的 FastAPI WebSocket 需要的最小介面。"""

    async def receive_bytes(self) -> bytes: ...

    async def send_bytes(self, data: bytes) -> None: ...


@dataclass(frozen=True)
class ServerInitInfo:
    width: int
    height: int
    pixel_format: bytes
    name: bytes


class ByteStream:
    """把 websocket 的 frame 序列包成可精確讀取 n bytes 的位元組流。"""

    def __init__(self, ws: UpstreamSocket) -> None:
        self._ws = ws
        self._buf = bytearray()

    async def recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            frame = await self._ws.recv()
            if isinstance(frame, str):
                raise RfbStreamError("expected binary frame from upstream")
            self._buf.extend(frame)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class _DownstreamStream:
    """同 ByteStream，但針對 FastAPI WebSocket 的 receive_bytes。"""

    def __init__(self, ws: DownstreamSocket) -> None:
        self._ws = ws
        self._buf = bytearray()

    async def recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._buf.extend(await self._ws.receive_bytes())
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


async def upstream_handshake(ws: UpstreamSocket, password: str) -> ServerInitInfo:
    """對 PVE vncwebsocket 執行 RFB 3.8 client 握手（VNC auth）。

    完成後已送出 SetPixelFormat（固定 32bpp）與 SetEncodings。
    """
    stream = ByteStream(ws)

    version = await stream.recv_exact(12)
    if not version.startswith(b"RFB "):
        raise RfbStreamError(f"unexpected RFB version banner: {version!r}")
    await ws.send(RFB_VERSION)

    ntypes = (await stream.recv_exact(1))[0]
    if ntypes == 0:
        (reason_len,) = struct.unpack(">I", await stream.recv_exact(4))
        reason = await stream.recv_exact(reason_len)
        raise RfbStreamError(f"upstream refused connection: {reason.decode(errors='replace')}")
    security_types = await stream.recv_exact(ntypes)
    if _SECURITY_VNC_AUTH not in security_types:
        raise RfbStreamError(f"upstream does not offer VNC auth: {security_types!r}")
    await ws.send(bytes([_SECURITY_VNC_AUTH]))

    challenge = await stream.recv_exact(16)
    await ws.send(vnc_auth_response(password, challenge))

    (security_result,) = struct.unpack(">I", await stream.recv_exact(4))
    if security_result != 0:
        (reason_len,) = struct.unpack(">I", await stream.recv_exact(4))
        reason = await stream.recv_exact(reason_len)
        raise RfbStreamError(f"VNC auth failed: {reason.decode(errors='replace')}")

    await ws.send(b"\x01")  # ClientInit: shared=1

    width, height = struct.unpack(">HH", await stream.recv_exact(4))
    await stream.recv_exact(16)  # server 原生 pixel format，丟棄（我們固定 32bpp）
    (name_len,) = struct.unpack(">I", await stream.recv_exact(4))
    name = await stream.recv_exact(name_len)

    await ws.send(struct.pack(">Bxxx", 0) + PIXEL_FORMAT_32BPP)
    await ws.send(
        struct.pack(">BxH", 2, len(ENCODINGS))
        + b"".join(struct.pack(">i", e) for e in ENCODINGS)
    )

    return ServerInitInfo(
        width=int(width), height=int(height), pixel_format=PIXEL_FORMAT_32BPP, name=name
    )


def full_update_request(width: int, height: int, *, incremental: bool) -> bytes:
    """FramebufferUpdateRequest（10 bytes）覆蓋整個畫面。"""
    return struct.pack(">BBHHHH", 3, 1 if incremental else 0, 0, 0, width, height)


async def downstream_handshake(websocket: DownstreamSocket, init: ServerInitInfo) -> None:
    """對下游訂閱者（noVNC）執行 RFB 3.8 server 握手，只提供 None auth。"""
    stream = _DownstreamStream(websocket)

    await websocket.send_bytes(RFB_VERSION)
    client_version = await stream.recv_exact(12)
    if not client_version.startswith(b"RFB "):
        raise RfbStreamError(f"unexpected client version banner: {client_version!r}")

    await websocket.send_bytes(bytes([1, _SECURITY_NONE]))
    choice = (await stream.recv_exact(1))[0]
    if choice != _SECURITY_NONE:
        raise RfbStreamError(f"client chose unsupported security type {choice}")
    await websocket.send_bytes(struct.pack(">I", 0))  # SecurityResult OK

    await stream.recv_exact(1)  # ClientInit（shared flag，忽略）

    await websocket.send_bytes(
        struct.pack(">HH", init.width, init.height)
        + init.pixel_format
        + struct.pack(">I", len(init.name))
        + init.name
    )
