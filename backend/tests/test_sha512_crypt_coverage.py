"""Coverage tests for app.utils.sha512_crypt libcrypt loader (simulated ctypes)."""

import ctypes
import ctypes.util
import logging

import pytest

from app.utils import sha512_crypt as module


class _FakeCryptR:
    """Stands in for a ctypes foreign function; ``mode`` picks the behaviour."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.restype = None
        self.argtypes = None
        self.calls: list[tuple[bytes, bytes]] = []

    def __call__(self, pw: bytes, setting: bytes, data) -> bytes | None:
        self.calls.append((pw, setting))
        assert len(data) == module._CRYPT_DATA_SIZE
        if self.mode == "null":
            return None
        if self.mode == "fail_marker":
            return b"*0"
        if self.mode == "wrong":
            return setting + b"not-the-reference-hash"
        salt = setting.decode("ascii")[3:-1]
        return module.sha512_crypt_hashlib(pw, salt).encode("ascii")


class _FakeLib:
    def __init__(self, crypt_r: _FakeCryptR | None) -> None:
        if crypt_r is not None:
            self.crypt_r = crypt_r


def _install_fake_ctypes(monkeypatch, *, found, libs):
    opened: list[str] = []

    def fake_cdll(name):
        opened.append(name)
        lib = libs.get(name)
        if lib is None:
            raise OSError(f"cannot open {name}")
        return lib

    monkeypatch.setattr(ctypes.util, "find_library", lambda name: found)
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
    return opened


def test_load_libcrypt_skips_unusable_libraries(monkeypatch, caplog) -> None:
    null_r = _FakeCryptR("null")
    wrong_r = _FakeCryptR("wrong")
    opened = _install_fake_ctypes(
        monkeypatch,
        found="libcrypt-found.so",
        libs={
            # find_library 找到的那個沒有 crypt_r（AttributeError）
            "libcrypt-found.so": _FakeLib(None),
            "libcrypt.so.1": _FakeLib(null_r),
            "libcrypt.so.2": _FakeLib(wrong_r),
        },
    )

    with caplog.at_level(logging.INFO, logger=module.logger.name):
        assert module._load_libcrypt() is None

    assert opened == ["libcrypt-found.so", "libcrypt.so.1", "libcrypt.so.2"]
    assert null_r.restype is ctypes.c_char_p
    assert null_r.argtypes == [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]
    assert "does not support SHA-512 crypt" in caplog.text
    assert "produced" in caplog.text


def test_loaded_backend_raises_when_crypt_r_fails_later(monkeypatch) -> None:
    flaky_r = _FakeCryptR("good")
    _install_fake_ctypes(
        monkeypatch, found=None, libs={"libcrypt.so.1": _FakeLib(flaky_r)}
    )
    backend = module._load_libcrypt()
    assert backend is not None

    flaky_r.mode = "null"
    with pytest.raises(RuntimeError, match="crypt_r failed"):
        backend(b"pw", "abcdefgh")
