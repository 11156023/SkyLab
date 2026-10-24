import { createRoot, hydrateRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";
import LandingPage from "./landing/LandingPage";
import { createI18n } from "./i18n";
import { languageFromPath } from "./languages";
import "./site.scss";

const container = document.getElementById("root");
const app = (
  <I18nextProvider i18n={createI18n(languageFromPath(window.location.pathname))}>
    <LandingPage />
  </I18nextProvider>
);

// 正式建置的每個語言頁都是預渲染好的 HTML，直接接手；dev server 沒有預渲染，從頭畫
if (container.firstElementChild) hydrateRoot(container, app);
else createRoot(container).render(app);
