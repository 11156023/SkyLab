<script lang="ts" setup>
import router from "@/router";
import { useAppStore } from "@/store/app";
import { on, removeRouterListeners, send } from "@/utils/ipcUtils";
import {
  findResourceForTunnel,
  groupResourcesByCourse
} from "@/utils/resourceGroups";
import { ElMessage } from "element-plus";
import {
  computed,
  defineComponent,
  onMounted,
  onUnmounted,
  ref,
  watch
} from "vue";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";
import MachineResourceTable from "./MachineResourceTable.vue";

defineComponent({ name: "Home" });

const { t } = useI18n();
const appStore = useAppStore();
const loading = ref(false);
const authenticating = ref(false);
const refreshing = ref(false);
const operationError = ref("");
const expandedCourses = ref<string[]>([]);

const status = computed(() => {
  if (appStore.tunnelStatus.connectionError) return "error";
  if (!appStore.tunnelStatus.running) return "stopped";
  return "running";
});

const orphanResources = computed<SkyLabResource[]>(() => {
  const rows = new Map<number, SkyLabResource>();
  for (const tunnel of appStore.tunnelStatus.tunnels) {
    if (findResourceForTunnel(tunnel, appStore.resources)) continue;
    const vmid = Number(tunnel.vmid);
    if (!Number.isFinite(vmid) || rows.has(vmid)) continue;
    rows.set(vmid, {
      vmid,
      name: tunnel.vm_name || tunnel.name || `VM-${vmid}`,
      type: "qemu",
      status: "running",
      environment_type: null,
      ip_address: null
    });
  }
  return [...rows.values()];
});

const visibleResources = computed(() => [
  ...appStore.resources,
  ...orphanResources.value
]);
const groupedResources = computed(() =>
  groupResourcesByCourse(visibleResources.value)
);
const machineCount = computed(() => visibleResources.value.length);
const resourceAclSignature = computed(() =>
  appStore.resources
    .map(resource =>
      [
        resource.vmid,
        resource.status,
        resource.ip_address,
        resource.can_control
      ].join(":")
    )
    .sort()
    .join("|")
);

watch(
  () => groupedResources.value.courseGroups.map(group => group.id),
  ids => {
    const available = new Set(ids);
    const retained = expandedCourses.value.filter(id => available.has(id));
    expandedCourses.value = [
      ...retained,
      ...ids.filter(id => !retained.includes(id))
    ];
  },
  { immediate: true }
);

watch(resourceAclSignature, (next, previous) => {
  if (
    appStore.tunnelStatus.running &&
    previous !== undefined &&
    next !== previous
  ) {
    send(ipcRouters.TUNNEL.refresh);
  }
});

watch(
  () => appStore.tunnelStatus.running,
  running => {
    if (running) {
      loading.value = false;
      operationError.value = "";
      appStore.refreshResources();
    }
  }
);

const startTunnel = () => {
  loading.value = true;
  authenticating.value = false;
  operationError.value = "";
  send(ipcRouters.TUNNEL.start);
};

const handleConnect = () => {
  if (appStore.loggedIn) {
    startTunnel();
    return;
  }

  loading.value = true;
  authenticating.value = true;
  operationError.value = "";
  appStore.loginInProgress = true;
  send(ipcRouters.AUTH.startLogin);
};

const authEventHandler = (_event: any, args: ApiResponse<any>) => {
  if (!args || args.bizCode !== "A1000") return;
  const payload = args.data;
  if (!payload) return;

  appStore.loginInProgress = false;
  if (payload.type === "login-success") {
    appStore.loggedIn = true;
    appStore.refreshAuth();
    appStore.refreshResources();
    ElMessage.success(t("login.success"));
    startTunnel();
    return;
  }

  if (payload.type === "login-failure") {
    loading.value = false;
    authenticating.value = false;
    operationError.value = t("login.failure", {
      error: payload.error || "unknown"
    });
    ElMessage.error(operationError.value);
  }
};

const handleDisconnect = () => {
  loading.value = true;
  operationError.value = "";
  send(ipcRouters.TUNNEL.stop);
};

const refresh = () => {
  if (appStore.loggedIn) appStore.refreshResources();
  if (appStore.tunnelStatus.running) {
    refreshing.value = true;
    send(ipcRouters.TUNNEL.refresh);
  } else {
    send(ipcRouters.TUNNEL.getStatus);
  }
};

const refreshAfterNetworkRecovery = () => {
  if (appStore.tunnelStatus.running) refresh();
};

const goSettings = () => router.push({ name: "Config" });

const openSsh = (target: { host: string; port: number }) => {
  send(ipcRouters.SYSTEM.openSsh, target);
};

const openRdp = (target: { host: string; port: number }) => {
  send(ipcRouters.SYSTEM.openRdp, target);
};

