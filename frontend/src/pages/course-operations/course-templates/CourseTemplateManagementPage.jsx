import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import DataTable, { DataTableCells } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import { useCourseEnvironmentCacheUpdater, useCourseEnvironmentsQuery } from "../../../queries/courseEnvironments";
import { useToast } from "../../../hooks/useToast";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import styles from "../CourseOperations.module.scss";
import { environmentSpecs } from "../nodeSpecs";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";

const STATUS_LABEL_KEYS = { published: "CourseTemplateManagementPage.statusPublished", draft: "CourseTemplateManagementPage.statusDraft", retired: "CourseTemplateManagementPage.statusRetired" };
const USAGE_LABEL_KEYS = { course: "CourseTemplateManagementPage.usageCourse", quick_practice: "CourseTemplateManagementPage.usageQuickPractice", both: "CourseTemplateManagementPage.usageBoth" };
const EMPTY = Object.freeze([]);

/* 草稿可以還沒取名（後端允許空名稱），列表與確認框不能出現一片空白 */
const displayName = (template, t) => template.name?.trim() || t("CourseTemplateManagementPage.unnamedEnv");

/* ── 表格欄位（TanStack Table）──
   v9 的 header／cell 會被當元件掛載，欄位定義放模組層級才不會每次 render 整格重新掛載。
   刪除中的狀態與開啟／刪除動作由頁面給 EnvironmentRow，經 rowState 交給動作欄 */
const columnHelper = createColumnHelper();

function useColumns() {
  const { t } = useTranslation("teaching");
  return [
    t("CourseTemplateManagementPage.thName"),
    t("CourseTemplateManagementPage.thMachinesPerStudent"),
    t("CourseTemplateManagementPage.thResourceTotal"),
    t("CourseTemplateManagementPage.thVersion"),
    t("CourseTemplateManagementPage.thProvideMode"),
    t("CourseTemplateManagementPage.thUsingClasses"),
    t("CourseTemplateManagementPage.thStatus"),
    t("CourseTemplateManagementPage.thActions"),
  ];
}

/* 欄名來自 useColumns() */
function ColumnTitle({ index }) {
  return useColumns()[index];
}

function NameCell({ template }) {
  const { t } = useTranslation("teaching");
  return <strong className={template.name?.trim() ? undefined : styles.envUnnamed}>{displayName(template, t)}</strong>;
}

function MachinesCell({ count }) {
  const { t } = useTranslation("teaching");
  return <strong>{t("CourseTemplateManagementPage.machinesPerStudentUnit", { count })}</strong>;
}

function ResourceCell({ nodes }) {
  const { t } = useTranslation("teaching");
  return t("CourseTemplateManagementPage.resourceSummary", environmentSpecs(nodes));
}

function UsageCell({ usageScope }) {
  const { t } = useTranslation("teaching");
  return <strong>{t(USAGE_LABEL_KEYS[usageScope] ?? USAGE_LABEL_KEYS.course)}</strong>;
}

function ClassesCell({ count }) {
  const { t } = useTranslation("teaching");
  return t("CourseTemplateManagementPage.classesCount", { count });
}

function StatusCell({ status }) {
  const { t } = useTranslation("teaching");
  return <span className={`${styles.statusBadge} ${styles[`status_${status}`]}`}>{t(STATUS_LABEL_KEYS[status])}</span>;
}

function ActionsCell({ busy, onRemove, onOpen }) {
  const { t } = useTranslation("teaching");
  return <div className={styles.rowActions} data-guide="course-tpl-row-actions">
    <button type="button" className={`${styles.iconBtn} ${styles.iconBtnDanger}`} title={t("CourseTemplateManagementPage.deleteLabel")} aria-label={t("CourseTemplateManagementPage.deleteLabel")} disabled={busy} onClick={onRemove}><MIcon name="delete" size={18} /></button>
    <button type="button" className={styles.iconBtn} aria-label={t("CourseTemplateManagementPage.openTemplateAria")} onClick={onOpen}><MIcon name="chevron_right" size={19} /></button>
  </div>;
}

