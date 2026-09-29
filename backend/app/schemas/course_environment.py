"""課程環境（老師管理、有版本、每位學生一份）的 API 請求 schemas。"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.i18n import t
from app.schemas.resource import (
    SPEC_CORES_MAX,
    SPEC_CORES_MIN,
    SPEC_DISK_MAX_GB,
    SPEC_DISK_MIN_GB,
    SPEC_MEMORY_MAX_MB,
    SPEC_MEMORY_MIN_MB,
)

# 草稿快照（configuration + editor）序列化後的上限
MAX_DRAFT_BYTES = 262144


class EnvironmentNodeIn(BaseModel):
    node_key: str = Field(min_length=1, max_length=80)
    source_type: Literal["template", "custom"] = "template"
    source_template_id: uuid.UUID | None = None
    custom_image_ref: str | None = Field(default=None, max_length=500)
    custom_username: str | None = Field(default=None, max_length=32)
    custom_unprivileged: bool = True
    name: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=120)
    resource_type: str = Field(pattern="^(qemu|lxc)$")
    cpu: int = Field(ge=SPEC_CORES_MIN, le=SPEC_CORES_MAX)
    memory_mb: int = Field(ge=SPEC_MEMORY_MIN_MB, le=SPEC_MEMORY_MAX_MB)
    disk_gb: int = Field(ge=SPEC_DISK_MIN_GB, le=SPEC_DISK_MAX_GB)
    network: str = Field(default="lab-net", min_length=1, max_length=255)
    position_x: float = Field(default=80.0, ge=-5000, le=5000)
    position_y: float = Field(default=120.0, ge=-5000, le=5000)

    @model_validator(mode="after")
    def validate_source(self) -> "EnvironmentNodeIn":
        if self.source_type == "template":
            if self.source_template_id is None:
                raise ValueError(t("course_env.node_template_required"))
            self.custom_image_ref = None
        else:
            if not (self.custom_image_ref or "").strip():
                raise ValueError(t("course_env.node_image_required"))
            self.source_template_id = None
            if self.resource_type == "qemu":
                try:
                    if int(self.custom_image_ref or "0") <= 0:
                        raise ValueError
                except ValueError as exc:
                    raise ValueError(t("course_env.node_invalid_vmid")) from exc
        return self


class EnvironmentEdgeIn(BaseModel):
    source_node_key: str = Field(min_length=1, max_length=80)
    target_node_key: str = Field(min_length=1, max_length=80)
    direction: Literal["one_way", "bidirectional"] = "one_way"
    protocol: Literal["any", "tcp", "udp", "icmp", "icmpv6", "sctp"] = "tcp"
    port: int | None = Field(default=22, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_edge(self) -> "EnvironmentEdgeIn":
        if self.source_node_key == self.target_node_key:
            raise ValueError(t("course_env.edge_same_node"))
        if self.protocol in {"any", "icmp", "icmpv6"}:
            # PVE 只有 tcp/udp/sctp 能帶 dport，其餘協定一律視為全開
            self.port = None
        elif self.port is None:
            raise ValueError(t("course_env.edge_port_required"))
        return self


class EnvironmentPublicationIn(BaseModel):
    """一條「外網 → 機器」的宣告。

    網域與對外 port 都是全域唯一的資源，而每位學生都會拿到一份自己的環境，
    所以模板上只能填主機名樣板、或只說「要一個對外 port」；實際網址與 port
    在開課／開練習時逐人組出來、配出來。
    """

    node_key: str = Field(min_length=1, max_length=80)
    mode: Literal["domain", "port_forward"] = "domain"
    port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp", "udp"] = "tcp"
    hostname_prefix: str | None = Field(default=None, max_length=120)
    zone_id: str | None = Field(default=None, max_length=64)
    enable_https: bool = True

    @model_validator(mode="after")
    def validate_mode(self) -> "EnvironmentPublicationIn":
        if self.mode != "domain":
            self.hostname_prefix = None
            self.zone_id = None
            return self
        if self.protocol != "tcp":
            raise ValueError(t("course_env.publication_domain_tcp_only"))
        if not (self.zone_id or "").strip():
            raise ValueError(t("course_env.publication_zone_required"))
        prefix = (self.hostname_prefix or "").strip().lower()
        if "{student}" not in prefix:
            # 少了它，全班會搶同一個網址，只有第一位學生拿得到
            raise ValueError(t("course_env.publication_student_placeholder"))
        self.hostname_prefix = prefix
        return self


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    # 快速練習開放給所有登入者（開放對象的設定已移除；舊前端仍送 audience /
    # audience_class_ids，未宣告的欄位會被忽略）
    usage_scope: Literal["course", "quick_practice", "both"] = "course"
    max_concurrent_sessions: int | None = Field(default=None, ge=1, le=500)
    nodes: list[EnvironmentNodeIn] = Field(min_length=1, max_length=3)
    edges: list[EnvironmentEdgeIn] = Field(default_factory=list, max_length=6)
    publications: list[EnvironmentPublicationIn] = Field(
        default_factory=list, max_length=6
    )
    # explicit：只開畫出的連線，沒畫就隔離；segment：同網段全部互通（舊行為）
    peer_policy: Literal["explicit", "segment"] = "explicit"


class EnvironmentUpdate(EnvironmentCreate):
    pass


class EnvironmentBasicsIn(BaseModel):
    """Name, purpose and offering — editable at any version status.

    These live on the environment row rather than on a version. Publication
    freezes the machine configuration, not what the environment is called or
    who it is offered to, so the teacher can keep them current without cutting
    a new version.
    """

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    usage_scope: Literal["course", "quick_practice", "both"]


class EnvironmentDraftIn(BaseModel):
    # The editor may contain empty fields or unfinished numeric input. Only
    # publication turns this into a validated, deployable configuration.
    configuration: dict[str, Any]
    editor: dict[str, Any]
    # 只用來找「要續寫哪一份既有草稿」，永遠不會變成新資料列的 id：
    # 讓 client 指定 primary key，等於可以先佔走一個還沒被用到的 id。
    draft_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_size(self) -> "EnvironmentDraftIn":
        if len(self.model_dump_json().encode()) > MAX_DRAFT_BYTES:
            raise ValueError("Draft exceeds 256 KiB")
        return self


__all__ = [
    "EnvironmentBasicsIn",
    "EnvironmentCreate",
    "EnvironmentDraftIn",
    "EnvironmentEdgeIn",
    "EnvironmentNodeIn",
    "EnvironmentPublicationIn",
    "EnvironmentUpdate",
]
