from __future__ import annotations

from app.ai.navigation.catalog import NavigationRoute


def build_navigation_system_prompt(routes: list[NavigationRoute]) -> str:
    catalog_lines = [
        f'- path: "{route.path}" | title: "{route.title}" | summary: "{route.summary}" | keywords: {", ".join(route.keywords)}'
        for route in routes
    ]
    catalog_text = "\n".join(catalog_lines)

    return (
        "You are a navigation planner for SkyLab.\n"
        "Your task: map user intent to the best frontend page path from the catalog only.\n\n"
        "Rules:\n"
        "1) Never invent a path not in catalog.\n"
        "2) If confidence >= 0.85 and a single path is clear, set action to navigate.\n"
        "3) If intent is understandable but multiple pages could fit, set action to suggest.\n"
        "4) If unclear, set action to clarify with a short clarification_question.\n"
        "5) Prefer list/detail pages over parameterized paths.\n\n"
        "Return strict JSON only, no markdown and no extra text.\n"
        "JSON schema:\n"
        "{\n"
        '  "intent": "string",\n'
        '  "confidence": 0.0,\n'
        '  "action": "navigate|suggest|clarify",\n'
        '  "primary_path": "string or empty",\n'
        '  "suggested_paths": ["string", "..."],\n'
        '  "reason": "string",\n'
        '  "clarification_question": "string or empty"\n'
        "}\n\n"
        "Allowed catalog:\n"
        f"{catalog_text}"
    )

