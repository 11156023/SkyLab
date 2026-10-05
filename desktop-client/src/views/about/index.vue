<script lang="ts" setup>
import Breadcrumb from "@/layout/compoenets/Breadcrumb.vue";
import { send } from "@/utils/ipcUtils";
import { defineComponent } from "vue";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";
import pkg from "../../../package.json";
import PixelOcto from "@/components/PixelOcto.vue";

defineComponent({ name: "About" });

const { t } = useI18n();

/* 建置時由 vite.config.mts 注入：授權、原始碼網址、直接依賴的授權清單 */
const about = __SKYLAB_ABOUT__;
const repositoryHost = about.repository.replace(/^https?:\/\//, "");

const openAppData = () => send(ipcRouters.SYSTEM.openAppData);
const openRepository = () =>
  send(ipcRouters.SYSTEM.openUrl, { url: about.repository });
const openLicense = () =>
  send(ipcRouters.SYSTEM.openUrl, {
    url: `${about.repository.replace(/\/$/, "")}/blob/main/LICENSE`
  });
const openThirdPartyNotices = () =>
  send(ipcRouters.SYSTEM.openThirdPartyNotices);
</script>

<template>
  <div class="main">
    <breadcrumb />
    <div class="app-container-breadcrumb">
      <div class="page-surface about-surface">
        <PixelOcto :scale="5" class="about-logo" />
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

        <dl class="about-meta">
          <dt>{{ t("about.license") }}</dt>
          <dd>
            <el-link type="primary" :underline="false" @click="openLicense">
              {{ t("about.licenseName") }}
            </el-link>
            <div class="about-hint">{{ t("about.licenseHint") }}</div>
          </dd>
          <dt>{{ t("about.repository") }}</dt>
          <dd>
            <el-link type="primary" :underline="false" @click="openRepository">
              {{ repositoryHost }}
            </el-link>
          </dd>
        </dl>

        <div class="about-actions">
          <el-button size="small" @click="openThirdPartyNotices">
            {{ t("about.thirdPartyNotices") }}
          </el-button>
          <el-button size="small" @click="openAppData">
            {{ t("about.openDataDir") }}
          </el-button>
        </div>

        <div class="about-components">
          <div class="about-components-title">
            {{ t("about.components.title") }}
          </div>
          <div class="about-hint">
            {{
              t("about.components.hint", { count: about.dependencies.length })
            }}
          </div>
          <el-table :data="about.dependencies" size="small" max-height="260">
            <el-table-column
              prop="name"
              :label="t('about.components.package')"
              min-width="160"
            >
              <template #default="{ row }">
                <el-link
                  v-if="row.repository"
                  type="primary"
                  :underline="false"
                  @click="
                    send(ipcRouters.SYSTEM.openUrl, { url: row.repository })
                  "
                >
                  {{ row.name }}
                </el-link>
                <span v-else>{{ row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column
              prop="version"
              :label="t('about.components.version')"
              width="110"
            />
            <el-table-column
              prop="license"
              :label="t('about.components.license')"
              width="110"
            />
          </el-table>
        </div>
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
  margin-bottom: 8px;
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

.about-meta {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 6px 16px;
  width: 100%;
  max-width: 520px;
  margin: 8px 0 0;
  text-align: left;
  font-size: 13px;

  dt {
    color: var(--color-text-secondary);
  }

  dd {
    margin: 0;
    color: var(--color-text-primary);
  }
}

.about-hint {
  margin-top: 2px;
  color: var(--color-text-muted);
  font-size: 12px;
  line-height: 1.5;
}

.about-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.about-components {
  width: 100%;
  max-width: 520px;
  margin-top: 8px;
  text-align: left;
}

.about-components-title {
  color: var(--color-text-primary);
  font-size: 14px;
  font-weight: 600;
}
</style>
