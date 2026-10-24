import { useTranslation } from "react-i18next";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useQuickPracticeTemplatesQuery } from "../../../queries/quickPractice";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import QuickTemplateCards from "./QuickTemplateCards";
import styles from "./QuickCreatePage.module.scss";

const EMPTY = [];

/** 側欄「教學 › 快速練習」：學生、老師、管理者共用的快速練習入口 */
export default function QuickCreatePage() {
  const { t } = useTranslation("personal");
  /* 範本清單與學生首頁共用快取，不輪詢；首次載不到才顯示錯誤，重試走 refetch */
  const templatesQuery = useQuickPracticeTemplatesQuery();
  const templates = Array.isArray(templatesQuery.data) ? templatesQuery.data : EMPTY;
  const { loading, error, retry } = useQueryView(templatesQuery);

  return (
    <div className={styles.page}>
      <PageHeader title={t("QuickCreatePage.title")} />
      <QuickTemplateCards templates={templates} loading={loading} error={error} from="/quick-create"
        onRetry={retry} />
    </div>
  );
}
