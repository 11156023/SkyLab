<script lang="ts" setup>
import router from "@/router";
import { useAppStore } from "@/store/app";
import { describeError, NOT_LOGGED_IN, toastError } from "@/utils/errors";
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
import EmptyState from "@/components/EmptyState.vue";
import LoadingState from "@/components/LoadingState.vue";
import MIcon from "@/components/MIcon.vue";
import PageHeader from "@/components/PageHeader.vue";
import SegmentedControl from "@/components/SegmentedControl.vue";
import { resolvedTheme, resourceView, toggleTheme } from "@/utils/appearance";

defineOptions({ name: "Home" });

const { t } = useI18n();
const appStore = useAppStore();
const loading = ref(false);
const authenticating = ref(false);
const refreshing = ref(false);
/** 首頁操作（登入、連線、更新授權）失敗的錯誤碼；login＝登入流程，顯示時加上「登入失敗」 */
const operationError = ref<{ code: string; login?: boolean } | null>(null);
const query = ref("");
const expandedCourseIds = ref<Set<string>>(new Set());
const toggleCourse = (id: string) => {
  const next = new Set(expandedCourseIds.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expandedCourseIds.value = next;
};
const stopping = ref(false);
const filters = ["all", "course", "practice", "personal"] as const;
const filter = ref<(typeof filters)[number]>("all");
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
/* 分類與檢視切換都是互斥選項：用 web 的 SegmentedControl，數量放徽章 */
const filterOptions = computed(() =>
  filters.map(item => ({
    value: item,
    label: t(`workspace.${item}`),
    badge: counts.value[item]
  }))
);
const viewOptions = computed(() => [
  { value: "grid" as const, icon: "grid_view", ariaLabel: t("workspace.grid") },
  { value: "list" as const, icon: "view_list", ariaLabel: t("workspace.list") }
]);
const clearFilters = () => {
  query.value = "";
  filter.value = "all";
};
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
const displayedError = computed(() => {
  const failure = operationError.value;
  if (failure) {
    const reason = describeError(failure.code);
    return failure.login ? t("login.failure", { error: reason }) : reason;
  }
  const { connectionError, connectionErrorCode } = appStore.tunnelStatus;
  return connectionError ? describeError(connectionErrorCode) : "";
});
/** 操作失敗顯示在連線卡下方；登入失效由 store 統一提示一次，這裡不重複 */
const showOperationError = (
  code: string,
  options: { login?: boolean; toast?: boolean } = {}
) => {
  if (code === NOT_LOGGED_IN) return;
  operationError.value = { code, login: options.login };
  if (options.toast) ElMessage.error(displayedError.value);
};
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
      operationError.value = null;
      appStore.refreshResources();
    }
  }
);

const startTunnel = () => {
  if (stopping.value) return;
  loading.value = true;
  authenticating.value = false;
  operationError.value = null;
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
  operationError.value = null;
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
    showOperationError(payload.errorCode || "B1000", {
      login: true,
      toast: true
    });
  }
};

