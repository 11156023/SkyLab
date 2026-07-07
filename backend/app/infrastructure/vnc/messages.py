"""RFB 訊息切框器：把任意分片的位元組流切成完整的 RFB 訊息。

fan-out 需要以「完整訊息」為單位廣播（尤其 FramebufferUpdate），
因此必須知道每種訊息的長度規則。像素格式固定 32bpp（PIXEL_BYTES）。
"""

import struct
from dataclasses import dataclass

PIXEL_BYTES = 4

# KeyEvent, PointerEvent, ClientCutText
CLIENT_INPUT_TYPES = frozenset({4, 5, 6})

# QEMU Client Message（PVE 的 noVNC 會用 submessage 0 = Extended Key Event 送按鍵）
CLIENT_QEMU_TYPE = 255

_HEXTILE_RAW = 0x01
_HEXTILE_BACKGROUND = 0x02
_HEXTILE_FOREGROUND = 0x04
_HEXTILE_ANY_SUBRECTS = 0x08
_HEXTILE_SUBRECTS_COLOURED = 0x10

_ENC_RAW = 0
_ENC_COPYRECT = 1
_ENC_HEXTILE = 5
_ENC_DESKTOP_SIZE = -223


class RfbStreamError(Exception):
    """RFB 資料流不符合協議，或含不支援的訊息/編碼。"""


@dataclass(frozen=True)
class FramebufferSize:
    width: int
    height: int


class _NeedMore(Exception):
    """緩衝區資料不足以構成完整訊息（等待下一次 feed）。"""


class _Reader:
    """緩衝區上的游標；讀取越界丟 _NeedMore（而非錯誤）。"""

    def __init__(self, buf: bytearray) -> None:
        self._buf = buf
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self._buf):
            raise _NeedMore
        out = bytes(self._buf[self.pos : end])
        self.pos = end
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int(struct.unpack(">H", self.take(2))[0])

    def u32(self) -> int:
        return int(struct.unpack(">I", self.take(4))[0])

    def i32(self) -> int:
        return int(struct.unpack(">i", self.take(4))[0])


class ServerMessageSplitter:
    """切出完整的 server->client 訊息；追蹤 DesktopSize 造成的尺寸變更。"""

    def __init__(self, width: int, height: int) -> None:
        self._buf = bytearray()
        self._size = FramebufferSize(width, height)

    @property
    def size(self) -> FramebufferSize:
        return self._size

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        messages: list[bytes] = []
        while self._buf:
            try:
                length, new_size = self._parse_one()
            except _NeedMore:
                break
            messages.append(bytes(self._buf[:length]))
            del self._buf[:length]
            if new_size is not None:
                self._size = new_size
        return messages

    def _parse_one(self) -> tuple[int, FramebufferSize | None]:
        reader = _Reader(self._buf)
        msg_type = reader.u8()
        if msg_type == 0:  # FramebufferUpdate
            return self._parse_framebuffer_update(reader)
        if msg_type == 1:  # SetColourMapEntries
            reader.take(3)  # padding + first-colour
            ncolours = reader.u16()
            reader.take(6 * ncolours)
            return reader.pos, None
        if msg_type == 2:  # Bell
            return reader.pos, None
        if msg_type == 3:  # ServerCutText
            reader.take(3)  # padding
            length = reader.u32()
            reader.take(length)
            return reader.pos, None
        raise RfbStreamError(f"unsupported server message type {msg_type}")

    def _parse_framebuffer_update(
        self, reader: _Reader
    ) -> tuple[int, FramebufferSize | None]:
        reader.take(1)  # padding
        nrects = reader.u16()
        new_size: FramebufferSize | None = None
        for _ in range(nrects):
            reader.take(4)  # x, y
            width = reader.u16()
            height = reader.u16()
            encoding = reader.i32()
            if encoding == _ENC_RAW:
                reader.take(width * height * PIXEL_BYTES)
            elif encoding == _ENC_COPYRECT:
                reader.take(4)
            elif encoding == _ENC_HEXTILE:
                self._walk_hextile(reader, width, height)
            elif encoding == _ENC_DESKTOP_SIZE:
                new_size = FramebufferSize(width, height)
            else:
                raise RfbStreamError(f"unsupported encoding {encoding}")
        return reader.pos, new_size

    @staticmethod
    def _walk_hextile(reader: _Reader, width: int, height: int) -> None:
        for tile_y in range(0, height, 16):
            tile_h = min(16, height - tile_y)
            for tile_x in range(0, width, 16):
                tile_w = min(16, width - tile_x)
                subencoding = reader.u8()
                if subencoding & _HEXTILE_RAW:
                    reader.take(tile_w * tile_h * PIXEL_BYTES)
                    continue
                if subencoding & _HEXTILE_BACKGROUND:
                    reader.take(PIXEL_BYTES)
                if subencoding & _HEXTILE_FOREGROUND:
                    reader.take(PIXEL_BYTES)
                if subencoding & _HEXTILE_ANY_SUBRECTS:
                    nsubrects = reader.u8()
                    per_subrect = 2 + (
                        PIXEL_BYTES if subencoding & _HEXTILE_SUBRECTS_COLOURED else 0
                    )
                    reader.take(nsubrects * per_subrect)


