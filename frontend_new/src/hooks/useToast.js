import { toast } from "sonner";

/* 穩定的單例 — 避免在 hook return 新物件造成 useCallback/useEffect 依賴循環。 */
const TOAST_API = Object.freeze({
  success: (message) => toast.success(message),
  error:   (message) => toast.error(message),
  info:    (message) => toast.info(message),
});

export function useToast() {
  return TOAST_API;
}
