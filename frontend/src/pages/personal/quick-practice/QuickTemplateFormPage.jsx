import { useContext, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { useToast } from "../../../hooks/useToast";
import { LayoutContext } from "../../../layout/layoutContext";
import { QuickPracticeService } from "../../../services/quickPractice";
import { useQuickPracticeTemplateQuery } from "../../../queries/quickPractice";
import { useResourceCacheUpdater } from "../../../queries/resources";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { folderTotals, nodeMemoryGb } from "./templateFolder";
import styles from "./QuickTemplateFormPage.module.scss";

export default function QuickTemplateFormPage() {
  const { t } = useTranslation("personal");
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const { setCompactFooter } = useContext(LayoutContext);
  const [submitting, setSubmitting] = useState(false);
  const submitLockRef = useRef(false);
  const resourceCache = useResourceCacheUpdater();

  useEffect(() => {
    setCompactFooter(true);
    return () => setCompactFooter(false);
  }, [setCompactFooter]);

  /* 範本內容只讀一次（不輪詢）；載不到就當成找不到這個環境 */
  const templateQuery = useQuickPracticeTemplateQuery(id);
  const template = templateQuery.data ?? null;
  const loading = id != null && templateQuery.data === undefined
    && (templateQuery.isPending || templateQuery.isFetching);

  const onBack = () => navigate(location.state?.from ?? "/dashboard");

  async function launch() {
    if (submitLockRef.current || !template) return;
    submitLockRef.current = true;
    setSubmitting(true);
    try {
      await QuickPracticeService.launch(template.id);
      toast.success(t("QuickTemplateFormPage.launchSuccess", { name: template.name, count: template.nodes.length }));
      /* 「我的資源」讀快取：新的練習環境要立刻出現，不能等下一輪刷新 */
      resourceCache.invalidate();
      navigate("/my-resources");
    } catch (error) {
      toast.error(error?.message ?? t("QuickTemplateFormPage.launchFailed"));
    } finally {
      submitLockRef.current = false;
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className={styles.page}>
        <LoadingState fullPage text={t("QuickTemplateFormPage.loadingEnvironment")} />
      </div>
    );
  }

  if (!template) {
    return <div className={styles.page}><div className={styles.notFound}><MIcon name="error_outline" size={40} /><h2>{t("QuickTemplateFormPage.notFoundTitle")}</h2><p>{t("QuickTemplateFormPage.notFoundDesc")}</p><button type="button" className={styles.btnSecondary} onClick={onBack}><MIcon name="arrow_back" size={16} />{t("QuickTemplateFormPage.back")}</button></div></div>;
  }

  const totalCpu = template.nodes.reduce((sum, node) => sum + Number(node.cpu || 0), 0);
  /* 記憶體照實際 MB 換算（512 MB 顯示 0.5），合計與資料夾卡片同一套算法，兩個畫面才不會對不上 */
  const totalMemory = folderTotals(template).memoryGb;
  const totalDisk = template.nodes.reduce((sum, node) => sum + Number(node.disk || 0), 0);

  return <div className={styles.page}>
    <PageHeader title={t("QuickTemplateFormPage.title")}>
      <button type="button" className={styles.backBtn} onClick={onBack}><MIcon name="arrow_back" size={16} />{t("QuickTemplateFormPage.back")}</button>
    </PageHeader>

    <div className={styles.form}>
      <section className={styles.templateHeader} data-guide="quick-launch-summary">
        <div className={styles.templateLogo}><MIcon name="account_tree" size={28} /></div>
        <div className={styles.templateMeta}>
          {/* 說明一律緊貼在大標下方，不在晶片後面自成一行 */}
          <div className={styles.templateTitleBlock}>
            <h2 className={styles.templateName}>{template.name}</h2>
            <p className={styles.templateStatus}>{t("QuickTemplateFormPage.autoApproveNote")}</p>
          </div>
          {template.description && <p className={styles.templateDesc}>{template.description}</p>}
          <div className={styles.templateChips}><span className={styles.portChip}><MIcon name="dns" size={12} />{t("QuickTemplateFormPage.machineCount", { count: template.nodes.length })}</span><span className={styles.portChip}><MIcon name="schedule" size={12} />{t("QuickTemplateFormPage.durationHours", { count: template.duration_hours })}</span><span className={styles.portChip}>v{template.version}</span></div>
        </div>
      </section>

      <section className={styles.section} data-guide="quick-launch-machines">
        <h3 className={styles.sectionTitle}>{t("QuickTemplateFormPage.machinesToCreate")}</h3>
        <div className={styles.machineList}>
          {template.nodes.map((node, index) => <article key={node.id}>
            <span className={styles.machineIndex}>{index + 1}</span>
            <span className={styles.machineIcon}><MIcon name={node.type === "lxc" ? "terminal" : "desktop_windows"} size={18} /></span>
            <div><strong>{node.name}</strong><small>{node.role} · {String(node.type).toUpperCase()}</small></div>
            <span className={styles.machineSpec}>{t("QuickTemplateFormPage.machineSpec", { cpu: node.cpu, memory: nodeMemoryGb(node), disk: node.disk })}</span>
          </article>)}
        </div>
        <div className={styles.environmentTotal}><span>{t("QuickTemplateFormPage.environmentTotal")}</span><strong>{t("QuickTemplateFormPage.environmentTotalSpec", { cpu: totalCpu, memory: totalMemory, disk: totalDisk })}</strong></div>
      </section>

      <section className={styles.section} data-guide="quick-launch-rules">
        <h3 className={styles.sectionTitle}>{t("QuickTemplateFormPage.usageRules")}</h3>
        <div className={styles.ruleList}>
          <p><MIcon name="verified" size={18} /><strong>{t("QuickTemplateFormPage.ruleNoReviewTitle")}</strong></p>
          <p><MIcon name="tune" size={18} /><strong>{t("QuickTemplateFormPage.ruleFixedConfigTitle")}</strong></p>
          <p><MIcon name="timer" size={18} /><strong>{t("QuickTemplateFormPage.ruleDurationTitle", { hours: template.duration_hours })}</strong></p>
        </div>
      </section>

      <div className={styles.actions} data-guide="quick-launch-actions">
        <button type="button" className={styles.btnSecondary} onClick={onBack}>{t("QuickTemplateFormPage.cancel")}</button>
        <button type="button" className={styles.btnPrimary} disabled={submitting} onClick={launch}><MIcon name={submitting ? "hourglass_top" : "bolt"} size={16} spin={submitting} />{submitting ? t("QuickTemplateFormPage.launching") : t("QuickTemplateFormPage.launchButton", { count: template.nodes.length })}</button>
      </div>
    </div>
  </div>;
}
