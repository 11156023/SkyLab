<script setup lang="ts" generic="T extends string">
import styles from "@web/components/SegmentedControl/SegmentedControl.module.scss";
import MIcon from "./MIcon.vue";

/* web 端 SegmentedControl 的 Vue 版，直接吃 web 的樣式檔：互斥選項的即時切換，36px 與按鈕、欄位同高。
   只有圖示的段（如卡片／列表檢視）要給 ariaLabel，螢幕閱讀器才唸得出來 */
export interface SegmentOption<V extends string> {
  value: V;
  label?: string;
  icon?: string;
  badge?: number | string;
  ariaLabel?: string;
}

defineProps<{
  options: SegmentOption<T>[];
  /** 整組的無障礙名稱 */
  label: string;
}>();
const model = defineModel<T>({ required: true });
</script>

<template>
  <div :class="styles.segment" role="group" :aria-label="label">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      :class="[
        styles.segmentBtn,
        { [styles.segmentActive]: model === option.value }
      ]"
      :aria-pressed="model === option.value"
      :aria-label="option.ariaLabel"
      :title="option.ariaLabel"
      @click="model = option.value"
    >
      <MIcon
        v-if="option.icon"
        :name="option.icon"
        :size="option.label ? 14 : 16"
      />
      {{ option.label }}
      <span v-if="option.badge != null" :class="styles.segmentBadge">{{
        option.badge
      }}</span>
    </button>
  </div>
</template>
