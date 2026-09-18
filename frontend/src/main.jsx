import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import "@material-design-icons/font/outlined.css";
import "@material-design-icons/font/filled.css";
import App from "./App";
import { ThemeProvider } from "./contexts/ThemeContext";
import { AuthProvider }  from "./contexts/AuthContext";
import { ConfirmProvider } from "./components/ConfirmDialog/ConfirmProvider";
import { UnsavedChangesProvider } from "./contexts/UnsavedChangesContext";
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
          <Toaster
            position="top-right"
            richColors
            closeButton
            toastOptions={{ duration: 4000 }}
          />
        </BrowserRouter>
      </ConfirmProvider>
    </AuthProvider>
  </ThemeProvider>,
);
