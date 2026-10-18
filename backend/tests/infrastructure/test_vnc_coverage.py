"""app.infrastructure.vnc（handshake／des／messages）錯誤分支覆蓋：非二進位訊框、版本列錯誤、拒絕連線與 challenge 長度。"""

from __future__ import annotations

import struct

import pytest

from app.infrastructure.vnc.des import vnc_auth_response
from app.infrastructure.vnc.handshake import (
    PIXEL_FORMAT_32BPP,
    ByteStream,
    ServerInitInfo,
    downstream_handshake,
    upstream_handshake,
)
from app.infrastructure.vnc.messages import (
    CLIENT_QEMU_TYPE,
    RfbStreamError,
    _NeedMore,
    _Reader,
)


class _Upstream:
    def __init__(self, frames: list[bytes | str]) -> None:
        self._frames = list(frames)
        self.sent: list[bytes] = []

    async def recv(self) -> bytes | str:
        return self._frames.pop(0)

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))


class _Downstream:
    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.sent: list[bytes] = []

    async def receive_bytes(self) -> bytes:
        return self._frames.pop(0)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(bytes(data))


async def test_byte_stream_rejects_text_frame() -> None:
    with pytest.raises(RfbStreamError, match="expected binary frame"):
        await ByteStream(_Upstream(["text"])).recv_exact(1)


async def test_upstream_rejects_bad_version_banner() -> None:
    ws = _Upstream([b"HTTP/1.1 400"])
    with pytest.raises(RfbStreamError, match="unexpected RFB version banner"):
        await upstream_handshake(ws, "pw")
    assert ws.sent == []


async def test_upstream_refused_connection_reports_reason() -> None:
    reason = b"too many clients"
    ws = _Upstream(
        [b"RFB 003.008\n", b"\x00" + struct.pack(">I", len(reason)) + reason]
    )
    with pytest.raises(
        RfbStreamError, match="upstream refused connection: too many clients"
    ):
        await upstream_handshake(ws, "pw")


async def test_downstream_rejects_bad_client_version() -> None:
    init = ServerInitInfo(
        width=10, height=10, pixel_format=PIXEL_FORMAT_32BPP, name=b"x"
    )
    ws = _Downstream([b"GET / HTTP/1"])
    with pytest.raises(RfbStreamError, match="unexpected client version banner"):
        await downstream_handshake(ws, init)


@pytest.mark.parametrize("length", [0, 8, 15, 17])
def test_vnc_auth_response_rejects_bad_challenge_length(length: int) -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        vnc_auth_response("pw", bytes(length))


def test_reader_u32_and_need_more() -> None:
    reader = _Reader(bytearray(struct.pack(">I", 0xDEADBEEF) + b"\x01"))
    assert reader.u32() == 0xDEADBEEF
    assert reader.pos == 4
    with pytest.raises(_NeedMore):
        reader.u32()


_QEMU_KEY = bytes([CLIENT_QEMU_TYPE, 0]) + bytes(10)
