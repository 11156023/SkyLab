import { Toaster } from "sonner";
import { useTheme } from "../contexts/ThemeContext";

/**
 * 全站 sonner Toaster 的包裝：
 * - theme 跟著 ThemeContext 走（sonner 感知不到 body.dark，深色模式下會維持白底）
 * - 帶按鈕的 toast（如桌面通知詢問）的按鈕樣式在 global.scss 對齊全站
 *   按鈕 mixin（action = primary、cancel = ghost），這裡不用 inline style，
 *   否則會壓過 CSS 讓 mixin 失效
 */
export default function AppToaster() {
  const { theme } = useTheme();

  /* 不放 closeButton：一般 toast 4s 自動消失，叉叉是多餘的；
     唯一不自動消失的詢問型 toast（JobsProvider 桌面通知）以自己的
     「允許／稍後再說」按鈕關閉 */
  return (
    <Toaster
      position="top-right"
      richColors
      theme={theme}
      toastOptions={{ duration: 4000 }}
    />
  );
}