class ClientMessageSplitter:
    """切出完整的 client->server 訊息，回傳 (msg_type, full_message_bytes)。"""

    _FIXED_LENGTHS = {
        0: 20,  # SetPixelFormat
        3: 10,  # FramebufferUpdateRequest
        4: 8,  # KeyEvent
        5: 6,  # PointerEvent
    }

    def __init__(self) -> None:
        self._buf = bytearray()

    @property
    def pending(self) -> bytes:
        """尚未構成完整訊息的緩衝 bytes（失去同步後 fail-open 原樣轉發用）。"""
        return bytes(self._buf)

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf.extend(data)
        messages: list[tuple[int, bytes]] = []
        while self._buf:
            try:
                msg_type, length = self._parse_one()
            except _NeedMore:
                break
            messages.append((msg_type, bytes(self._buf[:length])))
            del self._buf[:length]
        return messages

    def _parse_one(self) -> tuple[int, int]:
        reader = _Reader(self._buf)
        msg_type = reader.u8()
        fixed = self._FIXED_LENGTHS.get(msg_type)
        if fixed is not None:
            reader.take(fixed - 1)
            return msg_type, reader.pos
        if msg_type == 2:  # SetEncodings
            reader.take(1)  # padding
            nencodings = reader.u16()
            reader.take(4 * nencodings)
            return msg_type, reader.pos
        if msg_type == 6:  # ClientCutText
            reader.take(3)  # padding
            length = reader.u32()
            reader.take(length)
            return msg_type, reader.pos
        if msg_type == CLIENT_QEMU_TYPE:
            submessage = reader.u8()
            if submessage == 0:  # Extended Key Event
                reader.take(10)  # down-flag(2) + keysym(4) + keycode(4)
                return msg_type, reader.pos
            raise RfbStreamError(f"unsupported QEMU client submessage {submessage}")
        raise RfbStreamError(f"unsupported client message type {msg_type}")


def filter_client_bytes(
    splitter: ClientMessageSplitter, data: bytes, *, blocked: bool
) -> list[bytes]:
    """把 client 位元組流切成完整訊息，並在 blocked 時丟棄輸入訊息。

    未攔截時 splitter 也必須持續 feed（維持訊息邊界同步），
    這樣攔截旗標中途切換時仍能在正確的訊息邊界過濾。
    """
    forwarded: list[bytes] = []
    for msg_type, message in splitter.feed(data):
        if blocked and (msg_type in CLIENT_INPUT_TYPES or msg_type == CLIENT_QEMU_TYPE):
            continue
        forwarded.append(message)
    return forwarded
