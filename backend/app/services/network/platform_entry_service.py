"""平台入口：讓 SkyLab 主系統自己也經 Gateway 主機的 nginx 對外。

設計原則：
- DB（``platform_entry_config`` singleton）為 source of truth
- 不另開設定檔：平台入口的 server 區塊由 ``reverse_proxy_service`` 的同步流程
  一併寫進 ``/etc/nginx/skylab/http.conf``，所以重裝 Gateway 後按「重新同步」
  就會連同 VM 網域一起復原
- HTTPS 用 Gateway 的憑證設定（管理員自備，與 VM 網域共用，見
  ``gateway_certificate_service``）；開 HTTPS 前憑證必須已設定且涵蓋平台網域
- 套用前先從 Gateway 實際連一次上游：主系統自己的入口指錯位址，管理介面
  會跟著進不來，所以連不到就不存
- 同步失敗時把 DB 還原成原本的設定（Gateway 上的檔案有 ``nginx -t`` 失敗還原）
"""

from __future__ import annotations

import ipaddress
import logging
import shlex
from datetime import datetime, timezone
from typing import Any

from app.core.i18n import t
from app.exceptions import BadRequestError
from app.models.platform_entry_config import PlatformEntryConfig
from app.schemas.gateway import (
    PlatformEntryPublic,
    PlatformEntryStatus,
    PlatformEntryUpdate,
    PlatformEntryUpstreamTest,
)
from app.services.network import nginx_gateway_service as nginx
from app.services.network.cloudflare_service import (
    HOSTNAME_LABEL_PATTERN,
    is_valid_hostname,
)

logger = logging.getLogger(__name__)

_SINGLETON_ID = 1
DEFAULT_UPSTREAM_PORT = 8082
# 主系統內層 nginx 的健康檢查端點（nginx/default.conf.template）
_UPSTREAM_HEALTH_PATH = "/nginx-health"
_PROBE_TIMEOUT_SECONDS = 5


# ─── 讀取 ────────────────────────────────────────────────────────────────────


def _get_config(session: object) -> PlatformEntryConfig | None:
    """讀 singleton；``session`` 可能是測試用的簡化物件，不是真的設定列就當沒有。"""
    getter = getattr(session, "get", None)
    if getter is None:
        return None
    config = getter(PlatformEntryConfig, _SINGLETON_ID)
    return config if isinstance(config, PlatformEntryConfig) else None


def load_entry(session: object) -> nginx.PlatformEntry | None:
    """nginx 同步要寫進 http.conf 的平台入口；沒啟用或沒填完就回 ``None``。"""
    config = _get_config(session)
    if config is None or not config.enabled:
        return None
    if not config.domain or not config.upstream_host:
        return None
    return nginx.PlatformEntry(
        domain=config.domain,
        upstream_host=config.upstream_host,
        upstream_port=config.upstream_port,
        enable_https=config.enable_https,
    )


def is_platform_domain(session: object, domain: str) -> bool:
    """這個網域是不是留給主系統的。

    就算平台入口暫時停用也保留：否則停用期間 VM 擁有者可以把主系統的網域
    發布到自己的機器上。
    """
    config = _get_config(session)
    if config is None or not config.domain:
        return False
    return config.domain == domain.strip().lower().rstrip(".")


def _gateway_host(session: object) -> tuple[bool, str]:
    from app.repositories import gateway_config as gw_repo

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None:
        return False, ""
    return bool(config.host and config.encrypted_private_key), config.host


def get_config(session: object) -> PlatformEntryPublic:
    from app.services.network import gateway_certificate_service

    config = _get_config(session)
    gateway_ready, gateway_host = _gateway_host(session)
    return PlatformEntryPublic(
        enabled=bool(config and config.enabled),
        domain=config.domain if config else "",
        upstream_host=config.upstream_host if config else "",
        upstream_port=config.upstream_port if config else DEFAULT_UPSTREAM_PORT,
        enable_https=config.enable_https if config else True,
        updated_at=config.updated_at if config else None,
        gateway_ready=gateway_ready,
        certificate_configured=gateway_certificate_service.is_configured(session),
        gateway_host=gateway_host,
    )


# ─── 驗證 ────────────────────────────────────────────────────────────────────


def normalize_domain(value: str) -> str:
    """空字串原樣回傳（代表還沒填）；有填就必須是合法的完整網域。"""
    clean = (value or "").strip().lower().rstrip(".")
    if clean and not is_valid_hostname(clean):
        raise BadRequestError(t("gateway.platformEntryDomainInvalid", domain=value))
    return clean


