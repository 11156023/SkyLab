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
    FRONTEND_HOST: str = "http://localhost:5173"
    # Public base URL of the backend API as seen by desktop clients.
    # Defaults to http://localhost:8000 when unset (override in .env for deploys).
    DESKTOP_CLIENT_BACKEND_URL: str = "http://localhost:8000"
    # External URL for the desktop client zip (e.g. GitHub Releases asset).
    # When set, /desktop-client/download redirects here instead of serving a local file.
    DESKTOP_CLIENT_DOWNLOAD_URL: str = ""
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    ENABLE_SIGNUP: bool = True

    # Logging
    LOG_LEVEL: str = "INFO"
    # False = ANSI color console (local dev); True = JSON console (production/Docker)
    LOG_JSON: bool = False
    LOG_DIR: str = "logs"
    LOG_FILE_ENABLED: bool = True

    # When false, the lifespan skips starting the VM request scheduler.
    # Set to false in CI/test environments that cannot reach Proxmox,
    # so scheduler ticks don't block test startup on connection timeouts.
    SCHEDULER_ENABLED: bool = True

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

    # frp tunnel settings
    FRP_SERVER_ADDR: str = ""   # public IP/domain that desktop clients connect to
    FRP_SERVER_PORT: int = 7000
    FRP_TOKEN: str = ""

    PROXMOX_HOST: str = "localhost"
    PROXMOX_USER: str = ""
    PROXMOX_PASSWORD: str = ""
    PROXMOX_VERIFY_SSL: bool = False
    PROXMOX_ISO_STORAGE: str = "local"
    PROXMOX_DATA_STORAGE: str = "local-lvm"
    PROXMOX_API_TIMEOUT: int = 30  # API request timeout in seconds
    PROXMOX_TASK_CHECK_INTERVAL: int = 1  # Seconds between task status checks

    TRAEFIK_API_BASE_URL: str = "http://127.0.0.1:8080"
    TRAEFIK_API_TIMEOUT: int = 10

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

    TRAEFIK_API_BASE_URL: str = "http://127.0.0.1:8080"
    TRAEFIK_API_TIMEOUT: int = 10

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
