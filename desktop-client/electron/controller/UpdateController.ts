import { app } from "electron";
import { BusinessError, ResponseCode } from "../core/BusinessError";
import { listeners } from "../core/IpcRouter";
import Logger from "../core/Logger";
import UpdateService from "../service/UpdateService";
import ResponseUtils from "../utils/ResponseUtils";
import BaseController from "./BaseController";

class UpdateController extends BaseController {
  private readonly _updateService: UpdateService;

  constructor(updateService: UpdateService) {
    super();
    this._updateService = updateService;
  }

  check(req: ControllerParam) {
    this._updateService
      .check()
      .then(info => req.event.reply(req.channel, ResponseUtils.success(info)))
      .catch((error: Error) => {
        Logger.warn("UpdateController.check", error.message);
        req.event.reply(req.channel, ResponseUtils.success(null));
      });
  }

  install(req: ControllerParam) {
    this._updateService
      .install(progress => {
        if (!req.event.sender.isDestroyed()) {
          req.event.sender.send(
            listeners.updateProgress.channel,
            ResponseUtils.success(progress)
          );
        }
      })
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success(true));
        setTimeout(() => app.quit(), 1000);
      })
      .catch((error: Error) => {
        Logger.warn("UpdateController.install", error.message);
        req.event.reply(
          req.channel,
          ResponseUtils.fail(
            new BusinessError(ResponseCode.UPDATE_INSTALL_FAILED, error.message)
          )
        );
      });
  }
}

export default UpdateController;
