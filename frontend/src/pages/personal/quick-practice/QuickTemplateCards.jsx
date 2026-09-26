import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import QuickTemplateFolder from "./QuickTemplateFolder";
import styles from "./QuickTemplateCards.module.scss";

/**
 * 快速練習的模板清單，學生首頁與「快速練習」頁共用同一款資料夾卡（QuickTemplateFolder）；
 * from 決定確認頁的返回位置。
 */
export default function QuickTemplateCards({ templates, loading, error, from }) {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();

  if (loading) return <LoadingState />;
  if (error) return <div className={styles.emptyPanel}><MIcon name="cloud_off" size={20} /><span>{t("HomeOverview.templatesFailed")}</span></div>;
  if (!templates.length) return <div className={styles.emptyPanel}><MIcon name="inventory_2" size={20} /><span>{t("StudentHomePage.noQuickTemplatesTitle")}</span></div>;

  return <div className={styles.grid}>
    {templates.map((template) => <QuickTemplateFolder key={template.id} template={template}
      onOpen={() => navigate(`/quick-template/${template.id}`, { state: { from } })} />)}
  </div>;
}
