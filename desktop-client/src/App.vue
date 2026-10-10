<script setup lang="ts">
import { ElConfigProvider } from "element-plus";
import en from "element-plus/dist/locale/en.mjs";
import ja from "element-plus/dist/locale/ja.mjs";
import zhTw from "element-plus/dist/locale/zh-tw.mjs";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useAppStore } from "./store/app";
import AppDialog from "./components/AppDialog.vue";
import MIcon from "./components/MIcon.vue";

const ELEMENT_LOCALES: Record<string, typeof en> = {
  "zh-TW": zhTw,
  "en-US": en,
  ja
};

/* toast 跟 web 的 sonner 一樣出現在右上角（色票在 styles/element.scss） */
const MESSAGE_CONFIG = { placement: "top-right" };

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
  <el-config-provider :locale="elementLocale" :message="MESSAGE_CONFIG">
    <router-view />

    <!-- 同 web 的 SessionWarningDialog：標題前的狀態圖示（自動關機琥珀、到期紅）、
         ×／Esc／點遮罩等同「稍後再說」 -->
    <AppDialog
      v-model="warningVisible"
      :title="warningTitle"
      width="420px"
      :before-close="handleLater"
    >
      <template #icon>
        <MIcon
          :name="isExpiry ? 'event_busy' : 'schedule'"
          :class="
            isExpiry ? 'session-warning__icon--expiry' : 'session-warning__icon'
          "
        />
      </template>
      <p class="session-warning__message">{{ warningMessage }}</p>
      <el-checkbox v-model="doNotShow">
        {{ t("sessionWarning.doNotShow") }}
      </el-checkbox>

      <template #footer>
        <button
          v-if="showExtend"
          type="button"
          class="sl-btn-secondary"
          @click="handleLater"
        >
          {{ t("sessionWarning.later") }}
        </button>
        <button type="button" class="sl-btn-primary" @click="handleConfirm">
          <MIcon v-if="showExtend" name="autorenew" :size="16" />{{
            showExtend ? t("sessionWarning.extend") : t("sessionWarning.gotIt")
          }}
        </button>
      </template>
    </AppDialog>
  </el-config-provider>
</template>

<style scoped lang="scss">
.session-warning__icon {
  color: var(--color-pending);
}

.session-warning__icon--expiry {
  color: var(--color-danger);
}

.session-warning__message {
  margin-bottom: 12px;
  color: var(--color-text-secondary);
}
</style>
