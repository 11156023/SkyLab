<script lang="ts" setup>
import router from "@/router";
import { useAppStore } from "@/store/app";
import { on, removeRouterListeners, send } from "@/utils/ipcUtils";
import {
  findResourceForTunnel,
  groupResourcesByCourse
} from "@/utils/resourceGroups";
import { ElMessage } from "element-plus";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";
import ResourceCards from "./ResourceCards.vue";
import AppIcon from "@/components/AppIcon.vue";
import { resourceView, theme, toggleTheme } from "@/utils/appearance";

defineOptions({ name: "Home" });

const { t } = useI18n();
const appStore = useAppStore();
const loading = ref(false);
const authenticating = ref(false);
const refreshing = ref(false);
const operationError = ref("");
const query = ref("");
const filter = ref("all");
const expandedCourseIds = ref<Set<string>>(new Set());
const toggleCourse = (id: string) => {
  const next = new Set(expandedCourseIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expandedCourseIds.value = next;
};
const stopping = ref(false);
const filters = ["all", "course", "practice", "personal"] as const;
const practiceTitleByRequest = computed(() => {
  const titles = new Map<string, string>();
  for (const session of appStore.quickPracticeSessions) {
    for (const machine of session.machines ?? []) {
      if (machine.request_id) {
        titles.set(String(machine.request_id), session.title);
      }
    }
  }
  return titles;
});
const filteredResources = computed(() => {
  const search = query.value.trim().toLowerCase();
  return visibleResources.value.filter(resource =>
    [
      resource.name,
      resource.ip_address,
      resource.vmid,
      resource.teaching_class_name,
      resource.course_environment_name,
      practiceTitleByRequest.value.get(String(resource.request_id ?? "")),
      resource.owner_name
    ].some(value =>
      String(value ?? "")
        .toLowerCase()
        .includes(search)
    )
  );
});
const filteredGroups = computed(() =>
  groupResourcesByCourse(
    filteredResources.value,
    appStore.quickPracticeSessions
  )
);
const filteredFolders = computed(() => [
  ...(filter.value === "all" || filter.value === "course"
    ? filteredGroups.value.courseGroups
    : []),
  ...(filter.value === "all" || filter.value === "practice"
    ? filteredGroups.value.quickPracticeGroups
    : [])
]);
const counts = computed(() => ({
  all: machineCount.value,
  course: groupedResources.value.courseGroups.reduce(
    (total, group) => total + group.resources.length,
    0
  ),
  practice: groupedResources.value.quickPracticeGroups.reduce(
    (total, group) => total + group.resources.length,
    0
  ),
  personal: groupedResources.value.personalResources.length
}));
const hasResults = computed(
  () =>
    filteredFolders.value.length > 0 ||
    ((filter.value === "all" || filter.value === "personal") &&
      filteredGroups.value.personalResources.length > 0)
);
const connectionTitle = computed(() =>
  authenticating.value
    ? t("home.connect.authenticating")
    : loading.value
      ? t(
          stopping.value ? "workspace.disconnecting" : "home.connect.connecting"
        )
      : status.value === "running"
        ? t(
            appStore.tunnelStatus.connected
              ? "workspace.connected"
              : appStore.tunnelStatus.handshakeUnavailable
                ? "workspace.tunnelActive"
                : "workspace.waitingGateway"
          )
        : status.value === "error"
          ? t("home.status.error")
          : t("home.connect.title")
);
const connectionHint = computed(() =>
  authenticating.value
    ? t("home.connect.authHint")
    : status.value === "running"
      ? appStore.tunnelStatus.connected
        ? ""
        : t(
            appStore.tunnelStatus.handshakeUnavailable
              ? "workspace.tunnelActiveHint"
              : "workspace.waitingGatewayHint"
          )
      : t("home.connect.description")
);
const displayedError = computed(
  () => operationError.value || appStore.tunnelStatus.connectionError
);
const openWeb = () =>
  send(ipcRouters.SYSTEM.openUrl, { url: appStore.backendUrl });

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

const visibleResources = computed(() =>
  appStore.loggedIn ? [...appStore.resources, ...orphanResources.value] : []
);
const groupedResources = computed(() =>
  groupResourcesByCourse(visibleResources.value, appStore.quickPracticeSessions)
);
const machineCount = computed(() => visibleResources.value.length);
const resourceAclSignature = computed(() =>
  appStore.resources
    .map(resource =>
      [
        resource.vmid,
        resource.status,
        resource.ip_address,
        resource.can_control,
        resource.access_role,
        resource.start_blocked_reason,
        resource.window_end_at
      ].join(":")
    )
    .sort()
    .join("|")
);

watch(
  () => appStore.loggedIn,
  loggedIn => {
    if (loggedIn) appStore.refreshResources();
    else {
      loading.value = false;
      authenticating.value = false;
    }
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
      if (!stopping.value) loading.value = false;
      operationError.value = "";
      appStore.refreshResources();
    }
  }
);

