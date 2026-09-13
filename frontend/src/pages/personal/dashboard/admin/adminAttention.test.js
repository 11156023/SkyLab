import { describe, expect, it } from "vitest";
import {
  alertKey,
  buildFyiStats,
  buildTodayRows,
  buildUrgentRows,
  describeInfraProblem,
  formatCheckedAt,
  issueKeys,
  mergeInfraProblems,
  resourcePath,
} from "./adminAttention";

const t = (key, vars) => (vars ? `${key}(${Object.values(vars).join(",")})` : key);

describe("resourcePath", () => {
  it("links a guest to its own page and a node to monitoring", () => {
    expect(resourcePath({ scope: "qemu", vmid: 101 })).toBe("/resource-mgmt/101");
    expect(resourcePath({ scope: "lxc", vmid: 202 })).toBe("/resource-mgmt/202");
    expect(resourcePath({ scope: "node", target: "pve02" })).toBe("/monitoring");
    expect(resourcePath({ scope: "qemu", target: "vm-no-id" })).toBe("/monitoring");
  });
});

describe("formatCheckedAt", () => {
  it("shows a 24-hour clock and refuses unparseable input", () => {
    expect(formatCheckedAt("2026-09-08T06:32:18.000Z", "en-US")).toMatch(/\d{2}:\d{2}:\d{2}/);
    expect(formatCheckedAt(null, "en-US")).toBe("—");
    expect(formatCheckedAt("not-a-date", "en-US")).toBe("—");
  });
});

describe("dedupe keys", () => {
  it("normalises the two sources' different guest notation", () => {
    // issue: scope qemu/lxc、target 是機器名、vmid 另外給
    expect(issueKeys({ scope: "qemu", target: "web-01", vmid: 101, signals: [{ metric: "cpu" }] }))
      .toEqual(["guest|101|cpu"]);
    // alert: scope vm、target 就是 vmid
    expect(alertKey({ scope: "vm", target: "101", metric: "cpu" })).toBe("guest|101|cpu");
  });

  it("gives one key per signal", () => {
    expect(issueKeys({
      scope: "node",
      target: "pve1",
      signals: [{ metric: "cpu" }, { metric: "memory" }],
    })).toEqual(["node|pve1|cpu", "node|pve1|memory"]);
  });

  it("gives an offline node a key no alert can collide with", () => {
    expect(issueKeys({ kind: "node_offline", scope: "node", target: "pve2", signals: [] }))
      .toEqual(["node|pve2|offline"]);
  });
});

describe("mergeInfraProblems", () => {
  const offline = { kind: "node_offline", severity: "critical", scope: "node", target: "pve2", signals: [] };
  const nodeHot = {
    kind: "node_overloaded",
    severity: "warning",
    scope: "node",
    target: "pve1",
    signals: [{ metric: "memory", value: 94, threshold: 90 }],
  };

  it("drops the alert that merely restates an issue", () => {
    const rows = mergeInfraProblems(
      [nodeHot],
      [{ id: "a1", scope: "node", target: "pve1", metric: "memory", value: 94, threshold: 90 }],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].source).toBe("issue");
  });

  it("keeps a node disk alert, which issues never cover", () => {
    const rows = mergeInfraProblems(
      [nodeHot],
      [{ id: "a2", scope: "node", target: "pve1", metric: "disk", value: 96, threshold: 90 }],
    );
    expect(rows.map((row) => row.source)).toEqual(["issue", "alert"]);
    expect(rows[1].metric).toBe("disk");
  });

  it("keeps an offline node and any alert about it", () => {
    const rows = mergeInfraProblems(
      [offline],
      [{ id: "a3", scope: "node", target: "pve2", metric: "cpu", value: 99, threshold: 90 }],
    );
    expect(rows).toHaveLength(2);
  });

  it("puts critical first", () => {
    const rows = mergeInfraProblems([nodeHot, offline], []);
    expect(rows[0].target).toBe("pve2");
  });

  it("survives empty input", () => {
    expect(mergeInfraProblems()).toEqual([]);
  });
});

describe("describeInfraProblem", () => {
  it("names the metric and the threshold for an alert", () => {
    const text = describeInfraProblem(
      { source: "alert", metric: "disk", value: 96.4, threshold: 90 },
      t,
    );
    expect(text).toBe("AdminDashboardPage.infraAlertDetail(AdminDashboardPage.pveMetricdisk,96,90)");
  });

  it("says offline for an offline node rather than listing metrics", () => {
    expect(describeInfraProblem({ source: "issue", kind: "node_offline" }, t))
      .toBe("AdminDashboardPage.pveIssueNodeOffline");
  });

  it("lists the signals that tripped", () => {
    expect(describeInfraProblem(
      { source: "issue", kind: "node_overloaded", signals: [{ metric: "cpu", value: 97.2 }] },
      t,
    )).toBe("AdminDashboardPage.pveMetriccpu 97%");
  });
});

describe("buildUrgentRows", () => {
  it("puts infrastructure before our own job queue", () => {
    const rows = buildUrgentRows({
      infraProblems: mergeInfraProblems(
        [{ kind: "node_offline", severity: "critical", scope: "node", target: "pve2", signals: [] }],
        [],
      ),
      failedJobs: 2,
    }, t);
    expect(rows.map((row) => row.key)).toEqual(["issue-node-pve2", "failed-jobs"]);
    expect(rows[0].path).toBe("/monitoring");
  });

  it("lists undecided mining incidents between infra and jobs", () => {
    const rows = buildUrgentRows({ infraProblems: [], failedJobs: 1, miningIncidents: 2 }, t);
    expect(rows.map((row) => row.key)).toEqual(["mining-incidents", "failed-jobs"]);
    expect(rows[0].count).toBe(2);
    expect(rows[0].path).toBe("/monitoring");
  });

  it("omits the job and mining rows when there is nothing to act on", () => {
    expect(buildUrgentRows({ infraProblems: [], failedJobs: 0, miningIncidents: 0 }, t)).toEqual([]);
  });
});

describe("buildTodayRows", () => {
  it("only lists queues that actually have something waiting", () => {
    const rows = buildTodayRows({ requests: 0, batches: 3, aiRequests: 1 }, t);
    expect(rows.map((row) => row.key)).toEqual(["batches", "ai"]);
    expect(rows.every((row) => row.count > 0)).toBe(true);
  });
});

describe("buildFyiStats", () => {
  it("reads running over total for each kind", () => {
    const stats = buildFyiStats({
      nodes_online: 17,
      nodes_total: 18,
      vms_running: 1,
      vms_stopped: 1,
      lxc_running: 0,
      lxc_stopped: 4,
    }, t);
    expect(stats.map((row) => row.value)).toEqual(["17/18", "1/2", "0/4"]);
  });

  it("returns nothing when the overview never arrived", () => {
    expect(buildFyiStats(null, t)).toEqual([]);
  });
});
