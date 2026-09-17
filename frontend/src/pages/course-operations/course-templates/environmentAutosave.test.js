import { afterEach, expect, it, vi } from "vitest";
import { createEnvironmentAutosave } from "./environmentAutosave";

afterEach(() => vi.useRealTimers());

it("debounces typing and saves only the latest unfinished draft", async () => {
  vi.useFakeTimers();
  const save = vi.fn().mockResolvedValue({ id: "env-1" });
  const queue = createEnvironmentAutosave({ save });
  queue.schedule({ name: "L", nodes: [] });
  queue.schedule({ name: "Linux", nodes: [] });
  await vi.advanceTimersByTimeAsync(700);
  expect(save).toHaveBeenCalledExactlyOnceWith(null, { name: "Linux", nodes: [] });
  expect(queue.getId()).toBe("env-1");
  queue.dispose();
});

it("waits for creation and newer edits before publication can proceed", async () => {
  let finishCreate;
  const save = vi.fn()
    .mockImplementationOnce(() => new Promise((resolve) => { finishCreate = resolve; }))
    .mockResolvedValue({ id: "env-1" });
  const queue = createEnvironmentAutosave({ save });
  queue.schedule({ name: "first" });
  const flush = queue.flush();
  queue.schedule({ name: "latest" });
  expect(queue.flush()).toBe(flush);
  finishCreate({ id: "env-1" });
  expect(await flush).toBe(true);
  expect(save.mock.calls).toEqual([[null, { name: "first" }], ["env-1", { name: "latest" }]]);
  queue.dispose();
});

it("retains edits on failure and retries the existing ID", async () => {
  let fail;
  const save = vi.fn()
    .mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; }))
    .mockResolvedValue({ id: "env-1" });
  const queue = createEnvironmentAutosave({ id: "env-1", save });
  queue.schedule({ name: "first" });
  const flush = queue.flush();
  queue.schedule({ name: "latest" });
  fail(new Error("offline"));
  expect(await flush).toBe(false);
  expect(await queue.flush()).toBe(true);
  expect(save).toHaveBeenLastCalledWith("env-1", { name: "latest" });
  queue.dispose();
});

it("does not create an empty environment just by opening and leaving", async () => {
  const save = vi.fn();
  const queue = createEnvironmentAutosave({ save });
  await queue.flush();
  expect(save).not.toHaveBeenCalled();
  queue.dispose();
});
