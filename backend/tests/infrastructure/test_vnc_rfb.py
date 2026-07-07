"""RFB 協議基礎測試：VNC-DES、訊息切框器、上下游握手。

DES 已知向量由獨立的純 Python DES 參考實作產生（非 cryptography 套件），
參考實作本身以經典 DES 測試向量（FIPS 46 樣例）驗證過：
  key=133457799BBCDFF1, pt=0123456789ABCDEF -> ct=85E813540F0AB405
"""

import struct

import pytest

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

# ---------------------------------------------------------------------------
# VNC-DES
# ---------------------------------------------------------------------------


class TestVncDes:
    def test_known_vector_zero_challenge(self) -> None:
        # password="test"（不足 8 bytes 補 0），challenge 全零
        assert vnc_auth_response("test", bytes(16)) == bytes.fromhex(
            "77dfa81c9fd7b40777dfa81c9fd7b407"
        )

    def test_known_vector_sequential_challenge(self) -> None:
        assert vnc_auth_response("test", bytes(range(16))) == bytes.fromhex(
            "51a89fa0013d72c655019513af52c20c"
        )

    def test_password_truncated_to_eight_bytes(self) -> None:
        # 超過 8 bytes 的 ticket 只取前 8 bytes
        expected = bytes.fromhex("3e5bb2819923d6c43e5bb2819923d6c4")
        assert vnc_auth_response("vncticket-longer", b"\xff" * 16) == expected
        assert vnc_auth_response("vncticke", b"\xff" * 16) == expected

    def test_response_is_sixteen_bytes(self) -> None:
        assert len(vnc_auth_response("x", bytes(16))) == 16


# ---------------------------------------------------------------------------
# Server -> Client 訊息切框
# ---------------------------------------------------------------------------


