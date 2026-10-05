<script setup lang="ts">
defineProps<{ name: string }>();
const paths: Record<string, string> = {
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  list: "M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1",
  folder: "M3 6h7l2 2h9v11H3z",
  chevron: "m6 9 6 6 6-6",
  terminal: "M3 4h18v16H3zM7 9l3 3-3 3m6 0h4",
  monitor: "M3 4h18v13H3zM8 21h8m-4-4v4",
  shield: "m12 3 8 3v6c0 4-5 8-8 9-3-1-8-5-8-9V6zm-4 9 3 3 5-6",
  refresh: "M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5",
  search: "M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0zm-2 5 6 6",
  info: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zm-9-1v6m0-10v.1",
  external: "M14 3h7v7m0-7L10 14m0-9H3v16h16v-7",
  activity: "M2 12h4l3-8 6 16 3-8h4",
  sun: "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0zm-4-10v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1",
  settings:
    "m9 3-1 3-3 1-2 4 2 3v3l4 3 3-1 3 1 4-3v-3l2-3-2-4-3-1-1-3zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"
};
</script>
<template>
  <svg
    class="sl-icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.info" />
  </svg>
</template>
