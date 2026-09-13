<script lang="ts" setup>
import router from "@/router";
import { useAppStore } from "@/store/app";
import { on, removeRouterListeners, send } from "@/utils/ipcUtils";
import { ElMessage } from "element-plus";
import { defineComponent, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";

defineComponent({ name: "Login" });

const { t } = useI18n();
const appStore = useAppStore();
const waiting = ref(false);

const handleLogin = () => {
  waiting.value = true;
  send(ipcRouters.AUTH.startLogin);
};

const handleCancel = () => {
  send(ipcRouters.AUTH.logout);
  waiting.value = false;
};

const goSettings = () => router.push({ name: "Config" });

const authEventHandler = (_event: any, args: ApiResponse<any>) => {
  if (!args || args.bizCode !== "A1000") return;
  const payload = args.data;
  if (!payload) return;
  waiting.value = false;
  if (payload.type === "login-success") {
    ElMessage.success(t("login.success"));
    appStore.loggedIn = true;
    appStore.refreshAuth();
    appStore.refreshResources();
    router.replace({ name: "Home", query: { connect: "1" } });
  } else if (payload.type === "login-failure") {
    ElMessage.error(t("login.failure", { error: payload.error || "unknown" }));
  }
};

onMounted(() => {
  on(ipcRouters.AUTH.startLogin, () => {
    waiting.value = true;
  });
  window.electronIpcRenderer.on("auth:event", authEventHandler);
});

onUnmounted(() => {
  removeRouterListeners(ipcRouters.AUTH.startLogin);
  window.electronIpcRenderer.removeListener("auth:event", authEventHandler);
});
</script>

<template>
  <div class="login-page">
    <button
      type="button"
      class="settings-button"
      :aria-label="t('router.config.title')"
      @click="goSettings"
    >
      <IconifyIconOffline icon="settings" />
    </button>
    <div class="login-panel">
      <img src="/logo/only/128x128.png" class="login-logo" alt="Logo" />
      <div class="brand-name">SkyLab Connect</div>
      <h1 class="login-title">{{ t("login.connectTitle") }}</h1>
      <p class="login-description">
        {{ t("login.connectDescription") }}
      </p>
      <button
        type="button"
        class="connect-button"
        :class="{ 'connect-button--waiting': waiting }"
        :disabled="waiting"
        @click="handleLogin"
      >
        <span class="connect-button__icon" aria-hidden="true">
          <svg
            v-if="waiting"
            class="connect-button__spinner"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle cx="12" cy="12" r="8.5" stroke="currentColor" />
            <path d="M12 3.5a8.5 8.5 0 0 1 8.5 8.5" stroke="currentColor" />
          </svg>
          <svg v-else viewBox="0 0 24 24" fill="none">
            <path d="M12 3v9" stroke="currentColor" />
            <path
              d="M7.05 6.64a8 8 0 1 0 9.9 0"
              stroke="currentColor"
            />
          </svg>
        </span>
        <span class="connect-button__label">
          {{ waiting ? t("login.waitingShort") : t("login.connect") }}
        </span>
        <span v-if="waiting" class="connect-button__dots" aria-hidden="true">
          <i></i><i></i><i></i>
        </span>
        <span v-else class="connect-button__arrow" aria-hidden="true">→</span>
      </button>
      <p class="login-hint">
        {{ waiting ? t("login.waiting") : t("login.firstUseHint") }}
      </p>
      <button
        v-if="waiting"
        type="button"
        class="cancel-button"
        @click="handleCancel"
      >
        {{ t("login.cancelButton") }}
      </button>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.login-page {
  position: relative;
  z-index: 1;
  display: flex;
  width: 100%;
  height: 100vh;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.settings-button {
  position: absolute;
  top: 20px;
  right: 20px;
  display: flex;
  width: 38px;
  height: 38px;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  font-size: 20px;
  background: color-mix(in srgb, var(--color-surface) 72%, transparent);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  cursor: pointer;
  transition: 0.2s ease;
}

.settings-button:hover {
  color: var(--color-primary);
  background: var(--color-hover);
}

.login-panel {
  display: flex;
  width: min(100%, 520px);
  flex-direction: column;
  align-items: center;
  padding: 42px 36px;
  text-align: center;
  background: transparent;
}

.login-logo {
  width: 58px;
  height: 58px;
  margin-bottom: 10px;
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
}

.brand-name {
  margin-bottom: 34px;
  color: var(--color-text-primary);
  font-size: 16px;
  font-weight: 700;
}

.login-title {
  margin-bottom: 8px;
  color: var(--color-text-primary);
  font-size: 28px;
  font-weight: 700;
}

.login-description {
  max-width: 380px;
  margin-bottom: 30px;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.7;
}

.connect-button {
  position: relative;
  display: flex;
  width: min(100%, 318px);
  min-height: 66px;
  align-items: center;
  gap: 14px;
  padding: 10px 17px 10px 12px;
  overflow: hidden;
  color: white;
  font-size: 16px;
  font-weight: 650;
  text-align: left;
  background: linear-gradient(
    120deg,
    color-mix(in srgb, var(--color-primary) 92%, #738cff),
    var(--color-primary-dark) 72%,
    color-mix(in srgb, var(--color-primary-dark) 88%, #243978)
  );
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 18px;
  box-shadow:
    0 14px 30px color-mix(in srgb, var(--color-primary) 24%, transparent),
    0 3px 8px color-mix(in srgb, var(--color-primary-dark) 15%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.26);
  cursor: pointer;
  transition:
    transform 0.18s ease,
    box-shadow 0.18s ease,
    filter 0.18s ease;
}

.connect-button::before {
  position: absolute;
  top: -60%;
  left: -18%;
  width: 44%;
  height: 220%;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(255, 255, 255, 0.13),
    transparent
  );
  content: "";
  pointer-events: none;
  transform: rotate(18deg);
}

.connect-button__icon {
  position: relative;
  z-index: 1;
  display: grid;
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  place-items: center;
  font-size: 24px;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 13px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.16);
}

.connect-button__icon svg {
  width: 24px;
  height: 24px;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2;
}

.connect-button__label {
  position: relative;
  z-index: 1;
  flex: 1;
  letter-spacing: 0.01em;
}

.connect-button__arrow {
  position: relative;
  z-index: 1;
  color: rgba(255, 255, 255, 0.78);
  font-size: 22px;
  font-weight: 400;
  transition: transform 0.18s ease;
}

.connect-button:hover:not(:disabled) {
  box-shadow:
    0 17px 36px color-mix(in srgb, var(--color-primary) 31%, transparent),
    0 4px 10px color-mix(in srgb, var(--color-primary-dark) 17%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.3);
  filter: saturate(1.06) brightness(1.03);
  transform: translateY(-2px);
}

.connect-button:hover:not(:disabled) .connect-button__arrow {
  transform: translateX(3px);
}

.connect-button:active:not(:disabled) {
  box-shadow:
    0 9px 20px color-mix(in srgb, var(--color-primary) 22%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.22);
  transform: translateY(0) scale(0.99);
}

.connect-button--waiting {
  background: linear-gradient(
    120deg,
    color-mix(in srgb, var(--color-primary) 76%, #7d91d7),
    color-mix(in srgb, var(--color-primary-dark) 82%, #354c8a)
  );
  cursor: wait;
  opacity: 1;
}

.connect-button__spinner {
  animation: connect-spin 1.2s linear infinite;
}

.connect-button__spinner circle {
  opacity: 0.28;
}

.connect-button__spinner path {
  opacity: 0.95;
}

.connect-button__dots {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 4px;
}

.connect-button__dots i {
  width: 4px;
  height: 4px;
  background: rgba(255, 255, 255, 0.75);
  border-radius: 50%;
  animation: connect-dot 1.2s ease-in-out infinite;
}

.connect-button__dots i:nth-child(2) {
  animation-delay: 0.15s;
}

.connect-button__dots i:nth-child(3) {
  animation-delay: 0.3s;
}

.login-hint {
  min-height: 42px;
  max-width: 360px;
  margin-top: 22px;
  color: var(--color-text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.cancel-button {
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.cancel-button:hover {
  color: var(--color-danger);
}

@keyframes connect-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes connect-dot {
  0%,
  60%,
  100% {
    opacity: 0.42;
    transform: translateY(0);
  }

  30% {
    opacity: 1;
    transform: translateY(-3px);
  }
}
</style>
