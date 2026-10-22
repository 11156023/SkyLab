import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import { useCourseEnvironmentCacheUpdater, useCourseEnvironmentsQuery } from "../../../queries/courseEnvironments";
import { useToast } from "../../../hooks/useToast";
import EmptyState from "../../../components/EmptyState/EmptyState";
import styles from "../CourseOperations.module.scss";
import { environmentSpecs } from "../nodeSpecs";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";

const STATUS_LABEL_KEYS = { published: "CourseTemplateManagementPage.statusPublished", draft: "CourseTemplateManagementPage.statusDraft", retired: "CourseTemplateManagementPage.statusRetired" };
const USAGE_LABEL_KEYS = { course: "CourseTemplateManagementPage.usageCourse", quick_practice: "CourseTemplateManagementPage.usageQuickPractice", both: "CourseTemplateManagementPage.usageBoth" };
const EMPTY = Object.freeze([]);

export default function CourseTemplateManagementPage() {
  const { t } = useTranslation("teaching");
  const navigate = useNavigate();
  const location = useLocation();
  /* 從編輯頁返回時播回場動畫，比照「我的申請」 */
  const [returning, setReturning] = useState(Boolean(location.state?.returning));
  const confirm = useConfirm();
  const [busyId, setBusyId] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const toast = useToast();
  /* 教學環境清單走 TanStack Query：首次載不到才提示，背景刷新失敗沿用上一份 */
  const templatesQuery = useCourseEnvironmentsQuery();
  const envCache = useCourseEnvironmentCacheUpdater();
  const templates = templatesQuery.data ?? EMPTY;
  const loading = templatesQuery.data === undefined && (templatesQuery.isPending || templatesQuery.isFetching);
  /* 依訊息字串觸發：還沒有資料時輪詢重試又失敗，訊息相同就不重複提示 */
  const loadFailedMessage = templatesQuery.data === undefined && templatesQuery.isError
    ? templatesQuery.error?.message ?? t("Error.generic", { ns: "common" })
    : null;
  useEffect(() => {
    if (loadFailedMessage) toast.error(loadFailedMessage);
  }, [loadFailedMessage, toast]);
  async function remove(template) {
    const ok = await confirm({
      title: t("CourseTemplateManagementPage.removeConfirmTitle", { name: displayName(template) }),
      message: t("CourseTemplateManagementPage.removeConfirmMessage"),
      confirmText: t("CourseTemplateManagementPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    setBusyId(template.id);
    try {
      await CourseEnvironmentsService.remove(template.id);
      await envCache.remove(template.id);
    } catch (reason) {
      toast.error(reason?.message ?? t("CourseTemplateManagementPage.removeFailed"));
    } finally {
      setBusyId("");
    }
  }

  /* 草稿可以還沒取名（後端允許空名稱），列表與確認框不能出現一片空白 */
  const displayName = (template) => template.name?.trim() || t("CourseTemplateManagementPage.unnamedEnv");

  const rows = useMemo(() => templates.filter((template) => {
    const matchesQuery = `${template.name} ${template.description ?? ""}`.toLowerCase().includes(query.toLowerCase());
    return matchesQuery && (status === "all" || template.status === status);
  }), [query, status, templates]);

  const statusCounts = useMemo(() => templates.reduce(
    (counts, template) => ({ ...counts, [template.status]: (counts[template.status] ?? 0) + 1 }),
    { all: templates.length },
  ), [templates]);

  return <div
    className={`${styles.page} ${styles.listPage} ${returning ? styles.animSlideInLeft : ""}`}
    onAnimationEnd={returning ? () => setReturning(false) : undefined}
  >
    <PageHeader title={t("CourseTemplateManagementPage.pageTitle")}>
      <button type="button" className={styles.btnPrimary} onClick={() => navigate("/course-template-management/new")}><MIcon name="add" size={16} />{t("CourseTemplateManagementPage.createEnvBtn")}</button>
    </PageHeader>

    <div className={styles.envControls} data-guide="course-tpl-controls">
      <SegmentedControl
        className={styles.envTabs}
        options={[["all", "CourseTemplateManagementPage.filterAll"], ["published", "CourseTemplateManagementPage.statusPublished"], ["draft", "CourseTemplateManagementPage.statusDraft"]].map(([key, labelKey]) => ({ value: key, label: t(labelKey), badge: statusCounts[key] ?? 0 }))}
        value={status}
        onChange={setStatus}
        ariaLabel={t("CourseTemplateManagementPage.thStatus")}
      />
      <label className={styles.envSearch}><MIcon name="search" size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("CourseTemplateManagementPage.searchPlaceholder")} /></label>
    </div>

    <section>
      {loading ? <LoadingState /> : !rows.length ? <EmptyState icon="view_quilt" title={t("CourseTemplateManagementPage.emptyTitle")}
        action={templates.length
          ? <button type="button" className={styles.btnSecondary} onClick={() => { setQuery(""); setStatus("all"); }}><MIcon name="filter_alt_off" size={16} />{t("EmptyState.clearFilters", { ns: "common" })}</button>
          : <button type="button" className={styles.btnPrimary} onClick={() => navigate("/course-template-management/new")}><MIcon name="add" size={16} />{t("CourseTemplateManagementPage.createEnvBtn")}</button>} /> : <div className={styles.envTableWrap} data-guide="course-tpl-table"><table className={styles.envTable}><thead><tr><th>{t("CourseTemplateManagementPage.thName")}</th><th>{t("CourseTemplateManagementPage.thMachinesPerStudent")}</th><th>{t("CourseTemplateManagementPage.thResourceTotal")}</th><th>{t("CourseTemplateManagementPage.thVersion")}</th><th>{t("CourseTemplateManagementPage.thProvideMode")}</th><th>{t("CourseTemplateManagementPage.thUsingClasses")}</th><th>{t("CourseTemplateManagementPage.thStatus")}</th><th>{t("CourseTemplateManagementPage.thActions")}</th></tr></thead><tbody>{rows.map((template) => <tr key={template.id} onClick={() => navigate(`/course-template-management/${template.id}`)}>
        <td><strong className={template.name?.trim() ? undefined : styles.envUnnamed}>{displayName(template)}</strong></td>
        <td><strong>{t("CourseTemplateManagementPage.machinesPerStudentUnit", { count: template.nodes.length })}</strong></td>
        <td>{t("CourseTemplateManagementPage.resourceSummary", environmentSpecs(template.nodes))}</td><td>v{template.version}</td><td><strong>{t(USAGE_LABEL_KEYS[template.usageScope] ?? USAGE_LABEL_KEYS.course)}</strong></td><td>{t("CourseTemplateManagementPage.classesCount", { count: template.classes })}</td>
        <td><span className={`${styles.statusBadge} ${styles[`status_${template.status}`]}`}>{t(STATUS_LABEL_KEYS[template.status])}</span></td>
        <td onClick={(event) => event.stopPropagation()}><div className={styles.rowActions} data-guide="course-tpl-row-actions">
          <button type="button" className={`${styles.iconBtn} ${styles.iconBtnDanger}`} title={t("CourseTemplateManagementPage.deleteLabel")} aria-label={t("CourseTemplateManagementPage.deleteLabel")} disabled={busyId === template.id} onClick={() => remove(template)}><MIcon name="delete" size={18} /></button>
          <button type="button" className={styles.iconBtn} aria-label={t("CourseTemplateManagementPage.openTemplateAria")} onClick={() => navigate(`/course-template-management/${template.id}`)}><MIcon name="chevron_right" size={19} /></button>
        </div></td>
      </tr>)}</tbody></table></div>}
    </section>
  </div>;
}
