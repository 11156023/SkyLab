import BeanFactory from "../core/BeanFactory";
import { isIP } from "net";
import Logger from "../core/Logger";
import SystemService from "../service/SystemService";
import PathUtils from "../utils/PathUtils";
import ResponseUtils from "../utils/ResponseUtils";
import BaseController from "./BaseController";

class SystemController extends BaseController {
  private readonly _systemService: SystemService;

  constructor() {
    super();
    this._systemService = BeanFactory.getBean("systemService");
  }

  openUrl(req: ControllerParam) {
    this._systemService
      .openUrl(req.args?.url)
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openUrl", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
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
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openAppData", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
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
            : ResponseUtils.fail(new Error("notices file not found"))
        );
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openThirdPartyNotices", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
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
      req.event.reply(
        req.channel,
        ResponseUtils.fail(new Error("invalid port"))
      );
      return;
    }
    this._systemService
      .openSsh(port, "root", host)
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openSsh", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
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
      req.event.reply(
        req.channel,
        ResponseUtils.fail(new Error("invalid port"))
      );
      return;
    }
    this._systemService
      .openRdp(port, host)
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SystemController.openRdp", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }
}

export default SystemController;
