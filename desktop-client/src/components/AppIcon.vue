<script setup lang="ts">
import { Icon } from "@iconify/vue/dist/offline";
import cloudOff from "@iconify-icons/material-symbols/cloud-off-outline";
import darkMode from "@iconify-icons/material-symbols/dark-mode-outline";
import desktopWindows from "@iconify-icons/material-symbols/desktop-windows-outline";
import expandMore from "@iconify-icons/material-symbols/expand-more";
import folder from "@iconify-icons/material-symbols/folder-outline";
import gridView from "@iconify-icons/material-symbols/grid-view-outline";
import info from "@iconify-icons/material-symbols/info-outline";
import lightMode from "@iconify-icons/material-symbols/light-mode-outline";
import networkCheck from "@iconify-icons/material-symbols/network-check";
import openInNew from "@iconify-icons/material-symbols/open-in-new";
import progressActivity from "@iconify-icons/material-symbols/progress-activity";
import refresh from "@iconify-icons/material-symbols/refresh";
import search from "@iconify-icons/material-symbols/search";
import settings from "@iconify-icons/material-symbols/settings-outline";
import shield from "@iconify-icons/material-symbols/shield-outline";
import terminal from "@iconify-icons/material-symbols/terminal";
import viewList from "@iconify-icons/material-symbols/view-list-outline";

/* 圖示一律 Material Symbols（outlined），與 web 端 MIcon 同一套圖示語言。
   離線打包、不打網路；這裡只登記桌面端用到的，模板用語意名稱呼叫 */
const ICONS = {
  grid: gridView,
  list: viewList,
  folder,
  chevron: expandMore,
  terminal,
  monitor: desktopWindows,
  shield,
  refresh,
  search,
  info,
  external: openInNew,
  activity: networkCheck,
  sun: lightMode,
  moon: darkMode,
  settings,
  "cloud-off": cloudOff,
  spinner: progressActivity
} as const;

export type AppIconName = keyof typeof ICONS;

withDefaults(defineProps<{ name: AppIconName; size?: number }>(), {
  size: 18
});
</script>

<template>
  <Icon
    class="sl-icon"
    :icon="ICONS[name]"
    :width="size"
    :height="size"
    aria-hidden="true"
  />
</template>
