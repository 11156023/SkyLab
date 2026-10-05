<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { theme } from "@/utils/appearance";
import { LAYOUT } from "../../../frontend/src/components/OctoPet/octoBrain.js";
import { mountOctoPet } from "../../../frontend/src/components/OctoPet/octoController.js";

const props = withDefaults(
  defineProps<{
    scale?: number;
    activity?: "idle" | "thinking" | "done" | "error";
    quiet?: boolean;
  }>(),
  { scale: 2, activity: "idle", quiet: true }
);
const root = ref<HTMLElement | null>(null);
const canvas = ref<HTMLCanvasElement | null>(null);
let controller: ReturnType<typeof mountOctoPet> | null = null;

const mount = () => {
  controller?.destroy();
  if (root.value && canvas.value) {
    controller = mountOctoPet(canvas.value, root.value, {
      scale: props.scale,
      activity: props.activity,
      quiet: props.quiet
    });
  }
};

onMounted(mount);
onBeforeUnmount(() => controller?.destroy());
watch(
  () => props.activity,
  activity => controller?.setActivity(activity)
);
watch([() => props.scale, () => props.quiet, theme], mount);
</script>

<template>
  <span
    ref="root"
    class="pixel-octo"
    :style="{ width: `${20 * scale}px`, height: `${16 * scale}px` }"
    aria-hidden="true"
  >
    <canvas
      ref="canvas"
      class="pixel-octo__canvas"
      :style="{
        left: `${-LAYOUT.OX * scale}px`,
        top: `${-LAYOUT.OY * scale}px`,
        width: `${LAYOUT.CW * scale}px`,
        height: `${LAYOUT.CH * scale}px`
      }"
    />
  </span>
</template>

<style>
.pixel-octo {
  --octo-cap: #252c48;
  --octo-lid: #252c48;
  --octo-fx: #5471bf;
  --octo-heart: #ec7aa2;
  --octo-ink: #232a4d;
  --octo-shadow: rgba(30, 46, 100, 0.18);
  --octo-bubble-line: #202748;
  --octo-bubble-fill: #ffffff;
  --octo-bubble-dot: #202748;
  position: relative;
  display: inline-block;
  flex: none;
  vertical-align: middle;
}
html[data-theme="dark"] .pixel-octo {
  --octo-cap: #3a4470;
  --octo-lid: #3a4470;
  --octo-fx: #89a5e0;
  --octo-heart: #f08fb2;
  --octo-ink: #090c17;
  --octo-shadow: rgba(0, 0, 0, 0.38);
  --octo-bubble-line: #0d1122;
  --octo-bubble-fill: #e6ecf8;
  --octo-bubble-dot: #202748;
}
.pixel-octo__canvas {
  position: absolute;
  display: block;
  image-rendering: pixelated;
  pointer-events: none;
}
</style>
