<script lang="ts" setup>
import { computed } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{
  resources: SkyLabResource[];
  tunnels: SkyLabTunnelInfo[];
}>();

const emit = defineEmits<{
  ssh: [target: { host: string; port: number }];
  rdp: [target: { host: string; port: number }];
}>();

const { t } = useI18n();
const rows = computed(() => props.resources);

const statusTagType = (status: string) => {
  if (status === "running") return "success";
  if (["stopped", "paused", "deleted", "deleting"].includes(status))
    return "info";
  if (["scheduled", "provisioning", "starting"].includes(status))
    return "warning";
  return "danger";
};

const statusLabel = (status: string) => {
  const labels: Record<string, string> = {
    running: t("resources.status.running"),
    stopped: t("resources.status.stopped"),
    paused: t("resources.status.paused"),
    scheduled: t("resources.status.scheduled"),
    provisioning: t("resources.status.provisioning"),
    starting: t("resources.status.starting"),
    deleting: t("resources.status.deleting"),
    failed: t("resources.status.failed"),
    deleted: t("resources.status.deleted"),
    unknown: t("resources.status.unknown")
  };
  return labels[status] ?? status;
};

const typeLabel = (type?: string) =>
  type === "lxc" ? "LXC" : type === "qemu" ? "VM" : type || "VM";

const kindLabel = (resource: SkyLabResource) => {
  const kind = resource.machine_kind || "personal";
  return t(`resources.kind.${kind}`);
};

