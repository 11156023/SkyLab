import React from "react";
import ReactDOM from "react-dom/client";
import { Toaster } from "sonner";
import "@material-design-icons/font/outlined.css";
import "@material-design-icons/font/filled.css";
import App from "./App";
import { ThemeProvider } from "./contexts/ThemeContext";
import { AuthProvider }  from "./contexts/AuthContext";
import "./assets/styles/global.scss";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <App />
        <Toaster
          position="top-right"
          richColors
          closeButton
          toastOptions={{ duration: 4000 }}
        />
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
