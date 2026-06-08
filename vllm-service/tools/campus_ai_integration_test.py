from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx


DEFAULT_RUBRIC_FILE = (
    Path(__file__).resolve().parent.parent / "專題 AI 實戰評分測試表.docx"
)


@dataclass
class CaseResult:
    name: str
    success: bool
    status_code: int | None
    elapsed_ms: int
    detail: dict[str, Any]


class CampusAIIntegrationTester:
    def __init__(
        self,
        *,
        base_url: str,
        api_v1_str: str,
        username: str,
        password: str,
        timeout: float,
        verify_ssl: bool,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_v1_str = api_v1_str
        self.username = username
        self.password = password
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._access_token = ""

    @property
    def api_base(self) -> str:
        return f"{self.base_url}{self.api_v1_str}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self.api_base}{path}"
        async with httpx.AsyncClient(
            timeout=self.timeout,
            verify=self.verify_ssl,
        ) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                data=data,
                files=files,
            )
        return response

    async def login(self) -> None:
        response = await self._request(
            "POST",
            "/login/access-token",
            data={"username": self.username, "password": self.password},
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Login failed with status {response.status_code}: {response.text}"
            )
        payload = response.json()
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Login succeeded but access_token is missing")
        self._access_token = token

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            raise RuntimeError("Missing access token. Call login() first.")
        return {"Authorization": f"Bearer {self._access_token}"}

    async def run_rubric_upload(self, rubric_file: Path) -> CaseResult:
        started = time.perf_counter()
        if not rubric_file.exists():
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return CaseResult(
                name="rubric_upload",
                success=False,
                status_code=None,
                elapsed_ms=elapsed_ms,
                detail={
                    "error": f"Rubric file not found: {rubric_file}",
                    "skipped": True,
                },
            )

        mime_type, _ = mimetypes.guess_type(str(rubric_file))
        if not mime_type:
            mime_type = "application/octet-stream"

        with rubric_file.open("rb") as f:
            response = await self._request(
                "POST",
                "/rubric/upload",
                headers=self._auth_headers(),
                files={"file": (rubric_file.name, f, mime_type)},
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            return CaseResult(
                name="rubric_upload",
                success=False,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
                detail={"error": response.text},
            )

        data = response.json()
        analysis = data.get("analysis") or {}
        ai_metrics = data.get("ai_metrics") or {}
        return CaseResult(
            name="rubric_upload",
            success=True,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            detail={
                "file": str(rubric_file),
                "summary_preview": str(analysis.get("summary") or "")[:180],
                "total_items": int(analysis.get("total_items") or 0),
                "checked_count": int(analysis.get("checked_count") or 0),
                "ai_metrics": ai_metrics,
            },
        )

    async def run_template_chat(self, prompt: str) -> CaseResult:
        started = time.perf_counter()
        response = await self._request(
            "POST",
            "/ai/template-recommendation/chat",
            headers=self._auth_headers(),
            json_body={
                "messages": [{"role": "user", "content": prompt}],
                "top_k": 5,
            },
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            return CaseResult(
                name="template_recommendation_chat",
                success=False,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
                detail={"error": response.text},
            )

        data = response.json()
        return CaseResult(
            name="template_recommendation_chat",
            success=True,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            detail={
                "prompt": prompt,
                "reply_preview": str(data.get("reply") or "")[:180],
                "prompt_tokens": int(data.get("prompt_tokens") or 0),
                "completion_tokens": int(data.get("completion_tokens") or 0),
                "total_tokens": int(data.get("total_tokens") or 0),
                "tokens_per_second": float(data.get("tokens_per_second") or 0.0),
            },
        )

    async def run_pve_chat(self, prompt: str) -> CaseResult:
        started = time.perf_counter()
        response = await self._request(
            "POST",
            "/ai/pve-log/chat",
            headers=self._auth_headers(),
            json_body={"message": prompt},
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            return CaseResult(
                name="pve_chat",
                success=False,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
                detail={"error": response.text},
            )

        data = response.json()
        pve_error = data.get("error")
        pve_reply = str(data.get("reply") or "")
        is_success = not pve_error and bool(pve_reply.strip())
        return CaseResult(
            name="pve_chat",
            success=is_success,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            detail={
                "prompt": prompt,
                "reply_preview": pve_reply[:180],
                "tools_called": data.get("tools_called") or [],
                "error": pve_error,
            },
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SkyLab AI integration test against main backend routes"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("CAMPUS_BACKEND_BASE_URL", "http://localhost:8000"),
        help="Backend base URL",
    )
    parser.add_argument(
        "--api-v1-str",
        default=os.getenv("CAMPUS_BACKEND_API_V1", "/api/v1"),
        help="Backend API prefix",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("CAMPUS_BACKEND_USERNAME", ""),
        help="Backend login username/email",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("CAMPUS_BACKEND_PASSWORD", ""),
        help="Backend login password",
    )
    parser.add_argument(
        "--rubric-file",
        default=os.getenv("CAMPUS_RUBRIC_FILE", str(DEFAULT_RUBRIC_FILE)),
        help="Rubric file path for /rubric/upload (.docx or .pdf)",
    )
    parser.add_argument(
        "--template-prompt",
        default=os.getenv("CAMPUS_TEMPLATE_PROMPT", "我想建立python環境"),
        help="Prompt for template recommendation chat",
    )
    parser.add_argument(
        "--pve-prompt",
        default=os.getenv("CAMPUS_PVE_PROMPT", "請幫我看節點狀態"),
        help="Prompt for AI-PVE chat",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("CAMPUS_TEST_TIMEOUT", "90")),
        help="HTTP timeout in seconds",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification",
    )
    parser.add_argument(
        "--report-file",
        default="",
        help="Optional JSON report output path",
    )
    parser.add_argument(
        "--skip-rubric",
        action="store_true",
        help="Skip rubric upload scenario",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with non-zero code if any case fails",
    )
    return parser.parse_args()


def _print_summary(results: list[CaseResult]) -> None:
    print("=" * 78)
    print("SkyLab AI integration test summary")
    print("=" * 78)
    for result in results:
        status = "PASS" if result.success else "FAIL"
        print(
            f"[{status}] {result.name:<30} "
            f"status={result.status_code!s:<4} elapsed={result.elapsed_ms}ms"
        )
    print("-" * 78)


async def _run(args: argparse.Namespace) -> tuple[list[CaseResult], dict[str, Any]]:
    if not args.username or not args.password:
        raise RuntimeError(
            "Missing credentials. Set --username/--password or env "
            "CAMPUS_BACKEND_USERNAME/CAMPUS_BACKEND_PASSWORD"
        )

    tester = CampusAIIntegrationTester(
        base_url=args.base_url,
        api_v1_str=args.api_v1_str,
        username=args.username,
        password=args.password,
        timeout=args.timeout,
        verify_ssl=not args.insecure,
    )

    await tester.login()

    results: list[CaseResult] = []

    if not args.skip_rubric:
        results.append(await tester.run_rubric_upload(Path(args.rubric_file)))

    results.append(await tester.run_template_chat(args.template_prompt))
    results.append(await tester.run_pve_chat(args.pve_prompt))

    payload = {
        "base_url": args.base_url,
        "api_v1_str": args.api_v1_str,
        "executed_at_unix": int(time.time()),
        "results": [asdict(item) for item in results],
    }
    return results, payload


def main() -> int:
    import asyncio

    args = parse_args()

    try:
        results, payload = asyncio.run(_run(args))
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    _print_summary(results)
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.report_file:
        report_path = Path(args.report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Report written to: {report_path}")

    if args.strict and any(not item.success for item in results):
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
