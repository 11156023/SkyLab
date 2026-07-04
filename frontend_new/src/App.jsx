import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./contexts/AuthContext";
import DashboardLayout from "./layout/DashboardLayout";
import LoginPage from "./pages/login/LoginPage";

// 個人
import DashboardPage         from "./pages/personal/dashboard/DashboardPage";
import QuickTemplateFormPage from "./pages/personal/dashboard/QuickTemplateFormPage";
import ResourcesPage         from "./pages/personal/resources/ResourcesPage";
import ResourceDetailPage    from "./pages/personal/resources/detail/ResourceDetailPage";
import RequestsPage          from "./pages/personal/requests/RequestsPage";
import AccountSettingsPage   from "./pages/personal/account/AccountSettingsPage";

// 資源
import ResourceMgmtPage  from "./pages/resource/resource-mgmt/ResourceMgmtPage";
import RequestReviewPage from "./pages/resource/request-review/RequestReviewPage";
import GpuMgmtPage       from "./pages/resource/gpu-mgmt/GpuMgmtPage";
import BatchReviewPage   from "./pages/resource/batch-review/BatchReviewPage";
import TemplatesPage     from "./pages/resource/templates/TemplatesPage";

// AI
import AiApiPage       from "./pages/ai/ai-api/AiApiPage";
import AiApiReviewPage from "./pages/ai/ai-api-review/AiApiReviewPage";
import AiApiKeysPage   from "./pages/ai/ai-api-keys/AiApiKeysPage";
import AiMonitoringPage from "./pages/ai/ai-monitoring/AiMonitoringPage";
import AiManagementPage from "./pages/ai/ai-management/AiManagementPage";

// 教學
import TeachingPage from "./pages/teaching/TeachingPage";

// 系統管理
import GroupsPage    from "./pages/system/groups/GroupsPage";
import AdminPage     from "./pages/system/admin/AdminPage";
import SettingsPage  from "./pages/system/settings/SettingsPage";
import MonitoringPage from "./pages/system/monitoring/MonitoringPage";
import MigrationPage from "./pages/system/migration/MigrationPage";
import AuditPage     from "./pages/system/audit/AuditPage";
import JobsPage      from "./pages/system/jobs/JobsPage";
import DeployLogsPage from "./pages/system/deploy-logs/DeployLogsPage";

// 網路
import FirewallPage       from "./pages/network/firewall/FirewallPage";
import DomainPage         from "./pages/network/domain/DomainPage";
import GatewayPage        from "./pages/network/gateway/GatewayPage";
import ReverseProxyPage   from "./pages/network/reverse-proxy/ReverseProxyPage";
import IpManagementPage   from "./pages/network/ip-management/IpManagementPage";

function App() {
  const { user, loading } = useAuth();

  if (loading) return null;

  return (
    <Routes>
      <Route
        path="/login"
        element={user ? <Navigate to="/dashboard" replace /> : <LoginPage />}
      />

      {user ? (
        <Route element={<DashboardLayout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />

          {/* 個人 */}
          <Route path="/dashboard"            element={<DashboardPage />} />
          <Route path="/quick-template/:slug" element={<QuickTemplateFormPage />} />
          <Route path="/my-resources"         element={<ResourcesPage />} />
          <Route path="/my-resources/:vmid"   element={<ResourceDetailPage backTo="/my-resources" />} />
          <Route path="/my-requests"          element={<RequestsPage />} />
          <Route path="/account"              element={<AccountSettingsPage />} />

          {/* 資源 */}
          <Route path="/resource-mgmt"  element={<ResourceMgmtPage />} />
          <Route path="/resource-mgmt/:vmid" element={<ResourceDetailPage backTo="/resource-mgmt" />} />
          <Route path="/request-review" element={<RequestReviewPage />} />
          <Route path="/gpu-mgmt"       element={<GpuMgmtPage />} />
          <Route path="/batch-review"   element={<BatchReviewPage />} />
          <Route path="/templates"      element={<TemplatesPage />} />

          {/* AI */}
          <Route path="/ai-api"         element={<AiApiPage />} />
          <Route path="/ai-api-review"  element={<AiApiReviewPage />} />
          <Route path="/ai-api-keys"    element={<AiApiKeysPage />} />
          <Route path="/ai-monitoring"  element={<AiMonitoringPage />} />
          <Route path="/ai-management"  element={<AiManagementPage />} />

          {/* 教學 */}
          <Route path="/teaching"  element={<TeachingPage />} />

          {/* 系統管理 */}
          <Route path="/groups"    element={<GroupsPage />} />
          <Route path="/admin"     element={<AdminPage />} />
          <Route path="/settings"  element={<SettingsPage />} />
          <Route path="/monitoring" element={<MonitoringPage />} />
          <Route path="/migration" element={<MigrationPage />} />
          <Route path="/audit"     element={<AuditPage />} />
          <Route path="/jobs"      element={<JobsPage />} />
          <Route path="/deploy-logs" element={<DeployLogsPage />} />

          {/* 網路 */}
          <Route path="/firewall"       element={<FirewallPage />} />
          <Route path="/domain"         element={<DomainPage />} />
          <Route path="/gateway"        element={<GatewayPage />} />
          <Route path="/reverse-proxy"  element={<ReverseProxyPage />} />
          <Route path="/ip-management"  element={<IpManagementPage />} />

          {/* fallback */}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      ) : (
        <Route path="*" element={<Navigate to="/login" replace />} />
      )}
    </Routes>
  );
}

export default App;
