<script lang="ts" setup>
import { send } from "@/utils/ipcUtils";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";
import pkg from "../../../package.json";
import PixelOcto from "@/components/PixelOcto.vue";

defineOptions({ name: "About" });

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
  <main class="workspace-page">
    <header class="workspace-heading">
      <h1>{{ t("router.about.title") }}</h1>
    </header>

    <section class="sl-card about-card">
      <PixelOcto :scale="5" />
      <div class="about-name">{{ t("about.name") }}</div>
      <p class="about-description">{{ t("about.description") }}</p>
      <div class="about-tags">
        <el-tag size="small" type="primary">
          {{ t("about.features.oneClick") }}
        </el-tag>
        <el-tag size="small" type="primary">
          {{ t("about.features.bundled") }}
        </el-tag>
        <el-tag size="small" type="primary">
          {{ t("about.features.secure") }}
        </el-tag>
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
        <button type="button" class="sl-button" @click="openThirdPartyNotices">
          {{ t("about.thirdPartyNotices") }}
        </button>
        <button type="button" class="sl-button" @click="openAppData">
          {{ t("about.openDataDir") }}
        </button>
      </div>
    </section>

    <section class="sl-card">
      <div>
        <div class="sl-card__title">{{ t("about.components.title") }}</div>
        <div class="sl-card__hint">
          {{ t("about.components.hint", { count: about.dependencies.length }) }}
        </div>
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
              @click="send(ipcRouters.SYSTEM.openUrl, { url: row.repository })"
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
    </section>
  </main>
</template>

<style lang="scss" scoped>
.about-card {
  align-items: center;
  gap: 8px;
  text-align: center;
}

.about-name {
  margin-top: 8px;
  color: var(--color-text-primary);
  font-size: 20px;
  font-weight: 700;
}

.about-description {
  max-width: 440px;
  color: var(--color-text-secondary);
}

.about-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
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

  dt {
    color: var(--color-text-secondary);
  }

  dd {
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
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
