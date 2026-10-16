<script lang="ts" setup>
import router from "@/router";
import { useAppStore } from "@/store/app";
import { on, send } from "@/utils/ipcUtils";
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
import ResourceCards from "./ResourceCards.vue";
import AppIcon from "@/components/AppIcon.vue";
import { resourceView, toggleTheme } from "@/utils/appearance";

defineComponent({ name: "Home" });

const { t } = useI18n();
const appStore = useAppStore();
const tunnelLoading = ref(false);
const authenticating = computed(() => appStore.loginInProgress);
const loading = computed(
  () =>
    tunnelLoading.value || authenticating.value || appStore.connectionStarting
);
const disposers: Array<() => void> = [];
const listen: typeof on = (...args) => {
  const dispose = on(...args);
  disposers.push(dispose);
  return dispose;
};
const refreshing = ref(false);
const operationError = ref("");
const query = ref("");
const filter = ref("all");
const stopping = ref(false);
const filters = ["all", "course", "personal"] as const;
const filteredResources = computed(() => {
  const search = query.value.trim().toLowerCase();
  return visibleResources.value.filter(resource =>
    [
      resource.name,
      resource.ip_address,
      resource.vmid,
      resource.teaching_class_name,
      resource.course_environment_name,
      resource.owner_name
    ].some(value =>
      String(value ?? "")
        .toLowerCase()
        .includes(search)
    )
  );
});
const filteredGroups = computed(() =>
  groupResourcesByCourse(filteredResources.value)
);
const counts = computed(() => ({
  all: machineCount.value,
  course: groupedResources.value.courseGroups.reduce(
    (total, group) => total + group.resources.length,
    0
  ),
  personal: groupedResources.value.personalResources.length
}));
const hasResults = computed(
  () =>
    (filter.value !== "personal" &&
      filteredGroups.value.courseGroups.length > 0) ||
    (filter.value !== "course" &&
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
            appStore.tunnelStatus.latestHandshakeAt
              ? "workspace.connected"
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
      ? appStore.tunnelStatus.latestHandshakeAt
        ? ""
        : t("workspace.waitingGatewayHint")
      : t("home.connect.description")
);
const displayedError = computed(() => {
  if (operationError.value) return operationError.value;
  if (appStore.loginError) {
    return t("login.failure", {
      error:
        appStore.loginErrorCode === "B1002"
          ? t("login.timeout")
          : appStore.loginError
    });
  }
  return appStore.tunnelStatus.connectionError;
});
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
      tunnelLoading.value = false;
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
      if (!stopping.value) tunnelLoading.value = false;
      operationError.value = "";
      appStore.refreshResources();
    }
  }
);

const startTunnel = () => {
  if (stopping.value) return;
  tunnelLoading.value = true;
  operationError.value = "";
  send(ipcRouters.TUNNEL.start);
};

const handleConnect = () => {
  if (loading.value) return;
  if (appStore.loggedIn) {
    startTunnel();
    return;
  }

  operationError.value = "";
  appStore.startLogin();
};

const handleDisconnect = () => {
  if (loading.value) return;
  stopping.value = true;
  tunnelLoading.value = true;
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
  listen(
    ipcRouters.TUNNEL.start,
    () => {
      tunnelLoading.value = false;
    },
    (_code, message) => {
      tunnelLoading.value = false;
      operationError.value = message;
    }
  );
  listen(
    ipcRouters.TUNNEL.refresh,
    (data: TunnelStatusInfo) => {
      refreshing.value = false;
      if (data) appStore.tunnelStatus = data;
      appStore.refreshResources();
    },
    (_code, message) => {
      refreshing.value = false;
      operationError.value = message;
    }
  );
  listen(
    ipcRouters.TUNNEL.stop,
    () => {
      stopping.value = false;
      tunnelLoading.value = false;
      send(ipcRouters.TUNNEL.getStatus);
    },
    (_code, message) => {
      stopping.value = false;
      tunnelLoading.value = false;
      operationError.value = message;
    }
  );
  listen(
    ipcRouters.SYSTEM.openSsh,
    () => undefined,
    (_code, message) => ElMessage.error(message)
  );
  listen(
    ipcRouters.SYSTEM.openRdp,
    () => undefined,
    (_code, message) => ElMessage.error(message)
  );

  refresh();
  window.addEventListener("online", refreshAfterNetworkRecovery);
  if (router.currentRoute.value.query.connect === "1") {
    handleConnect();
    router.replace({ name: "Home" });
  }
});

onUnmounted(() => {
  disposers.forEach(dispose => dispose());
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
            :class="{ 'sl-spin': refreshing || appStore.resourcesLoading }"
          />{{ t("common.refresh") }}</button
        ><button
          class="sl-icon-button"
          :aria-label="t('workspace.toggleTheme')"
          :title="t('workspace.toggleTheme')"
          @click="toggleTheme"
        >
          <AppIcon name="sun" />
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
        :name="loading ? 'refresh' : 'shield'"
        :class="{
          'sl-spin': loading,
          'is-running':
            status === 'running' && !!appStore.tunnelStatus.latestHandshakeAt
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
      <button
        v-if="authenticating"
        class="sl-button"
        @click="appStore.cancelLogin()"
      >
        {{ t("login.cancelButton") }}
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
        {{ t("common.loading") }}
      </div>
      <template v-else>
        <template v-if="filter !== 'personal'"
          ><section
            v-for="group in filteredGroups.courseGroups"
            :key="group.id"
            class="resource-group"
          >
            <header>
              <h2>{{ group.title }}</h2>
              <span>{{
                t("workspace.machineCount", { count: group.resources.length })
              }}</span>
            </header>
            <ResourceCards
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
          v-if="filter !== 'course' && filteredGroups.personalResources.length"
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
      <AppIcon name="monitor" />
      <p>{{ t("home.empty.notLoggedIn") }}</p>
    </div>
  </main>
</template>