def _rect(x: int, y: int, w: int, h: int, encoding: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HHHHi", x, y, w, h, encoding) + payload


def _fb_update(*rects: bytes) -> bytes:
    return struct.pack(">BBH", 0, 0, len(rects)) + b"".join(rects)


def _hextile_20x20_payload() -> bytes:
    """20x20 rect -> tiles (16x16)(4x16)(16x4)(4x4)，涵蓋各 subencoding。"""
    parts = []
    # tile1 16x16: Raw
    parts.append(b"\x01" + b"\xaa" * (16 * 16 * PIXEL_BYTES))
    # tile2 4x16: Background + Foreground
    parts.append(b"\x06" + b"\xbb" * 4 + b"\xcc" * 4)
    # tile3 16x4: AnySubrects，2 個未上色 subrect（各 2 bytes）
    parts.append(b"\x08" + bytes([2]) + b"\x12\x34" + b"\x56\x78")
    # tile4 4x4: AnySubrects + SubrectsColoured，1 個 subrect（4+2 bytes）
    parts.append(b"\x18" + bytes([1]) + b"\xde\xad\xbe\xef" + b"\x11\x22")
    return b"".join(parts)


class TestServerMessageSplitter:
    def test_raw_rect(self) -> None:
        s = ServerMessageSplitter(800, 600)
        msg = _fb_update(_rect(0, 0, 2, 3, 0, b"\x00" * (2 * 3 * PIXEL_BYTES)))
        assert s.feed(msg) == [msg]

    def test_copyrect(self) -> None:
        s = ServerMessageSplitter(800, 600)
        msg = _fb_update(_rect(10, 10, 5, 5, 1, b"\x00\x01\x00\x02"))
        assert s.feed(msg) == [msg]

    def test_desktop_size_updates_size(self) -> None:
        s = ServerMessageSplitter(800, 600)
        assert s.size == FramebufferSize(800, 600)
        msg = _fb_update(_rect(0, 0, 1024, 768, -223))
        assert s.feed(msg) == [msg]
        assert s.size == FramebufferSize(1024, 768)

    def test_hextile_tile_walk_with_subrects(self) -> None:
        s = ServerMessageSplitter(800, 600)
        msg = _fb_update(_rect(0, 0, 20, 20, 5, _hextile_20x20_payload()))
        assert s.feed(msg) == [msg]

    def test_multiple_rects_in_one_update(self) -> None:
        s = ServerMessageSplitter(800, 600)
        msg = _fb_update(
            _rect(0, 0, 1, 1, 0, b"\x00" * PIXEL_BYTES),
            _rect(1, 1, 2, 2, 1, b"\x00\x00\x00\x00"),
        )
        assert s.feed(msg) == [msg]

    def test_set_colour_map_entries(self) -> None:
        s = ServerMessageSplitter(800, 600)
        msg = struct.pack(">BBHH", 1, 0, 0, 2) + b"\x00" * (6 * 2)
        assert s.feed(msg) == [msg]

    def test_bell(self) -> None:
        s = ServerMessageSplitter(800, 600)
        assert s.feed(b"\x02") == [b"\x02"]

    def test_server_cut_text(self) -> None:
        s = ServerMessageSplitter(800, 600)
        msg = struct.pack(">BxxxI", 3, 5) + b"hello"
        assert s.feed(msg) == [msg]

    def test_fragmented_byte_by_byte(self) -> None:
        s = ServerMessageSplitter(800, 600)
        m1 = _fb_update(_rect(0, 0, 20, 20, 5, _hextile_20x20_payload()))
        m2 = b"\x02"
        m3 = struct.pack(">BxxxI", 3, 3) + b"abc"
        stream = m1 + m2 + m3
        out: list[bytes] = []
        for i in range(len(stream)):
            out.extend(s.feed(stream[i : i + 1]))
        assert out == [m1, m2, m3]

    def test_two_messages_in_one_feed(self) -> None:
        s = ServerMessageSplitter(800, 600)
        m1 = b"\x02"
        m2 = _fb_update(_rect(0, 0, 1, 1, 0, b"\x00" * PIXEL_BYTES))
        assert s.feed(m1 + m2) == [m1, m2]

    def test_unknown_encoding_raises(self) -> None:
        s = ServerMessageSplitter(800, 600)
        with pytest.raises(RfbStreamError):
            s.feed(_fb_update(_rect(0, 0, 1, 1, 42, b"\x00" * 64)))

    def test_unknown_message_type_raises(self) -> None:
        s = ServerMessageSplitter(800, 600)
        with pytest.raises(RfbStreamError):
            s.feed(b"\xfa")


# ---------------------------------------------------------------------------
# Client -> Server 訊息切框
# ---------------------------------------------------------------------------


class TestClientMessageSplitter:
    def test_all_fixed_size_types(self) -> None:
        s = ClientMessageSplitter()
        set_pixel_format = b"\x00" + b"\x00" * 19
        fbur = full_bytes = struct.pack(">BBHHHH", 3, 1, 0, 0, 800, 600)
        key_event = struct.pack(">BBxxI", 4, 1, 0x41)
        pointer_event = struct.pack(">BBHH", 5, 1, 10, 20)
        stream = set_pixel_format + fbur + key_event + pointer_event
        assert s.feed(stream) == [
            (0, set_pixel_format),
            (3, full_bytes),
            (4, key_event),
            (5, pointer_event),
        ]

    def test_set_encodings_variable_length(self) -> None:
        s = ClientMessageSplitter()
        msg = struct.pack(">BxH", 2, 3) + struct.pack(">iii", 5, 1, -223)
        assert s.feed(msg) == [(2, msg)]

    def test_client_cut_text_variable_length(self) -> None:
        s = ClientMessageSplitter()
        msg = struct.pack(">BxxxI", 6, 4) + b"copy"
        assert s.feed(msg) == [(6, msg)]

    def test_fragmented(self) -> None:
        s = ClientMessageSplitter()
        key_event = struct.pack(">BBxxI", 4, 0, 0xFF0D)
        out: list[tuple[int, bytes]] = []
        for i in range(len(key_event)):
            out.extend(s.feed(key_event[i : i + 1]))
        assert out == [(4, key_event)]

    def test_unknown_type_raises(self) -> None:
        s = ClientMessageSplitter()
        with pytest.raises(RfbStreamError):
            s.feed(b"\x63")

    def test_input_types_constant(self) -> None:
        assert CLIENT_INPUT_TYPES == {4, 5, 6}


# ---------------------------------------------------------------------------
# Fakes for handshakes
# ---------------------------------------------------------------------------


class FakeUpstreamWs:
    """模擬 websockets client connection：recv() 依序回傳排好的 frames。"""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.sent: list[bytes] = []

    async def recv(self) -> bytes:
        if not self._frames:
            raise AssertionError("no more frames queued")
        return self._frames.pop(0)

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))


class FakeDownstreamWs:
    """模擬已 accept 的 FastAPI WebSocket。"""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.sent: list[bytes] = []

    async def receive_bytes(self) -> bytes:
        if not self._frames:
            raise AssertionError("no more frames queued")
        return self._frames.pop(0)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(bytes(data))


SERVER_PIXEL_FORMAT = bytes(range(16))


def _server_init(width: int, height: int, name: bytes) -> bytes:
    return (
        struct.pack(">HH", width, height)
        + SERVER_PIXEL_FORMAT
        + struct.pack(">I", len(name))
        + name
    )


# ---------------------------------------------------------------------------
# ByteStream
# ---------------------------------------------------------------------------


class TestByteStream:
    async def test_recv_exact_across_frames(self) -> None:
        ws = FakeUpstreamWs([b"ab", b"cdef", b"g"])
        bs = ByteStream(ws)
        assert await bs.recv_exact(3) == b"abc"
        assert await bs.recv_exact(3) == b"def"
        assert await bs.recv_exact(1) == b"g"


