/** Serialize writes and retain failed/latest edits for an explicit retry. */
export function createEnvironmentAutosave({ id = null, save, onState, onSaved, delay = 700 }) {
  let environmentId = id;
  let pending = null;
  let timer = null;
  let running = null;
  let disposed = false;

  function schedule(value) {
    if (disposed) return;
    pending = value;
    onState?.("pending");
    clearTimeout(timer);
    timer = setTimeout(() => { void flush(); }, delay);
  }

  async function drain() {
    while (pending && !disposed) {
      const value = pending;
      pending = null;
      onState?.("saving");
      try {
        const saved = await save(environmentId, value);
        environmentId = saved.id;
        onSaved?.(saved, pending ?? value);
      } catch (error) {
        pending ??= value;
        clearTimeout(timer);
        onState?.("error", error);
        return false;
      }
    }
    if (!disposed) onState?.("saved");
    return true;
  }

  function flush() {
    clearTimeout(timer);
    if (running) return running;
    running = drain().finally(() => { running = null; });
    return running;
  }

  return {
    schedule, flush,
    getId: () => environmentId,
    dispose() { disposed = true; clearTimeout(timer); },
  };
}
