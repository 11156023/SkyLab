import { afterEach, describe, expect, test, vi } from "vitest";
import { createRubricAnalysisAutosave } from "./rubricAnalysisAutosave";

afterEach(() => {
  vi.useRealTimers();
});

describe("createRubricAnalysisAutosave", () => {
  test("連續輸入只保存 debounce 後的最新內容", async () => {
    vi.useFakeTimers();
    const save = vi.fn().mockResolvedValue(undefined);
    const autosave = createRubricAnalysisAutosave({ save, delay: 650 });

    autosave.schedule({ title: "評" });
    autosave.schedule({ title: "評分" });
    autosave.schedule({ title: "檢查表" });

    expect(save).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(650);

    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ title: "檢查表" });
    expect(autosave.isPending()).toBe(false);
  });

  test("儲存進行中收到新輸入時會串行保存並使用最新內容", async () => {
    let finishFirst;
    const firstSave = new Promise((resolve) => {
      finishFirst = resolve;
    });
    const save = vi.fn()
      .mockReturnValueOnce(firstSave)
      .mockResolvedValueOnce(undefined);
    const autosave = createRubricAnalysisAutosave({ save, delay: 0 });

    autosave.schedule({ title: "第一版" });
    const flushing = autosave.flush();
    await Promise.resolve();
    autosave.schedule({ title: "輸入中的最新版" });

    expect(save).toHaveBeenCalledTimes(1);
    finishFirst();
    await flushing;
    await autosave.flush();

    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0]).toEqual({ title: "輸入中的最新版" });
    expect(autosave.isPending()).toBe(false);
  });

  test("pendingValue 反映排程中與保存中的內容，完成後清空", async () => {
    let finishFirst;
    const firstSave = new Promise((resolve) => {
      finishFirst = resolve;
    });
    const save = vi.fn()
      .mockReturnValueOnce(firstSave)
      .mockResolvedValueOnce(undefined);
    const autosave = createRubricAnalysisAutosave({ save, delay: 0 });

    expect(autosave.pendingValue()).toBeNull();
    autosave.schedule({ title: "第一版" });
    const flushing = autosave.flush();
    await Promise.resolve();
    expect(autosave.pendingValue()).toEqual({ title: "第一版" });

    autosave.schedule({ title: "輸入中的最新版" });
    expect(autosave.pendingValue()).toEqual({ title: "輸入中的最新版" });

    finishFirst();
    await flushing;

    expect(autosave.pendingValue()).toBeNull();
    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0]).toEqual({ title: "輸入中的最新版" });
  });
});
