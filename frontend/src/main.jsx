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
import AppToaster from "./components/AppToaster";
import "./assets/styles/global.scss";
import "./i18n";

ReactDOM.createRoot(document.getElementById("root")).render(
  <ThemeProvider>
    <AuthProvider>
      <ConfirmProvider>
        <BrowserRouter>
          <UnsavedChangesProvider>
            <App />
          </UnsavedChangesProvider>
          <AppToaster />
        </BrowserRouter>
      </ConfirmProvider>
    </AuthProvider>
  </ThemeProvider>,
);
