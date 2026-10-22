import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./AccountSettingsPage.module.scss";
import MIcon from "../../../components/MIcon";
import DataTable from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";

/* __SKYLAB_ABOUT__ 由 vite.config.js 在建置時注入（scripts/aboutInfo.mjs）；
   測試或其他工具沒注入時退回最小資料，分頁仍能顯示授權與連結 */
const FALLBACK = {
  name: "SkyLab",
  version: "",
  commit: "",
  builtAt: "",
  license: "AGPL-3.0",
  repository: "https://github.com/ntubclass/SkyLab",
  dependencies: [],
};

export const aboutInfo =
  typeof __SKYLAB_ABOUT__ !== "undefined" && __SKYLAB_ABOUT__ ? __SKYLAB_ABOUT__ : FALLBACK;

function ExternalLink({ href, children }) {
  return (
    <a className={styles.aboutLink} href={href} target="_blank" rel="noopener noreferrer">
      {children}
      <MIcon name="open_in_new" size={14} />
    </a>
  );
}

/* 「關於」卡的外部連結列：左圖示、中間標籤＋連結名稱、右邊外連箭頭，整列可點 */
function LinkRow({ icon, label, href, title, children }) {
  return (
    <a className={styles.aboutLinkRow} href={href} target="_blank" rel="noopener noreferrer" title={title}>
      <span className={styles.aboutLinkIcon}><MIcon name={icon} size={18} /></span>
      <span className={styles.aboutLinkText}>
        <span className={styles.aboutLinkLabel}>{label}</span>
        <span className={styles.aboutLinkValue}>{children}</span>
      </span>
      <MIcon name="open_in_new" size={16} className={styles.aboutLinkGo} />
    </a>
  );
}

/* 依賴清單：欄名的翻譯 key 放 meta.labelKey，表頭元件自己取 t */
function DependencyHeader({ column }) {
  const { t } = useTranslation("personal");
  return t(column.columnDef.meta.labelKey);
}

function DependencyNameCell({ row }) {
  const dep = row.original;
  return dep.repository ? <ExternalLink href={dep.repository}>{dep.name}</ExternalLink> : dep.name;
}

const columnHelper = createColumnHelper();
const DEPENDENCY_COLUMNS = columnHelper.columns([
  columnHelper.accessor("name", {
    header: DependencyHeader,
    cell: DependencyNameCell,
    meta: { labelKey: "AboutTab.colPackage" },
  }),
  columnHelper.accessor("version", {
    header: DependencyHeader,
    cell: (info) => info.getValue(),
    meta: { labelKey: "AboutTab.colVersion" },
  }),
  columnHelper.accessor("license", {
    header: DependencyHeader,
    cell: (info) => info.getValue() || "—",
    meta: { labelKey: "AboutTab.colLicense" },
  }),
]);

const getDependencyId = (dep) => dep.name;

export default function AboutTab() {
  const { t, i18n } = useTranslation("personal");
  const info = aboutInfo;
  const dependencyTable = useTable({
    features: baseFeatures,
    columns: DEPENDENCY_COLUMNS,
    data: info.dependencies,
    getRowId: getDependencyId,
  });
  const repoFile = (file) => `${info.repository.replace(/\/$/, "")}/blob/main/${file}`;
  const builtAt = info.builtAt
    ? new Date(info.builtAt).toLocaleString(i18n.language, { dateStyle: "medium", timeStyle: "short" })
    : "";

  return (
    <div className={styles.aboutWrap}>
    <div className={styles.aboutGrid} data-guide="account-about">
      {/* 標題下一行小字交代版本；授權說明獨立成段；三個外部連結做成同一款連結列，不再字級、圖示混在一起 */}
      <div className={styles.aboutSide}>
      <div className={styles.card}>
        <div className={styles.aboutHead}>
          <h2 className={styles.cardTitle}>{t("AboutTab.title")}</h2>
          <p className={styles.aboutMeta}>
            <span>{t("AboutTab.version")} {info.version || "—"}</span>
            {info.commit && <code className={styles.aboutCommit}>{info.commit}</code>}
            {builtAt && <span>{t("AboutTab.builtAt")} {builtAt}</span>}
          </p>
        </div>
        <p className={styles.aboutLead}>{t("AboutTab.licenseHint")}</p>
        <div className={styles.aboutLinks}>
          <LinkRow icon="gavel" label={t("AboutTab.license")} href={repoFile("LICENSE")}>
            {t("AboutTab.licenseName")}
          </LinkRow>
          <LinkRow icon="code" label={t("AboutTab.repository")} href={info.repository}>
            {info.repository.replace(/^https?:\/\//, "")}
          </LinkRow>
          <LinkRow
            icon="description"
            label={t("AboutTab.thirdParty")}
            href={repoFile("THIRD_PARTY_NOTICES.md")}
            title={t("AboutTab.thirdPartyHint")}
          >
            THIRD_PARTY_NOTICES.md
          </LinkRow>
        </div>
      </div>
      </div>

      <div className={`${styles.card} ${styles.aboutMain}`}>
        <h2 className={styles.cardTitle}>{t("AboutTab.componentsTitle")}</h2>
        <p className={styles.aboutHint}>{t("AboutTab.componentsHint", { count: info.dependencies.length })}</p>
        {info.dependencies.length > 0 && (
          <div className={styles.aboutTableWrap}>
            <DataTable table={dependencyTable} className={styles.aboutTable} />
          </div>
        )}
      </div>
    </div>
    </div>
  );
}