onMounted(() => {
  on(
    ipcRouters.AUTH.startLogin,
    () => {
      appStore.loginInProgress = true;
    },
    (_code, message) => {
      loading.value = false;
      authenticating.value = false;
      appStore.loginInProgress = false;
      operationError.value = message;
      ElMessage.error(message);
    }
  );
  on(
    ipcRouters.TUNNEL.start,
    () => {
      loading.value = false;
      send(ipcRouters.TUNNEL.getStatus);
    },
    (_code, message) => {
      loading.value = false;
      operationError.value = message;
    }
  );
  on(
    ipcRouters.TUNNEL.refresh,
    (data: TunnelStatusInfo) => {
      refreshing.value = false;
      if (data) appStore.tunnelStatus = data;
      appStore.refreshResources();
    },
    (_code, message) => {
      refreshing.value = false;
      operationError.value = message;
      ElMessage.error(message);
    }
  );
  on(
    ipcRouters.TUNNEL.stop,
    () => {
      loading.value = false;
      send(ipcRouters.TUNNEL.getStatus);
    },
    (_code, message) => {
      loading.value = false;
      ElMessage.error(message);
    }
  );
  on(ipcRouters.TUNNEL.getStatus, (data: TunnelStatusInfo) => {
    if (data) appStore.tunnelStatus = data;
  });
  on(
    ipcRouters.SYSTEM.openSsh,
    () => undefined,
    (_code, message) => ElMessage.error(message)
  );
  on(
    ipcRouters.SYSTEM.openRdp,
    () => undefined,
    (_code, message) => ElMessage.error(message)
  );

  window.electronIpcRenderer.on("auth:event", authEventHandler);

  refresh();
  window.addEventListener("online", refreshAfterNetworkRecovery);
  if (router.currentRoute.value.query.connect === "1") {
    handleConnect();
    router.replace({ name: "Home" });
  }
});

onUnmounted(() => {
  removeRouterListeners(ipcRouters.AUTH.startLogin);
  removeRouterListeners(ipcRouters.TUNNEL.start);
  removeRouterListeners(ipcRouters.TUNNEL.stop);
  removeRouterListeners(ipcRouters.TUNNEL.refresh);
  removeRouterListeners(ipcRouters.TUNNEL.getStatus);
  removeRouterListeners(ipcRouters.SYSTEM.openSsh);
  removeRouterListeners(ipcRouters.SYSTEM.openRdp);
  window.electronIpcRenderer.removeListener("auth:event", authEventHandler);
  window.removeEventListener("online", refreshAfterNetworkRecovery);
});
</script>