def normalize_upstream_host(value: str) -> str:
    """上游只收 IPv4 或主機名稱：這個值會寫進 nginx 設定與 Gateway 上的指令。"""
    clean = (value or "").strip().lower().rstrip(".")
    if not clean:
        return ""
    try:
        return str(ipaddress.IPv4Address(clean))
    except ValueError:
        pass
    labels = clean.split(".")
    # 全是數字卻不是合法 IPv4（例如 300.1.1.1）：是打錯的 IP，不當成主機名稱放行
    numeric = all(label.isdigit() for label in labels)
    if (
        not numeric
        and len(clean) <= 255
        and all(HOSTNAME_LABEL_PATTERN.fullmatch(label) for label in labels)
    ):
        return clean
    raise BadRequestError(t("gateway.platformEntryUpstreamInvalid", host=value))


def _require_certificate_paths(session: object) -> nginx.CertificatePaths:
    """平台入口要開 HTTPS，Gateway 的憑證必須先設定好（不必連線就能判斷）。"""
    from app.services.network import gateway_certificate_service

    paths = gateway_certificate_service.load_paths(session)
    if paths is None:
        raise BadRequestError(t("gateway.platformEntryCertificateRequired"))
    return paths


def _require_certificate(
    client: Any, paths: nginx.CertificatePaths, domain: str
) -> None:
    """在 Gateway 上檢查憑證：合格，而且涵蓋平台網域。"""
    from app.services.network import gateway_certificate_service

    inspection = nginx.inspect_certificate(client, paths.certificate, paths.key)
    gateway_certificate_service.verify_inspection(
        inspection,
        cert_path=paths.certificate,
        key_path=paths.key,
        now=datetime.now(timezone.utc),
    )
    gateway_certificate_service.ensure_covers_platform(inspection, domain)


# ─── Gateway 端探測 ──────────────────────────────────────────────────────────


def build_upstream_probe_command(host: str, port: int) -> str:
    """在 Gateway 上對主系統的 ``/nginx-health`` 發一次請求；2xx／3xx 才算通。"""
    url = shlex.quote(f"http://{host}:{port}{_UPSTREAM_HEALTH_PATH}")
    return (
        "if command -v curl >/dev/null 2>&1; then "
        f"curl -fsS -m {_PROBE_TIMEOUT_SECONDS} -o /dev/null {url} 2>&1; "
        "elif command -v wget >/dev/null 2>&1; then "
        f"wget -q -T {_PROBE_TIMEOUT_SECONDS} -t 1 -O /dev/null {url} 2>&1; "
        "else echo 'curl / wget not found'; exit 127; fi"
    )


def _probe_upstream(client: Any, host: str, port: int) -> PlatformEntryUpstreamTest:
    code, out, err = nginx._exec(
        client, build_upstream_probe_command(host, port), timeout=_PROBE_TIMEOUT_SECONDS + 5
    )
    if code == 0:
        return PlatformEntryUpstreamTest(
            reachable=True,
            detail=t("gateway.platformEntryUpstreamOk", upstream=f"{host}:{port}"),
        )
    detail = (out + err).strip() or t("gateway.noOutput")
    return PlatformEntryUpstreamTest(reachable=False, detail=detail[-400:])


def test_upstream(session: object, host: str, port: int) -> PlatformEntryUpstreamTest:
    """管理員按「測試上游」：從 Gateway 連主系統入口，回報通不通。"""
    from app.services.network import gateway_service

    clean_host = normalize_upstream_host(host)
    if not clean_host:
        raise BadRequestError(t("gateway.platformEntryUpstreamRequired"))
    with gateway_service.gateway_client_or_502(session) as client:
        return _probe_upstream(client, clean_host, port)


# ─── 儲存 ────────────────────────────────────────────────────────────────────


def _snapshot(config: PlatformEntryConfig | None) -> dict[str, Any]:
    if config is None:
        return {
            "enabled": False,
            "domain": "",
            "upstream_host": "",
            "upstream_port": DEFAULT_UPSTREAM_PORT,
            "enable_https": True,
        }
    return {
        "enabled": config.enabled,
        "domain": config.domain,
        "upstream_host": config.upstream_host,
        "upstream_port": config.upstream_port,
        "enable_https": config.enable_https,
    }


