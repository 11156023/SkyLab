import Logger from "../core/Logger";
import SkyLabService from "../service/SkyLabService";
import ResponseUtils from "../utils/ResponseUtils";
import BaseController from "./BaseController";

class ResourceController extends BaseController {
  private readonly _SkyLabService: SkyLabService;

  constructor(SkyLabService: SkyLabService) {
    super();
    this._SkyLabService = SkyLabService;
  }

  listMyResources(req: ControllerParam) {
    this._SkyLabService
      .listResources()
      .then(data => {
        req.event.reply(req.channel, ResponseUtils.success(data));
      })
      .catch((err: Error) => {
        Logger.error("ResourceController.listMyResources", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }

  listMyQuickPracticeSessions(req: ControllerParam) {
    this._SkyLabService
      .listQuickPracticeSessions()
      .then(data => {
        req.event.reply(req.channel, ResponseUtils.success(data));
      })
      .catch((err: Error) => {
        Logger.error("ResourceController.listMyQuickPracticeSessions", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }

  async getSessionStatuses(req: ControllerParam) {
    try {
      const statuses = await this._SkyLabService.listSessionStatuses();
      req.event.reply(req.channel, ResponseUtils.success(statuses));
    } catch (err) {
      Logger.error("ResourceController.getSessionStatuses", err as Error);
      req.event.reply(req.channel, ResponseUtils.fail(err as Error));
    }
  }

  extendSession(req: ControllerParam) {
    const vmid = req.args?.vmid as number | undefined;
    if (typeof vmid !== "number") {
      req.event.reply(
        req.channel,
        ResponseUtils.fail(new Error("vmid is required"))
      );
      return;
    }
    this._SkyLabService
      .extendSession(vmid)
      .then(data => {
        req.event.reply(req.channel, ResponseUtils.success(data));
      })
      .catch((err: Error) => {
        Logger.error("ResourceController.extendSession", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }
}

export default ResourceController;