# ---------------------------------------------------------------------------
# Upstream handshake（我們是 RFB client）
# ---------------------------------------------------------------------------


UPSTREAM_FRAMES = [
    b"RFB 003.008\n",
    b"\x01\x02",  # 1 個 security type: VNC auth
    bytes(range(16)),  # challenge
    b"\x00\x00\x00\x00",  # SecurityResult OK
    _server_init(800, 600, b"QEMU (test-vm)"),
]

EXPECTED_CLIENT_BYTES = (
    b"RFB 003.008\n"
    + b"\x02"
    + bytes.fromhex("51a89fa0013d72c655019513af52c20c")  # DES("test", 00..0f)
    + b"\x01"  # ClientInit shared=1
    + b"\x00\x00\x00\x00" + PIXEL_FORMAT_32BPP  # SetPixelFormat
    + struct.pack(">BxH", 2, 4) + struct.pack(">iiii", 5, 1, 0, -223)  # SetEncodings
)


class TestUpstreamHandshake:
    async def test_full_handshake(self) -> None:
        ws = FakeUpstreamWs(UPSTREAM_FRAMES)
        info = await upstream_handshake(ws, "test")
        assert info == ServerInitInfo(
            width=800,
            height=600,
            pixel_format=PIXEL_FORMAT_32BPP,
            name=b"QEMU (test-vm)",
        )
        assert b"".join(ws.sent) == EXPECTED_CLIENT_BYTES

    async def test_handshake_with_fragmented_frames(self) -> None:
        joined = b"".join(UPSTREAM_FRAMES)
        frames = [joined[i : i + 7] for i in range(0, len(joined), 7)]
        ws = FakeUpstreamWs(frames)
        info = await upstream_handshake(ws, "test")
        assert (info.width, info.height) == (800, 600)
        assert b"".join(ws.sent) == EXPECTED_CLIENT_BYTES

    async def test_no_vnc_auth_offered_raises(self) -> None:
        ws = FakeUpstreamWs([b"RFB 003.008\n", b"\x01\x01"])
        with pytest.raises(RfbStreamError):
            await upstream_handshake(ws, "test")

    async def test_auth_failure_raises(self) -> None:
        ws = FakeUpstreamWs(
            [
                b"RFB 003.008\n",
                b"\x01\x02",
                bytes(16),
                b"\x00\x00\x00\x01" + struct.pack(">I", 3) + b"bad",
            ]
        )
        with pytest.raises(RfbStreamError):
            await upstream_handshake(ws, "wrong")


# ---------------------------------------------------------------------------
# full_update_request
# ---------------------------------------------------------------------------


class TestFullUpdateRequest:
    def test_non_incremental(self) -> None:
        assert full_update_request(800, 600, incremental=False) == struct.pack(
            ">BBHHHH", 3, 0, 0, 0, 800, 600
        )

    def test_incremental(self) -> None:
        assert full_update_request(1024, 768, incremental=True) == struct.pack(
            ">BBHHHH", 3, 1, 0, 0, 1024, 768
        )


# ---------------------------------------------------------------------------
# Downstream handshake（我們是 RFB server，security=None）
# ---------------------------------------------------------------------------


DOWNSTREAM_INIT = ServerInitInfo(
    width=800, height=600, pixel_format=PIXEL_FORMAT_32BPP, name=b"classroom"
)

EXPECTED_SERVER_BYTES = (
    b"RFB 003.008\n"
    + b"\x01\x01"  # 1 個 security type: None
    + b"\x00\x00\x00\x00"  # SecurityResult OK
    + struct.pack(">HH", 800, 600)
    + PIXEL_FORMAT_32BPP
    + struct.pack(">I", 9)
    + b"classroom"
)


class TestDownstreamHandshake:
    async def test_full_handshake(self) -> None:
        ws = FakeDownstreamWs([b"RFB 003.008\n", b"\x01", b"\x01"])
        await downstream_handshake(ws, DOWNSTREAM_INIT)
        assert b"".join(ws.sent) == EXPECTED_SERVER_BYTES

    async def test_fragmented_client_frames(self) -> None:
        ws = FakeDownstreamWs([b"RFB 003", b".008\n\x01", b"\x01"])
        await downstream_handshake(ws, DOWNSTREAM_INIT)
        assert b"".join(ws.sent) == EXPECTED_SERVER_BYTES

    async def test_unsupported_security_choice_raises(self) -> None:
        ws = FakeDownstreamWs([b"RFB 003.008\n", b"\x02"])
        with pytest.raises(RfbStreamError):
            await downstream_handshake(ws, DOWNSTREAM_INIT)
