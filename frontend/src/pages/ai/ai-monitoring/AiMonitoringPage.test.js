import { describe, expect, test } from "vitest";
import {
  buildAttentionItems,
  formatDuration,
  formatModelDisplay,
  formatTokens,
  isOkStatus,
  mergeModelRows,
  presetToBucket,
} from "./AiMonitoringPage";

const t = (key, values = {}) => `${key}:${JSON.stringify(values)}`;

describe("AiMonitoringPage formatting", () => {
  test("長區間使用日 bucket，短區間使用小時 bucket", () => {
    expect(presetToBucket("7d")).toBe("hour");
    expect(presetToBucket("30d")).toBe("day");
    expect(presetToBucket("90d")).toBe("day");
  });

  test("監控數字以可讀單位呈現", () => {
    expect(formatTokens(1200)).toBe("1.2K");
    expect(formatTokens(1200000)).toBe("1.2M");
    expect(formatDuration(1200)).toBe("1.2s");
    expect(formatDuration(null)).toBe("—");
  });

  test("模型名稱保留公開可辨識部分", () => {
    expect(formatModelDisplay("models--Qwen--Qwen2.5-7B")).toBe("Qwen/Qwen2.5-7B");
    expect(formatModelDisplay("/home/hmr0836/models/gemma4-26b-a4b-it-fp8")).toBe("gemma4-26b-a4b-it-fp8");
    expect(formatModelDisplay("public-model")).toBe("public-model");
  });

  test("呼叫狀態只把明確成功值視為成功", () => {
    expect(isOkStatus("success")).toBe(true);
    expect(isOkStatus(200)).toBe(true);
    expect(isOkStatus("upstream_http_503")).toBe(false);
  });
});

describe("AiMonitoringPage attention rules", () => {
  test("does not create an alert when services and usage are healthy", () => {
    const items = buildAttentionItems({
      overview: { summary: { error_rate: 1.2 }, comparison: { error_rate_delta: -0.4 } },
      runtime: {
        gateway: { status: "available", readiness: true },
        summary: { offline: 0, degraded: 0 },
      },
      overviewError: null,
      runtimeError: null,
    }, t);

    expect(items).toEqual([]);
  });

  test("centralizes gateway, model, and error-rate problems", () => {
    const items = buildAttentionItems({
      overview: {
        summary: { error_rate: 12.3, failed_calls: 9 },
        comparison: { error_rate_delta: 2.1 },
      },
      runtime: {
        gateway: { status: "degraded", readiness: false },
        summary: { offline: 1, degraded: 2 },
      },
      overviewError: null,
      runtimeError: null,
    }, t);

    expect(items.map((item) => item.key)).toEqual(["gateway", "models", "errors"]);
    expect(items.map((item) => item.target)).toEqual(["runtime", "models", "api-errors"]);
    expect(items.every((item) => item.tone === "critical")).toBe(true);
  });

  test("reports failed overview and runtime requests", () => {
    const items = buildAttentionItems({
      overview: null,
      runtime: null,
      overviewError: new Error("overview"),
      runtimeError: new Error("runtime"),
    }, t);

    expect(items.map((item) => item.key)).toEqual(["usage", "gateway"]);
  });
});

describe("AiMonitoringPage model details", () => {
  test("merges usage and runtime models using their public display name", () => {
    const rows = mergeModelRows(
      [{
        model_name: "/models/gemma4-26b",
        total_calls: 12,
        total_tokens: 3400,
        failed_calls: 1,
        error_rate: 8.3,
        avg_latency_ms: 900,
      }],
      [{
        name: "gemma4-26b",
        status: "degraded",
        healthy_deployments: 2,
        unhealthy_deployments: 1,
      }],
    );

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      model_name: "/models/gemma4-26b",
      total_calls: 12,
      runtime_status: "degraded",
      healthy_deployments: 2,
      unhealthy_deployments: 1,
    });
  });

  test("keeps runtime-only models visible", () => {
    const rows = mergeModelRows([], [{ name: "unused-model", status: "online" }]);

    expect(rows).toEqual([expect.objectContaining({
      model_name: "unused-model",
      total_calls: 0,
      runtime_status: "online",
    })]);
  });
});
