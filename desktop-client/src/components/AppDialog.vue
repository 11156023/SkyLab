<script setup lang="ts">
import { useI18n } from "vue-i18n";
import MIcon from "./MIcon.vue";

/* 桌面端共用對話框，比照 web 的 components/Modal：
   置於畫面正中、標題前可放狀態圖示、× 用 web 同款的 close 圖示與 btn-dialog-close；
   ×、Esc、點遮罩都走同一個關閉流程（給了 beforeClose 就交給它決定，例如工作階段提醒的「稍後再說」） */
const props = withDefaults(
  defineProps<{
    title: string;
    width?: string;
    beforeClose?: () => void;
  }>(),
  { width: "480px", beforeClose: undefined }
);
const open = defineModel<boolean>({ required: true });
const { t } = useI18n();

const close = () => {
  if (props.beforeClose) props.beforeClose();
  else open.value = false;
};
</script>

<template>
  <el-dialog
    v-model="open"
    :width="width"
    align-center
    :show-close="false"
    :before-close="close"
  >
    <template #header>
      <span v-if="$slots.icon" class="sl-dialog__icon"
        ><slot name="icon"
      /></span>
      <span class="el-dialog__title">{{ title }}</span>
      <button
        type="button"
        class="sl-dialog__close"
        :aria-label="t('workspace.close')"
        :title="t('workspace.close')"
        @click="close"
      >
        <MIcon name="close" :size="20" />
      </button>
    </template>
    <slot />
    <template v-if="$slots.footer" #footer><slot name="footer" /></template>
  </el-dialog>
</template>
