import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import { useToast } from "../../../hooks/useToast";
import EmptyState from "../../../components/EmptyState/EmptyState";
import styles from "../CourseOperations.module.scss";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";

const STATUS_LABEL_KEYS = { published: "CourseTemplateManagementPage.statusPublished", draft: "CourseTemplateManagementPage.statusDraft", retired: "CourseTemplateManagementPage.statusRetired" };
const USAGE_LABEL_KEYS = { course: "CourseTemplateManagementPage.usageCourse", quick_practice: "CourseTemplateManagementPage.usageQuickPractice", both: "CourseTemplateManagementPage.usageBoth" };
const AUDIENCE_LABEL_KEYS = { owner: "CourseTemplateManagementPage.audienceOwner", class: "CourseTemplateManagementPage.audienceClass", campus: "CourseTemplateManagementPage.audienceCampus" };

function audienceNote(template, t) {
  if (template.usageScope === "course") return t("CourseTemplateManagementPage.notInStudentList");
  const label = t(AUDIENCE_LABEL_KEYS[template.audience] ?? AUDIENCE_LABEL_KEYS.class);
  return template.audience === "class"
    ? t("CourseTemplateManagementPage.audienceClassCount", { label, count: (template.audienceClassIds ?? []).length })
    : label;
}

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
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    CourseEnvironmentsService.list()
      .then((rows) => active && setTemplates(rows))
      .catch((reason) => active && toast.error(reason?.message ?? t("CourseTemplateManagementPage.loadFailed")))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [toast, t]);
  async function retire(template) {
    const ok = await confirm({
      title: t("CourseTemplateManagementPage.retireConfirmTitle", { name: template.name }),
      message: t("CourseTemplateManagementPage.retireConfirmMessage"),
      confirmText: t("CourseTemplateManagementPage.retireLabel"),
    });
    if (!ok) return;
    setBusyId(template.id);
    try {
      const updated = await CourseEnvironmentsService.retire(template.id);
      setTemplates((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
    } catch (reason) {
      toast.error(reason?.message ?? t("CourseTemplateManagementPage.retireFailed"));
    } finally {
      setBusyId("");
    }
  }

  async function remove(template) {
    const ok = await confirm({
      title: t("CourseTemplateManagementPage.removeConfirmTitle", { name: template.name }),
      message: t("CourseTemplateManagementPage.removeConfirmMessage"),
      confirmText: t("CourseTemplateManagementPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    setBusyId(template.id);
    try {
      await CourseEnvironmentsService.remove(template.id);
      setTemplates((prev) => prev.filter((row) => row.id !== template.id));
    } catch (reason) {
      toast.error(reason?.message ?? t("CourseTemplateManagementPage.removeFailed"));
    } finally {
      setBusyId("");
    }
  }

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
    <PageHeader title={t("CourseTemplateManagementPage.pageTitle")} subtitle={t("CourseTemplateManagementPage.pageSubtitle")}>
      <button type="button" className={styles.btnPrimary} onClick={() => navigate("/course-template-management/new")}><MIcon name="add" size={16} />{t("CourseTemplateManagementPage.createEnvBtn")}</button>
    </PageHeader>

    <div className={styles.envControls}>
      <SegmentedControl
        className={styles.envTabs}
        options={[["all", "CourseTemplateManagementPage.filterAll"], ["published", "CourseTemplateManagementPage.statusPublished"], ["draft", "CourseTemplateManagementPage.statusDraft"], ["retired", "CourseTemplateManagementPage.statusRetired"]].map(([key, labelKey]) => ({ value: key, label: t(labelKey), badge: statusCounts[key] ?? 0 }))}
        value={status}
        onChange={setStatus}
        ariaLabel={t("CourseTemplateManagementPage.thStatus")}
      />
      <label className={styles.envSearch}><MIcon name="search" size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("CourseTemplateManagementPage.searchPlaceholder")} /></label>
    </div>

    <section>
      {loading ? <LoadingState /> : !rows.length ? <EmptyState icon="view_quilt" title={t("CourseTemplateManagementPage.emptyTitle")} /> : <div className={styles.envTableWrap}><table className={styles.envTable}><thead><tr><th>{t("CourseTemplateManagementPage.thName")}</th><th>{t("CourseTemplateManagementPage.thMachinesPerStudent")}</th><th>{t("CourseTemplateManagementPage.thResourceTotal")}</th><th>{t("CourseTemplateManagementPage.thVersion")}</th><th>{t("CourseTemplateManagementPage.thProvideMode")}</th><th>{t("CourseTemplateManagementPage.thUsingClasses")}</th><th>{t("CourseTemplateManagementPage.thStatus")}</th><th /></tr></thead><tbody>{rows.map((template) => <tr key={template.id} onClick={() => navigate(`/course-template-management/${template.id}`)}>
        <td><strong>{template.name}</strong><small>{template.description}</small></td>
        <td><strong>{t("CourseTemplateManagementPage.machinesPerStudentUnit", { count: template.nodes.length })}</strong><small>{template.nodes.map((node) => node.name).join("、")}</small></td>
        <td>{t("CourseTemplateManagementPage.resourceSummary", { cpu: template.nodes.reduce((sum, node) => sum + node.cpu, 0), memory: template.nodes.reduce((sum, node) => sum + node.memory, 0) })}</td><td>v{template.version}</td><td><strong>{template.usageScope ? t(USAGE_LABEL_KEYS[template.usageScope] ?? USAGE_LABEL_KEYS.course) : t(USAGE_LABEL_KEYS.course)}</strong><small>{audienceNote(template, t)}</small></td><td>{t("CourseTemplateManagementPage.classesCount", { count: template.classes })}</td>
        <td><span className={`${styles.statusBadge} ${styles[`status_${template.status}`]}`}>{t(STATUS_LABEL_KEYS[template.status])}</span></td>
        <td onClick={(event) => event.stopPropagation()}><div className={styles.rowActions}>
          {template.status === "published" && <button type="button" className={styles.iconBtn} title={t("CourseTemplateManagementPage.retireLabel")} aria-label={t("CourseTemplateManagementPage.retireLabel")} disabled={busyId === template.id} onClick={() => retire(template)}><MIcon name="unpublished" size={18} /></button>}
          <button type="button" className={`${styles.iconBtn} ${styles.iconBtnDanger}`} title={t("CourseTemplateManagementPage.deleteLabel")} aria-label={t("CourseTemplateManagementPage.deleteLabel")} disabled={busyId === template.id} onClick={() => remove(template)}><MIcon name="delete" size={18} /></button>
          <button type="button" className={styles.iconBtn} aria-label={t("CourseTemplateManagementPage.openTemplateAria")} onClick={() => navigate(`/course-template-management/${template.id}`)}><MIcon name="chevron_right" size={19} /></button>
        </div></td>
      </tr>)}</tbody></table></div>}
    </section>
  </div>;
}