const environmentColumns = columnHelper.columns([
  columnHelper.accessor("name", { header: () => <ColumnTitle index={0} />, cell: ({ row }) => <NameCell template={row.original} /> }),
  columnHelper.display({ id: "machines", header: () => <ColumnTitle index={1} />, cell: ({ row }) => <MachinesCell count={row.original.nodes.length} /> }),
  columnHelper.display({ id: "resources", header: () => <ColumnTitle index={2} />, cell: ({ row }) => <ResourceCell nodes={row.original.nodes} /> }),
  columnHelper.accessor("version", { header: () => <ColumnTitle index={3} />, cell: ({ row }) => <>v{row.original.version}</> }),
  columnHelper.accessor("usageScope", { header: () => <ColumnTitle index={4} />, cell: ({ row }) => <UsageCell usageScope={row.original.usageScope} /> }),
  columnHelper.accessor("classes", { header: () => <ColumnTitle index={5} />, cell: ({ row }) => <ClassesCell count={row.original.classes} /> }),
  columnHelper.accessor("status", { header: () => <ColumnTitle index={6} />, cell: ({ row }) => <StatusCell status={row.original.status} /> }),
  /* 動作欄的點擊不往上冒泡到整列的「開啟」 */
  columnHelper.display({
    id: "actions",
    header: () => <ColumnTitle index={7} />,
    cell: ({ rowState }) => <ActionsCell busy={rowState.busy} onRemove={rowState.onRemove} onOpen={rowState.onOpen} />,
    meta: { tdProps: { onClick: (event) => event.stopPropagation() } },
  }),
]);

/* 整列點下去開啟編輯頁；template 是 row 的原始資料（一併傳進來，編譯後的元件才會跟著資料重繪） */
function EnvironmentRow({ row, template, busy, onOpen, onRemove }) {
  const open = () => onOpen(template.id);
  return <tr onClick={open}>
    <DataTableCells row={row} rowState={{ busy, onOpen: open, onRemove: () => onRemove(template) }} />
  </tr>;
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
  /* 教學環境清單走 TanStack Query：首次載不到才提示，背景刷新失敗沿用上一份 */
  const templatesQuery = useCourseEnvironmentsQuery();
  const envCache = useCourseEnvironmentCacheUpdater();
  const templates = templatesQuery.data ?? EMPTY;
  /* 用 useQueryView：首載失敗後的輪詢重試期間仍算失敗，表格不閃回載入中，同一個錯誤只提示一次
     （沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error） */
  const templatesView = useQueryView(templatesQuery);
  const loading = templatesView.loading;
  const loadFailedMessage = templatesView.error
    ? templatesView.error?.message ?? t("Error.generic", { ns: "common" })
    : null;
  useEffect(() => {
    if (loadFailedMessage) toast.error(loadFailedMessage);
  }, [loadFailedMessage, toast]);
  async function remove(template) {
    const ok = await confirm({
      title: t("CourseTemplateManagementPage.removeConfirmTitle", { name: displayName(template, t) }),
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

  const rows = useMemo(() => templates.filter((template) => {
    const matchesQuery = `${template.name} ${template.description ?? ""}`.toLowerCase().includes(query.toLowerCase());
    return matchesQuery && (status === "all" || template.status === status);
  }), [query, status, templates]);

  const table = useTable({
    features: baseFeatures,
    columns: environmentColumns,
    data: rows,
    getRowId: (template) => String(template.id),
  });
  const openTemplate = (id) => navigate(`/course-template-management/${id}`);

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
          : <button type="button" className={styles.btnPrimary} onClick={() => navigate("/course-template-management/new")}><MIcon name="add" size={16} />{t("CourseTemplateManagementPage.createEnvBtn")}</button>} /> : <div className={styles.envTableWrap} data-guide="course-tpl-table"><DataTable
        table={table}
        className={styles.envTable}
        renderRow={(row) => <EnvironmentRow key={row.id} row={row} template={row.original} busy={busyId === row.original.id} onOpen={openTemplate} onRemove={remove} />}
      /></div>}
    </section>
  </div>;
}
