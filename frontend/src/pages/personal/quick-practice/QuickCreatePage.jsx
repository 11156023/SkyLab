import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { QuickPracticeService } from "../../../services/quickPractice";
import QuickTemplateCards from "./QuickTemplateCards";
import styles from "./QuickCreatePage.module.scss";

/** 側欄「教學 › 快速練習」：學生、老師、管理者共用的快速練習入口 */
export default function QuickCreatePage() {
  const { t } = useTranslation("personal");
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    QuickPracticeService.listTemplates({ signal: controller.signal })
      .then((available) => setTemplates(available))
      .catch((err) => {
        if (!err?.cancelled) { setTemplates([]); setError(true); }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [reloadKey]);

  return (
    <div className={styles.page}>
      <PageHeader title={t("QuickCreatePage.title")} />
      <QuickTemplateCards templates={templates} loading={loading} error={error} from="/quick-create"
        onRetry={() => setReloadKey((key) => key + 1)} />
    </div>
  );
}
