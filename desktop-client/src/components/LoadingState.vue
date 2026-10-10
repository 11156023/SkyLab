<script setup lang="ts">
import styles from "@web/components/LoadingState/LoadingState.module.scss";
import { useI18n } from "vue-i18n";

/* web 端 LoadingState 的 Vue 版，直接吃 web 的樣式檔：3D 方塊堆疊動畫＋一行字 */
const props = withDefaults(
  defineProps<{ text?: string; fullPage?: boolean }>(),
  {
    text: undefined,
    fullPage: false
  }
);
const { t } = useI18n();
const BOXES = 8;
</script>

<template>
  <div
    :class="[styles.wrap, { [styles.wrapFull]: props.fullPage }]"
    role="status"
    aria-live="polite"
  >
    <div :class="styles.loader" aria-hidden="true">
      <div
        v-for="index in BOXES"
        :key="index"
        :class="[styles.box, styles[`box${index - 1}`]]"
      >
        <div />
      </div>
      <div :class="styles.ground"><div /></div>
    </div>
    <span :class="styles.text">{{ props.text ?? t("common.loading") }}</span>
  </div>
</template>
