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

  /**
   * Aggregates session-status for every running VM the user owns. The
   * renderer polls this so a single IPC round-trip surfaces all warnings.
   * Per-VM failures don't fail the whole call — they're logged and skipped.
   */
  async getSessionStatuses(req: ControllerParam) {
    try {
      const resources = await this._SkyLabService.listResources();
      const runningVmids = resources
        .filter(r => r.status === "running" && typeof r.vmid === "number")
        .map(r => r.vmid);
      const results = await Promise.all(
        runningVmids.map(vmid =>
          this._SkyLabService.getSessionStatus(vmid).catch(err => {
            Logger.warn(
              "ResourceController.getSessionStatuses",
              `vmid=${vmid} failed: ${(err as Error).message}`
            );
            return null;
          })
        )
      );
      const statuses = results.filter(
        (s): s is SkyLabSessionStatus => s !== null
      );
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
