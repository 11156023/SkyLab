import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import EmptyState from "../../../components/EmptyState/EmptyState";
import QuickTemplateFolder from "./QuickTemplateFolder";
import styles from "./QuickTemplateCards.module.scss";

/**
 * 快速練習的模板清單，學生首頁與「快速練習」頁共用同一款資料夾卡（QuickTemplateFolder）；
 * from 決定確認頁的返回位置。空的時候用全站共用的 EmptyState，emptyClassName 讓首頁縮小留白；
 * 給了 onRetry 才在載入失敗時放「重試」（快速練習頁給，首頁不給）。
 */
export default function QuickTemplateCards({ templates, loading, error, from, emptyClassName, onRetry }) {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();

  if (loading) return <LoadingState />;
  if (error) return <EmptyState icon="cloud_off" title={t("HomeOverview.templatesFailed")} className={emptyClassName}
    action={onRetry && <button type="button" className={styles.retry} onClick={onRetry}><MIcon name="refresh" size={16} />{t("Error.retry", { ns: "common" })}</button>} />;
  if (!templates.length) return <EmptyState icon="inventory_2" title={t("StudentHomePage.noQuickTemplatesTitle")} className={emptyClassName} />;

  return <div className={styles.grid}>
    {templates.map((template) => <QuickTemplateFolder key={template.id} template={template}
      onOpen={() => navigate(`/quick-template/${template.id}`, { state: { from } })} />)}
  </div>;
}
