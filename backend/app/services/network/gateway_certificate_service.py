"""Gateway 的 HTTPS 憑證：系統不簽發，管理員自己準備、放到 Gateway 主機上。

設計原則：
- 只有一張：平台入口與所有 VM 網域共用（通常是涵蓋 zone 的萬用憑證），
  DB（``gateway_config`` 的兩個路徑欄位）只記 Gateway 上的檔案路徑
- 存檔前先在 Gateway 上檢查：讀得到、格式正確、私鑰配對、還沒過期；
  平台入口已啟用 HTTPS 的話，還要涵蓋平台網域（管理介面自己的入口不能壞）
- VM 網域沒被涵蓋只提示不擋：站台照樣能連，瀏覽器會警告
- 兩個路徑都清空代表不使用，HTTPS 站台退回 Gateway 安裝時產生的自簽憑證
- 存檔後重寫 http.conf；同步失敗就把 DB 還原成原本的路徑
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.core.i18n import t
from app.exceptions import BadRequestError
from app.schemas.gateway import (
    GatewayCertificatePublic,
    GatewayCertificateStatus,
    GatewayCertificateUpdate,
)
from app.services.network import nginx_gateway_service as nginx

logger = logging.getLogger(__name__)

# 憑證路徑會原樣寫進 nginx 設定：只放行絕對路徑與檔名常見字元
# （不含空白、分號、引號、萬用字元），避免拼出別的設定或指令
_CERT_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._@+=,-]+(?:/[A-Za-z0-9._@+=,-]+)*$")
_CERT_PATH_MAX_LENGTH = 512


# ─── 讀取 ────────────────────────────────────────────────────────────────────


def _stored_paths(session: object) -> tuple[str, str]:
    from app.repositories import gateway_config as gw_repo

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None:
        return "", ""
    return config.ssl_certificate_path or "", config.ssl_certificate_key_path or ""


def load_paths(session: object) -> nginx.CertificatePaths | None:
    """nginx 設定要引用的憑證；還沒設定就回 ``None``（改掛自簽憑證）。"""
    cert_path, key_path = _stored_paths(session)
    if not cert_path or not key_path:
        return None
    return nginx.CertificatePaths(certificate=cert_path, key=key_path)


def is_configured(session: object) -> bool:
    return load_paths(session) is not None


def get_config(session: object) -> GatewayCertificatePublic:
    from app.repositories import gateway_config as gw_repo

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    cert_path, key_path = _stored_paths(session)
    return GatewayCertificatePublic(
        ssl_certificate_path=cert_path,
        ssl_certificate_key_path=key_path,
        configured=bool(cert_path and key_path),
        gateway_ready=bool(config and config.host and config.encrypted_private_key),
    )


def https_domains(session: object) -> list[str]:
    """目前啟用 HTTPS 的網域：平台入口（有啟用的話）加上所有 VM 網域。"""
    from app.repositories import reverse_proxy as rp_repo
    from app.services.network import platform_entry_service

    domains: list[str] = []
    platform = platform_entry_service.load_entry(session)
    if platform is not None and platform.enable_https:
        domains.append(platform.domain)
    for rule in rp_repo.list_rules(session):  # type: ignore[arg-type]
        if rule.enable_https and rule.domain not in domains:
            domains.append(rule.domain)
    return domains


# ─── 驗證 ────────────────────────────────────────────────────────────────────


def normalize_cert_path(value: str) -> str:
    """Gateway 上的憑證／私鑰路徑；空字串原樣回傳，有填就必須是安全的絕對路徑。"""
    clean = (value or "").strip()
    if not clean:
        return ""
    segments = clean.split("/")[1:]
    if (
        len(clean) > _CERT_PATH_MAX_LENGTH
        or not _CERT_PATH_PATTERN.fullmatch(clean)
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise BadRequestError(t("gateway.certificatePathInvalid", path=value))
    return clean


def verify_inspection(
    inspection: nginx.CertificateInspection,
    *,
    cert_path: str,
    key_path: str,
    now: datetime,
) -> None:
    """檢查結果不合格就丟 400，依檢查順序只報第一個問題。"""
    if not inspection.cert_readable:
        raise BadRequestError(t("gateway.certificateUnreadable", path=cert_path))
    if not inspection.key_readable:
        raise BadRequestError(t("gateway.certificateUnreadable", path=key_path))
    if not inspection.cert_valid:
        raise BadRequestError(t("gateway.certificateInvalid", path=cert_path))
    if not inspection.key_valid:
        raise BadRequestError(t("gateway.certificateKeyInvalid", path=key_path))
    if inspection.key_matches is not True:
        raise BadRequestError(t("gateway.certificateKeyMismatch"))
    if inspection.expires_at is not None and inspection.expires_at <= now:
        raise BadRequestError(
            t(
                "gateway.certificateExpired",
                date=inspection.expires_at.strftime("%Y-%m-%d"),
            )
        )


def ensure_covers_platform(
    inspection: nginx.CertificateInspection, domain: str
) -> None:
    """平台入口的網域一定要被涵蓋：管理介面自己的入口出現憑證警告就等於壞了。"""
    if not nginx.certificate_covers(domain, inspection.dns_names):
        raise BadRequestError(
            t(
                "gateway.certificateNotCoveringPlatform",
                domain=domain,
                names=", ".join(inspection.dns_names) or "-",
            )
        )


# ─── 儲存 ────────────────────────────────────────────────────────────────────


def save_config(
    session: object, data: GatewayCertificateUpdate
) -> GatewayCertificatePublic:
    """驗證 → 在 Gateway 上檢查憑證 → 寫 DB → 同步 nginx；同步失敗就還原 DB。"""
    from app.repositories import gateway_config as gw_repo
    from app.services.network import (
        gateway_service,
        platform_entry_service,
        reverse_proxy_service,
    )

    cert_path = normalize_cert_path(data.ssl_certificate_path)
    key_path = normalize_cert_path(data.ssl_certificate_key_path)
    if bool(cert_path) != bool(key_path):
        raise BadRequestError(t("gateway.certificatePathPairRequired"))

    platform = platform_entry_service.load_entry(session)
    if cert_path:
        with gateway_service.gateway_client_or_502(session) as client:
            inspection = nginx.inspect_certificate(client, cert_path, key_path)
        verify_inspection(
            inspection,
            cert_path=cert_path,
            key_path=key_path,
            now=datetime.now(timezone.utc),
        )
        if platform is not None and platform.enable_https:
            ensure_covers_platform(inspection, platform.domain)
    elif platform is not None and platform.enable_https:
        # 清掉憑證會讓啟用中的 HTTPS 平台入口改掛自簽憑證，管理介面會被瀏覽器擋
        raise BadRequestError(t("gateway.certificateRequiredByPlatform"))

    previous_cert, previous_key = _stored_paths(session)
    gw_repo.save_certificate_paths(
        session,  # type: ignore[arg-type]
        ssl_certificate_path=cert_path,
        ssl_certificate_key_path=key_path,
    )

    # 清空憑證時不必連 Gateway；Gateway 還沒設定好就只存，等之後的同步再套用
    if get_config(session).gateway_ready:
        try:
            reverse_proxy_service.sync_to_gateway(session)
        except Exception:
            rollback = getattr(session, "rollback", None)
            if rollback is not None:
                rollback()
            try:
                gw_repo.save_certificate_paths(
                    session,  # type: ignore[arg-type]
                    ssl_certificate_path=previous_cert,
                    ssl_certificate_key_path=previous_key,
                )
            except Exception:
                logger.exception(
                    "憑證設定同步失敗後還原也失敗，DB 與 Gateway 可能不一致"
                )
            raise

    return get_config(session)


# ─── 狀態 ────────────────────────────────────────────────────────────────────


def get_status(session: object) -> GatewayCertificateStatus:
    """SSH 到 Gateway 檢查目前設定的憑證，並列出啟用 HTTPS 的網域有沒有被涵蓋。"""
    from app.services.network import gateway_service

    now = datetime.now(timezone.utc)
    paths = load_paths(session)
    domains = https_domains(session)
    if paths is None:
        return GatewayCertificateStatus(
            configured=False, uncovered_domains=domains, checked_at=now
        )

    with gateway_service.gateway_client_or_502(session) as client:
        inspection = nginx.inspect_certificate(client, paths.certificate, paths.key)

    covered = [d for d in domains if nginx.certificate_covers(d, inspection.dns_names)]
    return GatewayCertificateStatus(
        configured=True,
        cert_readable=inspection.cert_readable,
        key_readable=inspection.key_readable,
        cert_valid=inspection.cert_valid,
        key_valid=inspection.key_valid,
        key_matches=inspection.key_matches,
        expires_at=inspection.expires_at,
        dns_names=list(inspection.dns_names),
        covered_domains=covered,
        uncovered_domains=[d for d in domains if d not in covered],
        checked_at=now,
    )


__all__ = [
    "ensure_covers_platform",
    "get_config",
    "get_status",
    "https_domains",
    "is_configured",
    "load_paths",
    "normalize_cert_path",
    "save_config",
    "verify_inspection",
]
