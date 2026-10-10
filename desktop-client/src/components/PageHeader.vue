<script setup lang="ts">
import styles from "@web/components/PageHeader/PageHeader.module.scss";

/* web 端 PageHeader 的 Vue 版，直接吃 web 的樣式檔：左側標題（＋副標），右側 slot 放動作按鈕 */
defineProps<{ title: string; subtitle?: string }>();
</script>

<template>
  <div :class="styles.pageHeader">
    <div :class="styles.pageHeading">
      <div :class="styles.titleRow">
        <h1 :class="styles.pageTitle">{{ title }}</h1>
      </div>
      <p v-if="subtitle" :class="styles.pageSubtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.default" :class="styles.pageActions">
      <slot />
    </div>
  </div>
</template>