<template>
  <div class="main connect-page">
    <header class="app-topbar">
      <div class="brand">
        <img src="/logo/only/128x128.png" alt="Logo" />
        <span>SkyLab Connect</span>
      </div>
      <div class="topbar-actions">
        <div class="status-pill">
          <span
            class="status-dot"
            :class="{
              'status-dot--success': status === 'running',
              'status-dot--danger': status === 'error',
              'status-dot--muted': status === 'stopped'
            }"
          />
          {{ t(`home.status.${status}`) }}
        </div>
        <button
          type="button"
          class="icon-button"
          :aria-label="t('router.config.title')"
          @click="goSettings"
        >
          <IconifyIconOffline icon="settings" />
        </button>
      </div>
    </header>

    <div class="connect-content">
      <section v-if="status !== 'running'" class="connect-hero">
        <button
          type="button"
          class="connect-button"
          :class="{ 'connect-button--loading': loading }"
          :disabled="loading"
          @click="handleConnect"
        >
          <span class="connect-button__icon">
            <IconifyIconOffline
              :icon="loading ? 'refresh-rounded' : 'settings-ethernet-rounded'"
            />
          </span>
          <span class="connect-button__copy">
            <strong>{{
              authenticating
                ? t("home.connect.authenticating")
                : loading
                  ? t("home.connect.connecting")
                  : t("home.connect.button")
            }}</strong>
            <small>{{
              authenticating
                ? t("home.connect.authHint")
                : t("home.connect.secureHint")
            }}</small>
          </span>
          <span class="connect-button__arrow">
            <IconifyIconOffline icon="arrow-forward-rounded" />
          </span>
        </button>
        <h1>{{ t("home.connect.title") }}</h1>
        <p>{{ t("home.connect.description") }}</p>
        <div v-if="operationError || status === 'error'" class="connect-error">
          <IconifyIconOffline icon="error" />
          <span>{{
            operationError || appStore.tunnelStatus.connectionError
          }}</span>
        </div>
      </section>

      <template v-else>
        <div class="resource-header">
          <div>
            <h1>{{ t("resources.webTitle") }}</h1>
            <p>{{ t("resources.webSubtitle") }}</p>
          </div>
          <div class="resource-actions">
            <el-button size="small" :loading="refreshing" @click="refresh">
              <IconifyIconOffline icon="refresh-rounded" />
              {{ t("common.refresh") }}
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="loading"
              @click="handleDisconnect"
            >
              <IconifyIconOffline icon="stop-rounded" />
              {{ t("home.button.stop") }}
            </el-button>
          </div>
        </div>

        <div class="connection-summary">
          <span class="summary-check">
            <IconifyIconOffline icon="check-circle-rounded" />
          </span>
          <span>{{
            t("home.machines.summary", {
              machines: machineCount,
              courses: groupedResources.courseGroups.length
            })
          }}</span>
        </div>

        <div v-if="machineCount" class="resource-sections">
          <section
            v-if="groupedResources.courseGroups.length"
            class="resource-panel"
          >
            <el-collapse v-model="expandedCourses" class="course-collapse">
              <el-collapse-item
                v-for="group in groupedResources.courseGroups"
                :key="group.id"
                :name="group.id"
              >
                <template #title>
                  <div class="course-header">
                    <span class="course-icon">
                      <IconifyIconOffline icon="school" />
                    </span>
                    <span class="course-copy">
                      <strong
                        >{{ t("resources.course.kind") }}｜{{
                          group.title
                        }}</strong
                      >
                      <small>{{
                        t("resources.course.machineCount", {
                          count: group.resources.length
                        })
                      }}</small>
                    </span>
                    <el-tag size="small" type="success">
                      {{
                        t("resources.course.runningCount", {
                          running: group.runningCount,
                          total: group.resources.length
                        })
                      }}
                    </el-tag>
                  </div>
                </template>
                <MachineResourceTable
                  :resources="group.resources"
                  :tunnels="appStore.tunnelStatus.tunnels"
                  @ssh="openSsh"
                  @rdp="openRdp"
                />
              </el-collapse-item>
            </el-collapse>
          </section>

          <section
            v-if="groupedResources.personalResources.length"
            class="resource-panel"
          >
            <div class="section-heading">
              <span class="course-icon">
                <IconifyIconOffline icon="computer" />
              </span>
              <span>
                <strong>{{ t("resources.personal.title") }}</strong>
                <small>{{ t("resources.personal.description") }}</small>
              </span>
            </div>
            <MachineResourceTable
              :resources="groupedResources.personalResources"
              :tunnels="appStore.tunnelStatus.tunnels"
              @ssh="openSsh"
              @rdp="openRdp"
            />
          </section>
        </div>

        <section v-else class="resource-panel empty-state">
          <div class="empty-icon">
            <IconifyIconOffline icon="cloud-off-rounded" />
          </div>
          {{ t("resources.empty") }}
        </section>
      </template>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.connect-page {
  gap: 0;
  overflow: hidden;
  background: color-mix(in srgb, var(--color-surface-glass) 88%, transparent);
  border: 1px solid var(--color-surface-glass-border);
  border-radius: 12px;
  box-shadow: var(--shadow-glass);
  backdrop-filter: blur(16px) saturate(1.25);
}

.app-topbar {
  display: flex;
  min-height: 64px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  padding: 10px 18px;
  border-bottom: 1px solid var(--color-divider);
}

.brand,
.topbar-actions,
.resource-actions,
.connection-summary,
.course-header,
.section-heading {
  display: flex;
  align-items: center;
}

.brand {
  gap: 10px;
  color: var(--color-text-primary);
  font-size: 15px;
  font-weight: 700;
}

.brand img {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
}

.topbar-actions,
.resource-actions {
  gap: 8px;
}

.icon-button {
  display: flex;
  width: 34px;
  height: 34px;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  font-size: 19px;
  border-radius: 8px;
  cursor: pointer;
}

.icon-button:hover {
  color: var(--color-primary);
  background: var(--color-hover);
}

.connect-content {
  flex: 1;
  min-height: 0;
  padding: 22px;
  overflow: auto;
}

.connect-hero {
  display: flex;
  min-height: 100%;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 30px;
  text-align: center;
}

.connect-button {
  position: relative;
  display: flex;
  width: min(430px, 100%);
  min-height: 112px;
  align-items: center;
  gap: 16px;
  padding: 18px 20px;
  overflow: hidden;
  color: white;
  background: linear-gradient(
    125deg,
    #6d86d0 0%,
    var(--color-primary) 42%,
    var(--color-primary-dark) 100%
  );
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 24px;
  box-shadow:
    0 20px 44px color-mix(in srgb, var(--color-primary) 30%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.4);
  cursor: pointer;
  transition:
    transform 0.22s ease,
    box-shadow 0.22s ease,
    filter 0.22s ease;
}

.connect-button::before,
.connect-button::after {
  position: absolute;
  content: "";
  pointer-events: none;
  border-radius: 999px;
}

