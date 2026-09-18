/**
 * 首頁「需要處理的事」的資料整理。
 *
 * 分類軸是急迫度而不是資料來源：現在就處理 / 排進今天 / 只是知會。
 * 照來源分會讓同一件事出現兩次——監控告警與即時異常本來就重疊。
 */

const GUEST_SCOPES = new Set(["qemu", "lxc"]);

/** 異常項目要能點回那台機器；節點沒有專屬頁面，回監控頁。 */
export function resourcePath(row) {
  if (row?.vmid != null && GUEST_SCOPES.has(row.scope)) {
    return `/resource-mgmt/${row.vmid}`;
  }
  return "/monitoring";
}

export function formatCheckedAt(value, locale) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

/* 兩個來源對 guest 的標示法不同：alert 的 scope 是 vm、target 放 vmid；
   issue 的 scope 是 qemu/lxc、target 放機器名稱、vmid 另外給。
   要判斷「是不是同一件事」就得先正規化成同一組 key。 */
function guestKey(id, metric) {
  return `guest|${id}|${metric}`;
}

/** 一筆 issue 可能同時有 CPU 與記憶體兩個 signal，各算一個 key。 */
export function issueKeys(issue) {
  const signals = issue?.signals ?? [];
  if (!signals.length) {
    // 節點離線不是門檻問題，不會有對應的 alert，給它一個不會撞到的 key
    return [`${issue?.scope}|${issue?.target}|offline`];
  }
  return signals.map((signal) => (GUEST_SCOPES.has(issue.scope)
    ? guestKey(issue.vmid, signal.metric)
    : `node|${issue.target}|${signal.metric}`));
}

export function alertKey(alert) {
  if (alert?.scope === "vm") return guestKey(alert.target, alert.metric);
  return `${alert?.scope}|${alert?.target}|${alert?.metric}`;
}

/**
 * 合併「即時異常」與「未解除告警」。
 *
 * 兩邊的門檻判斷共用同一組設定，所以節點／VM 的 CPU 與記憶體會重複；
 * 但只有 alert 涵蓋節點磁碟，只有 issue 涵蓋節點離線，兩邊都不能砍。
 * 作法是 issue 優先（它是即時算的、帶機器名稱），alert 只補 issue 沒說到的。
 */
export function mergeInfraProblems(issues = [], alerts = []) {
  const covered = new Set();
  const rows = [];

  for (const issue of issues) {
    for (const key of issueKeys(issue)) covered.add(key);
    rows.push({
      source: "issue",
      key: `issue-${issue.scope}-${issue.target}`,
      kind: issue.kind,
      severity: issue.severity,
      scope: issue.scope,
      target: issue.target,
      vmid: issue.vmid ?? null,
      signals: issue.signals ?? [],
    });
  }

  for (const alert of alerts) {
    const key = alertKey(alert);
    if (covered.has(key)) continue;
    covered.add(key);
    rows.push({
      source: "alert",
      key: `alert-${alert.id}`,
      severity: "warning",
      scope: alert.scope,
      target: alert.target,
      vmid: alert.scope === "vm" ? Number(alert.target) || null : null,
      metric: alert.metric,
      value: alert.value,
      threshold: alert.threshold,
    });
  }

  // 嚴重的排前面，其餘維持來源順序
  return rows.sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "critical" ? -1 : 1));
}

/** 一列異常要顯示的說明文字。 */
export function describeInfraProblem(row, t) {
  if (row.source === "alert") {
    const metric = t(`AdminDashboardPage.pveMetric${row.metric}`);
    return t("AdminDashboardPage.infraAlertDetail", {
      metric,
      value: Number(row.value).toFixed(0),
      threshold: Number(row.threshold).toFixed(0),
    });
  }
  if (row.kind === "node_offline") return t("AdminDashboardPage.pveIssueNodeOffline");
  const signals = (row.signals ?? [])
    .map((signal) => `${t(`AdminDashboardPage.pveMetric${signal.metric}`)} ${Number(signal.value).toFixed(0)}%`)
    .join(" · ");
  return signals || t("AdminDashboardPage.pveIssueOverloaded");
}

/**
 * 現在就處理：服務已經受影響，看到就該動手。
 * 基礎設施異常放前面，因為它會連帶讓其他東西壞掉。
 */
export function buildUrgentRows({ infraProblems = [], failedJobs = 0, miningIncidents = 0 }, t) {
  const rows = infraProblems.map((row) => ({
    key: row.key,
    tone: row.severity === "critical" ? "critical" : "warning",
    icon: row.severity === "critical" ? "error" : "warning",
    title: row.target,
    detail: describeInfraProblem(row, t),
    path: resourcePath(row),
  }));
  /* 挖礦事件卡著一台被凍結的機器與一個等著被定奪的帳號，急迫度等同基礎設施問題 */
  if (miningIncidents > 0) {
    rows.push({
      key: "mining-incidents",
      tone: "critical",
      icon: "report",
      title: t("AdminDashboardPage.issueMiningTitle"),
      detail: t("AdminDashboardPage.issueMiningDesc"),
      count: miningIncidents,
      path: "/monitoring",
    });
  }
  if (failedJobs > 0) {
    rows.push({
      key: "failed-jobs",
      tone: "critical",
      icon: "error_outline",
      title: t("AdminDashboardPage.issueJobsTitle"),
      detail: t("AdminDashboardPage.issueJobsDesc"),
      count: failedJobs,
      path: "/jobs",
    });
  }
  return rows;
}

/** 排進今天：有人被卡住等我放行，但服務沒壞，不必立刻中斷手邊的事。 */
export function buildTodayRows(checks, t) {
  const rows = [];
  if (checks.requests > 0) {
    rows.push({ key: "requests", icon: "pending_actions", title: t("AdminDashboardPage.issueRequestsTitle"), count: checks.requests, path: "/request-review" });
  }
  if (checks.batches > 0) {
    rows.push({ key: "batches", icon: "library_add_check", title: t("AdminDashboardPage.issueBatchesTitle"), count: checks.batches, path: "/batch-review" });
  }
  if (checks.aiRequests > 0) {
    rows.push({ key: "ai", icon: "rate_review", title: t("AdminDashboardPage.issueAiTitle"), count: checks.aiRequests, path: "/ai-api-review" });
  }
  return rows;
}

/** 只是知會：不需要動作的運作數字。 */
export function buildFyiStats(overview, t) {
  if (!overview) return [];
  const guests = (scope) => `${overview[`${scope}_running`] ?? 0}/${(overview[`${scope}_running`] ?? 0) + (overview[`${scope}_stopped`] ?? 0)}`;
  return [
    { key: "nodes", icon: "dns", label: t("AdminDashboardPage.pveNodes"), value: `${overview.nodes_online ?? 0}/${overview.nodes_total ?? 0}`, path: "/monitoring" },
    { key: "vms", icon: "desktop_windows", label: "VM", value: guests("vms"), path: "/resource-mgmt" },
    { key: "lxc", icon: "view_agenda", label: "LXC", value: guests("lxc"), path: "/resource-mgmt" },
  ];
}
