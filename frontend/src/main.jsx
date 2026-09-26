import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@material-design-icons/font/outlined.css";
import "@material-design-icons/font/filled.css";
import App from "./App";
import { ThemeProvider } from "./contexts/ThemeContext";
import { AuthProvider }  from "./contexts/AuthContext";
import { ConfirmProvider } from "./components/ConfirmDialog/ConfirmProvider";
import { UnsavedChangesProvider } from "./contexts/UnsavedChangesContext";
import ErrorBoundary from "./components/ErrorBoundary/ErrorBoundary";
import AppToaster from "./components/AppToaster";
import "./assets/styles/global.scss";
import "./i18n";
import { initSentry } from "./utils/sentry";

// 建置時有 VITE_SENTRY_DSN 才會載入 SDK（動態 import，不影響首屏）
initSentry();

ReactDOM.createRoot(document.getElementById("root")).render(
  <ThemeProvider>
    <AuthProvider>
      <ConfirmProvider>
        <BrowserRouter>
          <UnsavedChangesProvider>
            {/* 根層 boundary：保住 DashboardLayout 以外的頁面（登入、導入、setup），
                避免整頁白畫面；頁面內容另有 DashboardLayout 的 ErrorBoundary 先攔 */}
            <ErrorBoundary>
              <App />
            </ErrorBoundary>
          </UnsavedChangesProvider>
          <AppToaster />
        </BrowserRouter>
      </ConfirmProvider>
    </AuthProvider>
  </ThemeProvider>,
);
