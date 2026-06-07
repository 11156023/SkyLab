from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SUPPORTED_TEMPLATE_SLUGS = frozenset(
    {
        "wordpress",
        "n8n",
        "postgresql",
        "openwebui",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    key: str
    label: str
    aliases: tuple[str, ...]
    preferred_templates: tuple[str, ...]
    fallback_resource_type: str
    fallback_reason: str
    needs_public_web: bool = False
    needs_database: bool = False
    requires_gpu: bool = False
    gpu_optional: bool = False


CAPABILITIES: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        key="website_cms",
        label="網站 / 部落格 / CMS",
        aliases=(
            "wordpress",
            "word press",
            "website",
            "blog",
            "cms",
            "site",
            "web site",
            "網站",
            "架站",
            "部落格",
            "部落客",
            "內容管理",
            "內容管理系統",
            "形象網站",
            "官方網站",
            "官網",
        ),
        preferred_templates=("wordpress",),
        fallback_resource_type="lxc",
        fallback_reason="若 WordPress 模板不可用，可先用通用 Linux LXC 建置網站服務。",
        needs_public_web=True,
        needs_database=True,
    ),
    CapabilityDefinition(
        key="workflow_automation",
        label="流程自動化 / API 串接",
        aliases=(
            "n8n",
            "workflow",
            "automation",
            "webhook",
            "api integration",
            "api automation",
            "流程自動化",
            "工作流",
            "自動化",
            "任務自動化",
            "api 串接",
            "api串接",
            "服務串接",
            "資料串接",
            "webhook",
            "排程任務",
        ),
        preferred_templates=("n8n",),
        fallback_resource_type="lxc",
        fallback_reason="若 n8n 模板不可用，可用通用 Linux LXC 手動部署自動化服務。",
        needs_public_web=True,
    ),
    CapabilityDefinition(
        key="relational_database",
        label="PostgreSQL / SQL 資料庫",
        aliases=(
            "postgresql",
            "postgres",
            "postgre",
            "pgsql",
            "sql",
            "database",
            "relational database",
            "資料庫",
            "關聯式資料庫",
            "資料庫伺服器",
            "sql 資料庫",
            "sql資料庫",
            "儲存資料",
            "存資料",
        ),
        preferred_templates=("postgresql",),
        fallback_resource_type="lxc",
        fallback_reason="若 PostgreSQL 模板不可用，可用通用 Linux LXC 安裝資料庫服務。",
        needs_database=True,
    ),
    CapabilityDefinition(
        key="ai_web_interface",
        label="Open WebUI / AI 模型介面",
        aliases=(
            "openwebui",
            "open webui",
            "ollama",
            "llm",
            "large language model",
            "ai chat",
            "chatbot",
            "模型介面",
            "ai 介面",
            "ai聊天",
            "ai 聊天",
            "大語言模型",
            "語言模型",
            "離線 ai",
            "本地 ai",
            "聊天機器人",
            "chatgpt",
        ),
        preferred_templates=("openwebui",),
        fallback_resource_type="vm",
        fallback_reason="若 Open WebUI 模板不可用，或需要本地模型/GPU，建議改用 VM 手動部署。",
        gpu_optional=True,
    ),
)


def normalize_multilingual_text(text: str) -> str:
    normalized = str(text or "").casefold()
    normalized = re.sub(r"[_\-/]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _alias_matches(normalized_text: str, alias: str) -> bool:
    normalized_alias = normalize_multilingual_text(alias)
    if not normalized_alias:
        return False
    if re.search(r"[a-z0-9]", normalized_alias):
        compact_text = re.sub(r"[^a-z0-9]+", "", normalized_text)
        compact_alias = re.sub(r"[^a-z0-9]+", "", normalized_alias)
        if len(compact_alias) >= 3 and compact_alias in compact_text:
            return True
    return normalized_alias in normalized_text


def match_capabilities(goal: str) -> list[CapabilityDefinition]:
    normalized_goal = normalize_multilingual_text(goal)
    matches: list[CapabilityDefinition] = []
    for capability in CAPABILITIES:
        if any(_alias_matches(normalized_goal, alias) for alias in capability.aliases):
            matches.append(capability)
    return matches


def serialize_capability(capability: CapabilityDefinition) -> dict[str, Any]:
    return {
        "key": capability.key,
        "label": capability.label,
        "preferred_templates": list(capability.preferred_templates),
        "fallback": {
            "resource_type": capability.fallback_resource_type,
            "reason": capability.fallback_reason,
        },
        "signals": {
            "needs_public_web": capability.needs_public_web,
            "needs_database": capability.needs_database,
            "requires_gpu": capability.requires_gpu,
            "gpu_optional": capability.gpu_optional,
        },
    }

