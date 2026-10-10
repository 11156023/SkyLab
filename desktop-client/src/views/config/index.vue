<script lang="ts" setup>
import { useAppStore } from "@/store/app";
import { describeError } from "@/utils/errors";
import { on } from "@/utils/ipcUtils";
import { theme } from "@/utils/appearance";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, onUnmounted, ref, watch, type Ref } from "vue";
import { useI18n } from "vue-i18n";
import { onBeforeRouteLeave } from "vue-router";
import { ipcRouters } from "../../../electron/core/IpcRouter";
import BackendUrlUtils from "../../../electron/utils/BackendUrlUtils";
import pkg from "../../../package.json";
import MIcon from "@/components/MIcon.vue";
import PageHeader from "@/components/PageHeader.vue";
import SegmentedControl from "@/components/SegmentedControl.vue";

defineOptions({ name: "Config" });

const { t } = useI18n();
const appStore = useAppStore();
const disposers: Array<() => void> = [];

/* ── 軟體更新 ── */
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
      showClose: false,
      type: "warning"
    });
    appStore.installUpdate();
  } catch {
    // User cancelled.
  }
};

/* 等主程序回覆的存檔，回覆後才跳成功或失敗通知 */
const awaitingSave = ref(false);

/* ── 一般：外觀、語言、開機自動啟動先改草稿，按頁尾「儲存」才套用 ── */
const themeOptions = computed(() => [
  { value: "light" as const, label: t("workspace.light"), icon: "light_mode" },
  { value: "dark" as const, label: t("workspace.dark"), icon: "dark_mode" },
  { value: "system" as const, label: t("workspace.system"), icon: "monitor" }
]);
const languageOptions = computed(() => [
  { value: "zh-TW", label: t("config.language.zhTW") },
  { value: "en-US", label: t("config.language.enUS") },
  { value: "ja", label: t("config.language.ja") }
]);
const autoStartOptions = computed(() => [
  { value: "on" as const, label: t("common.on") },
  { value: "off" as const, label: t("common.off") }
]);
const themeDraft = ref(theme.value);
const languageDraft = ref(appStore.language);
const autoStartDraft = ref<"on" | "off">(appStore.autoStart ? "on" : "off");
const generalDirty = computed(
  () =>
    themeDraft.value !== theme.value ||
    languageDraft.value !== appStore.language ||
    (autoStartDraft.value === "on") !== appStore.autoStart
);

/* ── 伺服器：後端網址。檢查規則跟主程序存檔時同一套（BackendUrlUtils），畫面上的錯誤就是真正會被擋下的原因 ── */
const backendUrlDraft = ref(appStore.backendUrl);
const urlCheck = computed(() => BackendUrlUtils.parse(backendUrlDraft.value));
const urlDirty = computed(() => {
  const check = urlCheck.value;
  return (
    (check.ok ? check.url : backendUrlDraft.value.trim()) !==
    appStore.backendUrl
  );
});
const urlError = computed(() => {
  const check = urlCheck.value;
  return urlDirty.value && check.ok === false
    ? t(`config.backend.error.${check.problem}`)
    : "";
});
/* 換伺服器等於登出：已登入或通道開著時要先講清楚 */
const urlChangeLogsOut = computed(
  () => appStore.loggedIn || appStore.tunnelStatus.running
);

/* 存好的值變了（啟動時讀回、存檔成功或失敗拉回）：沒動過的欄位跟著換，正在改的不蓋掉 */
const syncDraft = <T,>(draft: Ref<T>, source: () => T) =>
  watch(source, (next, previous) => {
    if (draft.value === previous) draft.value = next;
  });
syncDraft(themeDraft, () => theme.value);
syncDraft(languageDraft, () => appStore.language);
syncDraft(autoStartDraft, () => (appStore.autoStart ? "on" : "off"));
watch(
  () => appStore.backendUrl,
  (next, previous) => {
    if (!urlDirty.value || backendUrlDraft.value.trim() === previous) {
      backendUrlDraft.value = next;
    }
  }
);

/* ── 頁尾儲存列（同 web 設定頁的 saveBar）：一般＋伺服器一起存，網址格式不對就整個不能存 ── */
const dirty = computed(() => generalDirty.value || urlDirty.value);
const canSave = computed(
  () =>
    dirty.value &&
    (!urlDirty.value || urlCheck.value.ok) &&
    !appStore.settingsSaving
);

const confirmDanger = (title: string, message: string, confirmText: string) =>
  ElMessageBox.confirm(message, title, {
    confirmButtonText: confirmText,
    cancelButtonText: t("common.cancel"),
    confirmButtonClass: "sl-confirm-danger",
    showClose: false,
    type: "warning"
  })
    .then(() => true)
    .catch(() => false);

