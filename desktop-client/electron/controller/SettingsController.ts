import Logger from "../core/Logger";
import SettingsService from "../service/SettingsService";
import ResponseUtils from "../utils/ResponseUtils";
import BaseController from "./BaseController";

class SettingsController extends BaseController {
  private readonly _settingsService: SettingsService;

  constructor(settingsService: SettingsService) {
    super();
    this._settingsService = settingsService;
  }

  getSettings(req: ControllerParam) {
    this._settingsService
      .get()
      .then(data => {
        req.event.reply(
          req.channel,
          ResponseUtils.success({
            language: data.language,
            backendUrl: data.backendUrl,
            launchAtStartup: data.launchAtStartup
          })
        );
      })
      .catch((err: Error) => {
        Logger.error("SettingsController.getSettings", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }

  saveSettings(req: ControllerParam) {
    const patch = {
      language: req.args?.language,
      backendUrl: req.args?.backendUrl,
      launchAtStartup: req.args?.launchAtStartup
    };
    this._settingsService
      .save(patch)
      .then(data => {
        req.event.reply(
          req.channel,
          ResponseUtils.success({
            language: data.language,
            backendUrl: data.backendUrl,
            launchAtStartup: data.launchAtStartup
          })
        );
      })
      .catch((err: Error) => {
        Logger.error("SettingsController.saveSettings", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }

  getLanguage(req: ControllerParam) {
    this._settingsService
      .getLanguage()
      .then(language => {
        req.event.reply(req.channel, ResponseUtils.success(language));
      })
      .catch((err: Error) => {
        Logger.error("SettingsController.getLanguage", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }

  saveLanguage(req: ControllerParam) {
    this._settingsService
      .saveLanguage(req.args?.language)
      .then(() => {
        req.event.reply(req.channel, ResponseUtils.success());
      })
      .catch((err: Error) => {
        Logger.error("SettingsController.saveLanguage", err);
        req.event.reply(req.channel, ResponseUtils.fail(err));
      });
  }
}

export default SettingsController;
