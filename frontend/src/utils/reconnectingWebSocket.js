/** Shared reconnect lifecycle for jobs, classroom signalling and course progress. */
export const RECONNECT_BASE_MS = 5_000;
export const RECONNECT_MAX_MS = 60_000;

export function connectReconnectingWebSocket(resolveUrl, { onOpen, onMessage, onClose, retryPolicyClose = false } = {}) {
  let socket = null;
  let stopped = false;
  let timer = null;
  let retryDelay = RECONNECT_BASE_MS;

  const schedule = () => {
    if (stopped || timer !== null) return;
    const delay = retryDelay * (0.5 + Math.random() * 0.5);
    retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS);
    timer = setTimeout(() => {
      timer = null;
      open();
    }, delay);
  };

  const open = () => {
    if (stopped) return;
    const url = resolveUrl();
    if (!url) { schedule(); return; }
    let current;
    try { current = new WebSocket(url); }
    catch { schedule(); return; }
    socket = current;
    current.onopen = () => {
      if (stopped || socket !== current) return;
      retryDelay = RECONNECT_BASE_MS;
      onOpen?.();
    };
    current.onmessage = (event) => {
      if (!stopped && socket === current) onMessage?.(event);
    };
    current.onclose = (event) => {
      if (stopped || socket !== current) return;
      socket = null;
      onClose?.(event);
      if (event.code === 1008 && !retryPolicyClose) { stopped = true; return; }
      schedule();
    };
    current.onerror = () => {
      if (!stopped && socket === current) current.close();
    };
  };

  open();
  return () => {
    stopped = true;
    clearTimeout(timer);
    timer = null;
    const current = socket;
    socket = null;
    if (current) {
      try { current.close(); } catch { /* Already closed. */ }
    }
  };
}
