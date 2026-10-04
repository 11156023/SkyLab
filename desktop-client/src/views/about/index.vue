<script lang="ts" setup>
import Breadcrumb from "@/layout/compoenets/Breadcrumb.vue";
import { send } from "@/utils/ipcUtils";
import { defineComponent } from "vue";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";
import pkg from "../../../package.json";

defineComponent({ name: "About" });

const { t } = useI18n();

const openAppData = () => send(ipcRouters.SYSTEM.openAppData);
</script>

<template>
  <div class="main">
    <breadcrumb />
    <div class="app-container-breadcrumb">
      <div class="page-surface about-surface">
        <img src="/logo/only/128x128.png" class="about-logo" alt="Logo" />
        <div class="about-name">{{ t("about.name") }}</div>
        <div class="about-description">
          {{ t("about.description") }}
        </div>
        <div class="about-tags">
          <el-tag size="small" type="success">{{
            t("about.features.oneClick")
          }}</el-tag>
          <el-tag size="small" type="primary">{{
            t("about.features.bundled")
          }}</el-tag>
          <el-tag size="small" type="danger">{{
            t("about.features.secure")
          }}</el-tag>
        </div>
        <div class="about-version">
          {{ t("about.version") }} v{{ pkg.version }}
        </div>
        <el-button size="small" @click="openAppData">
          {{ t("about.openDataDir") }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.about-surface {
  align-items: center;
  justify-content: center;
  text-align: center;
}

.about-logo {
  width: 88px;
  height: 88px;
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
}

.about-name {
  margin-top: 10px;
  color: var(--color-text-primary);
  font-size: 22px;
  font-weight: 700;
}

.about-description {
  max-width: 440px;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.about-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.about-version {
  color: var(--color-text-muted);
  font-size: 12px;
}
</style>
