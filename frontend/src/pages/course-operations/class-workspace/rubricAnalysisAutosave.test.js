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

describe("儲存失敗（409 以外）", () => {
  const networkError = () => Object.assign(new Error("網路中斷"), { status: 503 });

  test("保留沒存成的內容、標成未儲存，並以退避自動重試", async () => {
    vi.useFakeTimers();
    const save = vi.fn()
      .mockRejectedValueOnce(networkError())
      .mockRejectedValueOnce(networkError())
      .mockResolvedValueOnce(undefined);
    const onError = vi.fn();
    const onStatusChange = vi.fn();
    const autosave = createRubricAnalysisAutosave({
      save, onError, onStatusChange, delay: 10, retryDelays: [100, 300],
    });

    autosave.schedule({ title: "改到一半" });
    await vi.advanceTimersByTimeAsync(10);
    expect(save).toHaveBeenCalledTimes(1);
    expect(autosave.hasUnsaved()).toBe(true);
    expect(autosave.isPending()).toBe(true);
    expect(autosave.pendingValue()).toEqual({ title: "改到一半" });
    expect(autosave.getStatus()).toMatchObject({ unsaved: true, autoRetryScheduled: true });
    expect(onStatusChange).toHaveBeenLastCalledWith(expect.objectContaining({ unsaved: true }));
    expect(onError).toHaveBeenLastCalledWith(expect.any(Error), { conflict: false, announce: true });

    await vi.advanceTimersByTimeAsync(99);
    expect(save).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0]).toEqual({ title: "改到一半" });
    // 自動重試的失敗不再逐次通知
    expect(onError).toHaveBeenLastCalledWith(expect.any(Error), { conflict: false, announce: false });

    await vi.advanceTimersByTimeAsync(300);
    expect(save).toHaveBeenCalledTimes(3);
    expect(autosave.hasUnsaved()).toBe(false);
    expect(autosave.isPending()).toBe(false);
    expect(onStatusChange).toHaveBeenLastCalledWith(expect.objectContaining({ unsaved: false, saving: false }));
  });

  test("自動重試次數用完就停，等使用者手動重試", async () => {
    vi.useFakeTimers();
    const save = vi.fn().mockRejectedValue(networkError());
    const autosave = createRubricAnalysisAutosave({ save, delay: 10, retryDelays: [100, 300] });

    autosave.schedule({ title: "未儲存" });
    await vi.advanceTimersByTimeAsync(10 + 100 + 300);
    expect(save).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(save).toHaveBeenCalledTimes(3);
    expect(autosave.getStatus()).toMatchObject({ unsaved: true, autoRetryScheduled: false });
    expect(autosave.isPending()).toBe(true);

    save.mockResolvedValueOnce(undefined);
    await expect(autosave.retry()).resolves.toBe(true);
    expect(save).toHaveBeenCalledTimes(4);
    expect(save.mock.calls[3][0]).toEqual({ title: "未儲存" });
    expect(autosave.hasUnsaved()).toBe(false);
  });

  test("失敗期間的新輸入取代舊內容，之後存的是最新版", async () => {
    let failFirst;
    const save = vi.fn()
      .mockReturnValueOnce(new Promise((_, reject) => { failFirst = reject; }))
      .mockResolvedValueOnce(undefined);
    const autosave = createRubricAnalysisAutosave({ save, delay: 0, retryDelays: [] });

    autosave.schedule({ title: "第一版" });
    const flushing = autosave.flush();
    await Promise.resolve();
    autosave.schedule({ title: "最新版" });
    failFirst(networkError());
    await expect(flushing).resolves.toBe(false);
    expect(autosave.pendingValue()).toEqual({ title: "最新版" });

    await expect(autosave.flush()).resolves.toBe(true);
    expect(save.mock.calls[1][0]).toEqual({ title: "最新版" });
  });

  test("flush 失敗回傳 false 並保留內容；quiet 時不要求通知", async () => {
    const save = vi.fn().mockRejectedValue(networkError());
    const onError = vi.fn();
    const autosave = createRubricAnalysisAutosave({ save, onError, delay: 0, retryDelays: [] });

    autosave.schedule({ title: "未儲存" });
    await expect(autosave.flush({ quiet: true })).resolves.toBe(false);
    expect(onError).toHaveBeenLastCalledWith(expect.any(Error), { conflict: false, announce: false });
    expect(autosave.hasUnsaved()).toBe(true);

    await expect(autosave.flush()).resolves.toBe(false);
    expect(onError).toHaveBeenLastCalledWith(expect.any(Error), { conflict: false, announce: true });
    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0]).toEqual({ title: "未儲存" });
  });

  test("409 照舊丟掉排隊中的內容，不重試", async () => {
    vi.useFakeTimers();
    const save = vi.fn().mockRejectedValue(Object.assign(new Error("conflict"), { status: 409 }));
    const onError = vi.fn();
    const autosave = createRubricAnalysisAutosave({ save, onError, delay: 10, retryDelays: [100] });

    autosave.schedule({ title: "衝突" });
    await vi.advanceTimersByTimeAsync(10);
    expect(onError).toHaveBeenCalledWith(expect.any(Error), { conflict: true, announce: true });
    expect(autosave.hasUnsaved()).toBe(false);
    expect(autosave.isPending()).toBe(false);
    expect(autosave.pendingValue()).toBeNull();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(save).toHaveBeenCalledTimes(1);
  });

  test("dispose 後停止自動重試", async () => {
    vi.useFakeTimers();
    const save = vi.fn().mockRejectedValue(networkError());
    const autosave = createRubricAnalysisAutosave({ save, delay: 10, retryDelays: [100] });

    autosave.schedule({ title: "未儲存" });
    await vi.advanceTimersByTimeAsync(10);
    autosave.dispose();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(save).toHaveBeenCalledTimes(1);
  });
});
