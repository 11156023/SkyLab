<script setup lang="ts">
import styles from "@web/components/EmptyState/EmptyState.module.scss";
import MIcon from "./MIcon.vue";

/* web 端 EmptyState 的 Vue 版，直接吃 web 的樣式檔：置中圖示＋一行灰字，
   有下一步時用 action slot 帶按鈕（純資訊不加） */
withDefaults(
  defineProps<{
    icon?: string;
    iconSize?: number;
    title?: string;
    description?: string;
  }>(),
  { icon: "inbox", iconSize: 44, title: "", description: "" }
);
</script>

<template>
  <div :class="styles.empty" data-empty-state="true">
    <MIcon v-if="icon" :name="icon" :size="iconSize" :class="styles.icon" />
    <p v-if="title" :class="styles.title">{{ title }}</p>
    <p v-if="description" :class="styles.description">{{ description }}</p>
    <div v-if="$slots.action" :class="styles.action">
      <slot name="action" />
    </div>
  </div>
</template>
