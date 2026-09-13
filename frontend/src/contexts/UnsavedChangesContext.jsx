/**
 * UnsavedChangesProvider / useUnsavedChanges / useUnsavedChangesGuard
 * 表單頁註冊「尚未儲存」狀態，跳離時統一攔截：
 *   - 站內 <a>/<Link> 連結：capture 階段先攔，經共用確認框同意後才放行
 *   - 側欄等 navigate() 導覽：呼叫端先 await confirmLeave()
 *   - 重整／關閉分頁：beforeunload 交給瀏覽器原生對話框
 * 瀏覽器上一頁（popstate）在 declarative router 下攔不住，屬已知缺口。
 *
 * 頁面用法（dirty 為布林值）：
 *   useUnsavedChangesGuard(dirty);
 * 導覽端用法：
 *   const { confirmLeave } = useUnsavedChanges();
 *   if (!(await confirmLeave())) return;
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useConfirm } from "../components/ConfirmDialog/ConfirmProvider";

const UnsavedChangesContext = createContext(null);

export function useUnsavedChanges() {
  const ctx = useContext(UnsavedChangesContext);
  if (!ctx) throw new Error("useUnsavedChanges 必須在 <UnsavedChangesProvider> 內使用");
  return ctx;
}

/** 表單頁一行註冊：dirty 為 true 時啟動跳離攔截，頁面卸載自動解除 */
export function useUnsavedChangesGuard(dirty) {
  const { setDirty } = useUnsavedChanges();
  useEffect(() => {
    setDirty(dirty);
    return () => setDirty(false);
  }, [dirty, setDirty]);
}

export function UnsavedChangesProvider({ children }) {
  const { t } = useTranslation("common");
  const confirm = useConfirm();
  const navigate = useNavigate();
  const dirtyRef = useRef(false);

  const setDirty = useCallback((value) => {
    dirtyRef.current = Boolean(value);
  }, []);

  const confirmLeave = useCallback(async () => {
    if (!dirtyRef.current) return true;
    const ok = await confirm({
      title: t("UnsavedGuard.title"),
      message: t("UnsavedGuard.message"),
      confirmText: t("UnsavedGuard.leave"),
      danger: true,
    });
    if (ok) dirtyRef.current = false;
    return ok;
  }, [confirm, t]);

  // 重整／關閉分頁：瀏覽器原生攔截（自訂文字已被各瀏覽器忽略，給空值即可）
  useEffect(() => {
    const onBeforeUnload = (e) => {
      if (!dirtyRef.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  // 站內連結：capture 階段搶在 React Router 的 Link handler 之前攔下
  useEffect(() => {
    const onClickCapture = (e) => {
      if (!dirtyRef.current) return;
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const anchor = e.target?.closest?.("a[href]");
      if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return; // 外部連結交給 beforeunload
      e.preventDefault();
      e.stopPropagation();
      confirmLeave().then((ok) => {
        if (ok) navigate(url.pathname + url.search + url.hash);
      });
    };
    document.addEventListener("click", onClickCapture, true);
    return () => document.removeEventListener("click", onClickCapture, true);
  }, [confirmLeave, navigate]);

  const value = useMemo(() => ({ setDirty, confirmLeave }), [setDirty, confirmLeave]);
  return <UnsavedChangesContext.Provider value={value}>{children}</UnsavedChangesContext.Provider>;
}
