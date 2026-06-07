from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.ai.template_recommendation.capability_catalog import (
    SUPPORTED_TEMPLATE_SLUGS,
    match_capabilities,
    normalize_multilingual_text,
    serialize_capability,
)
from app.ai.template_recommendation.config import settings

IGNORED_FILES = {"metadata.json", "versions.json", "github-versions.json"}


@dataclass(slots=True)
class TemplateItem:
    slug: str
    name: str
    description: str
    categories: list[int]
    template_type: str
    interface_port: int | None
    website: str | None
    documentation: str | None
    updateable: bool
    raw: dict[str, Any]


@dataclass(slots=True)
class TemplateCatalog:
    items: list[TemplateItem]
    categories: dict[int, str]


def load_catalog(json_dir: Path) -> TemplateCatalog:
    if not json_dir.exists():
        raise FileNotFoundError(f"Template directory not found: {json_dir}")

    metadata_path = json_dir / "metadata.json"
    categories: dict[int, str] = {}
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        categories = {item["id"]: item["name"] for item in metadata.get("categories", [])}

    items: list[TemplateItem] = []
    for file_path in sorted(json_dir.glob("*.json")):
        if file_path.name in IGNORED_FILES:
            continue
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        items.append(
            TemplateItem(
                slug=payload.get("slug") or file_path.stem,
                name=payload.get("name") or file_path.stem,
                description=payload.get("description") or "",
                categories=list(payload.get("categories") or []),
                template_type=(payload.get("type") or "lxc").lower(),
                interface_port=payload.get("interface_port"),
                website=payload.get("website"),
                documentation=payload.get("documentation"),
                updateable=bool(payload.get("updateable")),
                raw=payload,
            )
        )
    return TemplateCatalog(items=items, categories=categories)


@lru_cache(maxsize=1)
def get_catalog() -> TemplateCatalog:
    return load_catalog(settings.resolved_templates_dir)


def serialize_template(item: TemplateItem) -> dict[str, Any]:
    default_resources: dict[str, Any] = {}
    install_methods = item.raw.get("install_methods") or []
    if install_methods:
        default_resources = dict(install_methods[0].get("resources") or {})
    return {
        "slug": item.slug,
        "name": item.name,
        "description": item.description,
        "logo": item.raw.get("logo"),
        "type": item.template_type,
        "categories": item.categories,
        "interface_port": item.interface_port,
        "website": item.website,
        "documentation": item.documentation,
        "updateable": item.updateable,
        "default_resources": default_resources,
    }


def catalog_lookup(template_catalog: TemplateCatalog) -> dict[str, TemplateItem]:
    return {item.slug.lower(): item for item in template_catalog.items}


def build_catalog_prompt_bundle(
    template_catalog: TemplateCatalog,
    goal: str,
    top_k: int,
    *,
    needs_public_web: bool,
    needs_database: bool,
) -> dict[str, Any]:
    supported_catalog = _supported_catalog(template_catalog)
    capability_matches = match_capabilities(goal)
    capability_slugs = {
        slug.lower()
        for capability in capability_matches
        for slug in capability.preferred_templates
    }
    explicit_matches = find_explicit_template_matches(supported_catalog, goal)
    explicit_matches = _unique_items(
        [
            *explicit_matches,
            *[
                item
                for item in supported_catalog.items
                if item.slug.lower() in capability_slugs
            ],
        ]
    )
    support_candidates = suggest_support_templates(
        supported_catalog,
        needs_public_web=needs_public_web,
        needs_database=needs_database,
    )
    candidate_items = _select_ranked_candidates(
        supported_catalog,
        goal,
        top_k,
        explicit_matches=explicit_matches,
        support_candidates=support_candidates,
        capability_slugs=capability_slugs,
    )
    return {
        "matched_capabilities": [
            serialize_capability(capability) for capability in capability_matches
        ],
        "explicit_matches": [serialize_template(item) for item in explicit_matches],
        "support_candidates": [serialize_template(item) for item in support_candidates],
        "candidate_templates": [serialize_template(item) for item in candidate_items],
    }


def _supported_catalog(template_catalog: TemplateCatalog) -> TemplateCatalog:
    return TemplateCatalog(
        items=[
            item
            for item in template_catalog.items
            if item.slug.lower() in SUPPORTED_TEMPLATE_SLUGS
        ],
        categories=template_catalog.categories,
    )