const formatTime = (value?: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

const blockedReason = (resource: SkyLabResource) => {
  if (resource.start_blocked_reason === "window_not_started") {
    return t("resources.window.notStarted", {
      time: formatTime(resource.window_start_at)
    });
  }
  if (resource.start_blocked_reason === "window_ended") {
    return t("resources.window.ended", {
      time: formatTime(resource.window_end_at)
    });
  }
  return "";
};

const tunnelFor = (resource: SkyLabResource, service: string) =>
  props.tunnels.find(
    tunnel =>
      Number(tunnel.vmid) === Number(resource.vmid) &&
      String(tunnel.service).toLowerCase() === service
  );

const validTarget = (tunnel?: SkyLabTunnelInfo) => {
  const port = Number(tunnel?.port);
  return !!tunnel?.host && Number.isInteger(port) && port > 0 && port <= 65535;
};

const canConnect = (resource: SkyLabResource, tunnel?: SkyLabTunnelInfo) =>
  resource.status === "running" &&
  resource.can_control !== false &&
  !resource.start_blocked_reason &&
  validTarget(tunnel);

const connect = (service: "ssh" | "rdp", tunnel?: SkyLabTunnelInfo) => {
  if (!validTarget(tunnel)) return;
  const target = { host: String(tunnel?.host), port: Number(tunnel?.port) };
  if (service === "ssh") emit("ssh", target);
  else emit("rdp", target);
};
</script>

<template>
  <el-table :data="rows" size="small" class="machine-table">
    <el-table-column :label="t('resources.table.name')" min-width="300">
      <template #default="{ row }">
        <div class="name-cell">
          <span class="name-icon">
            <IconifyIconOffline
              :icon="row.type === 'lxc' ? 'terminal-rounded' : 'computer'"
            />
          </span>
          <span class="name-copy">
            <strong>{{ row.name }}</strong>
            <small>
              {{ typeLabel(row.type) }} · VMID {{ row.vmid }} ·
              {{ kindLabel(row as SkyLabResource) }}
              <template v-if="row.environment_type">
                · {{ row.environment_type }}
              </template>
              <template v-if="row.os_info"> · {{ row.os_info }} </template>
            </small>
            <small v-if="row.owner_name || row.owner_email">
              {{
                t("resources.owner", {
                  owner: row.owner_name || row.owner_email
                })
              }}
            </small>
            <small
              v-if="blockedReason(row as SkyLabResource)"
              class="blocked-reason"
            >
              {{ blockedReason(row as SkyLabResource) }}
            </small>
          </span>
        </div>
      </template>
    </el-table-column>

    <el-table-column :label="t('resources.table.status')" width="92">
      <template #default="{ row }">
        <el-tag size="small" :type="statusTagType(row.status)">
          {{ statusLabel(row.status) }}
        </el-tag>
      </template>
    </el-table-column>

    <el-table-column :label="t('resources.table.ip')" width="125">
      <template #default="{ row }">
        <span class="mono">{{ row.ip_address || "—" }}</span>
      </template>
    </el-table-column>

    <el-table-column
      :label="t('home.tunnels.action')"
      width="210"
      fixed="right"
      align="right"
    >
      <template #default="{ row }">
        <div class="row-actions">
          <el-button
            v-if="tunnelFor(row as SkyLabResource, 'ssh')"
            size="small"
            type="primary"
            plain
            :disabled="
              !canConnect(
                row as SkyLabResource,
                tunnelFor(row as SkyLabResource, 'ssh')
              )
            "
            class="quick-connect quick-connect--ssh"
            @click.stop="
              connect('ssh', tunnelFor(row as SkyLabResource, 'ssh'))
            "
          >
            <IconifyIconOffline icon="terminal-rounded" />
            {{ t("home.tunnels.connectSsh") }}
          </el-button>
          <el-button
            v-if="tunnelFor(row as SkyLabResource, 'rdp')"
            size="small"
            type="primary"
            plain
            :disabled="
              !canConnect(
                row as SkyLabResource,
                tunnelFor(row as SkyLabResource, 'rdp')
              )
            "
            class="quick-connect"
            @click.stop="
              connect('rdp', tunnelFor(row as SkyLabResource, 'rdp'))
            "
          >
            <IconifyIconOffline icon="desktop-windows-rounded" />
            {{ t("home.tunnels.connectRdp") }}
          </el-button>
          <span
            v-if="
              !tunnelFor(row as SkyLabResource, 'ssh') &&
              !tunnelFor(row as SkyLabResource, 'rdp')
            "
            class="action-empty"
          >
            {{
              blockedReason(row as SkyLabResource) ||
              (row.status === "running"
                ? t("home.machines.unavailable")
                : t("home.tunnels.machineStopped"))
            }}
          </span>
        </div>
      </template>
    </el-table-column>
  </el-table>
</template>

<style lang="scss" scoped>
.machine-table {
  --el-table-header-bg-color: color-mix(in srgb, var(--color-hover) 55%, white);
  --el-table-row-hover-bg-color: color-mix(
    in srgb,
    var(--color-primary) 5%,
    white
  );
  --el-table-border-color: var(--color-divider);
}

.name-cell,
.name-icon,
.name-copy,
.cell-stack,
.row-actions {
  display: flex;
}

.name-cell {
  min-width: 0;
  align-items: center;
  gap: 9px;
}

.name-icon {
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  color: var(--color-primary);
  background: var(--color-hover);
  border-radius: 8px;
}

.name-copy,
.cell-stack {
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.name-copy strong,
.cell-stack span {
  overflow: hidden;
  color: var(--color-text-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.name-copy small,
.cell-stack small,
.muted,
.action-empty {
  color: var(--color-text-muted);
  font-size: 11px;
}

.name-copy small {
  max-width: 270px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.name-copy .blocked-reason {
  color: var(--el-color-danger);
}

.mono {
  color: var(--color-text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
}

.row-actions {
  min-height: 28px;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

.row-actions :deep(.el-button) {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  margin-left: 0;
}

.row-actions :deep(.quick-connect) {
  min-width: 82px;
  height: 30px;
  padding-inline: 10px;
  font-weight: 650;
  border-radius: 8px;
}

.row-actions :deep(.quick-connect--ssh) {
  box-shadow: 0 4px 10px
    color-mix(in srgb, var(--color-primary) 22%, transparent);
}

.machine-table :deep(.el-table__fixed-right) {
  box-shadow: -8px 0 18px rgba(67, 90, 149, 0.05);
}
</style>
