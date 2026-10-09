import { ElMessage } from "element-plus";

/* 渲染程序與主程序之間的 IPC 包裝：
   send 送請求、on 收該路由的回覆（成功走 listerHandler，失敗走 errHandler，沒給就直接 toast） */

const ipcRenderer = window.electronIpcRenderer;

export const send = (router: IpcRouter, params?: any) => {
  ipcRenderer.send(router.path, params);
};

/** 回傳取消監聽的函式，元件卸載時呼叫 */
export const on = (
  router: IpcRouter,
  listerHandler: (data: any) => void,
  errHandler?: (bizCode: string, message: string) => void
) => {
  const handler = (_event: unknown, args: ApiResponse<any>) => {
    const { bizCode, data, message } = args;
    if (bizCode === "A1000") {
      listerHandler(data);
      return;
    }
    /* B1001：登入狀態已失效，交給 store 統一清掉登入與連線 */
    if (bizCode === "B1001") {
      window.dispatchEvent(new CustomEvent("skylab:auth-expired"));
    }
    if (errHandler) errHandler(bizCode, message);
    else ElMessage({ message, type: "error" });
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