const startTunnel = () => {
  if (stopping.value) return;
  loading.value = true;
  authenticating.value = false;
  operationError.value = "";
  send(ipcRouters.TUNNEL.start);
};

const handleConnect = () => {
  if (loading.value) return;
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
  if (loading.value) return;
  stopping.value = true;
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

const openSsh = (target: { host: string; port: number }) => {
  if (loading.value || status.value !== "running") return;
  send(ipcRouters.SYSTEM.openSsh, target);
};

const openRdp = (target: { host: string; port: number }) => {
  if (loading.value || status.value !== "running") return;
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
      stopping.value = false;
      loading.value = false;
      send(ipcRouters.TUNNEL.getStatus);
    },
    (_code, message) => {
      stopping.value = false;
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
  <main class="workspace-page">
    <header class="workspace-heading">
      <h1>{{ t("resources.webTitle") }}</h1>
      <div class="workspace-heading-actions">
        <button
          class="sl-button"
          :disabled="refreshing || appStore.resourcesLoading || loading"
          @click="refresh"
        >
          <AppIcon
            name="refresh"
            :size="16"
            :class="{ 'sl-spin': refreshing || appStore.resourcesLoading }"
          />{{ t("common.refresh") }}</button
        ><button
          class="sl-icon-button"
          :aria-label="t('workspace.toggleTheme')"
          :title="t('workspace.toggleTheme')"
          @click="toggleTheme"
        >
          <AppIcon :name="theme === 'dark' ? 'sun' : 'moon'" />
        </button>
      </div>
    </header>
    <section
      class="connection-banner"
      :class="{ 'connection-banner--error': !!displayedError }"
      :aria-label="t('workspace.connectionInfo')"
      aria-live="polite"
    >
      <AppIcon
        :name="loading ? 'spinner' : 'shield'"
        :size="22"
        :class="{
          'sl-spin': loading,
          'is-running':
            status === 'running' && !!appStore.tunnelStatus.connected
        }"
      />
      <div class="connection-copy">
        <h2>{{ connectionTitle }}</h2>
        <p v-if="connectionHint">{{ connectionHint }}</p>
      </div>
      <button
        class="sl-button"
        :class="{ 'sl-button--primary': status !== 'running' }"
        :disabled="loading"
        @click="status === 'running' ? handleDisconnect() : handleConnect()"
      >
        {{
          loading
            ? t("common.loading")
            : t(
                status === "running"
                  ? "home.button.stop"
                  : "home.connect.button"
              )
        }}
      </button>
    </section>
    <el-alert
      v-if="displayedError"
      class="workspace-alert"
      :title="displayedError"
      type="error"
      show-icon
      :closable="false"
    />
    <el-alert
      v-if="appStore.tunnelStatus.leaseRefreshError && status === 'running'"
      class="workspace-alert"
      :title="t('home.status.leaseRefreshFailed')"
      :description="appStore.tunnelStatus.leaseRefreshError"
      type="warning"
      show-icon
      :closable="false"
    />
    <el-alert
      v-if="appStore.resourcesError"
      class="workspace-alert"
      :title="t('workspace.resourceError')"
      :description="appStore.resourcesError"
      type="error"
      show-icon
      :closable="false"
    />
    <el-alert
      v-if="
        status === 'running' &&
        !appStore.tunnelStatus.tunnels.length &&
        !appStore.resourcesLoading
      "
      class="workspace-alert"
      :title="t('home.machines.noTargets')"
      type="warning"
      show-icon
      :closable="false"
    />
    <template v-if="appStore.loggedIn">
      <div class="resource-toolbar">
        <div
          class="resource-filters"
          role="group"
          :aria-label="t('workspace.filter')"
        >
          <button
            v-for="item in filters"
            :key="item"
            :class="{ active: filter === item }"
            :aria-pressed="filter === item"
            @click="filter = item"
          >
            {{ t(`workspace.${item}`) }} <span>{{ counts[item] }}</span>
          </button>
        </div>
        <label class="resource-search"
          ><AppIcon name="search" /><input
            v-model="query"
            :placeholder="t('workspace.search')"
            :aria-label="t('workspace.search')"
        /></label>
        <div class="resource-views">
          <button
            class="sl-icon-button"
            :class="{ active: resourceView === 'grid' }"
            :aria-label="t('workspace.grid')"
            :title="t('workspace.grid')"
            :aria-pressed="resourceView === 'grid'"
            @click="resourceView = 'grid'"
          >
            <AppIcon name="grid" /></button
          ><button
            class="sl-icon-button"
            :class="{ active: resourceView === 'list' }"
            :aria-label="t('workspace.list')"
            :title="t('workspace.list')"
            :aria-pressed="resourceView === 'list'"
            @click="resourceView = 'list'"
          >
            <AppIcon name="list" />
          </button>
        </div>
      </div>
      <div
        v-if="appStore.resourcesLoading && !machineCount"
        class="workspace-empty"
        role="status"
      >
        <AppIcon name="spinner" class="sl-spin" :size="32" />
        {{ t("common.loading") }}
      </div>
      <template v-else>
        <template v-if="filteredFolders.length"
          ><section
            v-for="group in filteredFolders"
            :key="group.id"
            class="resource-group course-folder"
          >
            <button
              type="button"
              class="course-folder__toggle"
              :aria-expanded="expandedCourseIds.has(group.id)"
              @click="toggleCourse(group.id)"
            >
              <AppIcon name="folder" />
              <span class="course-folder__name">{{
                group.title || t("workspace.unnamedCourse")
              }}</span>
              <span class="course-folder__count">{{
                t("workspace.machineCount", { count: group.resources.length })
              }}</span>
              <span class="course-folder__status">{{
                t("resources.course.runningCount", {
                  running: group.runningCount,
                  total: group.resources.length
                })
              }}</span>
              <AppIcon
                name="chevron"
                class="course-folder__chevron"
                :class="{ 'is-open': expandedCourseIds.has(group.id) }"
              />
            </button>
            <ResourceCards
              v-if="expandedCourseIds.has(group.id)"
              :resources="group.resources"
              :tunnels="appStore.tunnelStatus.tunnels"
              :connected="status === 'running'"
              :busy="loading"
              :view="resourceView"
              @ssh="openSsh"
              @rdp="openRdp"
            /></section
        ></template>
        <section
          v-if="
            (filter === 'all' || filter === 'personal') &&
            filteredGroups.personalResources.length
          "
          class="resource-group"
        >
          <header>
            <h2>{{ t("resources.personal.title") }}</h2>
            <span>{{
              t("workspace.machineCount", {
                count: filteredGroups.personalResources.length
              })
            }}</span>
          </header>
          <ResourceCards
            :resources="filteredGroups.personalResources"
            :tunnels="appStore.tunnelStatus.tunnels"
            :connected="status === 'running'"
            :busy="loading"
            :view="resourceView"
            @ssh="openSsh"
            @rdp="openRdp"
          />
        </section>
        <div
          v-if="!hasResults && !appStore.resourcesError"
          class="workspace-empty"
        >
          <p>
            {{ t(machineCount ? "workspace.noMatches" : "resources.empty") }}
          </p>
          <button v-if="!machineCount" class="sl-button" @click="openWeb">
            {{ t("workspace.openWeb") }}
          </button>
        </div>
      </template>
    </template>
    <div v-else class="workspace-empty">
      <AppIcon name="cloud-off" :size="36" />
      <p>{{ t("home.empty.notLoggedIn") }}</p>
    </div>
  </main>
</template>
