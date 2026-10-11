import { describe, expect, test, vi } from "vitest";
import { runBatch } from "./runBatch";

describe("runBatch", () => {
  test("一筆失敗不影響其他筆，結果依原本順序", async () => {
    const res = await runBatch([1, 2, 3], async (n) => {
      if (n === 2) throw new Error("boom");
      return n * 10;
    });
    expect(res).toMatchObject({ total: 3, succeeded: 2, failed: 1 });
    expect(res.results.map((r) => r.ok)).toEqual([true, false, true]);
    expect(res.results[0].value).toBe(10);
    expect(res.results[1].error.message).toBe("boom");
  });

  test("同時最多 concurrency 筆，每做完一筆回報進度", async () => {
    let running = 0;
    let peak = 0;
    const onProgress = vi.fn();
    await runBatch([1, 2, 3, 4, 5], async () => {
      running += 1;
      peak = Math.max(peak, running);
      await new Promise((resolve) => setTimeout(resolve, 5));
      running -= 1;
    }, { concurrency: 2, onProgress });
    expect(peak).toBe(2);
    expect(onProgress).toHaveBeenCalledTimes(5);
    expect(onProgress).toHaveBeenLastCalledWith({ done: 5, total: 5 });
  });

  test("沒有項目時直接回傳", async () => {
    await expect(runBatch([], async () => {})).resolves.toMatchObject({ total: 0, succeeded: 0, failed: 0 });
  });
});
