import { ElMessage } from "element-plus";

const ipcRenderer = window.electronIpcRenderer;

export const send = (router: IpcRouter, params?: any) => {
  ipcRenderer.send(router.path, params);
};

// export const invoke = (router: IpcRouter, params?: any) => {
//   return new Promise((resolve, reject) => {
//     ipcRenderer
//       .invoke(router.path, params)
//       .then((args: ApiResponse<any>) => {
//         const { success, data, message } = args;
//         if (success) {
//           resolve(data);
//         } else {
//           // reject(new Error(message));
//         }
//       })
//       .catch(err => reject(err));
//   });
// };

export const on = (
  router: IpcRouter,
  listerHandler: (data: any) => void,
  errHandler?: (bizCode: string, message: string) => void
) => {
  const handler = (event: unknown, args: ApiResponse<any>) => {
    const { bizCode, data, message } = args;
    if (bizCode === "A1000") {
      listerHandler(data);
    } else {
      if (bizCode === "B1001") {
        window.dispatchEvent(new CustomEvent("skylab:auth-expired"));
      }
      if (errHandler) {
        errHandler(bizCode, message);
      } else {
        // ElMessageBox.alert(message,"出错了");
        ElMessage({
          message: message,
          type: "error"
        });
      }
      // reject(new Error(message));
    }
  };
  ipcRenderer.on(`${router.path}:hook`, handler);
  return () => ipcRenderer.removeListener(`${router.path}:hook`, handler);
};

export const onListener = (
  listener: Listener,
  listerHandler: (data: any) => void
) => {
  // return new Promise((resolve, reject) => {
  ipcRenderer.on(`${listener.channel}`, (event, args: ApiResponse<any>) => {
    const { bizCode, data } = args;
    if (bizCode === "A1000") {
      listerHandler(data);
    }
  });
  // });
};

export const removeRouterListeners = (router: IpcRouter) => {
  ipcRenderer.removeAllListeners(`${router.path}:hook`);
};

export const removeRouterListeners2 = (listen: Listener) => {
  ipcRenderer.removeAllListeners(`${listen.channel}`);
};
// export const removeAllListeners = (listen: Listener) => {
//   ipcRenderer.removeAllListeners(`${listen.channel}:hook`);
// };