const handleDisconnect = () => {
  if (loading.value) return;
  stopping.value = true;
  loading.value = true;
  operationError.value = null;
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
    code => {
      loading.value = false;
      authenticating.value = false;
      appStore.loginInProgress = false;
      showOperationError(code, { login: true, toast: true });
    }
  );
  on(
    ipcRouters.TUNNEL.start,
    () => {
      loading.value = false;
      send(ipcRouters.TUNNEL.getStatus);
    },
    code => {
      loading.value = false;
      showOperationError(code);
    }
  );
  on(
    ipcRouters.TUNNEL.refresh,
    (data: TunnelStatusInfo) => {
      refreshing.value = false;
      if (data) appStore.tunnelStatus = data;
      appStore.refreshResources();
    },
    code => {
      refreshing.value = false;
      showOperationError(code, { toast: true });
    }
  );
  on(
    ipcRouters.TUNNEL.stop,
    () => {
      stopping.value = false;
      loading.value = false;
      send(ipcRouters.TUNNEL.getStatus);
    },
    code => {
      stopping.value = false;
      loading.value = false;
      toastError(code);
      /* 中斷失敗時主程序記下原因，馬上拉狀態讓連線卡顯示 */
      send(ipcRouters.TUNNEL.getStatus);
    }
  );
  on(ipcRouters.TUNNEL.getStatus, (data: TunnelStatusInfo) => {
    if (data) appStore.tunnelStatus = data;
  });
  /* 開 SSH／RDP 只在失敗時回饋，用預設的錯誤 toast */
  on(ipcRouters.SYSTEM.openSsh, () => undefined);
  on(ipcRouters.SYSTEM.openRdp, () => undefined);

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
    <PageHeader :title="t('resources.webTitle')">
      <button
        type="button"
        class="sl-btn-secondary"
        :disabled="refreshing || appStore.resourcesLoading || loading"
        @click="refresh"
      >
        <MIcon
          name="refresh"
          :size="16"
          :spin="refreshing || appStore.resourcesLoading"
        />{{ t("common.refresh") }}
      </button>
      <button
        type="button"
        class="sl-btn-icon-secondary"
        :aria-label="t('workspace.toggleTheme')"
        :title="t('workspace.toggleTheme')"
        @click="toggleTheme"
      >
        <MIcon
          :name="resolvedTheme === 'dark' ? 'light_mode' : 'dark_mode'"
          :size="18"
        />
      </button>
    </PageHeader>

    <section
      class="connection-banner"
      :class="{ 'connection-banner--error': !!displayedError }"
      :aria-label="t('workspace.connectionInfo')"
      aria-live="polite"
    >
      <MIcon
        :name="loading ? 'autorenew' : 'shield'"
        :size="24"
        :spin="loading"
        :class="{
          'is-running':
            status === 'running' && !!appStore.tunnelStatus.connected
        }"
      />
      <div class="connection-copy">
        <h2>{{ connectionTitle }}</h2>
        <p v-if="connectionHint">{{ connectionHint }}</p>
      </div>
      <button
        type="button"
        :class="status === 'running' ? 'sl-btn-secondary' : 'sl-btn-primary'"
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

    <p v-if="displayedError" class="sl-notice sl-notice--danger" role="alert">
      <MIcon name="error_outline" :size="18" />{{ displayedError }}
    </p>
    <p
      v-if="appStore.tunnelStatus.leaseRefreshError && status === 'running'"
      class="sl-notice sl-notice--pending"
    >
      <MIcon name="warning_amber" :size="18" />{{
        t("home.status.leaseRefreshFailed")
      }}
    </p>
    <p v-if="appStore.resourcesErrorCode" class="sl-notice sl-notice--danger">
      <MIcon name="error_outline" :size="18" />
      <span
        >{{ t("workspace.resourceError")
        }}<small>{{ describeError(appStore.resourcesErrorCode) }}</small></span
      >
    </p>
    <p
      v-if="
        status === 'running' &&
        !appStore.tunnelStatus.tunnels.length &&
        !appStore.resourcesLoading
      "
      class="sl-notice sl-notice--pending"
    >
      <MIcon name="warning_amber" :size="18" />{{
        t("home.machines.noTargets")
      }}
    </p>

    <template v-if="appStore.loggedIn">
      <div class="resource-toolbar">
        <SegmentedControl
          v-model="filter"
          :options="filterOptions"
          :label="t('workspace.filter')"
        />
        <div class="resource-toolbar__end">
          <label class="resource-search">
            <MIcon name="search" :size="18" />
            <input
              v-model="query"
              type="search"
              :placeholder="t('workspace.search')"
              :aria-label="t('workspace.search')"
            />
          </label>
          <SegmentedControl
            v-model="resourceView"
            :options="viewOptions"
            :label="t('workspace.view')"
          />
        </div>
      </div>

      <LoadingState v-if="appStore.resourcesLoading && !machineCount" />
      <template v-else>
        <section
          v-for="group in filteredFolders"
          :key="group.id"
          class="workspace-section"
        >
          <button
            type="button"
            class="course-folder__toggle"
            :aria-expanded="expandedCourseIds.has(group.id)"
            @click="toggleCourse(group.id)"
          >
            <MIcon name="folder" />
            <span class="course-folder__name">{{
              group.title || t("workspace.unnamedCourse")
            }}</span>
            <span class="course-folder__meta">{{
              t("workspace.machineCount", { count: group.resources.length })
            }}</span>
            <span class="sl-badge sl-badge--success">{{
              t("resources.course.runningCount", {
                running: group.runningCount,
                total: group.resources.length
              })
            }}</span>
            <MIcon
              name="expand_more"
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
          />
        </section>
        <section
          v-if="
            (filter === 'all' || filter === 'personal') &&
            filteredGroups.personalResources.length
          "
          class="workspace-section resource-group"
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
        <EmptyState
          v-if="!hasResults && !appStore.resourcesErrorCode && machineCount"
          icon="search_off"
          :title="t('workspace.noMatches')"
        >
          <template #action>
            <button
              type="button"
              class="sl-btn-secondary"
              @click="clearFilters"
            >
              <MIcon name="filter_alt_off" :size="16" />{{
                t("workspace.clearFilters")
              }}
            </button>
          </template>
        </EmptyState>
        <EmptyState
          v-else-if="!hasResults && !appStore.resourcesErrorCode"
          icon="dns"
          :title="t('resources.empty')"
        >
          <template #action>
            <button type="button" class="sl-btn-secondary" @click="openWeb">
              <MIcon name="open_in_new" :size="16" />{{
                t("workspace.openWeb")
              }}
            </button>
          </template>
        </EmptyState>
      </template>
    </template>
    <EmptyState v-else icon="cloud_off" :title="t('home.empty.notLoggedIn')" />
  </main>
</template>
