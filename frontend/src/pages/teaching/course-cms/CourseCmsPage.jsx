import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import DataTable from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useUnsavedChanges } from "../../../contexts/UnsavedChangesContext";
import { useLoadError, useQueryView } from "../../../hooks/useQueryLoadState";
import { AuthStorage } from "../../../services/auth";
import { connectReconnectingWebSocket } from "../../../utils/reconnectingWebSocket";
import {
  CourseAdminService,
  courseProgressWsUrl,
} from "../../../services/courses";
import { useAdminCoursePathsQuery, useCourseAdminCacheUpdater, usePathProgressQuery } from "../../../queries/courses";
import { useTeachingClassesQuery } from "../../../queries/teachingClasses";
import styles from "./CourseCmsPage.module.scss";
import PageHeader from "../../../components/PageHeader/PageHeader";
import ContentEditor from "./ContentEditor";

const EMPTY = Object.freeze([]);

/* ══════════════ 進度報表欄位（TanStack Table） ══════════════
   v9 的 header／cell 會被當元件掛載，欄位定義不可以每次 render 新建（整格會重新掛載）。
   固定的兩欄放模組層級；關卡欄依位置建、以 index 快取，關卡數不變就是同一組欄位定義：
   表頭標題取第一位學生的關卡（同原本），每列的數字取該學生同位置的關卡 */
const columnHelper = createColumnHelper();

function StudentHeader() {
  const { t } = useTranslation("teaching");
  return t("CourseCmsPage.thStudent");
}

function TotalProgressHeader() {
  const { t } = useTranslation("teaching");
  return t("CourseCmsPage.thTotalProgress");
}

const fixedProgressColumns = [
  columnHelper.display({
    id: "student",
    header: () => <StudentHeader />,
    cell: ({ row }) => (
      <div className={styles.studentCell}>
        <span>{row.original.user_name ?? row.original.user_email}</span>
        <span className={styles.studentEmail}>{row.original.user_email}</span>
      </div>
    ),
  }),
  columnHelper.accessor("progress_percent", {
    header: () => <TotalProgressHeader />,
    cell: ({ row }) => (
      <div className={styles.cellProgress}>
        <div className={styles.progressBarSm}>
          <div
            className={styles.progressFillSm}
            style={{ width: `${row.original.progress_percent}%` }}
          />
        </div>
        {row.original.progress_percent}%
      </div>
    ),
  }),
];

const roomColumnCache = [];
function roomColumn(index) {
  roomColumnCache[index] ??= columnHelper.display({
    id: `room-${index}`,
    header: ({ table }) => table.options.meta.headerRooms[index]?.room_title,
    cell: ({ row }) => {
      const room = row.original.rooms[index];
      return room ? <>{room.completed_questions}/{room.total_questions}</> : null;
    },
    meta: { tdClassName: styles.numCell },
  });
  return roomColumnCache[index];
}

