"""PVE 連線用的 CA 憑證檢查與指紋計算。

PVE 連線管理 API 與首次安裝精靈都要驗證管理員貼上的 CA 憑證，放在 service
層共用（驗證失敗要丟 i18n 的 BadRequestError，所以不放 infrastructure）。
"""

from __future__ import annotations

import hashlib

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import Encoding

from app.core.i18n import t
from app.exceptions import BadRequestError


def fingerprint_of(cert: x509.Certificate) -> str:
    """計算已載入憑證的 SHA-256 指紋（格式：AA:BB:CC:...）"""
    digest = hashlib.sha256(cert.public_bytes(encoding=Encoding.DER)).digest()
    return ":".join(f"{b:02X}" for b in digest)


def cert_fingerprint(pem: str) -> str:
    """計算 PEM 憑證的 SHA-256 指紋（格式：AA:BB:CC:...）"""
    return fingerprint_of(
        x509.load_pem_x509_certificate(pem.encode(), default_backend())
    )


def validate_ca_cert_pem(pem: str | None) -> None:
    """有帶 CA 憑證時必須是可解析的 PEM，否則回 400（None／空字串不檢查）"""
    if not pem:
        return
    try:
        x509.load_pem_x509_certificate(pem.encode(), default_backend())
    except Exception:
        raise BadRequestError(t("proxmoxConfig.invalidCaCert"))
