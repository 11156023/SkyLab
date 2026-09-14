"""API schemas for the AI PVE template test feature."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.ai.pve_log.schemas import SSHConfirmRequest, SSHExecResult, ToolCallRecord
from app.core.i18n import t


class AIPVETemplateRead(BaseModel):
    id: uuid.UUID
    template_key: str
    display_name: str
    description: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AIPVETemplateTarget(BaseModel):
    """One VMID and the AI role template selected for the test run."""

    vmid: int = Field(ge=1)
    template_key: str = Field(min_length=1, max_length=50)


class AIPVETemplateTargetRead(AIPVETemplateTarget):
    display_name: str


class AIPVETemplateChatRequest(BaseModel):
    targets: list[AIPVETemplateTarget] = Field(min_length=1, max_length=3)
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    messages: list[dict[str, Any]] | None = Field(default=None, max_length=40)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_single_target(cls, value: Any) -> Any:
        """Accept the old harness payload while clients migrate to targets."""
        if not isinstance(value, dict) or value.get("targets"):
            return value
        template_key = value.get("template_key")
        if template_key:
            return {
                **value,
                "targets": [
                    {"vmid": value.get("vmid", 102), "template_key": template_key}
                ],
            }
        return value

    @model_validator(mode="after")
    def require_message_or_history(self) -> AIPVETemplateChatRequest:
        has_message = bool(self.message and self.message.strip())
        has_history = bool(self.messages)
        if has_message and has_history:
            raise ValueError(t("pveTemplate.messageAndMessagesExclusive"))
        if not has_message and not has_history:
            raise ValueError(t("pveTemplate.messageOrMessagesRequired"))
        vmids = [target.vmid for target in self.targets]
        if len(vmids) != len(set(vmids)):
            raise ValueError(t("pveTemplate.duplicateVmid"))
        return self


class AIPVETemplateChatResponse(BaseModel):
    targets: list[AIPVETemplateTargetRead] = Field(default_factory=list)
    reply: str = ""
    tools_called: list[ToolCallRecord] = Field(default_factory=list)
    needs_confirmation: bool = False
    messages: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    confirmation_result: SSHExecResult | None = None


class AIPVETemplateSSHConfirmRequest(SSHConfirmRequest):
    """Confirmation body; host, key, and VMID are never accepted from client."""

    pass
