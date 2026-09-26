import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import HomeCard from "../dashboard/HomeCard";
import card from "../dashboard/HomeCard.module.scss";
import styles from "./QuickTemplateCards.module.scss";

/**
 * 快速練習的模板卡片，學生首頁與「快速練習」頁共用；from 決定確認頁的返回位置。
 * variant="home"：學生首頁用，外殼同首頁機器卡（HomeCard），兩區塊看起來是同一套。
 */
export default function QuickTemplateCards({ templates, loading, error, from, variant = "default" }) {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();

  if (loading) return <LoadingState />;
  if (error) return <div className={styles.emptyPanel}><MIcon name="cloud_off" size={20} /><span>{t("HomeOverview.templatesFailed")}</span></div>;
  if (!templates.length) return <div className={styles.emptyPanel}><MIcon name="inventory_2" size={20} /><span>{t("StudentHomePage.noQuickTemplatesTitle")}</span></div>;

  if (variant === "home") return <div className={styles.homeGrid}>
    {templates.map((template) => <HomeCard as="button" key={template.id} icon="layers"
      onClick={() => navigate(`/quick-template/${template.id}`, { state: { from } })}
      band={<span>{t("StudentHomePage.noManualReviewChip")}</span>}>
      <strong className={card.name}>{template.name}</strong>
      <span className={card.sub}>{t("HomeOverview.machineCount", { count: template.nodes?.length ?? 0 })}</span>
      <span className={card.cta}>{t("StudentHomePage.createNow")}<MIcon name="arrow_forward" size={16} /></span>
    </HomeCard>)}
  </div>;

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
