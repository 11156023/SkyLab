<script lang="ts" setup>
import { send } from "@/utils/ipcUtils";
import { useI18n } from "vue-i18n";
import { ipcRouters } from "../../../electron/core/IpcRouter";
import pkg from "../../../package.json";
import MIcon from "@/components/MIcon.vue";
import PageHeader from "@/components/PageHeader.vue";
import PixelOcto from "@/components/PixelOcto.vue";

defineOptions({ name: "About" });

const { t } = useI18n();

/* 建置時由 vite.config.mts 注入：授權、原始碼網址、直接依賴的授權清單 */
const about = __SKYLAB_ABOUT__;
const repositoryHost = about.repository.replace(/^https?:\/\//, "");
const features = [
  { key: "about.features.oneClick", icon: "bolt" },
  { key: "about.features.bundled", icon: "vpn_lock" },
  { key: "about.features.secure", icon: "verified_user" }
];

const openUrl = (url: string) => send(ipcRouters.SYSTEM.openUrl, { url });
const openAppData = () => send(ipcRouters.SYSTEM.openAppData);
const openLicense = () =>
  openUrl(`${about.repository.replace(/\/$/, "")}/blob/main/LICENSE`);
const openThirdPartyNotices = () =>
  send(ipcRouters.SYSTEM.openThirdPartyNotices);
</script>

<template>
  <main class="workspace-page">
    <PageHeader :title="t('router.about.title')" />

    <!-- 寬視窗兩欄：左邊是 App 名片，右邊是授權與開源元件；窄視窗疊成一欄 -->
    <div class="about-grid">
      <section class="sl-card about-hero">
        <PixelOcto :scale="5" />
        <h2 class="about-name">{{ t("about.name") }}</h2>
        <span class="sl-badge sl-badge--muted">v{{ pkg.version }}</span>
        <p class="about-description">{{ t("about.description") }}</p>
        <ul class="about-features">
          <li v-for="feature in features" :key="feature.key">
            <MIcon :name="feature.icon" :size="18" />{{ t(feature.key) }}
          </li>
        </ul>
        <button
          type="button"
          class="sl-btn-secondary about-data"
          @click="openAppData"
        >
          <MIcon name="folder_open" :size="16" />{{ t("about.openDataDir") }}
        </button>
      </section>

      <div class="about-side">
        <section class="sl-card">
          <div class="sl-card__header license-header">
            <h2 class="sl-card__title">{{ t("about.licenseTitle") }}</h2>
            <button
              type="button"
              class="sl-btn-secondary"
              @click="openThirdPartyNotices"
            >
              <MIcon name="description" :size="16" />{{
                t("about.thirdPartyNotices")
              }}
            </button>
          </div>
          <dl class="about-meta">
            <div>
              <dt>{{ t("about.license") }}</dt>
              <dd>
                <button type="button" class="sl-link" @click="openLicense">
                  {{ t("about.licenseName") }}
                </button>
              </dd>
            </div>
            <div>
              <dt>{{ t("about.repository") }}</dt>
              <dd>
                <button
                  type="button"
                  class="sl-link"
                  @click="openUrl(about.repository)"
                >
                  {{ repositoryHost }}
                </button>
              </dd>
            </div>
          </dl>
          <p class="about-hint">{{ t("about.licenseHint") }}</p>
        </section>

        <section class="sl-card">
          <div>
            <h2 class="sl-card__title">{{ t("about.components.title") }}</h2>
            <p class="sl-card__hint">
              {{
                t("about.components.hint", { count: about.dependencies.length })
              }}
            </p>
          </div>
          <div class="components-table">
            <table class="sl-table">
              <thead>
                <tr>
                  <th>{{ t("about.components.package") }}</th>
                  <th>{{ t("about.components.version") }}</th>
                  <th>{{ t("about.components.license") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="dep in about.dependencies" :key="dep.name">
                  <td>
                    <button
                      v-if="dep.repository"
                      type="button"
                      class="sl-link"
                      @click="openUrl(dep.repository)"
                    >
                      {{ dep.name }}
                    </button>
                    <span v-else>{{ dep.name }}</span>
                  </td>
                  <td class="cell-mono">{{ dep.version }}</td>
                  <td>
                    <span class="sl-badge sl-badge--muted">{{
                      dep.license || "—"
                    }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  </main>
</template>

<style lang="scss" scoped>
/* 標題只有一行：跟右邊的按鈕垂直置中（軟體更新卡片有副標，才用靠上對齊） */
.license-header {
  align-items: center;
}

.about-grid,
.about-side {
  @include flex-column;
  gap: $spacing-24;
}

/* 內容區夠寬才分兩欄（.desktop-main 是 container）；名片固定寬、右欄吃剩下的 */
@container (min-width: 880px) {
  .about-grid {
    display: grid;
    grid-template-columns: minmax(280px, 340px) minmax(0, 1fr);
    align-items: start;
  }
}

.about-hero {
  align-items: center;
  gap: 12px;
  padding-block: 32px;
  text-align: center;
}

.about-name {
  margin-top: $spacing-8;
  color: var(--color-text-primary);
  font-size: $font-size-18;
  font-weight: $font-weight-700;
}

.about-description {
  max-width: 280px;
  color: var(--color-text-secondary);
  font-size: $font-size-14;
}

/* 三個賣點：圖示＋一行字，比三顆徽章好讀 */
.about-features {
  @include flex-column;
  gap: $spacing-8;
  width: 100%;
  padding: 12px $spacing-16;
  border: 1px solid var(--color-border);
  border-radius: $radius-12;
  background: var(--color-surface);
  max-width: 360px;
  text-align: left;

  li {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--color-text-primary);
    font-size: $font-size-14;
  }

  .material-icons-outlined {
    color: var(--color-primary-on-surface);
  }
}

.about-data {
  margin-top: $spacing-4;
}

/* 授權與原始碼排同一列（標籤＋連結成對），視窗太窄放不下兩組時自然換行；
   AGPL 說明是兩者共同的補充，獨立放在下一行，不擠在半邊欄位裡 */
.about-meta {
  display: flex;
  flex-wrap: wrap;
  gap: $spacing-8 40px;

  > div {
    display: flex;
    align-items: baseline;
    gap: 12px;
    min-width: 0;
  }

  dt {
    color: var(--color-text-muted);
    font-size: $font-size-14;
  }

  dd {
    color: var(--color-text-primary);
    font-size: $font-size-14;
  }
}

.about-hint {
  margin-top: -$spacing-8;
  color: var(--color-text-muted);
  font-size: $font-size-12;
  line-height: $line-height-base;
}

/* 內容區塊白底細框（玻璃卡內容區塊規範） */
.components-table {
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: $radius-8;
  background: var(--color-surface);
}

.cell-mono {
  color: var(--color-text-secondary);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 13px;
}
</style>
