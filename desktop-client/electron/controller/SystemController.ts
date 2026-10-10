import BeanFactory from "../core/BeanFactory";
import { isIP } from "net";
import { BusinessError, ResponseCode } from "../core/BusinessError";
import Logger from "../core/Logger";
import SettingsService from "../service/SettingsService";
import SystemService from "../service/SystemService";
import PathUtils from "../utils/PathUtils";
import ResponseUtils from "../utils/ResponseUtils";
import BaseController from "./BaseController";

/** 開網址、資料夾、終端機或遠端桌面失敗：畫面統一顯示「無法開啟」，細節留在記錄檔 */
const openFailed = (err: Error) =>
  ResponseUtils.fail(new BusinessError(ResponseCode.OPEN_FAILED, err.message));

class SystemController extends BaseController {
  private readonly _systemService: SystemService;

  constructor() {
    super();
    this._systemService = BeanFactory.getBean("systemService");
  }

  openUrl(req: ControllerParam) {
    const settingsService: SettingsService =
      BeanFactory.getBean("settingsService");
    settingsService
      .getBackendUrl()
      .then(backendUrl =>
        this._systemService.openUrl(req.args?.url, backendUrl)
      )
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openUrl", err);
        req.event.reply(req.channel, openFailed(err));
      });
  }

  relaunchApp(req: ControllerParam) {
    this._systemService
      .relaunch()
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SystemController.relaunchApp", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }

  openAppData(req: ControllerParam) {
    this._systemService
      .openLocalPath(PathUtils.getAppData())
      .then(opened => {
        req.event.reply(
          req.channel,
          opened
            ? ResponseUtils.success()
            : openFailed(new Error("data folder could not be opened"))
        );
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openAppData", err);
        req.event.reply(req.channel, openFailed(err));
      });
  }

  openThirdPartyNotices(req: ControllerParam) {
    this._systemService
      .openLocalFile(PathUtils.getThirdPartyNoticesPath())
      .then(opened => {
        req.event.reply(
          req.channel,
          opened
            ? ResponseUtils.success()
            : openFailed(new Error("notices file not found"))
        );
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openThirdPartyNotices", err);
        req.event.reply(req.channel, openFailed(err));
      });
  }

  openSsh(req: ControllerParam) {
    const port = Number(req.args?.port);
    const host = String(req.args?.host || "");
    if (
      !Number.isInteger(port) ||
      port <= 0 ||
      port > 65535 ||
      isIP(host) !== 4
    ) {
      req.event.reply(req.channel, openFailed(new Error("invalid target")));
      return;
    }
    this._systemService
      .openSsh(port, "root", host)
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openSsh", err);
        req.event.reply(req.channel, openFailed(err));
      });
  }

  openRdp(req: ControllerParam) {
    const port = Number(req.args?.port);
    const host = String(req.args?.host || "");
    if (
      !Number.isInteger(port) ||
      port <= 0 ||
      port > 65535 ||
      isIP(host) !== 4
    ) {
      req.event.reply(req.channel, openFailed(new Error("invalid target")));
      return;
    }
    this._systemService
      .openRdp(port, host)
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openRdp", err);
        req.event.reply(req.channel, openFailed(err));
      });
  }
}

export default SystemController;
