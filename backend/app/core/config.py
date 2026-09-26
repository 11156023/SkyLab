import secrets
import warnings
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    HttpUrl,
    PostgresDsn,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 1 day
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7  # 7 days
    FRONTEND_HOST: str = "http://127.0.0.1:5173"
    # Public base URL of the backend API as seen by desktop clients.
    # Defaults to http://localhost:8000 when unset (override in .env for deploys).
    DESKTOP_CLIENT_BACKEND_URL: str = "http://localhost:8000"
    # External URL for the desktop client zip (e.g. GitHub Releases asset).
    # When set, /desktop-client/download redirects here instead of serving a local file.
    DESKTOP_CLIENT_DOWNLOAD_URL: str = (
        "https://github.com/1Ray0/SkyLab-Connect-Releases/"
        "releases/latest/download/SkyLab-Connect-Setup.exe"
    )
    # Desktop VPN control plane. The public endpoint host falls back to the
    # configured Gateway VM host when left empty.
    WIREGUARD_ENDPOINT_HOST: str = ""
    WIREGUARD_ENDPOINT_PORT: int = 51821
    WIREGUARD_INTERFACE: str = "wg0"
    WIREGUARD_CLIENT_SUBNET: str = "10.250.0.0/16"
    WIREGUARD_VM_SUBNET: str = "10.10.0.0/16"
    WIREGUARD_KEEPALIVE_SECONDS: int = 25
    WIREGUARD_SESSION_TTL_SECONDS: int = 28800
    WIREGUARD_RECONCILE_ENABLED: bool = True
    WIREGUARD_RECONCILE_INTERVAL_SECONDS: int = 60
    WIREGUARD_REVOKED_RETENTION_DAYS: int = 30
    # Local/test convenience for switching accounts on one physical device.
    # Production should keep this disabled so public keys remain account-bound.
    WIREGUARD_ALLOW_INACTIVE_PEER_TRANSFER: bool = False
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    ENABLE_SIGNUP: bool = True

    # Logging
    LOG_LEVEL: str = "INFO"
    # False = ANSI color console (local dev); True = JSON console (production/Docker)
    LOG_JSON: bool = False
    LOG_DIR: str = "logs"
    LOG_FILE_ENABLED: bool = True

    # Redis：HTTP 限流、JWT 撤銷名單與 arq 任務佇列共用同一台。
    # 這裡是開關與連線字串的唯一來源，`features/ai/config.py` 只是代理過來；
    # 兩份設定各自讀 .env 時，一邊 true 一邊 false 會讓限流靜默失效。
    # 非 local 環境啟用後連不上 Redis，lifespan 會直接讓啟動失敗（fail-closed）。
    REDIS_ENABLED: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"

    # When false, the lifespan skips starting the VM request scheduler.
    # Set to false in CI/test environments that cannot reach Proxmox,
    # so scheduler ticks don't block test startup on connection timeouts.
    SCHEDULER_ENABLED: bool = True

    # 虛擬教室：單一 VNC session 的下游訂閱者上限
    CLASSROOM_MAX_SUBSCRIBERS: int = 250
    # 虛擬教室：每個訂閱者的訊息佇列深度（滿了直接斷開該訂閱者）
    CLASSROOM_SUBSCRIBER_QUEUE_SIZE: int = 256
    # 虛擬教室：下游 RFB 握手逾時（秒）。握手不完成就一直佔著名額，
    # 惡意或壞掉的 client 可以靠這點把整個 session 的訂閱名額耗光。
    CLASSROOM_HANDSHAKE_TIMEOUT_SECONDS: float = 10.0
    # 虛擬教室：上游 PVE vncwebsocket 連線與握手的逾時（秒）
    CLASSROOM_UPSTREAM_TIMEOUT_SECONDS: float = 10.0

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    # Fraction of transactions sampled for Sentry performance tracing.
    # 1.0 (100%) is only sensible for low-traffic staging; keep low in prod.
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    # 版本標記（例如 git commit），Sentry 用來區分哪一版開始出錯；留空不送。
    SENTRY_RELEASE: str | None = None
    # /metrics 的 Bearer token。留空＝不驗證（/metrics 只綁在內網與 127.0.0.1，
    # nginx 不轉發）；有設時 Prometheus 要帶同一個 token 才抓得到。
    METRICS_TOKEN: str | None = None
    # Gateway 上的 exporter port（install.sh 裝的 prometheus-node-exporter／
    # prometheus-nginx-exporter 預設值）；Prometheus 經 /metrics/gateway-targets 取得
    GATEWAY_NODE_EXPORTER_PORT: int = 9100
    GATEWAY_NGINX_EXPORTER_PORT: int = 9113
    # 監控 stack 的 Grafana：後端探測內網位址判斷有沒有啟用（沒開 monitoring
    # profile 時連不到），資源監控頁的「在 Grafana 查看詳細」按鈕連到 GRAFANA_ROOT_URL
    # （與 compose 帶給 Grafana 的同一個值），未設定時用同網域的 /grafana/。
    GRAFANA_INTERNAL_URL: str = "http://grafana:3000/grafana"
    GRAFANA_ROOT_URL: str | None = None
    # 管理員免密碼進 Grafana：資源監控頁發一個只在 /grafana/ 有效的 httponly cookie，
    # nginx auth_request 以它向後端換身分標頭交給 Grafana auth.proxy。這是它的效期。
    GRAFANA_SESSION_EXPIRE_MINUTES: int = 480
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        return PostgresDsn.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr
    FIRST_SUPERUSER_PASSWORD: str

    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None

    PROXMOX_HOST: str = "localhost"
    PROXMOX_USER: str = ""
    PROXMOX_PASSWORD: str = ""
    PROXMOX_VERIFY_SSL: bool = False
    PROXMOX_ISO_STORAGE: str = "local"
    PROXMOX_DATA_STORAGE: str = "local-lvm"
    PROXMOX_API_TIMEOUT: int = 30  # API request timeout in seconds
    PROXMOX_TASK_CHECK_INTERVAL: int = 1  # Seconds between task status checks

    # vLLM settings for AI Teacher Judge
    VLLM_BASE_URL: str = "http://localhost:8000/v1"
    VLLM_API_KEY: str = "vllm-secret-key-change-me"
    VLLM_MODEL_NAME: str = ""
    VLLM_ENABLE_THINKING: bool = False
    VLLM_TIMEOUT: int = 60
    VLLM_TEMPERATURE: float = 0.2
    VLLM_CHAT_TEMPERATURE: float = 0.7
    VLLM_TOP_P: float = 0.95
    VLLM_TOP_K: int = 20
    VLLM_MAX_TOKENS: int = 8192
    VLLM_CHAT_MAX_TOKENS: int = 4096
    VLLM_REPETITION_PENALTY: float = 1.0
    VLLM_MAX_UPLOAD_SIZE_MB: int = 10

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _validate_cors_origins(self) -> Self:
        if self.ENVIRONMENT == "production" and self.BACKEND_CORS_ORIGINS:
            origins = (
                [self.BACKEND_CORS_ORIGINS]
                if isinstance(self.BACKEND_CORS_ORIGINS, str)
                else self.BACKEND_CORS_ORIGINS
            )
            if any(str(o).strip() == "*" for o in origins):
                raise ValueError(
                    "Wildcard '*' CORS origin is not allowed in production. "
                    "Please specify explicit origins."
                )
        return self

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )

        return self


settings = Settings()  # type: ignore
