<script setup lang="ts">
import { ElConfigProvider } from "element-plus";
import en from "element-plus/dist/locale/en.mjs";
import ja from "element-plus/dist/locale/ja.mjs";
import zhTw from "element-plus/dist/locale/zh-tw.mjs";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useAppStore } from "./store/app";

const ELEMENT_LOCALES: Record<string, typeof en> = {
  "zh-TW": zhTw,
  "en-US": en,
  ja
};

const appStore = useAppStore();
const { t } = useI18n();
const warningVisible = ref(false);
const doNotShow = ref(false);

const elementLocale = computed(() => ELEMENT_LOCALES[appStore.language] ?? en);
const warning = computed(() => appStore.activeWarning);
const isExpiry = computed(() => warning.value?.warn_reason === "expiry");
/* 只有課堂時段／練習額度型（有 warn_reason 且可延長）才給「延長使用時間」；到期型只能知道了 */
const showExtend = computed(
  () =>
    !!warning.value?.warn_reason &&
    !isExpiry.value &&
    (warning.value?.can_extend ?? false)
);
const warningTitle = computed(() =>
  isExpiry.value
    ? t("sessionWarning.expiryTitle")
    : t("sessionWarning.autoStopTitle")
);
const warningMessage = computed(() => {
  if (!warning.value) return "";
  return isExpiry.value
    ? t("sessionWarning.expiryBody", {
        vmid: warning.value.vmid,
        hours: warning.value.hours_until_expiry ?? "?"
      })
    : t("sessionWarning.autoStopBody", {
        vmid: warning.value.vmid,
        minutes: warning.value.minutes_until_stop ?? "?"
      });
});

/* 登入／登出時啟停工作階段狀態輪詢 */
watch(
  () => appStore.loggedIn,
  loggedIn => {
    if (loggedIn) appStore.startSessionPolling();
    else appStore.stopSessionPolling();
  },
  { immediate: true }
);

watch(
  warning,
  next => {
    if (next && !warningVisible.value) {
      doNotShow.value = false;
      warningVisible.value = true;
    } else if (!next) {
      warningVisible.value = false;
    }
  },
  { immediate: true }
);

/* 勾了「不再顯示」記到 localStorage；否則只在這一輪 should_warn 期間不再跳 */
const dismiss = (vmid: number) => {
  if (doNotShow.value) appStore.dismissWarningPermanent(vmid);
  else appStore.dismissWarning(vmid);
};

const handleLater = () => {
  if (!warning.value) return;
  warningVisible.value = false;
  dismiss(warning.value.vmid);
};

const handleConfirm = () => {
  if (!warning.value) return;
  warningVisible.value = false;
  if (showExtend.value) appStore.extendSession(warning.value.vmid);
  dismiss(warning.value.vmid);
};
</script>

<template>
  <el-config-provider :locale="elementLocale">
    <router-view />

    <el-dialog
      v-model="warningVisible"
      :title="warningTitle"
      width="420px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
    >
      <p class="session-warning__message">{{ warningMessage }}</p>
      <el-checkbox v-model="doNotShow">
        {{ t("sessionWarning.doNotShow") }}
      </el-checkbox>

      <template #footer>
        <div class="session-warning__footer">
          <el-button v-if="showExtend" @click="handleLater">
            {{ t("sessionWarning.later") }}
          </el-button>
          <el-button type="primary" @click="handleConfirm">
            {{
              showExtend
                ? t("sessionWarning.extend")
                : t("sessionWarning.gotIt")
            }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </el-config-provider>
</template>

<style scoped lang="scss">
.session-warning__message {
  margin-bottom: 16px;
  color: var(--color-text);
}

.session-warning__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
