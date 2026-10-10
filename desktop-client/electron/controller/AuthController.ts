import { BrowserWindow } from "electron";
import BeanFactory from "../core/BeanFactory";
import Logger from "../core/Logger";
import AuthService from "../service/AuthService";
import ResponseUtils from "../utils/ResponseUtils";
import BaseController from "./BaseController";

const AUTH_EVENT_CHANNEL = "auth:event";

class AuthController extends BaseController {
  private readonly _authService: AuthService;

  constructor(authService: AuthService) {
    super();
    this._authService = authService;
  }

  /** 登入結果另走事件推給畫面；失敗只帶錯誤碼，畫面自己翻譯（英文訊息留在記錄檔） */
  private _emitAuthEvent(payload: {
    type: "login-success" | "login-failure";
    errorCode?: string;
  }) {
    const win: BrowserWindow = BeanFactory.getBean("win");
    if (win && !win.isDestroyed()) {
      win.webContents.send(AUTH_EVENT_CHANNEL, ResponseUtils.success(payload));
    }
  }

  startLogin(req: ControllerParam) {
    this._authService
      .startLogin((success, error) => {
        if (success) {
          this._emitAuthEvent({ type: "login-success" });
        } else {
          Logger.warn("AuthController.startLogin.result", error?.message ?? "");
          this._emitAuthEvent({
            type: "login-failure",
            errorCode: error?.bizCode
          });
        }
      })
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("AuthController.startLogin", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }

  logout(req: ControllerParam) {
    this._authService
      .logout()
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("AuthController.logout", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }

  getAuthState(req: ControllerParam) {
    this._authService
      .isLoggedIn()
      .then(loggedIn => {
        req.event.reply(
          req.channel,
          ResponseUtils.success({
            loggedIn,
            loginInProgress: this._authService.isLoginInProgress()
          })
        );
      })
      .catch((err: Error) => {
        Logger.error("AuthController.getAuthState", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }
}

export default AuthController;
