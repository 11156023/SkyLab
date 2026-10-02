<script lang="ts" setup>
import router from "@/router";
import { computed } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{
  resources: SkyLabResource[];
}>();

const { t } = useI18n();
const rows = computed(() => props.resources);

const statusTagType = (status: string) => {
  if (status === "running") return "success";
  if (["stopped", "paused", "deleted", "deleting"].includes(status)) return "info";
  if (["scheduled", "provisioning", "starting"].includes(status)) return "warning";
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

const typeLabel = (type: string) =>
  type === "lxc" ? "LXC" : type === "qemu" ? "VM" : type;
const kindLabel = (resource: SkyLabResource) =>
  t(`resources.kind.${resource.machine_kind || "personal"}`);
const blockedReason = (resource: SkyLabResource) => {
  const rawTime =
    resource.start_blocked_reason === "window_not_started"
      ? resource.window_start_at
      : resource.window_end_at;
  const date = rawTime ? new Date(rawTime) : null;
  const time = date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : rawTime || "";
  if (resource.start_blocked_reason === "window_not_started") {
    return t("resources.window.notStarted", { time });
  }
  if (resource.start_blocked_reason === "window_ended") {
    return t("resources.window.ended", { time });
  }
  return "";
};
const goConnect = () => router.push({ name: "Home" });
</script>

<template>
  <el-table :data="rows" size="small" stripe>
    <el-table-column
      prop="name"
      :label="t('resources.table.name')"
      min-width="175"
    >
      <template #default="{ row }">
        <div class="resource-name">
          <span class="resource-icon">
            <IconifyIconOffline
              :icon="row.type === 'lxc' ? 'terminal-rounded' : 'computer'"
            />
          </span>
          <span class="resource-title">
            <strong>{{ row.name }}</strong>
            <small>
              {{ typeLabel(row.type) }} · VMID {{ row.vmid }} ·
              {{ kindLabel(row as SkyLabResource) }}
            </small>
            <small v-if="row.owner_name || row.owner_email">
              {{ t("resources.owner", { owner: row.owner_name || row.owner_email }) }}
            </small>
            <small v-if="blockedReason(row as SkyLabResource)" class="blocked-reason">
              {{ blockedReason(row as SkyLabResource) }}
            </small>
          </span>
        </div>
      </template>
    </el-table-column>
    <el-table-column :label="t('resources.table.environment')" min-width="145">
      <template #default="{ row }">
        <div class="cell-stack">
          <span>{{
            row.environment_type || t("resources.customEnvironment")
          }}</span>
          <small>{{ row.os_info || "—" }}</small>
        </div>
      </template>
    </el-table-column>
    <el-table-column :label="t('resources.table.status')" width="105">
      <template #default="{ row }">
        <el-tag size="small" :type="statusTagType(row.status)">
          {{ statusLabel(row.status) }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column
      prop="ip_address"
      :label="t('resources.table.ip')"
      min-width="135"
    >
      <template #default="{ row }">
        <span class="font-mono">{{ row.ip_address || "N/A" }}</span>
      </template>
    </el-table-column>
    <el-table-column
      prop="node"
      :label="t('resources.table.node')"
      width="100"
    />
    <el-table-column
      :label="t('home.tunnels.action')"
      width="105"
      align="right"
    >
      <template #default="{ row }">
        <el-button
          size="small"
          type="primary"
          plain
          :disabled="
            row.status !== 'running' ||
            row.can_control === false ||
            !!row.start_blocked_reason
          "
          @click="goConnect"
        >
          <IconifyIconOffline icon="settings-ethernet-rounded" />
          {{ t("resources.connect") }}
        </el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<style lang="scss" scoped>
.resource-name,
.resource-icon,
.resource-title,
.cell-stack {
  display: flex;
}

.resource-name {
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.resource-icon {
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  color: var(--color-primary);
  background: var(--color-hover);
  border-radius: 8px;
}

.resource-title,
.cell-stack {
  min-width: 0;
  flex-direction: column;
}

.resource-title strong,
.cell-stack span {
  overflow: hidden;
  color: var(--color-text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-title small,
.cell-stack small {
  margin-top: 2px;
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-title .blocked-reason {
  color: var(--el-color-danger);
}

:deep(.el-button) {
  display: inline-flex;
  gap: 5px;
  align-items: center;
}
</style>
