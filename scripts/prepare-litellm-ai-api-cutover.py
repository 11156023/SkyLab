#!/usr/bin/env python3
"""Prepare a reversible Campus backend switch from the legacy Gateway to LiteLLM.

This command intentionally changes only a supplied dotenv file.  It never
starts, stops, or restarts a production service.  The restricted LiteLLM
service key must be injected through an environment variable so it is not put
in shell history or command-line arguments.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ENV_KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--endpoint",
        required=True,
        help="LiteLLM internal endpoint, e.g. http://litellm:4000",
    )
    parser.add_argument(
        "--service-key-env",
        default="LITELLM_SERVICE_API_KEY",
        help="Environment variable containing the restricted LiteLLM service key",
    )
    parser.add_argument("--timeout", type=int, default=320)
    parser.add_argument(
        "--allow-hostname",
        action="store_true",
        help="Acknowledge that a non-local hostname resolves only to a private/TLS endpoint",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the backup and updated dotenv file; omit for a no-write preflight",
    )
    return parser.parse_args()


def validate_endpoint(value: str, *, allow_hostname: bool) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("endpoint must not contain credentials, a query string, or a fragment")
    if parsed.hostname in {"0.0.0.0", "localhost"}:
        raise ValueError("endpoint cannot use 0.0.0.0 or localhost for a Docker backend")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        safe_hostnames = {"litellm", "host.docker.internal"}
        if parsed.hostname not in safe_hostnames and not allow_hostname:
            raise ValueError(
                "non-local hostname requires --allow-hostname after verifying its private/TLS network boundary"
            )
    else:
        if not (address.is_private or address.is_loopback):
            raise ValueError("endpoint IP address must be private or loopback")
    return value.rstrip("/")


def dotenv_value(value: str) -> str:
    """Produce a Docker Compose-compatible dotenv value without shell interpolation."""
    if re.fullmatch(r"[A-Za-z0-9_./:@+%=,-]+", value):
        return value
    return json.dumps(value)


def update_dotenv(
    path: Path, updates: dict[str, str], *, remove: frozenset[str] = frozenset()
) -> str:
    if remove.intersection(updates):
        raise ValueError("a dotenv key cannot be both updated and removed")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    counts = {key: 0 for key in updates}
    rendered: list[str] = []
    for line in lines:
        match = ENV_KEY_PATTERN.match(line)
        key = match.group(1) if match else None
        if key in remove:
            continue
        if key in updates:
            counts[key] += 1
            if counts[key] > 1:
                raise ValueError(f"{path} defines {key} more than once; resolve it before cutover")
            rendered.append(f"{key}={dotenv_value(updates[key])}\n")
        else:
            rendered.append(line)
    for key, count in counts.items():
        if count == 0:
            rendered.append(f"{key}={dotenv_value(updates[key])}\n")
    return "".join(rendered)


def main() -> int:
    args = parse_args()
    if not args.env_file.is_file():
        raise ValueError(f"dotenv file does not exist: {args.env_file}")
    if not 1 <= args.timeout <= 3_600:
        raise ValueError("timeout must be between 1 and 3600 seconds")

    endpoint = validate_endpoint(args.endpoint, allow_hostname=args.allow_hostname)
    service_key = os.environ.get(args.service_key_env, "").strip()
    if not service_key:
        raise ValueError(f"inject the service key through {args.service_key_env}")
    if service_key.startswith("ccai_"):
        raise ValueError("the LiteLLM service key must not be a Campus ccai_* user key")
    updates = {
        "AI_API_BASE_URL": endpoint,
        "AI_API_API_KEY": service_key,
        "AI_API_TIMEOUT": str(args.timeout),
        # The admin-only runtime snapshot is deliberately separate from the
        # public data-plane routes. Reuse the same restricted service
        # identity, never the LiteLLM master key, and keep it on the internal
        # endpoint selected for the Campus relay.
        "LITELLM_RUNTIME_BASE_URL": endpoint,
        "LITELLM_RUNTIME_API_KEY": service_key,
    }
    rendered = update_dotenv(
        args.env_file,
        updates,
        remove=frozenset({"AI_API_ALLOWED_MODELS"}),
    )
    print("LiteLLM cutover preflight passed:")
    print(f"  dotenv: {args.env_file}")
    print(f"  endpoint: {endpoint}")
    print(f"  timeout: {args.timeout}s")
    print("  models: managed by vllm-service/models.json via LiteLLM")
    print("  removed obsolete AI_API_ALLOWED_MODELS setting")
    print(f"  service key source: {args.service_key_env} (value redacted)")
    print("  runtime monitoring identity: restricted service key (value redacted)")

    if not args.apply:
        print("No files changed. Re-run with --apply after production preflight succeeds.")
        return 0

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = args.env_file.with_name(f"{args.env_file.name}.pre-litellm-{timestamp}.bak")
    shutil.copy2(args.env_file, backup)
    backup.chmod(0o600)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=args.env_file.parent, delete=False
    ) as temporary:
        temporary.write(rendered)
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o600)
    temporary_path.replace(args.env_file)
    print(f"Updated {args.env_file}; rollback backup: {backup}")
    print("Restart only the Campus backend/worker using the normal deployment process, then run scripts/verify-ai-api-cutover.sh.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
