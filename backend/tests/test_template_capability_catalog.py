from __future__ import annotations

from app.ai.template_recommendation.capability_catalog import match_capabilities
from app.ai.template_recommendation.catalog_service import (
    TemplateCatalog,
    TemplateItem,
    build_catalog_prompt_bundle,
)


def _template(slug: str, name: str | None = None) -> TemplateItem:
    return TemplateItem(
        slug=slug,
        name=name or slug,
        description=f"{name or slug} service",
        categories=[],
        template_type="ct",
        interface_port=None,
        website=None,
        documentation=None,
        updateable=True,
        raw={
            "slug": slug,
            "name": name or slug,
            "install_methods": [
                {
                    "type": "default",
                    "resources": {
                        "cpu": 1,
                        "ram": 1024,
                        "hdd": 8,
                        "os": "debian",
                        "version": "13",
                    },
                }
            ],
        },
    )


def test_chinese_website_need_matches_wordpress_capability() -> None:
    matches = match_capabilities("\u6211\u60f3\u67b6\u4e00\u500b\u8ab2\u7a0b\u7db2\u7ad9\u548c\u90e8\u843d\u683c")

    assert [item.key for item in matches] == ["website_cms"]
    assert matches[0].preferred_templates == ("wordpress",)


def test_chinese_automation_need_matches_n8n_capability() -> None:
    matches = match_capabilities("\u6211\u8981\u505a webhook \u548c API \u4e32\u63a5\u7684\u6d41\u7a0b\u81ea\u52d5\u5316")

    assert [item.key for item in matches] == ["workflow_automation"]
    assert matches[0].preferred_templates == ("n8n",)


def test_catalog_bundle_filters_to_supported_templates() -> None:
    catalog = TemplateCatalog(
        items=[
            _template("wordpress", "WordPress"),
            _template("n8n", "n8n"),
            _template("postgresql", "PostgreSQL"),
            _template("openwebui", "Open WebUI"),
            _template("mysql", "MySQL"),
            _template("nginx", "Nginx"),
        ],
        categories={},
    )

    bundle = build_catalog_prompt_bundle(
        catalog,
        "\u6211\u9700\u8981\u8cc7\u6599\u5eab\uff0c\u53ef\u80fd\u7528 SQL",
        top_k=5,
        needs_public_web=False,
        needs_database=True,
    )

    candidate_slugs = {
        item["slug"] for item in bundle["candidate_templates"]
    }
    explicit_slugs = {
        item["slug"] for item in bundle["explicit_matches"]
    }

    assert "postgresql" in explicit_slugs
    assert candidate_slugs <= {"wordpress", "n8n", "postgresql", "openwebui"}
    assert "mysql" not in candidate_slugs
    assert "nginx" not in candidate_slugs
