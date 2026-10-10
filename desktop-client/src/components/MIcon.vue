<script setup lang="ts">
/* web 端 components/MIcon.jsx 的 Vue 版：同一個 Material Icons 字型（@material-design-icons/font）、
   同樣的 prop 與 class，兩邊的圖示長得一模一樣。名稱查 https://fonts.google.com/icons */
withDefaults(
  defineProps<{
    name: string;
    size?: number;
    /** 實心款，特別強調時才用 */
    filled?: boolean;
    /** 處理中的 autorenew／refresh 轉圈（.micon-spin 在 web 的 global.scss） */
    spin?: boolean;
  }>(),
  { size: 20, filled: false, spin: false }
);
</script>

<template>
  <span
    :class="[
      filled ? 'material-icons' : 'material-icons-outlined',
      { 'micon-spin': spin }
    ]"
    :style="{ fontSize: `${size}px`, lineHeight: 1 }"
    aria-hidden="true"
    >{{ name }}</span
  >
</template>
