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
}

export default UpdateController;
