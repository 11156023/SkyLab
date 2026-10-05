<script lang="ts" setup>
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { useAppStore } from "@/store/app";
import { send } from "@/utils/ipcUtils";
import { ipcRouters } from "../../electron/core/IpcRouter";
import AppIcon from "@/components/AppIcon.vue";
import "@/components/IconifyIcon/src/offlineIcon";
import "@/utils/appearance";

const { t } = useI18n();
const store = useAppStore();
const route = useRoute();
const detailsOpen = ref(false);
const tunnel = computed(() => store.tunnelStatus);
const connectionLabel = computed(() =>
  tunnel.value.connectionError
    ? t("home.status.error")
    : !tunnel.value.running
      ? t("home.status.stopped")
      : tunnel.value.latestHandshakeAt
        ? t("home.status.running")
        : t("workspace.waitingGateway")
);
const openWeb = () =>
  send(ipcRouters.SYSTEM.openUrl, { url: store.backendUrl });
</script>
<template>
  <div class="desktop-shell">
    <aside class="desktop-sidebar" :aria-label="t('workspace.navigation')">
      <div class="desktop-brand">
        <img src="/logo/only/128x128.png" alt="SkyLab" />
        <div><strong>SkyLab</strong><small>CONNECT</small></div>
      </div>
      <router-link
        class="desktop-nav"
        :class="{ active: route.name === 'Home' }"
        :to="{ name: 'Home' }"
        ><AppIcon name="grid" /><span>{{
          t("resources.webTitle")
        }}</span></router-link
      >
      <button class="desktop-nav" @click="detailsOpen = true">
        <AppIcon name="activity" /><span>{{
          t("workspace.connectionInfo")
        }}</span>
      </button>
      <button class="desktop-nav" @click="openWeb">
        <AppIcon name="external" /><span>{{ t("workspace.openWeb") }}</span>
      </button>
      <div class="desktop-sidebar-bottom">
        <router-link
          class="desktop-nav"
          :class="{ active: route.name === 'Config' }"
          :to="{ name: 'Config' }"
          ><AppIcon name="settings" /><span>{{
            t("router.config.title")
          }}</span></router-link
        >
        <router-link
          class="desktop-nav"
          :class="{ active: route.name === 'About' }"
          :to="{ name: 'About' }"
          ><AppIcon name="info" /><span>{{
            t("router.about.title")
          }}</span></router-link
        >
        <div class="desktop-account">
          <AppIcon name="shield" /><span>{{
            t(
              store.loggedIn
                ? "config.account.loggedIn"
                : "config.account.notLoggedIn"
            )
          }}</span>
        </div>
      </div>
    </aside>
    <div class="desktop-main"><router-view /></div>
    <el-dialog
      v-model="detailsOpen"
      :title="t('workspace.connectionInfo')"
      width="520px"
    >
      <dl class="sl-details">
        <div>
          <dt>{{ t("resources.table.status") }}</dt>
          <dd>{{ connectionLabel }}</dd>
        </div>
        <div>
          <dt>{{ t("config.backend.label") }}</dt>
          <dd>{{ store.backendUrl }}</dd>
        </div>
        <div>
          <dt>{{ t("workspace.protocol") }}</dt>
          <dd>WireGuard</dd>
        </div>
        <div v-if="tunnel.interfaceName">
          <dt>{{ t("workspace.interface") }}</dt>
          <dd>{{ tunnel.interfaceName }}</dd>
        </div>
        <div>
          <dt>{{ t("workspace.handshake") }}</dt>
          <dd>
            {{
              tunnel.latestHandshakeAt
                ? new Date(tunnel.latestHandshakeAt).toLocaleString()
                : t("workspace.noHandshake")
            }}
          </dd>
        </div>
      </dl>
      <el-alert
        v-if="tunnel.connectionError || tunnel.leaseRefreshError"
        :title="tunnel.connectionError || tunnel.leaseRefreshError || ''"
        type="warning"
        :closable="false"
      />
      <template #footer
        ><el-button @click="detailsOpen = false">{{
          t("workspace.close")
        }}</el-button></template
      >
    </el-dialog>
  </div>
</template>
