import { useCallback, useMemo, useState } from "react";
import { useAuth } from "./contexts/AuthContext";
import DashboardLayout from "./layout/DashboardLayout";
import LoginPage from "./pages/login/LoginPage";

// 個人
import DashboardPage   from "./pages/personal/dashboard/DashboardPage";
import ResourcesPage   from "./pages/personal/resources/ResourcesPage";
import RequestsPage    from "./pages/personal/requests/RequestsPage";

// 資源
import ResourceMgmtPage  from "./pages/resource/resource-mgmt/ResourceMgmtPage";
import RequestReviewPage from "./pages/resource/request-review/RequestReviewPage";
import GpuMgmtPage       from "./pages/resource/gpu-mgmt/GpuMgmtPage";
import BatchReviewPage   from "./pages/resource/batch-review/BatchReviewPage";

// AI
import AiApiPage       from "./pages/ai/ai-api/AiApiPage";
import AiApiReviewPage from "./pages/ai/ai-api-review/AiApiReviewPage";
import AiApiKeysPage   from "./pages/ai/ai-api-keys/AiApiKeysPage";
import AiMonitoringPage from "./pages/ai/ai-monitoring/AiMonitoringPage";
import AiManagementPage from "./pages/ai/ai-management/AiManagementPage";

// 系統管理
import GroupsPage    from "./pages/system/groups/GroupsPage";
import AdminPage     from "./pages/system/admin/AdminPage";
import SettingsPage  from "./pages/system/settings/SettingsPage";
import MigrationPage from "./pages/system/migration/MigrationPage";
import AuditPage     from "./pages/system/audit/AuditPage";
import JobsPage      from "./pages/system/jobs/JobsPage";

// 網路
import FirewallPage       from "./pages/network/firewall/FirewallPage";
import DomainPage         from "./pages/network/domain/DomainPage";
import GatewayPage        from "./pages/network/gateway/GatewayPage";
import ReverseProxyPage   from "./pages/network/reverse-proxy/ReverseProxyPage";
import IpManagementPage   from "./pages/network/ip-management/IpManagementPage";

function App() {
  const { user, loading } = useAuth();
  const [activePage, setActivePage] = useState("dashboard");
  const [pageIntent, setPageIntent] = useState(null);

  // 初始化時驗證 token，避免未登入畫面閃爍

  // 未登入 → 顯示登入頁

  const handleNavigate = useCallback((page, intent = null) => {
    setPageIntent(intent ? { ...intent, nonce: Date.now() } : null);
    setActivePage(page);
  }, []);

  const page = useMemo(() => {
    const pages = {
      dashboard:        <DashboardPage onNavigate={handleNavigate} />,
      "my-resources":   <ResourcesPage />,
      "my-requests":    <RequestsPage intent={pageIntent} />,
      "resource-mgmt":  <ResourceMgmtPage />,
      "request-review": <RequestReviewPage />,
      "gpu-mgmt":       <GpuMgmtPage />,
      "batch-review":   <BatchReviewPage />,
      "ai-api":         <AiApiPage />,
      "ai-api-review":  <AiApiReviewPage />,
      "ai-api-keys":    <AiApiKeysPage />,
      "ai-monitoring":  <AiMonitoringPage />,
      "ai-management":  <AiManagementPage />,
      groups:           <GroupsPage />,
      admin:            <AdminPage />,
      settings:         <SettingsPage />,
      migration:        <MigrationPage />,
      audit:            <AuditPage />,
      jobs:             <JobsPage />,
      firewall:         <FirewallPage />,
      domain:           <DomainPage />,
      gateway:          <GatewayPage />,
      "reverse-proxy":  <ReverseProxyPage />,
      "ip-management":  <IpManagementPage />,
    };
    return pages[activePage] ?? pages.dashboard;
  }, [activePage, handleNavigate, pageIntent]);

  if (loading) return null;

  if (!user) return <LoginPage />;

  return (
    <DashboardLayout activePage={activePage} onNavigate={handleNavigate}>
      {page}
    </DashboardLayout>
  );
}

export default App;