const save = async () => {
  if (!canSave.value) return;
  if (
    urlDirty.value &&
    urlChangeLogsOut.value &&
    !(await confirmDanger(
      t("config.backend.confirmTitle"),
      t("config.backend.confirmMessage"),
      t("config.backend.confirmButton")
    ))
  ) {
    return;
  }
  /* 外觀只存在這台電腦（localStorage），不經過主程序 */
  theme.value = themeDraft.value;
  const patch: SkyLabSettingsPatch = {};
  if (languageDraft.value !== appStore.language) {
    patch.language = languageDraft.value;
  }
  const launchAtStartup = autoStartDraft.value === "on";
  if (launchAtStartup !== appStore.autoStart) {
    patch.launchAtStartup = launchAtStartup;
  }
  const check = urlCheck.value;
  if (urlDirty.value && check.ok) {
    patch.backendUrl = check.url;
    backendUrlDraft.value = check.url;
  }
  if (Object.keys(patch).length) {
    awaitingSave.value = true;
    appStore.saveSettings(patch);
  } else {
    ElMessage.success(t("config.saveSuccess"));
  }
};

const restore = () => {
  themeDraft.value = theme.value;
  languageDraft.value = appStore.language;
  autoStartDraft.value = appStore.autoStart ? "on" : "off";
  backendUrlDraft.value = appStore.backendUrl;
};

/* 改了沒存就要離開：比照 web 的跳離確認 */
onBeforeRouteLeave(async () => {
  if (!dirty.value) return true;
  const leave = await confirmDanger(
    t("unsavedGuard.title"),
    t("unsavedGuard.message"),
    t("unsavedGuard.leave")
  );
  if (leave) restore();
  return leave;
});

/* ── 帳號 ── */
const handleLogout = () => {
  appStore.logout();
};

onMounted(() => {
  appStore.checkForUpdates();
  appStore.refreshSettings();
  disposers.push(
    on(
      ipcRouters.SETTINGS.saveSettings,
      () => {
        if (!awaitingSave.value) return;
        awaitingSave.value = false;
        ElMessage.success(t("config.saveSuccess"));
      },
      code => {
        awaitingSave.value = false;
        ElMessage.error(t("config.saveFailed", { error: describeError(code) }));
      }
    )
  );
});

onUnmounted(() => {
  disposers.forEach(dispose => dispose());
});
</script>

