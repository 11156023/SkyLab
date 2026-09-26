#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base_url="${LITELLM_BASE_URL:-http://127.0.0.1:4000}"
: "${LITELLM_MASTER_KEY:?export the deployment LITELLM_MASTER_KEY before running this check}"

expected_models="$(jq -ce '[.[] | .alias, (.legacy_aliases[]?.name)] | sort' "$repo_root/vllm-service/models.json")"
deployment_count="$(jq 'length' <<<"$expected_models")"

curl_json() {
  curl --fail --silent --show-error "$@"
}

curl_auth() {
  # 金鑰透過 --config 傳入，不出現在 curl 的命令列參數（ps / shell history）
  curl --fail --silent --show-error \
    --config <(printf 'header = "Authorization: Bearer %s"\n' "$LITELLM_MASTER_KEY") \
    "$@"
}

printf 'Checking LiteLLM liveliness and readiness...\n'
curl_json "$base_url/health/liveliness" | jq -e '. == "I\u0027m alive!"' >/dev/null
curl_json "$base_url/health/readiness" | jq -e '.status == "healthy"' >/dev/null

printf 'Checking the public model allowlist...\n'
curl_auth "$base_url/v1/models" \
  | jq -e --argjson expected "$expected_models" '
      [.data[].id] | sort == $expected
    ' >/dev/null

printf 'Checking hosted vLLM deployments...\n'
curl_auth "$base_url/health" \
  | jq -e --argjson count "$deployment_count" '.healthy_count == $count and .unhealthy_count == 0' >/dev/null

printf 'Checking backend-to-host gateway reachability...\n'
cd "$repo_root"
docker compose exec -T backend python -c '
import os, urllib.request
base = os.environ["AI_API_BASE_URL"].rstrip("/")
urllib.request.urlopen(base + "/health/liveliness", timeout=5).read()
' >/dev/null

printf 'LiteLLM operational verification passed.\n'
