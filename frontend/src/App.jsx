import { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "./contexts/AuthContext";
import DashboardLayout from "./layout/DashboardLayout";
import LoginPage from "./pages/login/LoginPage";
import MIcon from "./components/MIcon";
import { LoadingSpinner } from "./components/LoadingState/LoadingState";
import { AuthSessionStatus } from "./services/authSession";
import { useSetupStatus } from "./pages/setup/useSetupStatus";
import styles from "./App.module.scss";

// 導入介紹首頁（未登入的 /；獨立 chunk，gsap 只在這裡載入）
const LandingPage = lazy(() => import("./pages/landing/LandingPage"));
// 首次安裝初始化精靈（免登入；後端 system_setup.completed 之前登入頁會導過來）
const SetupPage = lazy(() => import("./pages/setup/SetupPage"));
// 404：登入後開到不存在的路徑
const NotFoundPage = lazy(() => import("./pages/not-found/NotFoundPage"));

// 個人
const AdminDashboardPage = lazy(() => import("./pages/personal/dashboard/admin/AdminDashboardPage"));
const TeacherDashboardPage = lazy(() => import("./pages/personal/dashboard/teacher/TeacherDashboardPage"));
const StudentHomePage = lazy(() => import("./pages/personal/dashboard/StudentHomePage"));
const StudentCoursesPage = lazy(() => import("./pages/personal/courses/StudentCoursesPage"));
const StudentCoursePage = lazy(() => import("./pages/personal/dashboard/student/StudentCoursePage"));
const StudentWeekPage = lazy(() => import("./pages/personal/courses/StudentWeekPage"));
const QuickTemplateFormPage = lazy(() => import("./pages/personal/quick-practice/QuickTemplateFormPage"));
const QuickCreatePage = lazy(() => import("./pages/personal/quick-practice/QuickCreatePage"));
const ResourcesPage = lazy(() => import("./pages/personal/resources/ResourcesPage"));
const ResourceDetailPage = lazy(() => import("./pages/personal/resources/detail/ResourceDetailPage"));
const RequestsPage = lazy(() => import("./pages/personal/requests/RequestsPage"));
const AccountSettingsPage = lazy(() => import("./pages/personal/account/AccountSettingsPage"));

// 資源
const ResourceMgmtPage = lazy(() => import("./pages/resource/resource-mgmt/ResourceMgmtPage"));
const RequestReviewPage = lazy(() => import("./pages/resource/request-review/RequestReviewPage"));
const GpuMgmtPage = lazy(() => import("./pages/resource/gpu-mgmt/GpuMgmtPage"));
const BatchReviewPage = lazy(() => import("./pages/resource/batch-review/BatchReviewPage"));
const TemplatesPage = lazy(() => import("./pages/resource/templates/TemplatesPage"));

// AI
const AiApiPage = lazy(() => import("./pages/ai/ai-api/AiApiPage"));
const AiApiReviewPage = lazy(() => import("./pages/ai/ai-api-review/AiApiReviewPage"));
const AiApiKeysPage = lazy(() => import("./pages/ai/ai-api-keys/AiApiKeysPage"));
const AiMonitoringPage = lazy(() => import("./pages/ai/ai-monitoring/AiMonitoringPage"));

// 教學
const CourseCmsPage = lazy(() => import("./pages/teaching/course-cms/CourseCmsPage"));
const CourseTemplateManagementPage = lazy(() => import("./pages/course-operations/course-templates/CourseTemplateManagementPage"));
const CourseTemplateEditorPage = lazy(() => import("./pages/course-operations/course-templates/CourseTemplateEditorPage"));
const ClassManagementPage = lazy(() => import("./pages/course-operations/class-management/ClassManagementPage"));
const ClassWorkspacePage = lazy(() => import("./pages/course-operations/class-workspace/ClassWorkspacePage"));
const AiJudgePage = lazy(() => import("./pages/course-operations/ai-judge/AiJudgePage"));
const ClassSetupPage = lazy(() => import("./pages/course-operations/class-setup/ClassSetupPage"));

// 系統管理
const AdminPage = lazy(() => import("./pages/system/admin/AdminPage"));
const PveConnectionsPage = lazy(() => import("./pages/system/settings/PveConnectionsPage"));
const SchedulerPage = lazy(() => import("./pages/system/settings/SchedulerPage"));
const GovernancePage = lazy(() => import("./pages/system/settings/GovernancePage"));
const QuotasPage = lazy(() => import("./pages/system/settings/QuotasPage"));
const LdapPage = lazy(() => import("./pages/system/settings/LdapPage"));
const NodesPage = lazy(() => import("./pages/system/settings/NodesPage"));
const StoragePage = lazy(() => import("./pages/system/settings/StoragePage"));
const MonitoringPage = lazy(() => import("./pages/system/monitoring/MonitoringPage"));
const IpManagementPage = lazy(() => import("./pages/system/ip-management/IpManagementPage"));
const AuditPage = lazy(() => import("./pages/system/audit/AuditPage"));
const JobsPage = lazy(() => import("./pages/system/jobs/JobsPage"));

// 網路
const FirewallPage = lazy(() => import("./pages/network/firewall/FirewallPage"));
const DomainPage = lazy(() => import("./pages/system/domain/DomainPage"));
const GatewayPage = lazy(() => import("./pages/system/gateway/GatewayPage"));
const ReverseProxyPage = lazy(() => import("./pages/network/reverse-proxy/ReverseProxyPage"));

function AuthBootstrapState({ unavailable = false, retrying = false, onRetry }) {
  const { t } = useTranslation("common");
  return (
    <main className={styles.authStatePage}>
      <section className={styles.authStateCard} role={unavailable ? "alert" : "status"}>
        {unavailable ? (
          <span className={styles.authStateIcon} aria-hidden="true">
            <MIcon name="cloud_off" size={42} />
          </span>
        ) : (
          <LoadingSpinner size={42} />
        )}
        <h1 className={styles.authStateTitle}>
          {unavailable ? t("App.connectionUnavailable") : t("App.verifyingLogin")}
        </h1>
        <p className={styles.authStateDescription}>
          {unavailable
            ? t("App.connectionUnavailableDesc")
            : t("App.verifyingLoginDesc")}
        </p>
        {unavailable && (
          <button
            type="button"
            className={styles.retryButton}
            disabled={retrying}
            onClick={onRetry}
          >
            <span aria-hidden="true">
              <MIcon name="refresh" size={18} spin={retrying} />
            </span>
            {retrying ? t("App.retrying") : t("App.retryConnect")}
          </button>
        )}
      </section>
    </main>
  );
}

/** 登入頁：後端還沒初始化（沒有管理員／PVE）就先導去精靈，狀態取不到時照常顯示登入 */
function LoginRoute() {
  const { status, loading, setupRequired } = useSetupStatus();
  if (loading && !status) return <AuthBootstrapState />;
  if (setupRequired) return <Navigate to="/setup" replace />;
  return <LoginPage />;
}

function LegacyAiJudgeEditorRedirect() {
  const { classId, sessionId } = useParams();
  const query = sessionId ? `?check=${encodeURIComponent(sessionId)}` : "";
  return <Navigate to={`/class-management/${classId}/ai${query}`} replace />;
}

function LegacyStudentCourseRedirect() {
  const { pathId } = useParams();
  return <Navigate to={`/courses/${encodeURIComponent(pathId)}`} replace />;
}

/** 舊「系統設定」的 ?tab= 值 → 升格後的獨立頁面；沒帶 tab 就是原本的第一個分頁（PVE 連線）。 */
const LEGACY_SETTINGS_TABS = {
  pve: "/pve-connections",
  scheduler: "/scheduler",
  governance: "/governance",
  quotas: "/quotas",
  ldap: "/ldap",
  nodes: "/nodes",
  storage: "/storage",
};

function LegacySettingsRedirect() {
  const [searchParams] = useSearchParams();
  const target = LEGACY_SETTINGS_TABS[searchParams.get("tab")] ?? LEGACY_SETTINGS_TABS.pve;
  return <Navigate to={target} replace />;
}

function App() {
  const { user, loading, authStatus, retrySession } = useAuth();
  const isAdmin = Boolean(user?.is_superuser || user?.role === "admin");
  const canTeach = isAdmin || user?.role === "teacher";
  const isDeviceApproval = Boolean(
    new URLSearchParams(window.location.search).get("device_code"),
  );

  /* 導入頁是純靜態內容，不依賴 session 檢查：
     後端連不上或 session 驗證中時，/ 照樣直接呈現，其餘路徑維持原本的啟動畫面。 */
  const landingElement = (
    <Suspense fallback={null}>
      <LandingPage />
    </Suspense>
  );

  if (authStatus === AuthSessionStatus.UNAVAILABLE && !user) {
    return (
      <Routes>
        <Route path="/" element={landingElement} />
        <Route
          path="*"
          element={
            <AuthBootstrapState
              unavailable
              retrying={loading}
              onRetry={retrySession}
            />
          }
        />
      </Routes>
    );
  }
  if (loading && !user) {
    return (
      <Routes>
        <Route path="/" element={landingElement} />
        <Route path="*" element={<AuthBootstrapState />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route
        path="/login"
        element={
          user && !isDeviceApproval ? (
            <Navigate to="/dashboard" replace />
          ) : (
            <LoginRoute />
          )
        }
      />
      {/* 初始化精靈：登入前後都可開，完成後頁面自己會提示已初始化 */}
      <Route
        path="/setup"
        element={
          <Suspense fallback={<AuthBootstrapState />}>
            <SetupPage />
          </Suspense>
        }
      />

      {user ? (
        <Route element={<DashboardLayout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />

          {/* 個人 */}
          {/* 首頁依角色顯示：admin → 管理首頁、teacher → 教師首頁、其他 → 學生首頁 */}
          <Route
            path="/dashboard"
            element={
              isAdmin
                ? <AdminDashboardPage />
                : user?.role === "teacher"
                  ? <TeacherDashboardPage />
                  : <StudentHomePage />
            }
          />
          {/* 單一課程總覽：課堂環境、課堂機器與截至今天的 AI 任務 */}
          <Route path="/courses" element={!canTeach ? <StudentCoursesPage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/courses/:pathId" element={!canTeach ? <StudentCoursePage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/courses/:pathId/weeks/:weekId" element={!canTeach ? <StudentWeekPage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/dashboard/course/:pathId" element={!canTeach ? <LegacyStudentCourseRedirect /> : <Navigate to="/dashboard" replace />} />
          {/* 快速練習：學生、老師、管理者共用，側欄「教學」群組的入口 */}
          <Route path="/quick-create"         element={<QuickCreatePage />} />
          <Route path="/quick-template/:id"   element={<QuickTemplateFormPage />} />
          <Route path="/my-resources"         element={<ResourcesPage />} />
          <Route path="/my-resources/:vmid"   element={<ResourceDetailPage backTo="/my-resources" />} />
          <Route path="/my-requests"          element={<RequestsPage />} />
          <Route path="/account"              element={<AccountSettingsPage />} />

          {/* 資源 */}
          {isAdmin && (
            <>
              <Route path="/resource-mgmt"  element={<ResourceMgmtPage />} />
              <Route path="/resource-mgmt/:vmid" element={<ResourceDetailPage backTo="/resource-mgmt" />} />
              <Route path="/request-review" element={<RequestReviewPage />} />
              <Route path="/gpu-mgmt"       element={<GpuMgmtPage />} />
              <Route path="/batch-review"   element={<BatchReviewPage />} />
            </>
          )}
          {/* 機器範本頁只給老師／管理員；學生在申請表單的「資源設定」選用範本 */}
          <Route path="/templates"      element={canTeach ? <TemplatesPage /> : <Navigate to="/my-requests" state={{ create: true }} replace />} />

          {/* AI */}
          <Route path="/ai-api"         element={<AiApiPage />} />
          {isAdmin && (
            <>
              <Route path="/ai-api-review" element={<AiApiReviewPage />} />
              <Route path="/ai-api-keys" element={<AiApiKeysPage />} />
              <Route path="/ai-monitoring" element={<AiMonitoringPage />} />
            </>
          )}
          <Route
            path="/ai-management"
            element={<Navigate to={isAdmin ? "/ai-monitoring" : "/ai-api"} replace />}
          />

          {/* 教學 */}
          <Route path="/course-cms"            element={canTeach ? <CourseCmsPage /> : <Navigate to="/dashboard" replace />} />

          {/* 課務管理 */}
          <Route path="/course-template-management" element={canTeach ? <CourseTemplateManagementPage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/course-template-management/new" element={canTeach ? <CourseTemplateEditorPage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/course-template-management/:templateId" element={canTeach ? <CourseTemplateEditorPage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/class-management" element={canTeach ? <ClassManagementPage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/class-management/new" element={<Navigate to={canTeach ? "/class-setup" : "/dashboard"} replace />} />
          <Route path="/class-setup" element={canTeach ? <ClassSetupPage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/class-management/:classId/ai" element={canTeach ? <AiJudgePage /> : <Navigate to="/dashboard" replace />} />
          {/* 舊檢查表連結保留導回主工作頁，避免書籤落到不存在的獨立 editor。 */}
          <Route
            path="/class-management/:classId/ai/checks/:sessionId/edit"
            element={canTeach ? <LegacyAiJudgeEditorRedirect /> : <Navigate to="/dashboard" replace />}
          />
          <Route path="/class-management/:classId" element={canTeach ? <ClassWorkspacePage /> : <Navigate to="/dashboard" replace />} />
          <Route path="/class-management/:classId/:section" element={canTeach ? <ClassWorkspacePage /> : <Navigate to="/dashboard" replace />} />

          {/* 系統管理 */}
          {isAdmin && (
            <>
              <Route path="/admin"     element={<AdminPage />} />
              {/* 原「系統設定」的七個分頁，2026-09 各自升格為獨立頁面 */}
              <Route path="/pve-connections" element={<PveConnectionsPage />} />
              <Route path="/scheduler" element={<SchedulerPage />} />
              <Route path="/governance" element={<GovernancePage />} />
              <Route path="/quotas"    element={<QuotasPage />} />
              <Route path="/ldap"      element={<LdapPage />} />
              <Route path="/nodes"     element={<NodesPage />} />
              <Route path="/storage"   element={<StoragePage />} />
              {/* 舊的 /settings?tab=… 書籤依分頁導到對應的新頁面 */}
              <Route path="/settings"  element={<LegacySettingsRedirect />} />
              <Route path="/ip-management" element={<IpManagementPage />} />
              <Route path="/monitoring" element={<MonitoringPage />} />
              <Route path="/audit"     element={<AuditPage />} />
            </>
          )}
          <Route path="/jobs"      element={<JobsPage />} />

          {/* 網路 */}
          <Route path="/firewall"       element={<FirewallPage />} />
          {isAdmin && (
            <>
              <Route path="/domain"         element={<DomainPage />} />
              <Route path="/gateway"        element={<GatewayPage />} />
            </>
          )}
          {/* 反向代理頁已併入網域管理（管理員）；一般使用者請到資源詳情的進階設定 */}
          <Route
            path="/reverse-proxy"
            element={<Navigate to={isAdmin ? "/domain?tab=reverse-proxy" : "/my-resources"} replace />}
          />

          {/* 404：不明路徑顯示找不到頁面，不再靜默導回儀表板 */}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      ) : (
        <>
          {/* 未登入的 / 是導入介紹首頁,其餘路徑照舊導向登入 */}
          <Route path="/" element={landingElement} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </>
      )}
    </Routes>
  );
}

export default App;
