<script lang="ts" setup>
import router from "@/router";
import { useAppStore } from "@/store/app";
import { on, removeRouterListeners, send } from "@/utils/ipcUtils";
import { ElMessage } from "element-plus";
import { defineComponent, onMounted, onUnmounted, reactive, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";

defineComponent({ name: "Config" });

const { t } = useI18n();
const appStore = useAppStore();

const form = reactive({
  language: "zh-CN",
  launchAtStartup: false,
  backendUrl: ""
});

const syncFromStore = (settings: Partial<SkyLabSettings> | null) => {
  if (!settings) return;
  form.language = settings.language || "zh-CN";
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
  on(ipcRouters.SETTINGS.getSettings, (data: SkyLabSettings) => {
    syncFromStore(data);
  });
  on(ipcRouters.SETTINGS.saveSettings, (data: SkyLabSettings) => {
    syncFromStore(data);
    ElMessage.success(t("config.saveSuccess"));
  });
  send(ipcRouters.SETTINGS.getSettings);
});

onUnmounted(() => {
  removeRouterListeners(ipcRouters.SETTINGS.getSettings);
  removeRouterListeners(ipcRouters.SETTINGS.saveSettings);
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

        <el-form
          class="settings-form section-panel"
          label-width="140px"
          label-position="left"
        >
          <el-form-item :label="t('config.language.label')">
            <el-radio-group v-model="form.language">
              <el-radio value="zh-CN">{{ t("config.language.zhCN") }}</el-radio>
              <el-radio value="en-US">{{ t("config.language.enUS") }}</el-radio>
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
              placeholder="http://localhost:8000"
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
