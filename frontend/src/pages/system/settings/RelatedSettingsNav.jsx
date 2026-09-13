import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";

/**
 * 配額／資源排程／治理三頁的分工互連（稽核 #17）。
 * 三者是彼此獨立的設定 singleton（准入上限／放置引擎／事後回收），
 * 名字看不出分工，這裡在頁首下方各放另外兩頁的入口與一句定位。
 */
const PAGES = [
  { key: "quotas", path: "/quotas", labelKey: "SettingsPage.relatedQuotas" },
  { key: "scheduler", path: "/scheduler", labelKey: "SettingsPage.relatedScheduler" },
  { key: "governance", path: "/governance", labelKey: "SettingsPage.relatedGovernance" },
];

export default function RelatedSettingsNav({ current }) {
  const { t } = useTranslation("system");
  return (
    <p className={styles.relatedNav}>
      <span>{t("SettingsPage.relatedLabel")}</span>
      {PAGES.filter((page) => page.key !== current).map((page) => (
        <Link key={page.key} to={page.path} className={styles.inlineLink}>
          {t(page.labelKey)}
        </Link>
      ))}
    </p>
  );
}
