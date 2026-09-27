import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useUnsavedChanges } from "../../../contexts/UnsavedChangesContext";
import { AuthStorage } from "../../../services/auth";
import {
  CourseAdminService,
  courseProgressWsUrl,
} from "../../../services/courses";
import { TeachingClassesService } from "../../../services/teachingClasses";
import styles from "./CourseCmsPage.module.scss";
import PageHeader from "../../../components/PageHeader/PageHeader";
import ContentEditor from "./ContentEditor";

/* ══════════════ 進度監控 ══════════════ */
function ProgressPanel({ paths, initialPathId = "" }) {
  const { t } = useTranslation("teaching");
  const [pathId, setPathId] = useState(initialPathId);
  const [report, setReport] = useState(null);
  const [failed, setFailed] = useState(false);
  const [live, setLive] = useState(false);
  const refetchTimer = useRef(null);
  /* 目前顯示的是哪條路徑：慢回來的舊請求不可以蓋掉新路徑的報表 */
  const activePathIdRef = useRef("");
  activePathIdRef.current = pathId;

  useEffect(() => {
    if (initialPathId) setPathId(initialPathId);
  }, [initialPathId]);

  const fetchReport = useCallback((id) => {
    CourseAdminService.getPathProgress(id)
      .then((data) => {
        if (activePathIdRef.current === id) {
          setReport(data);
          setFailed(false);
        }
      })
      /* 第一次就讀不到才顯示錯誤；已有報表時即時更新失敗就沿用舊報表 */
      .catch(() => {
        if (activePathIdRef.current === id) setFailed(true);
      });
  }, []);

  useEffect(() => {
    if (!pathId) {
      setReport(null);
      return undefined;
    }
    /* 先清掉上一條路徑的報表，免得新報表還沒回來前顯示的是別條路徑的進度 */
    setReport(null);
    setFailed(false);
    fetchReport(pathId);

    // WS 即時推播：收到事件後 debounce 重拉快照
    const token = AuthStorage.getAccessToken() ?? "";
    const ws = new WebSocket(courseProgressWsUrl(pathId, token));
    ws.onopen = () => setLive(true);
    ws.onmessage = () => {
      clearTimeout(refetchTimer.current);
      refetchTimer.current = setTimeout(() => fetchReport(pathId), 800);
    };
    ws.onclose = () => setLive(false);
    ws.onerror = () => setLive(false);

    return () => {
      clearTimeout(refetchTimer.current);
      ws.close();
    };
  }, [pathId, fetchReport]);

  return (
    <div className={styles.progressPanel}>
      <div className={styles.progressToolbar}>
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
            <table className={styles.progressTable}>
              <thead>
                <tr>
                  <th>{t("CourseCmsPage.thStudent")}</th>
                  <th>{t("CourseCmsPage.thTotalProgress")}</th>
                  {report.students[0]?.rooms.map((r) => (
                    <th key={r.room_id}>{r.room_title}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.students.map((s) => (
                  <tr key={s.user_id}>
                    <td>
                      <div className={styles.studentCell}>
                        <span>{s.user_name ?? s.user_email}</span>
                        <span className={styles.studentEmail}>{s.user_email}</span>
                      </div>
                    </td>
                    <td>
                      <div className={styles.cellProgress}>
                        <div className={styles.progressBarSm}>
                          <div
                            className={styles.progressFillSm}
                            style={{ width: `${s.progress_percent}%` }}
                          />
                        </div>
                        {s.progress_percent}%
                      </div>
                    </td>
                    {s.rooms.map((r) => (
                      <td key={r.room_id} className={styles.numCell}>
                        {r.completed_questions}/{r.total_questions}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : failed ? (
        <ErrorState onRetry={() => { setFailed(false); fetchReport(pathId); }} />
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
  const [pathsLoading, setPathsLoading] = useState(true);
  const [paths, setPaths] = useState([]);
  const [teachingClasses, setTeachingClasses] = useState([]);

  const { confirmLeave } = useUnsavedChanges();

  /* 回傳 promise：新增路徑後要等清單更新完再選中它 */
  const reloadPaths = useCallback(() => {
    return CourseAdminService.listPaths()
      .then(setPaths)
      .catch(() => {})
      .finally(() => setPathsLoading(false));
  }, []);

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

  useEffect(() => {
    reloadPaths();
    TeachingClassesService.list().then(setTeachingClasses).catch(() => {});
  }, [reloadPaths]);

  return (
    <div className={styles.page}>
      <PageHeader title={t("CourseCmsPage.pageTitle")}>
        <SegmentedControl
          options={[
            { value: "editor", label: t("CourseCmsPage.tabContentEditor"), icon: "edit_note" },
            { value: "progress", label: t("CourseCmsPage.tabStudentProgress"), icon: "insights" },
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
