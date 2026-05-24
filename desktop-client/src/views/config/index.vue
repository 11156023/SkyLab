<script lang="ts" setup>
import Breadcrumb from "@/layout/compoenets/Breadcrumb.vue";
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
    <breadcrumb />
    <div class="app-container-breadcrumb">
      <div
        class="flex flex-col gap-6 p-6 w-full h-full bg-white rounded drop-shadow-lg overflow-auto"
      >
        <h2 class="text-lg font-semibold">{{ t("config.title") }}</h2>

        <el-form label-width="140px" label-position="left">
          <el-form-item :label="t('config.language.label')">
            <el-radio-group v-model="form.language">
              <el-radio value="zh-CN">{{ t("config.language.zhCN") }}</el-radio>
              <el-radio value="en-US">{{ t("config.language.enUS") }}</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item :label="t('config.autoStart.label')">
            <el-switch v-model="form.launchAtStartup" />
            <div class="ml-3 text-xs text-gray-400">
              {{ t("config.autoStart.tips") }}
            </div>
          </el-form-item>

          <el-form-item :label="t('config.backend.label')">
            <el-input v-model="form.backendUrl" placeholder="http://localhost:8000" />
            <div class="mt-1 text-xs text-gray-400">
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
