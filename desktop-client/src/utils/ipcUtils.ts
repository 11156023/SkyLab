import { NOT_LOGGED_IN, toastError } from "@/utils/errors";

/* 渲染程序與主程序之間的 IPC 包裝：
   send 送請求、on 收該路由的回覆（成功走 listerHandler，失敗走 errHandler，沒給就跳在地化的錯誤 toast） */

const ipcRenderer = window.electronIpcRenderer;

export const send = (router: IpcRouter, params?: any) => {
  ipcRenderer.send(router.path, params);
};

/** 回傳取消監聽的函式，元件卸載時呼叫。errHandler 拿到錯誤碼，用 describeError 換成給使用者看的文字 */
export const on = (
  router: IpcRouter,
  listerHandler: (data: any) => void,
  errHandler?: (bizCode: string) => void
) => {
  const handler = (_event: unknown, args: ApiResponse<any>) => {
    const { bizCode, data, message } = args;
    if (bizCode === "A1000") {
      listerHandler(data);
      return;
    }
    /* 主程序的原始訊息（英文、可能含技術細節）只留給除錯，畫面一律依錯誤碼顯示 */
    console.warn(`[ipc] ${router.path} ${bizCode}: ${message}`);
    /* 登入狀態已失效：交給 store 統一清掉登入與連線，並只提示一次 */
    if (bizCode === NOT_LOGGED_IN) {
      window.dispatchEvent(new CustomEvent("skylab:auth-expired"));
    }
    if (errHandler) errHandler(bizCode);
    else toastError(bizCode);
  };
  ipcRenderer.on(`${router.path}:hook`, handler);
  return () => ipcRenderer.removeListener(`${router.path}:hook`, handler);
};

/** 主程序主動推送的事件（通道狀態、更新進度） */
export const onListener = (
  listener: Listener,
  listerHandler: (data: any) => void
) => {
  ipcRenderer.on(listener.channel, (_event, args: ApiResponse<any>) => {
    if (args.bizCode === "A1000") listerHandler(args.data);
  });
};

export const removeRouterListeners = (router: IpcRouter) => {
  ipcRenderer.removeAllListeners(`${router.path}:hook`);
};