<template>
  <main class="workspace-page">
    <PageHeader :title="t('config.title')" />

    <!-- 寬視窗兩欄：左邊是要存檔的一般與伺服器（頁尾一顆儲存），右邊是軟體更新與帳號；窄視窗依序疊成一欄 -->
    <div class="settings-grid">
      <form class="settings-column" novalidate @submit.prevent="save">
        <section class="sl-card">
          <h2 class="sl-card__title">{{ t("config.general") }}</h2>
          <div class="sl-field">
            <span>{{ t("workspace.appearance") }}</span>
            <SegmentedControl
              v-model="themeDraft"
              :options="themeOptions"
              :label="t('workspace.appearance')"
            />
          </div>
          <div class="sl-field">
            <span>{{ t("config.language.label") }}</span>
            <SegmentedControl
              v-model="languageDraft"
              :options="languageOptions"
              :label="t('config.language.label')"
            />
          </div>
          <div class="sl-field">
            <span>{{ t("config.autoStart.label") }}</span>
            <SegmentedControl
              v-model="autoStartDraft"
              :options="autoStartOptions"
              :label="t('config.autoStart.label')"
            />
            <small class="sl-field__hint">{{
              t("config.autoStart.tips")
            }}</small>
          </div>
        </section>

        <section class="sl-card">
          <h2 class="sl-card__title">{{ t("config.server") }}</h2>
          <label class="sl-field">
            <span>{{ t("config.backend.label") }}</span>
            <input
              v-model="backendUrlDraft"
              class="sl-input"
              :class="{ 'sl-input--invalid': !!urlError }"
              type="url"
              placeholder="https://skylab-tw.com"
              spellcheck="false"
              :aria-invalid="!!urlError"
              aria-describedby="backend-url-hint"
            />
            <small
              id="backend-url-hint"
              class="sl-field__hint"
              :class="{ 'sl-field__hint--error': !!urlError }"
              :role="urlError ? 'alert' : undefined"
              >{{ urlError || t("config.backend.tips") }}</small
            >
          </label>
          <p
            v-if="urlDirty && !urlError && urlChangeLogsOut"
            class="sl-notice sl-notice--pending"
          >
            <MIcon name="warning_amber" :size="18" />{{
              t("config.backend.logoutNotice")
            }}
          </p>
        </section>

        <div class="save-bar">
          <button
            v-if="dirty"
            type="button"
            class="sl-btn-secondary"
            :disabled="appStore.settingsSaving"
            @click="restore"
          >
            {{ t("config.discard") }}
          </button>
          <button type="submit" class="sl-btn-primary" :disabled="!canSave">
            <MIcon
              v-if="appStore.settingsSaving"
              name="autorenew"
              :size="16"
              spin
            />{{ t("common.save") }}
          </button>
        </div>
      </form>

      <div class="settings-column">
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
                {{ t("update.currentVersion") }} v{{ pkg.version }}
              </p>
            </div>
            <button
              type="button"
              class="sl-btn-secondary"
              :disabled="appStore.updateChecking || appStore.updateInstalling"
              @click="appStore.checkForUpdates(true)"
            >
              <MIcon name="sync" :size="16" :spin="appStore.updateChecking" />{{
                t("update.check")
              }}
            </button>
          </div>
          <p
            v-if="appStore.updateCheckError"
            class="sl-notice sl-notice--danger"
          >
            <MIcon name="error_outline" :size="18" />{{
              t("update.checkError")
            }}
          </p>
          <template v-else-if="appStore.updateInfo">
            <div v-if="appStore.updateInfo.updateAvailable" class="update-row">
              <span class="sl-badge sl-badge--info">
                <MIcon name="new_releases" :size="14" />{{
                  t("update.availableVersion", {
                    version: appStore.updateInfo.latestVersion
                  })
                }}
              </span>
              <button
                type="button"
                class="sl-btn-primary"
                :disabled="appStore.updateInstalling"
                @click="handleInstallUpdate"
              >
                <MIcon name="download" :size="16" />{{ t("update.install") }}
              </button>
            </div>
            <span v-else class="sl-badge sl-badge--success update-latest">
              <MIcon name="check_circle" :size="14" />{{ t("update.upToDate") }}
            </span>
          </template>
          <template v-if="appStore.updateInstalling && appStore.updateProgress">
            <p class="update-progress">
              {{ t(`update.${appStore.updateProgress.stage}`)
              }}<span v-if="appStore.updateProgress.stage === 'downloading'">
                {{ updatePercent }}%</span
              >
            </p>
            <el-progress
              v-if="appStore.updateProgress.stage === 'downloading'"
              :percentage="updatePercent"
              :stroke-width="8"
              :show-text="false"
            />
          </template>
          <p
            v-if="appStore.updateInstallErrorCode"
            class="sl-notice sl-notice--danger"
          >
            <MIcon name="error_outline" :size="18" />{{
              describeError(appStore.updateInstallErrorCode)
            }}
          </p>
        </section>

        <section class="sl-card">
          <h2 class="sl-card__title">{{ t("config.account.label") }}</h2>
          <div class="account-row">
            <span
              class="sl-badge"
              :class="
                appStore.loggedIn ? 'sl-badge--success' : 'sl-badge--muted'
              "
            >
              <MIcon
                :name="appStore.loggedIn ? 'verified_user' : 'person_off'"
                :size="14"
              />{{
                t(
                  appStore.loggedIn
                    ? "config.account.loggedIn"
                    : "config.account.notLoggedIn"
                )
              }}
            </span>
            <button
              v-if="appStore.loggedIn"
              type="button"
              class="sl-btn-danger-outline"
              @click="handleLogout"
            >
              <MIcon name="logout" :size="16" />{{ t("config.account.logout") }}
            </button>
          </div>
          <p v-if="!appStore.loggedIn" class="sl-card__hint">
            {{ t("config.account.loginHint") }}
          </p>
        </section>
      </div>
    </div>
  </main>
</template>

<style lang="scss" scoped>
.settings-grid,
.settings-column {
  @include flex-column;
  gap: $spacing-24;
}

/* 內容區夠寬才分兩欄（.desktop-main 是 container）；兩欄各自往下長，不用等高 */
@container (min-width: 880px) {
  .settings-grid {
    display: grid;
    grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
    align-items: start;
  }
}

.sl-card__title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.update-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-danger);
}

/* 徽章裡帶圖示：圖示與文字間距 */
.sl-badge {
  gap: 4px;
}

.update-latest {
  align-self: flex-start;
}

.update-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: $spacing-8;
}

.update-progress {
  color: var(--color-text-muted);
  font-size: $font-size-14;
}

.account-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

/* 頁尾儲存列：同 web 設定頁的 .saveBar（一般頁尾、靠右，不黏在畫面底） */
.save-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: $spacing-8;
}
</style>
