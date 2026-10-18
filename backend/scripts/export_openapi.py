"""Write the backend's OpenAPI schema to a file.

Used by the documentation site (``website/``) to render the REST API
reference. The FastAPI app is imported but never started, so no database,
Redis or Proxmox connection is needed; the settings still have to be
loadable, i.e. the project ``.env`` or the equivalent environment variables
(``SECRET_KEY``, ``FIRST_SUPERUSER`` …) must be present.

Usage (from ``backend/``)::

    uv run --no-sync python scripts/export_openapi.py ../website/public/openapi.json
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

logger = logging.getLogger("export_openapi")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(argv) != 2:
        logger.error("usage: %s <output.json>", argv[0])
        return 2
    output = Path(argv[1]).resolve()

    from app.main import app  # noqa: PLC0415 - import after sys.path setup

    schema = app.openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "OpenAPI schema with %s paths written to %s",
        len(schema.get("paths", {})),
        output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