def save_config(session: object, data: PlatformEntryUpdate) -> PlatformEntryPublic:
    """驗證 → 在 Gateway 上檢查憑證、測試上游 → 寫 DB → 同步 nginx；同步失敗就還原 DB。"""
    from app.repositories import platform_entry as repo
    from app.repositories import reverse_proxy as rp_repo
    from app.services.network import gateway_service, reverse_proxy_service

    domain = normalize_domain(data.domain)
    upstream_host = normalize_upstream_host(data.upstream_host)

    if data.enabled and not domain:
        raise BadRequestError(t("gateway.platformEntryDomainRequired"))
    if data.enabled and not upstream_host:
        raise BadRequestError(t("gateway.platformEntryUpstreamRequired"))
    if domain and rp_repo.is_domain_taken(session, domain):  # type: ignore[arg-type]
        raise BadRequestError(t("gateway.platformEntryDomainUsedByVm", domain=domain))

    previous = _snapshot(_get_config(session))

    if data.enabled:
        paths = _require_certificate_paths(session) if data.enable_https else None
        with gateway_service.gateway_client_or_502(session) as client:
            if paths is not None:
                _require_certificate(client, paths, domain)
            probe = _probe_upstream(client, upstream_host, data.upstream_port)
        if not probe.reachable:
            raise BadRequestError(
                t(
                    "gateway.platformEntryUpstreamUnreachable",
                    upstream=f"{upstream_host}:{data.upstream_port}",
                    detail=probe.detail,
                )
            )

    repo.upsert_platform_entry_config(
        session,  # type: ignore[arg-type]
        enabled=data.enabled,
        domain=domain,
        upstream_host=upstream_host,
        upstream_port=data.upstream_port,
        enable_https=data.enable_https,
    )

    # 啟用中或剛停用都要重寫 http.conf；從頭到尾都沒啟用就只是存欄位
    if data.enabled or previous["enabled"]:
        try:
            reverse_proxy_service.sync_to_gateway(session)
        except Exception:
            rollback = getattr(session, "rollback", None)
            if rollback is not None:
                rollback()
            try:
                repo.upsert_platform_entry_config(session, **previous)  # type: ignore[arg-type]
            except Exception:
                logger.exception("平台入口同步失敗後還原設定也失敗，DB 與 Gateway 可能不一致")
            raise

    return get_config(session)


# ─── 狀態 ────────────────────────────────────────────────────────────────────


def get_status(
    session: object,
    *,
    observed_client_ip: str | None = None,
    observed_scheme: str | None = None,
) -> PlatformEntryStatus:
    """SSH 到 Gateway 讀回實際套用的平台入口、檢查引用中的憑證，並測一次上游。"""
    from app.services.network import gateway_certificate_service, gateway_service

    config = _get_config(session)
    expected = load_entry(session)
    expected_certificate = gateway_certificate_service.load_paths(session)

    with gateway_service.gateway_client_or_502(session) as client:
        applied = nginx.parse_platform_entry(nginx.read_http_config(client))
        inspection = (
            nginx.inspect_certificate(
                client, applied["certificate"], applied["certificate_key"] or ""
            )
            if applied is not None and applied["certificate"]
            else None
        )
        probe = (
            _probe_upstream(client, config.upstream_host, config.upstream_port)
            if config is not None and config.upstream_host
            else None
        )

    if expected is None:
        in_sync = applied is None
    else:
        in_sync = (
            applied is not None
            and applied["domain"] == expected.domain
            and applied["upstream"] == expected.upstream
            and applied["https"] == expected.enable_https
            # 換了憑證路徑但還沒同步上去，也算沒套用
            and (
                not expected.enable_https
                or applied["certificate"]
                == (expected_certificate.certificate if expected_certificate else None)
            )
        )

    certificate_ready: bool | None = None
    if applied is not None and applied["https"]:
        certificate_ready = inspection is not None and inspection.usable

    return PlatformEntryStatus(
        applied=in_sync,
        applied_domain=applied["domain"] if applied else None,
        applied_upstream=applied["upstream"] if applied else None,
        applied_https=applied["https"] if applied else None,
        certificate=applied["certificate"] if applied else None,
        certificate_ready=certificate_ready,
        certificate_expires_at=inspection.expires_at if inspection else None,
        certificate_matches_domain=(
            nginx.certificate_covers(applied["domain"], inspection.dns_names)
            if applied is not None and inspection is not None and inspection.cert_valid
            else None
        ),
        upstream_reachable=probe.reachable if probe else None,
        upstream_detail=probe.detail if probe else None,
        observed_client_ip=observed_client_ip,
        observed_scheme=observed_scheme,
        checked_at=datetime.now(timezone.utc),
    )


__all__ = [
    "DEFAULT_UPSTREAM_PORT",
    "build_upstream_probe_command",
    "get_config",
    "get_status",
    "is_platform_domain",
    "load_entry",
    "normalize_domain",
    "normalize_upstream_host",
    "save_config",
    "test_upstream",
]
