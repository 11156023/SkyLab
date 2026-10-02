<script lang="ts" setup>
import { IconifyIconOffline } from "@/components/IconifyIcon";
import router from "@/router";
import { useAppStore } from "@/store/app";
import { computed, defineComponent, onMounted, ref } from "vue";
import { RouteRecordRaw } from "vue-router";
import pkg from "../../../package.json";

defineComponent({
  name: "LeftMenu"
});

const appStore = useAppStore();
const routes = ref<Array<RouteRecordRaw>>([]);
const currentRoute = computed(() => router.currentRoute.value);
const statusBadge = computed(() => {
  if (!appStore.loggedIn) return { type: "info", label: "offline" };
  if (appStore.tunnelStatus.running) return { type: "success", label: "on" };
  return { type: "warning", label: "off" };
});

const handleMenuChange = (route: any) => {
  if (currentRoute.value.name === route.name) return;
  router.push({ path: route.path });
};

const handleOpenAbout = () => {
  router.push({ name: "About" });
};

onMounted(() => {
  routes.value = router.options.routes
    .find(r => r.name === "Index")
    ?.children?.filter(f => !f.meta?.hidden) as Array<RouteRecordRaw>;
});
</script>

<template>
  <div class="drop-shadow-xl left-menu-container">
    <div class="logo-container">
      <img src="/logo/only/128x128.png" class="logo" alt="Logo" />
    </div>
    <ul class="menu-container">
      <li
        v-for="r in routes"
        :key="r.name"
        class="menu"
        :class="currentRoute?.name === r.name ? 'menu-selected' : ''"
        @click="handleMenuChange(r)"
      >
        <IconifyIconOffline :icon="r?.meta?.icon as string" />
      </li>
    </ul>
    <div class="mb-2 menu-footer">
      <div class="flex flex-col gap-1 justify-center items-center text-[12px]">
        <span
          class="footer-status-dot"
          :class="{
            'footer-status-dot--success': statusBadge.type === 'success',
            'footer-status-dot--danger': statusBadge.type === 'warning',
            'footer-status-dot--muted': statusBadge.type === 'info'
          }"
        />
      </div>
      <div class="version" @click="handleOpenAbout">
        {{ pkg.version }}
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.footer-status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--color-status-neutral);
}

.footer-status-dot--success {
  background: var(--color-success);
}

.footer-status-dot--danger {
  background: var(--color-danger);
}

.footer-status-dot--muted {
  background: var(--color-status-neutral);
}
</style>