def find_explicit_template_matches(template_catalog: TemplateCatalog, goal: str) -> list[TemplateItem]:
    normalized_goal = _normalize_text(goal)
    matches: list[TemplateItem] = []
    for item in template_catalog.items:
        if any(_goal_mentions_alias(normalized_goal, alias) for alias in _template_aliases(item)):
            matches.append(item)
    return _unique_items(matches)


def suggest_support_templates(
    template_catalog: TemplateCatalog,
    *,
    needs_public_web: bool,
    needs_database: bool,
) -> list[TemplateItem]:
    support: list[TemplateItem] = []
    for item in template_catalog.items:
        category_names = _category_names(template_catalog, item)
        if needs_database and "databases" in category_names:
            support.append(item)
        if needs_public_web and "webservers proxies" in category_names:
            support.append(item)
    return _unique_items(support)


def _select_ranked_candidates(
    template_catalog: TemplateCatalog,
    goal: str,
    top_k: int,
    *,
    explicit_matches: list[TemplateItem],
    support_candidates: list[TemplateItem],
    capability_slugs: set[str] | None = None,
) -> list[TemplateItem]:
    explicit_slugs = {item.slug.lower() for item in explicit_matches}
    support_slugs = {item.slug.lower() for item in support_candidates}
    capability_slugs = capability_slugs or set()
    ranked_items = sorted(
        template_catalog.items,
        key=lambda item: (
            _template_relevance_score(
                item,
                goal,
                explicit_slugs=explicit_slugs,
                support_slugs=support_slugs,
                capability_slugs=capability_slugs,
            ),
            item.slug.lower() in SUPPORTED_TEMPLATE_SLUGS,
            item.updateable,
            item.interface_port is not None,
            item.slug,
        ),
        reverse=True,
    )
    limit = max(top_k * 10, 30)
    combined = [*explicit_matches, *support_candidates, *ranked_items[:limit]]
    return _unique_items(combined)[:limit]


def _template_relevance_score(
    item: TemplateItem,
    goal: str,
    *,
    explicit_slugs: set[str],
    support_slugs: set[str],
    capability_slugs: set[str],
) -> int:
    score = 0
    slug = item.slug.lower()
    if slug in explicit_slugs:
        score += 100
    if slug in capability_slugs:
        score += 90
    if slug in SUPPORTED_TEMPLATE_SLUGS:
        score += 20
    if slug in support_slugs:
        score += 35

    normalized_goal = _normalize_multilingual_text(goal)
    alias_hits = sum(
        1
        for alias in _template_aliases(item)
        if alias and _goal_mentions_alias(normalized_goal, alias)
    )
    score += alias_hits * 12

    goal_tokens = set(normalized_goal.split())
    item_tokens = set(_normalize_text(" ".join((item.slug, item.name, item.description))).split())
    score += len(goal_tokens & item_tokens)
    return score


def _template_aliases(item: TemplateItem) -> set[str]:
    aliases = {
        _normalize_text(item.slug),
        _normalize_text(item.name),
        _normalize_text(item.slug.replace("-", " ")),
        _normalize_text(item.name.replace("-", " ")),
    }
    aliases.add(_normalize_text(re.sub(r"[^a-z0-9]+", "", item.slug.lower())))
    aliases.add(_normalize_text(re.sub(r"[^a-z0-9]+", "", item.name.lower())))
    return {alias for alias in aliases if alias}


def _normalize_multilingual_text(text: str) -> str:
    return normalize_multilingual_text(text)


def _normalize_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _goal_mentions_alias(normalized_goal: str, alias: str) -> bool:
    if alias in normalized_goal:
        return True

    compact_goal_tokens = normalized_goal.split()
    alias_tokens = alias.split()
    if len(alias_tokens) == 1:
        alias_token = alias_tokens[0]
        if len(alias_token) < 4:
            return False
        return any(
            token.startswith(alias_token) or alias_token.startswith(token)
            for token in compact_goal_tokens
            if len(token) >= 4
        )

    compact_alias = "".join(alias_tokens)
    compact_goal = "".join(compact_goal_tokens)
    return len(compact_alias) >= 6 and compact_alias in compact_goal


def _category_names(template_catalog: TemplateCatalog, item: TemplateItem) -> set[str]:
    return {_normalize_text(template_catalog.categories.get(category_id, "")) for category_id in item.categories}


def _unique_items(items: list[TemplateItem]) -> list[TemplateItem]:
    seen: set[str] = set()
    unique: list[TemplateItem] = []
    for item in items:
        slug = item.slug.lower()
        if slug in seen:
            continue
        seen.add(slug)
        unique.append(item)
    return unique
