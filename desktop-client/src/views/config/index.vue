<script lang="ts" setup>
import router from "@/router";
import { useAppStore } from "@/store/app";
import { on, send } from "@/utils/ipcUtils";
import { theme } from "@/utils/appearance";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, onUnmounted, reactive, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";

defineOptions({ name: "Config" });

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
  <main class="workspace-page">
    <header class="workspace-heading">
      <h1>{{ t("config.title") }}</h1>
      <button type="button" class="sl-button--ghost" @click="goBack">
        {{ t("config.back") }}
      </button>
    </header>

    <section class="sl-card" aria-live="polite">
      <div class="sl-card__header">
        <div>
          <h2 class="sl-card__title">
            {{ t("update.settingsTitle") }}
            <i
              v-if="appStore.updateInfo?.updateAvailable"
              class="update-dot"
              :aria-label="t('update.available')"
            />
          </h2>
          <p class="sl-card__hint">
            {{ t("update.currentVersion") }}:
            {{ appStore.updateInfo?.currentVersion || "—" }}
          </p>
        </div>
        <el-button
          :loading="appStore.updateChecking"
          :disabled="appStore.updateInstalling"
          @click="appStore.checkForUpdates(true)"
        >
          {{ t("update.check") }}
        </el-button>
      </div>
      <p v-if="appStore.updateCheckError" class="update-error">
        {{ t("update.checkError") }}
      </p>
      <template v-if="appStore.updateInfo">
        <p v-if="appStore.updateInfo.updateAvailable" class="update-available">
          {{
            t("update.availableVersion", {
              version: appStore.updateInfo.latestVersion
            })
          }}
        </p>
        <p v-else class="update-status">{{ t("update.upToDate") }}</p>
        <div v-if="appStore.updateInfo.updateAvailable">
          <el-button
            type="primary"
            :disabled="appStore.updateInstalling"
            @click="handleInstallUpdate"
          >
            {{ t("update.install") }}
          </el-button>
        </div>
      </template>
      <template v-if="appStore.updateInstalling && appStore.updateProgress">
        <p class="update-status">
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
      <p v-if="appStore.updateInstallError" class="update-error">
        {{ t("update.installError") }}: {{ appStore.updateInstallError }}
      </p>
    </section>

    <el-form
      class="sl-card settings-form"
      label-width="140px"
      label-position="left"
    >
      <el-form-item :label="t('workspace.appearance')">
        <el-radio-group v-model="theme">
          <el-radio value="light">{{ t("workspace.light") }}</el-radio>
          <el-radio value="dark">{{ t("workspace.dark") }}</el-radio>
        </el-radio-group>
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
        <div class="form-hint">{{ t("config.autoStart.tips") }}</div>
      </el-form-item>

      <el-form-item :label="t('config.backend.label')">
        <el-input
          v-model="form.backendUrl"
          placeholder="https://skylab-tw.com"
        />
        <div class="form-hint form-hint--block">
          {{ t("config.backend.tips") }}
        </div>
      </el-form-item>

      <el-form-item :label="t('config.account.label')">
        <template v-if="appStore.loggedIn">
          <el-tag type="success" size="small" class="account-tag">
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
  </main>
</template>

<style lang="scss" scoped>
.update-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-left: 6px;
  vertical-align: middle;
  border-radius: 50%;
  background: var(--color-danger);
}

.update-available {
  color: var(--color-primary-on-surface);
}

.update-status {
  color: var(--color-text-muted);
}

.update-error {
  color: var(--color-danger);
}

/* el-form-item 自帶下邊距，卡片本身不再加 gap */
.settings-form {
  gap: 0;
}

.settings-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
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

.account-tag {
  margin-right: 8px;
}
</style>
