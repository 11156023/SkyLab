<script lang="ts" setup>
import router from "@/router";
import { useAppStore } from "@/store/app";
import { on, send } from "@/utils/ipcUtils";
import { theme } from "@/utils/appearance";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  computed,
  defineComponent,
  onMounted,
  onUnmounted,
  reactive,
  watch
} from "vue";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";

defineComponent({ name: "Config" });

const { t } = useI18n();
const appStore = useAppStore();
const updatePercent = computed(() => {
  const progress = appStore.updateProgress;
  return progress?.total
    ? Math.round((progress.received / progress.total) * 100)
    : 0;
});
const handleInstallUpdate = async () => {
  if (!appStore.updateInfo?.updateAvailable) return;
  try {
    await ElMessageBox.confirm(t("update.confirmMessage"), t("update.title"), {
      confirmButtonText: t("update.install"),
      cancelButtonText: t("update.later"),
      type: "warning"
    });
    appStore.installUpdate();
  } catch {
    // User cancelled.
  }
};
const disposers: Array<() => void> = [];

const form = reactive({
  language: "zh-TW",
  launchAtStartup: false,
  backendUrl: ""
});

const syncFromStore = (settings: Partial<SkyLabSettings> | null) => {
  if (!settings) return;
  form.language = settings.language || "zh-TW";
  form.launchAtStartup = !!settings.launchAtStartup;
  form.backendUrl = settings.backendUrl || "";
};

const handleSave = () => {
  send(ipcRouters.SETTINGS.saveSettings, {
    language: form.language,
    launchAtStartup: form.launchAtStartup,
    backendUrl: form.backendUrl
  });
};

const handleLogout = () => {
  appStore.logout();
};

const goBack = () => {
  router.replace({ name: "Home" });
};

watch(
  () => appStore.language,
  lang => {
    if (lang) form.language = lang;
  }
);

watch(
  () => appStore.autoStart,
  v => {
    form.launchAtStartup = v;
  }
);

onMounted(() => {
  appStore.checkForUpdates();
  disposers.push(
    on(ipcRouters.SETTINGS.getSettings, (data: SkyLabSettings) => {
      syncFromStore(data);
    })
  );
  disposers.push(
    on(ipcRouters.SETTINGS.saveSettings, (data: SkyLabSettings) => {
      syncFromStore(data);
      ElMessage.success(t("config.saveSuccess"));
    })
  );
  send(ipcRouters.SETTINGS.getSettings);
});

onUnmounted(() => {
  disposers.forEach(dispose => dispose());
});
</script>

<template>
  <div class="main">
    <div class="app-container-breadcrumb settings-container">
      <div class="page-surface">
        <div class="page-header">
          <div>
            <div class="page-title">{{ t("config.title") }}</div>
          </div>
          <el-button text @click="goBack">
            {{ t("config.back") }}
          </el-button>
        </div>

        <section class="update-panel section-panel" aria-live="polite">
          <div class="update-panel__heading">
            <div>
              <h2>
                {{ t("update.settingsTitle") }}
                <i
                  v-if="appStore.updateInfo?.updateAvailable"
                  class="update-panel__dot"
                />
              </h2>
              <p>
                {{ t("update.currentVersion") }}:
                {{ appStore.updateInfo?.currentVersion || "—" }}
              </p>
            </div>
            <el-button
              :loading="appStore.updateChecking"
              :disabled="appStore.updateInstalling"
              @click="appStore.checkForUpdates(true)"
              >{{ t("update.check") }}</el-button
            >
          </div>
          <p v-if="appStore.updateCheckError" class="update-panel__error">
            {{ t("update.checkError") }}
          </p>
          <template v-if="appStore.updateInfo">
            <p
              v-if="appStore.updateInfo.updateAvailable"
              class="update-panel__available"
            >
              {{
                t("update.availableVersion", {
                  version: appStore.updateInfo.latestVersion
                })
              }}
            </p>
            <p v-else class="update-panel__status">
              {{ t("update.upToDate") }}
            </p>
            <el-button
              v-if="appStore.updateInfo.updateAvailable"
              type="primary"
              :disabled="appStore.updateInstalling"
              @click="handleInstallUpdate"
              >{{ t("update.install") }}</el-button
            >
          </template>
          <template v-if="appStore.updateInstalling && appStore.updateProgress">
            <p class="update-panel__status">
              {{ t(`update.${appStore.updateProgress.stage}`)
              }}<span v-if="appStore.updateProgress.stage === 'downloading'">
                {{ updatePercent }}%</span
              >
            </p>
            <el-progress
              v-if="appStore.updateProgress.stage === 'downloading'"
              :percentage="updatePercent"
              :stroke-width="8"
            />
          </template>
          <p v-if="appStore.updateInstallError" class="update-panel__error">
            {{ t("update.installError") }}: {{ appStore.updateInstallError }}
          </p>
        </section>

        <el-form
          class="settings-form section-panel"
          label-width="140px"
          label-position="left"
        >
          <el-form-item :label="t('workspace.appearance')">
            <el-radio-group v-model="theme"
              ><el-radio value="dark">{{ t("workspace.dark") }}</el-radio
              ><el-radio value="light">{{
                t("workspace.light")
              }}</el-radio></el-radio-group
            >
          </el-form-item>
          <el-form-item :label="t('config.language.label')">
            <el-radio-group v-model="form.language">
              <el-radio value="zh-TW">{{ t("config.language.zhTW") }}</el-radio>
              <el-radio value="en-US">{{ t("config.language.enUS") }}</el-radio>
              <el-radio value="ja">{{ t("config.language.ja") }}</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item :label="t('config.autoStart.label')">
            <el-switch v-model="form.launchAtStartup" />
            <div class="form-hint">
              {{ t("config.autoStart.tips") }}
            </div>
          </el-form-item>

          <el-form-item :label="t('config.backend.label')">
            <el-input
              v-model="form.backendUrl"
              placeholder="https://skylab.ntubimdbirc.tw"
            />
            <div class="form-hint form-hint--block">
              {{ t("config.backend.tips") }}
            </div>
          </el-form-item>

          <el-form-item :label="t('config.account.label')">
            <template v-if="appStore.loggedIn">
              <el-tag type="success" size="small" class="mr-2">
                {{ t("config.account.loggedIn") }}
              </el-tag>
              <el-button size="small" type="danger" plain @click="handleLogout">
                {{ t("config.account.logout") }}
              </el-button>
            </template>
            <el-tag v-else type="info" size="small">
              {{ t("config.account.notLoggedIn") }}
            </el-tag>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" @click="handleSave">
              {{ t("common.save") }}
            </el-button>
          </el-form-item>
        </el-form>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.settings-form {
  padding: 20px 20px 6px;
}

.update-panel {
  padding: 20px;
  margin-bottom: 16px;
}
.update-panel__heading {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 16px;
}
.update-panel h2 {
  margin: 0;
  font-size: 17px;
}
.update-panel p {
  margin: 8px 0 12px;
}
.update-panel__heading p,
.update-panel__status {
  color: var(--color-text-muted);
}
.update-panel__available {
  color: var(--color-primary);
}
.update-panel__error {
  color: var(--color-danger);
}
.update-panel__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-left: 4px;
  vertical-align: middle;
  border-radius: 50%;
  background: var(--color-danger);
}

.settings-container {
  height: 100%;
}

.form-hint {
  margin-left: 12px;
  color: var(--color-text-muted);
  font-size: 12px;
}

.form-hint--block {
  width: 100%;
  margin-top: 6px;
  margin-left: 0;
}
</style>
