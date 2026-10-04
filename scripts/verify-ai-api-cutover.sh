#!/usr/bin/env bash
# Minimal post-deployment smoke test. It discovers every model visible to the
# restricted Campus credential and sends one small chat completion to each.
# It never calls LiteLLM administration endpoints.
set -euo pipefail

: "${AI_API_SMOKE_KEY:?set an isolated, approved ccai_* smoke credential}"

base_url="${AI_API_PUBLIC_BASE_URL:-http://127.0.0.1:8000/api/v1}"
timeout="${AI_API_SMOKE_TIMEOUT:-120}"
python_bin="${AI_API_SMOKE_PYTHON:-python3}"
base_url="${base_url%/}"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

if ! command -v "$python_bin" >/dev/null 2>&1; then
  printf 'Python interpreter not found: %s\n' "$python_bin" >&2
  exit 1
fi

curl_api() {
  # Keep the user credential out of curl's command arguments and output.
  curl --silent --show-error --fail-with-body \
    --connect-timeout 10 \
    --max-time "$timeout" \
    --config <(printf 'header = "Authorization: Bearer %s"\n' "$AI_API_SMOKE_KEY") \
    "$@"
}

printf 'Checking public model list...\n'
curl_api "$base_url/ai-proxy/models" >"$workdir/models.json"
mapfile -t models < <(
  "$python_bin" - "$workdir/models.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)

seen: set[str] = set()
for item in payload.get("data", []):
    model_id = item.get("id") if isinstance(item, dict) else None
    if isinstance(model_id, str) and model_id and model_id not in seen:
        seen.add(model_id)
        print(model_id)
PY
)
if (( ${#models[@]} == 0 )); then
  printf 'No public AI models were returned.\n' >&2
  exit 1
fi

total="${#models[@]}"
for index in "${!models[@]}"; do
  model="${models[$index]}"
  result_file="$workdir/chat-$index.json"
  payload="$(
    "$python_bin" - "$model" <<'PY'
import json
import sys

print(json.dumps({
    "model": sys.argv[1],
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "max_tokens": 8,
    "stream": False,
}))
PY
  )"

  printf '[%d/%d] Checking chat/completions: %s\n' "$((index + 1))" "$total" "$model"
  curl_api -H 'Content-Type: application/json' \
    --data "$payload" \
    "$base_url/ai-proxy/chat/completions" >"$result_file"
  "$python_bin" - "$result_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)

choices = payload.get("choices")
usage = payload.get("usage")
if not isinstance(choices, list) or not choices:
    raise SystemExit("chat response choices are missing")
if not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
    raise SystemExit("chat response message is missing")
if not isinstance(usage, dict):
    raise SystemExit("chat response usage is missing")
PY
done

printf 'AI API smoke test passed for all %d public models.\n' "$total"
