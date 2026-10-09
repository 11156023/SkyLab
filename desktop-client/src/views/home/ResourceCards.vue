<script lang="ts" setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import AppIcon from "@/components/AppIcon.vue";
import {
  canConnectResource,
  resourceTargets,
  resourceWindowBlock
} from "@/utils/resourceAccess";
const props = defineProps<{
  resources: SkyLabResource[];
  tunnels: SkyLabTunnelInfo[];
  connected: boolean;
  view: string;
  busy: boolean;
}>();
const emit = defineEmits<{
  ssh: [target: { host: string; port: number }];
  rdp: [target: { host: string; port: number }];
}>();
const { t } = useI18n();
const selected = ref<SkyLabResource | null>(null);
const selectedResource = computed(
  () =>
    props.resources.find(
      r =>
        r === selected.value ||
        (selected.value?.vmid != null && r.vmid === selected.value.vmid)
    ) || selected.value
);
const detailsOpen = ref(false);
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval>;
onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now();
  }, 1000);
});
onUnmounted(() => clearInterval(timer));
const block = (resource: SkyLabResource) =>
  resourceWindowBlock(resource, now.value);
const targets = (resource: SkyLabResource) =>
  resourceTargets(resource, props.tunnels);
const connectable = (resource: SkyLabResource, target: SkyLabTunnelInfo) =>
  !props.busy &&
  canConnectResource(resource, target, props.connected, now.value);
const reason = (resource: SkyLabResource) => {
  const blocked = block(resource);
  if (blocked) return t(`workspace.${blocked}`);
  if (resource.can_control === false) return t("workspace.readOnly");
  if (resource.status !== "running")
    return t(`resources.status.${resource.status}`);
  if (!props.connected) return t("workspace.connectFirst");
  return t("home.machines.unavailable");
};
const connect = (resource: SkyLabResource, target: SkyLabTunnelInfo) => {
  if (props.busy || !canConnectResource(resource, target, props.connected))
    return;
  const destination = { host: String(target.host), port: Number(target.port) };
  if (String(target.service).toLowerCase() === "ssh") emit("ssh", destination);
  else emit("rdp", destination);
};
const showDetails = (resource: SkyLabResource) => {
  selected.value = resource;
  detailsOpen.value = true;
};
const date = (value?: string | null) =>
  value
    ? /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? value
      : Number.isNaN(Date.parse(value))
        ? value
        : new Date(value).toLocaleString()
    : "—";
</script>
<template>
  <div
    class="resource-grid"
    :class="{ 'resource-grid--list': view === 'list' }"
  >
    <article
      v-for="(resource, index) in resources"
      :key="`${resource.vmid ?? resource.request_id ?? index}`"
      class="resource-card"
      :class="{ 'resource-card--blocked': !!block(resource) }"
    >
      <div class="resource-card-heading">
        <div
          class="resource-status"
          :class="{
            'is-running': resource.status === 'running',
            'is-failed': resource.status === 'failed'
          }"
        >
          <span class="sl-dot" />{{ t(`resources.status.${resource.status}`) }}
        </div>
        <h3>{{ resource.name }}</h3>
      </div>
      <div class="resource-address">{{ resource.ip_address || "—" }}</div>
      <div class="resource-card-actions">
        <template
          v-if="
            targets(resource).length &&
            !block(resource) &&
            resource.can_control !== false
          "
        >
          <button
            v-for="target in targets(resource)"
            :key="`${target.service}:${target.host}:${target.port}`"
            class="sl-button resource-launch"
            :disabled="!connectable(resource, target)"
            @click="connect(resource, target)"
          >
            <AppIcon
              :name="
                String(target.service).toLowerCase() === 'ssh'
                  ? 'terminal'
                  : 'monitor'
              "
              :size="16"
            />{{
              t(
                String(target.service).toLowerCase() === "ssh"
                  ? "home.tunnels.connectSsh"
                  : "home.tunnels.connectRdp"
              )
            }}
          </button>
        </template>
        <span
          v-else
          class="resource-unavailable"
          :class="{ 'is-expired': block(resource) === 'window_ended' }"
          >{{ reason(resource) }}</span
        >
        <button
          class="sl-icon-button resource-info"
          :aria-label="t('workspace.detailsFor', { name: resource.name })"
          :title="t('workspace.details')"
          @click="showDetails(resource)"
        >
          <AppIcon name="info" />
        </button>
      </div>
    </article>
  </div>
  <el-dialog
    v-model="detailsOpen"
    :title="t('workspace.details')"
    width="540px"
  >
    <dl v-if="selectedResource" class="sl-details">
      <div>
        <dt>{{ t("resources.table.name") }}</dt>
        <dd>{{ selectedResource.name }}</dd>
      </div>
      <div>
        <dt>{{ t("resources.table.vmid") }}</dt>
        <dd>{{ selectedResource.vmid ?? "—" }}</dd>
      </div>
      <div>
        <dt>{{ t("resources.table.status") }}</dt>
        <dd>{{ t(`resources.status.${selectedResource.status}`) }}</dd>
      </div>
      <div>
        <dt>{{ t("resources.table.ip") }}</dt>
        <dd>{{ selectedResource.ip_address || "—" }}</dd>
      </div>
      <div>
        <dt>{{ t("resources.table.environment") }}</dt>
        <dd>
          {{
            [
              selectedResource.type === "lxc" ? "LXC" : "VM",
              selectedResource.environment_type,
              selectedResource.os_info
            ]
              .filter(Boolean)
              .join(" · ")
          }}
        </dd>
      </div>
      <div v-if="selectedResource.node">
        <dt>{{ t("resources.table.node") }}</dt>
        <dd>{{ selectedResource.node }}</dd>
      </div>
      <div v-if="selectedResource.owner_name || selectedResource.owner_email">
        <dt>{{ t("workspace.owner") }}</dt>
        <dd>
          {{ selectedResource.owner_name || selectedResource.owner_email }}
        </dd>
      </div>
      <div v-if="selectedResource.window_start_at">
        <dt>{{ t("workspace.startsAt") }}</dt>
        <dd>{{ date(selectedResource.window_start_at) }}</dd>
      </div>
      <div
        v-if="selectedResource.window_end_at || selectedResource.expiry_date"
      >
        <dt>{{ t("resources.table.expiry") }}</dt>
        <dd>
          {{
            date(selectedResource.window_end_at || selectedResource.expiry_date)
          }}
        </dd>
      </div>
      <div
        v-if="block(selectedResource) || selectedResource.can_control === false"
      >
        <dt>{{ t("workspace.access") }}</dt>
        <dd>{{ reason(selectedResource) }}</dd>
      </div>
    </dl>
    <template #footer
      ><el-button @click="detailsOpen = false">{{
        t("workspace.close")
      }}</el-button></template
    >
  </el-dialog>
</template>