.connect-button::before {
  top: -74px;
  right: -38px;
  width: 190px;
  height: 190px;
  background: rgba(255, 255, 255, 0.1);
}

.connect-button::after {
  bottom: -80px;
  left: 80px;
  width: 150px;
  height: 150px;
  border: 24px solid rgba(255, 255, 255, 0.05);
}

.connect-button__icon,
.connect-button__arrow {
  position: relative;
  z-index: 1;
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
}

.connect-button__icon {
  width: 64px;
  height: 64px;
  font-size: 35px;
  background: rgba(255, 255, 255, 0.17);
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: 19px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.26);
}

.connect-button__copy {
  position: relative;
  z-index: 1;
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  align-items: flex-start;
  text-align: left;
}

.connect-button__copy strong {
  font-size: 19px;
  font-weight: 750;
  letter-spacing: 0.01em;
}

.connect-button__copy small {
  margin-top: 6px;
  color: rgba(255, 255, 255, 0.78);
  font-size: 12px;
  font-weight: 500;
}

.connect-button__arrow {
  width: 36px;
  height: 36px;
  font-size: 21px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 50%;
  transition: transform 0.22s ease;
}

.connect-button:hover:not(:disabled) {
  box-shadow:
    0 24px 52px color-mix(in srgb, var(--color-primary) 40%, transparent),
    inset 0 1px 0 rgba(255, 255, 255, 0.4);
  filter: saturate(1.08);
  transform: translateY(-3px);
}

.connect-button:hover:not(:disabled) .connect-button__arrow {
  transform: translateX(3px);
}

.connect-button--loading {
  cursor: wait;
  animation: connect-pulse 1.6s ease-in-out infinite;
}

.connect-button--loading .connect-button__icon svg {
  animation: connect-spin 1.2s linear infinite;
}

.connect-hero h1 {
  margin-top: 28px;
  color: var(--color-text-primary);
  font-size: 26px;
  font-weight: 700;
}

.connect-hero > p {
  max-width: 430px;
  margin-top: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}

.connect-error {
  display: flex;
  max-width: 520px;
  align-items: flex-start;
  gap: 7px;
  margin-top: 20px;
  padding: 10px 14px;
  color: var(--color-danger);
  font-size: 12px;
  background: color-mix(in srgb, var(--color-danger) 8%, transparent);
  border-radius: 8px;
  text-align: left;
}

.resource-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.resource-header h1 {
  color: var(--color-text-primary);
  font-size: 22px;
  font-weight: 700;
}

.resource-header p {
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.resource-actions :deep(.el-button) {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  margin-left: 0;
}

.connection-summary {
  gap: 8px;
  margin-top: 18px;
  padding: 10px 14px;
  color: var(--color-text-secondary);
  font-size: 12px;
  background: color-mix(in srgb, var(--color-success) 7%, var(--color-surface));
  border: 1px solid
    color-mix(in srgb, var(--color-success) 18%, var(--color-border));
  border-radius: 8px;
}

.summary-check {
  color: var(--color-success);
  font-size: 19px;
}

.resource-sections {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 16px;
}

.resource-panel {
  overflow: hidden;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
}

.course-collapse {
  border: 0;
}

.course-collapse :deep(.el-collapse-item__header) {
  height: auto;
  min-height: 64px;
  padding: 9px 16px;
  color: var(--color-text-primary);
  background: color-mix(in srgb, var(--color-primary) 5%, var(--color-surface));
  border-bottom-color: var(--color-divider);
}

.course-collapse :deep(.el-collapse-item__content) {
  padding-bottom: 0;
}

.course-collapse :deep(.el-collapse-item__wrap) {
  border-bottom-color: var(--color-divider);
}

.course-header {
  width: 100%;
  min-width: 0;
  gap: 9px;
  padding-right: 12px;
}

.course-icon {
  display: flex;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  color: var(--color-primary);
  font-size: 19px;
  background: var(--color-hover);
  border-radius: 8px;
}

.course-copy,
.section-heading > span:last-child {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.course-copy strong,
.section-heading strong {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.course-copy small,
.section-heading small {
  margin-top: 2px;
  color: var(--color-text-muted);
  font-size: 11px;
}

.section-heading {
  gap: 9px;
  padding: 13px 16px;
  color: var(--color-text-primary);
  background: color-mix(in srgb, var(--color-primary) 5%, var(--color-surface));
  border-bottom: 1px solid var(--color-divider);
}

@keyframes connect-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes connect-pulse {
  50% {
    box-shadow:
      0 23px 54px color-mix(in srgb, var(--color-primary) 45%, transparent),
      inset 0 1px 0 rgba(255, 255, 255, 0.4);
    transform: translateY(-2px);
  }
}

@media (max-width: 720px) {
  .connect-content {
    padding: 16px;
  }

  .resource-header {
    flex-direction: column;
  }
}
</style>
