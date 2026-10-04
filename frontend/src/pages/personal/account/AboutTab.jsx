import { useTranslation } from "react-i18next";
import styles from "./AccountSettingsPage.module.scss";
import MIcon from "../../../components/MIcon";

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

export default function AboutTab() {
  const { t, i18n } = useTranslation("personal");
  const info = aboutInfo;
  const repoFile = (file) => `${info.repository.replace(/\/$/, "")}/blob/main/${file}`;
  const builtAt = info.builtAt
    ? new Date(info.builtAt).toLocaleString(i18n.language, { dateStyle: "medium", timeStyle: "short" })
    : "";

  return (
    <div className={styles.aboutGrid}>
      <div className={styles.card}>
        <h2 className={styles.cardTitle}>{t("AboutTab.title")}</h2>
        <dl className={styles.aboutRows}>
          <dt>{t("AboutTab.version")}</dt>
          <dd>
            {info.version || "—"}
            {info.commit && <code className={styles.aboutCommit}>{info.commit}</code>}
          </dd>
          {builtAt && (
            <>
              <dt>{t("AboutTab.builtAt")}</dt>
              <dd>{builtAt}</dd>
            </>
          )}
          <dt>{t("AboutTab.license")}</dt>
          <dd>
            <ExternalLink href={repoFile("LICENSE")}>{t("AboutTab.licenseName")}</ExternalLink>
            <p className={styles.aboutHint}>{t("AboutTab.licenseHint")}</p>
          </dd>
          <dt>{t("AboutTab.repository")}</dt>
          <dd>
            <ExternalLink href={info.repository}>{info.repository.replace(/^https?:\/\//, "")}</ExternalLink>
          </dd>
          <dt>{t("AboutTab.thirdParty")}</dt>
          <dd>
            <ExternalLink href={repoFile("THIRD_PARTY_NOTICES.md")}>THIRD_PARTY_NOTICES.md</ExternalLink>
            <p className={styles.aboutHint}>{t("AboutTab.thirdPartyHint")}</p>
          </dd>
        </dl>
      </div>

      <div className={styles.card}>
        <h2 className={styles.cardTitle}>{t("AboutTab.componentsTitle")}</h2>
        <p className={styles.aboutHint}>{t("AboutTab.componentsHint", { count: info.dependencies.length })}</p>
        {info.dependencies.length > 0 && (
          <div className={styles.aboutTableWrap}>
            <table className={styles.aboutTable}>
              <thead>
                <tr>
                  <th>{t("AboutTab.colPackage")}</th>
                  <th>{t("AboutTab.colVersion")}</th>
                  <th>{t("AboutTab.colLicense")}</th>
                </tr>
              </thead>
              <tbody>
                {info.dependencies.map((dep) => (
                  <tr key={dep.name}>
                    <td>
                      {dep.repository ? (
                        <ExternalLink href={dep.repository}>{dep.name}</ExternalLink>
                      ) : (
                        dep.name
                      )}
                    </td>
                    <td>{dep.version}</td>
                    <td>{dep.license || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
