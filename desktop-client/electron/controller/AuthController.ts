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

  private _emitAuthEvent(payload: {
    type: "login-success" | "login-failure" | "login-cancelled";
    error?: string;
    code?: string;
  }) {
    const win: BrowserWindow = BeanFactory.getBean("win");
    if (win && !win.isDestroyed()) {
      win.webContents.send(AUTH_EVENT_CHANNEL, ResponseUtils.success(payload));
    }
  }

  startLogin(req: ControllerParam) {
    this._authService
      .startLogin((success, error, code) => {
        if (success) {
          this._emitAuthEvent({ type: "login-success" });
        } else if (code === "B1003") {
          this._emitAuthEvent({ type: "login-cancelled" });
        } else {
          this._emitAuthEvent({ type: "login-failure", error, code });
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

  cancelLogin(req: ControllerParam) {
    this._authService.cancelLogin();
    req.event.reply(req.channel, ResponseUtils.success());
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
