#!/usr/bin/env bash
# self-hosted runner（skylab-main）的 job-started hook：每個 job 在任何 step 之前先經過這裡。
#
# 這台 runner 掛在公開 repo 上：任何人送的 PR 都能新增一支 runs-on: skylab-main 的
# workflow，而 pve-test environment 的審核只擋有宣告 environment 的 job。這支腳本在
# runner 端只放行 main 上手動觸發的部署與建班 workflow，其他 job 一律在 "Set up runner"
# 就失敗、不會執行任何 step。變數缺少時同樣拒絕（fail closed）。
#
# 安裝方式見 website/guides/deployment.md 的 "Restricting the runner"：放在 runner 目錄外、
# 由 root 擁有，runner 使用者不能改，並在 runner 的 .env 設 ACTIONS_RUNNER_HOOK_JOB_STARTED。
set -euo pipefail

ALLOWED_REPOSITORY="SkyLab-tw/SkyLab"
ALLOWED_REF="refs/heads/main"
ALLOWED_WORKFLOWS=(
  ".github/workflows/deploy-pve-test.yml"
  ".github/workflows/provision-class-event.yml"
)

refuse() {
  echo "job-started-guard: refusing this job: $*" >&2
  exit 1
}

repository="${GITHUB_REPOSITORY:-}"
event="${GITHUB_EVENT_NAME:-}"
workflow_ref="${GITHUB_WORKFLOW_REF:-}"

[ "$repository" = "$ALLOWED_REPOSITORY" ] ||
  refuse "repository '${repository:-<unset>}' is not $ALLOWED_REPOSITORY"
[ "$event" = "workflow_dispatch" ] ||
  refuse "event '${event:-<unset>}' is not workflow_dispatch"

for workflow in "${ALLOWED_WORKFLOWS[@]}"; do
  if [ "$workflow_ref" = "$ALLOWED_REPOSITORY/$workflow@$ALLOWED_REF" ]; then
    echo "job-started-guard: allowed $workflow_ref"
    exit 0
  fi
done

refuse "workflow '${workflow_ref:-<unset>}' is not an allowed workflow on $ALLOWED_REF"
