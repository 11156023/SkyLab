<script lang="ts" setup>
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import sb from "@web/components/Sidebar/Sidebar.module.scss";
import { useAppStore } from "@/store/app";
import { describeError } from "@/utils/errors";
import { send } from "@/utils/ipcUtils";
import { ipcRouters } from "../../electron/core/IpcRouter";
import AppDialog from "@/components/AppDialog.vue";
import MIcon from "@/components/MIcon.vue";
import PixelOcto from "@/components/PixelOcto.vue";
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
      : tunnel.value.connected
        ? t("home.status.running")
        : tunnel.value.handshakeUnavailable
          ? t("workspace.tunnelActive")
          : t("workspace.waitingGateway")
);
const openWeb = () =>
  send(ipcRouters.SYSTEM.openUrl, { url: store.backendUrl });
/* 側欄項目直接套 web Sidebar 的 .navItem／.active，選中＝主色實底白字 */
const navClass = (name: string) => [
  sb.navItem,
  { [sb.active]: route.name === name }
];
</script>
<template>
  <div class="desktop-shell">
    <aside class="desktop-sidebar" :aria-label="t('workspace.navigation')">
      <div :class="sb.brand">
        <PixelOcto :scale="2" />
        <div :class="sb.brandText">
          SkyLab<small class="desktop-brand-sub">CONNECT</small>
        </div>
      </div>
      <div :class="sb.divider" />
      <nav class="desktop-nav-list">
        <router-link :class="navClass('Home')" :to="{ name: 'Home' }">
          <MIcon name="dns" />{{ t("resources.webTitle") }}
        </router-link>
        <button type="button" :class="sb.navItem" @click="detailsOpen = true">
          <MIcon name="network_check" />{{ t("workspace.connectionInfo") }}
        </button>
        <button type="button" :class="sb.navItem" @click="openWeb">
          <MIcon name="open_in_new" />{{ t("workspace.openWeb") }}
        </button>
      </nav>
      <div class="desktop-sidebar-bottom">
        <div :class="sb.divider" />
        <nav class="desktop-nav-list">
          <router-link :class="navClass('Config')" :to="{ name: 'Config' }">
            <MIcon name="settings" />{{ t("router.config.title") }}
            <i
              v-if="store.updateInfo?.updateAvailable"
              class="update-dot"
              :aria-label="t('update.available')"
            />
          </router-link>
          <router-link :class="navClass('About')" :to="{ name: 'About' }">
            <MIcon name="info" />{{ t("router.about.title") }}
          </router-link>
        </nav>
        <div class="desktop-account">
          <MIcon
            :name="store.loggedIn ? 'verified_user' : 'shield'"
            :size="18"
          />
          {{
            t(
              store.loggedIn
                ? "config.account.loggedIn"
                : "config.account.notLoggedIn"
            )
          }}
        </div>
      </div>
    </aside>
    <div class="desktop-main"><router-view /></div>
    <AppDialog
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
                : t(
                    tunnel.handshakeUnavailable
                      ? "workspace.handshakeUnavailable"
                      : "workspace.noHandshake"
                  )
            }}
          </dd>
        </div>
      </dl>
      <p
        v-if="tunnel.connectionError || tunnel.leaseRefreshError"
        class="sl-notice sl-notice--pending connection-dialog-notice"
      >
        <MIcon name="warning_amber" :size="18" />
        {{
          tunnel.connectionError
            ? describeError(tunnel.connectionErrorCode)
            : t("home.status.leaseRefreshFailed")
        }}
      </p>
      <template #footer>
        <button
          type="button"
          class="sl-btn-primary"
          @click="detailsOpen = false"
        >
          {{ t("workspace.close") }}
        </button>
      </template>
    </AppDialog>
  </div>
</template>

<style scoped lang="scss">
.connection-dialog-notice {
  margin-top: $spacing-16;
}
</style>
