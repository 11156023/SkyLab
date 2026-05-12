<script lang="ts">
import { ElConfigProvider } from "element-plus";
import en from "element-plus/dist/locale/en.mjs";
import zhCn from "element-plus/dist/locale/zh-cn.mjs";
import { defineComponent, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useAppStore } from "./store/app";

export default defineComponent({
  name: "App",
  components: {
    [ElConfigProvider.name]: ElConfigProvider
  },
  setup() {
    const appStore = useAppStore();
    const { t } = useI18n();

    const warningVisible = ref(false);
    const doNotShow = ref(false);

    // Start / stop the session-status poller as the user logs in & out.
    watch(
      () => appStore.loggedIn,
      loggedIn => {
        if (loggedIn) appStore.startSessionPolling();
        else appStore.stopSessionPolling();
      },
      { immediate: true }
    );

    watch(
      () => appStore.activeWarning,
      warning => {
        if (warning && !warningVisible.value) {
          doNotShow.value = false;
          warningVisible.value = true;
        } else if (!warning) {
          warningVisible.value = false;
        }
      }
    );

    function handleConfirm() {
      const warning = appStore.activeWarning;
      if (!warning) return;
      warningVisible.value = false;
      if (!warning.warn_reason || warning.warn_reason === "expiry" || !warning.can_extend) {
        if (doNotShow.value) {
          appStore.dismissWarningPermanent(warning.vmid);
        } else {
          appStore.dismissWarning(warning.vmid);
        }
      } else {
        appStore.extendSession(warning.vmid);
        if (doNotShow.value) {
          appStore.dismissWarningPermanent(warning.vmid);
        } else {
          appStore.dismissWarning(warning.vmid);
        }
      }
    }

    function handleLater() {
      const warning = appStore.activeWarning;
      if (!warning) return;
      warningVisible.value = false;
      if (doNotShow.value) {
        appStore.dismissWarningPermanent(warning.vmid);
      } else {
        appStore.dismissWarning(warning.vmid);
      }
    }

    return {
      appStore,
      warningVisible,
      doNotShow,
      handleConfirm,
      handleLater,
      t,
      currentLocale: () =>
        useAppStore().language === "zh-CN" ? zhCn : en
    };
  },
  computed: {
    currentLocale() {
      return useAppStore().language === "zh-CN" ? zhCn : en;
    },
    warningInfo(): CampusCloudSessionStatus | null {
      return this.appStore.activeWarning;
    },
    isExpiry(): boolean {
      return this.warningInfo?.warn_reason === "expiry";
    },
    showExtend(): boolean {
      return !this.isExpiry && (this.warningInfo?.can_extend ?? false);
    },
    warningTitle(): string {
      return this.isExpiry
        ? this.t("sessionWarning.expiryTitle")
        : this.t("sessionWarning.autoStopTitle");
    },
    warningMessage(): string {
      if (!this.warningInfo) return "";
      return this.isExpiry
        ? this.t("sessionWarning.expiryBody", {
            vmid: this.warningInfo.vmid,
            hours: this.warningInfo.hours_until_expiry ?? "?"
          })
        : this.t("sessionWarning.autoStopBody", {
            vmid: this.warningInfo.vmid,
            minutes: this.warningInfo.minutes_until_stop ?? "?"
          });
    }
  }
});
</script>

<template>
  <el-config-provider :locale="currentLocale">
    <router-view />

    <el-dialog
      v-model="warningVisible"
      :title="warningTitle"
      width="420px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
    >
      <p class="text-sm text-gray-700 dark:text-gray-300 mb-4">{{ warningMessage }}</p>
      <el-checkbox v-model="doNotShow">{{ t("sessionWarning.doNotShow") }}</el-checkbox>

      <template #footer>
        <div class="flex justify-end gap-2">
          <el-button v-if="showExtend" @click="handleLater">
            {{ t("sessionWarning.later") }}
          </el-button>
          <el-button type="primary" @click="handleConfirm">
            {{ showExtend ? t("sessionWarning.extend") : t("sessionWarning.gotIt") }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </el-config-provider>
</template>
