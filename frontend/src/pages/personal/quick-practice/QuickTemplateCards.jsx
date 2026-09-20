import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import styles from "./QuickTemplateCards.module.scss";

/** 快速建立的模板卡片，學生首頁與「快速建立」頁共用；from 決定確認頁的返回位置 */
export default function QuickTemplateCards({ templates, loading, error, from }) {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();

  if (loading) return <LoadingState />;
  if (error) return <div className={styles.emptyPanel}><MIcon name="cloud_off" size={20} /><span>{t("HomeOverview.templatesFailed")}</span></div>;
  if (!templates.length) return <div className={styles.emptyPanel}><MIcon name="inventory_2" size={20} /><span>{t("StudentHomePage.noQuickTemplatesTitle")}</span></div>;

  return <div className={styles.templateGrid}>
    {templates.map((template) => <button type="button" className={styles.templateCard} key={template.id}
      onClick={() => navigate(`/quick-template/${template.id}`, { state: { from } })}>
      <span className={styles.templateTop}><span className={styles.templateIcon}><MIcon name="layers" size={20} /></span>
        <strong>{template.name}</strong>
        <span className={styles.templateBadge}>{t("StudentHomePage.noManualReviewChip")}</span></span>
      <span className={styles.templateFooter}><span>{t("HomeOverview.machineCount", { count: template.nodes?.length ?? 0 })}</span>
        <span>{t("StudentHomePage.createNow")}<MIcon name="arrow_forward" size={18} /></span></span>
    </button>)}
  </div>;
}