/* ══════════════ 進度監控 ══════════════ */
function ProgressPanel({ paths, initialPathId = "" }) {
  const { t } = useTranslation("teaching");
  const [pathId, setPathId] = useState(initialPathId);
  /* 報表走 TanStack Query：換路徑時有快取就先顯示那條路徑的上一份；即時推播（WebSocket）觸發的
     快照由下面自己排先後再寫進快取，所以這裡不輪詢、切回分頁也不重抓 */
  const progressQuery = usePathProgressQuery(pathId || null, { refetchInterval: false, refetchOnWindowFocus: false });
  const courseAdminCache = useCourseAdminCacheUpdater();
  const report = pathId ? progressQuery.data ?? null : null;
  /* 第一次就讀不到才顯示錯誤；已有報表時即時更新失敗就沿用舊報表。
     重抓期間維持錯誤畫面、不閃回載入中，按重試才回到載入中（見 useLoadError） */
  const { error: progressError, retry: retryProgress } = useLoadError(progressQuery);
  const failed = Boolean(progressError);
  /* 報表表格：關卡欄的表頭跟第一位學生的關卡走 */
  const students = report?.students ?? EMPTY;
  const headerRooms = students[0]?.rooms ?? EMPTY;
  const roomCount = headerRooms.length;
  const progressColumns = useMemo(
    () => [...fixedProgressColumns, ...Array.from({ length: roomCount }, (_, index) => roomColumn(index))],
    [roomCount],
  );
  const progressTable = useTable({
    features: baseFeatures,
    columns: progressColumns,
    data: students,
    getRowId: (student) => String(student.user_id),
    meta: { headerRooms },
  });
  const [live, setLive] = useState(false);
  const refetchTimer = useRef(null);
  const reportRequestRef = useRef(0);
  const appliedReportRequestRef = useRef(0);
  /* 目前顯示的是哪條路徑：慢回來的舊請求不可以蓋掉新路徑的報表 */
  const activePathIdRef = useRef("");
  activePathIdRef.current = pathId;

  useEffect(() => {
    if (initialPathId) setPathId(initialPathId);
  }, [initialPathId]);

  /* 推播觸發的快照：同時可能有好幾支在路上，晚送出的先回來就以它為準，慢回來的舊快照不可以蓋掉；
     已經回來的那份不等更新的請求，先顯示。失敗就沿用目前的報表（首次載入的錯誤由 query 自己顯示） */
  const fetchReport = useCallback((id) => {
    const requestId = ++reportRequestRef.current;
    CourseAdminService.getPathProgress(id)
      .then((data) => {
        if (activePathIdRef.current === id && requestId > appliedReportRequestRef.current) {
          appliedReportRequestRef.current = requestId;
          return courseAdminCache.setPathProgress(id, data);
        }
        return undefined;
      })
      .catch(() => {});
  }, [courseAdminCache]);

  useEffect(() => {
    setLive(false);
    if (!pathId) return undefined;
    /* 首次載入交給 query（換路徑就是換 key，不會顯示別條路徑的進度）；這裡只接推播 */

    // 合併 800 ms 內的事件；持續推播不能無限延後快照更新。
    const stop = connectReconnectingWebSocket(() => {
      const token = AuthStorage.getAccessToken();
      return token ? courseProgressWsUrl(pathId, token) : null;
    }, {
      onOpen: () => {
        clearTimeout(refetchTimer.current);
        refetchTimer.current = null;
        setLive(true);
        fetchReport(pathId);
      },
      onMessage: () => {
        if (refetchTimer.current !== null) return;
        refetchTimer.current = setTimeout(() => {
          refetchTimer.current = null;
          fetchReport(pathId);
        }, 800);
      },
      onClose: () => setLive(false),
    });

    return () => {
      appliedReportRequestRef.current = ++reportRequestRef.current;
      clearTimeout(refetchTimer.current);
      refetchTimer.current = null;
      stop();
    };
  }, [pathId, fetchReport]);

  return (
    <div className={styles.progressPanel} data-guide="course-cms-progress">
      <div className={styles.progressToolbar} data-guide="course-cms-progress-toolbar">
        <select
          className={styles.pathSelect}
          value={pathId}
          onChange={(e) => setPathId(e.target.value)}
          aria-label={t("CourseCmsPage.selectPathOption")}
        >
          <option value="">{t("CourseCmsPage.selectPathOption")}</option>
          {paths.map((p) => (
            <option key={p.id} value={p.id}>{p.title}</option>
          ))}
        </select>
        {pathId && (
          <span className={`${styles.liveBadge} ${live ? styles.liveOn : ""}`}>
            <span className={styles.liveDot} />
            {live ? t("CourseCmsPage.liveUpdating") : t("CourseCmsPage.connectionLost")}
          </span>
        )}
      </div>

      {/* 沒選路徑、載入中、讀取失敗、沒有學生紀錄都用整塊的狀態元件置中，不留一張只有表頭的空表格 */}
      {!pathId ? (
        <EmptyState icon="insights" title={t("CourseCmsPage.selectPathHint")} />
      ) : report ? (
        report.students.length === 0 ? (
          <EmptyState icon="insights" title={t("CourseCmsPage.noStudentRecordsText")} />
        ) : (
          <div className={styles.progressTableWrap}>
            <DataTable table={progressTable} className={styles.progressTable} />
          </div>
        )
      ) : failed ? (
        <ErrorState onRetry={() => retryProgress()} />
      ) : (
        <LoadingState />
      )}
    </div>
  );
}

/* ══════════════ 主頁 ══════════════ */
export default function CourseCmsPage() {
  const { t } = useTranslation("teaching");
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState(searchParams.get("tab") === "progress" ? "progress" : "editor");
  /* 路徑清單與班級清單走 TanStack Query。這是編輯頁：不輪詢，改動後由內容編輯區要求重抓；
     讀不到就當成空清單（與原本相同，不提示） */
  const pathsQuery = useAdminCoursePathsQuery({ refetchInterval: false });
  const paths = pathsQuery.data ?? EMPTY;
  /* 首載失敗後切回視窗重抓時不閃回載入中（沒資料時 v5 每次重抓（切回視窗）都把 status 改回 pending、清掉 error） */
  const pathsLoading = useQueryView(pathsQuery).loading;
  const classesQuery = useTeachingClassesQuery({ refetchInterval: false });
  const teachingClasses = classesQuery.data ?? EMPTY;
  const courseAdminCache = useCourseAdminCacheUpdater();

  const { confirmLeave } = useUnsavedChanges();

  /* 回傳 promise：新增路徑後要等清單重抓完再選中它（重抓失敗沿用舊清單，不丟例外） */
  const reloadPaths = useCallback(() => courseAdminCache.invalidatePaths(), [courseAdminCache]);

  /* 內容編輯有未儲存的修改時，切分頁先確認（會卸載編輯區） */
  async function changeTab(nextTab) {
    if (nextTab === tab) return;
    if (!(await confirmLeave())) return;
    setTab(nextTab);
    const next = new URLSearchParams(searchParams);
    next.set("tab", nextTab);
    if (nextTab !== "progress") next.delete("pathId");
    setSearchParams(next, { replace: true });
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("CourseCmsPage.pageTitle")}>
        <SegmentedControl
          options={[
            { value: "editor", label: t("CourseCmsPage.tabContentEditor"), icon: "edit_note", buttonProps: { "data-guide-tab": "course-cms-tab-editor" } },
            { value: "progress", label: t("CourseCmsPage.tabStudentProgress"), icon: "insights", buttonProps: { "data-guide-tab": "course-cms-tab-progress" } },
          ]}
          value={tab}
          onChange={changeTab}
          ariaLabel={t("CourseCmsPage.pageTitle")}
        />
      </PageHeader>

      {pathsLoading ? (
        <LoadingState fullPage />
      ) : tab === "editor" ? (
        <ContentEditor
          paths={paths}
          teachingClasses={teachingClasses}
          initialPathId={searchParams.get("pathId") ?? ""}
          onReloadPaths={reloadPaths}
        />
      ) : (
        <ProgressPanel paths={paths} initialPathId={searchParams.get("pathId") ?? ""} />
      )}
    </div>
  );
}
