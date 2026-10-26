/**
 * /dev/style — 開發用的樣式對照頁：用真元件渲染 style guide 的每一條規範，旁邊標元件路徑與 mixin 名稱。
 * 只在 import.meta.env.DEV 註冊（App.jsx），正式 build 不含這頁；不需登入、不打後端，資料都是假的。
 * 與 claude.ai 的「樣式對照」「流程對照」Artifact 並存：這頁永不過時，Artifact 不用登入可給組員看。
 * 頂欄的亮暗／風格切換直接走 ThemeContext（會存進 localStorage，跟帳號設定的外觀分頁是同一份偏好）。
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { STYLE_OPTIONS, THEME_OPTIONS, useTheme } from "../../contexts/ThemeContext";
import { SUPPORTED_LANGUAGES, setLanguage } from "../../i18n";
import SegmentedControl from "../../components/SegmentedControl/SegmentedControl";
import OctoMark from "../../components/OctoPet/OctoMark";
import { ColorsSection, DimsSection, TypeSection, ZIndexSection } from "./sections/FoundationSections";
import { BadgesSection, ButtonsSection, FormsSection, SegmentSection, StepperSection } from "./sections/ControlSections";
import { CardRulesSection, DropzoneSection, TablesSection } from "./sections/DataSections";
import { EmptyStateSection, LoadingSection, MiscSection, ModalSection, ToastSection } from "./sections/StateSections";
import { ConnectionLostSection, CrashSection, NotFoundSection, TotpSection } from "./sections/FlowSections";
import styles from "./DevStylePage.module.scss";

const LANGUAGE_LABELS = { "zh-TW": "中文", en: "English", ja: "日本語" };

/* 分區順序照 Artifact 樣式對照頁；流程型放第二個分頁 */
const TABS = {
  components: {
    label: "共用元件",
    sections: [
      { id: "colors", label: "顏色", Component: ColorsSection },
      { id: "type", label: "字體", Component: TypeSection },
      { id: "dims", label: "尺寸", Component: DimsSection },
      { id: "buttons", label: "按鈕", Component: ButtonsSection },
      { id: "forms", label: "表單", Component: FormsSection },
      { id: "tables", label: "表格／工具列", Component: TablesSection },
      { id: "dropzone", label: "檔案上傳", Component: DropzoneSection },
      { id: "cardrules", label: "卡片規則", Component: CardRulesSection },
      { id: "badges", label: "徽章", Component: BadgesSection },
      { id: "segment", label: "分段切換", Component: SegmentSection },
      { id: "stepper", label: "步驟列", Component: StepperSection },
      { id: "emptystate", label: "空狀態", Component: EmptyStateSection },
      { id: "modal", label: "對話框", Component: ModalSection },
      { id: "toast", label: "通知", Component: ToastSection },
      { id: "loading", label: "載入狀態", Component: LoadingSection },
      { id: "misc", label: "其他元件", Component: MiscSection },
      { id: "zindex", label: "層級", Component: ZIndexSection },
    ],
  },
  flows: {
    label: "流程型",
    sections: [
      { id: "notfound", label: "404", Component: NotFoundSection },
      { id: "totp", label: "兩步驟驗證", Component: TotpSection },
      { id: "crash", label: "畫面當掉", Component: CrashSection },
      { id: "connectionlost", label: "無法連線", Component: ConnectionLostSection },
    ],
  },
};

function readTab() {
  const hash = window.location.hash.replace(/^#/, "");
  return TABS.flows.sections.some((s) => s.id === hash) ? "flows" : "components";
}

export default function DevStylePage() {
  const { i18n } = useTranslation();
  const { theme, mode, setMode, style, setStyle } = useTheme();
  /* 分頁跟著網址 hash：直接開 /dev/style#totp 會落在流程型分頁 */
  const [tab, setTab] = useState(readTab);
  useEffect(() => {
    const sync = () => setTab(readTab());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  /* 頂欄在窄一點的視窗會折成兩行：量實際高度，目錄的 sticky 位置與錨點捲動的留白都跟著它 */
  const barRef = useRef(null);
  const [barHeight, setBarHeight] = useState(64);
  useEffect(() => {
    const el = barRef.current;
    if (!el) return undefined;
    const update = () => setBarHeight(el.offsetHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    document.documentElement.style.scrollPaddingTop = `${barHeight + 16}px`;
    return () => document.documentElement.style.removeProperty("scroll-padding-top");
  }, [barHeight]);
  const sections = TABS[tab].sections;

  /* 白底限淺色、黑底限深色（ThemeContext 會自動對調），不符目前明暗的不列 */
  const styleOptions = STYLE_OPTIONS
    .filter((opt) => !(opt.key === "white" && theme === "dark") && !(opt.key === "black" && theme === "light"))
    .map((opt) => ({ value: opt.key, label: opt.label, icon: opt.icon }));
  const modeOptions = THEME_OPTIONS.map((opt) => ({ value: opt.key, label: opt.label, icon: opt.icon }));
  const languageOptions = SUPPORTED_LANGUAGES.map((lang) => ({ value: lang, label: LANGUAGE_LABELS[lang] ?? lang }));
  const tabOptions = Object.entries(TABS).map(([key, value]) => ({ value: key, label: value.label }));

  function selectTab(next) {
    setTab(next);
    window.location.hash = TABS[next].sections[0].id;
  }

  return (
    <div className={styles.page}>
      <header ref={barRef} className={styles.topBar}>
        <div className={styles.topBarInner}>
          <div className={styles.brand}>
            <OctoMark size={28} />
            <span className={styles.brandTitle}>樣式對照</span>
            <span className={styles.brandSub}>/dev/style · 真元件 · 僅開發模式</span>
          </div>
          <div className={styles.controlGroup}>
            <SegmentedControl ariaLabel="對照分頁" options={tabOptions} value={tab} onChange={selectTab} />
          </div>
          <div className={styles.controlGroup}>
            <SegmentedControl ariaLabel="明暗模式" options={modeOptions} value={mode} onChange={setMode} />
          </div>
          <div className={styles.controlGroup}>
            <SegmentedControl ariaLabel="介面風格" options={styleOptions} value={style} onChange={setStyle} />
          </div>
          <div className={styles.controlGroup}>
            <SegmentedControl ariaLabel="介面語言" options={languageOptions} value={i18n.language} onChange={setLanguage} />
          </div>
        </div>
      </header>

      <div className={styles.layout}>
        <nav className={styles.nav} aria-label="分區目錄" style={{ top: barHeight + 16, maxHeight: `calc(100dvh - ${barHeight + 32}px)` }}>
          <p className={styles.navTitle}>{TABS[tab].label}</p>
          <ul className={styles.navList}>
            {sections.map((section) => (
              <li key={section.id}>
                <a className={styles.navLink} href={`#${section.id}`}>
                  {section.label}
                  <span className={styles.navKey}>{section.id}</span>
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <main className={styles.content}>
          <p className={styles.pageIntro}>
            每一區都是真元件的實例，旁邊的晶片是元件路徑與 mixin 名稱；文字規範在 website/guides/frontend-style-guide.md。
            資料全是假的，按鈕只會跳 toast，不會打後端。切換頂欄的明暗與風格會寫進你自己的外觀偏好。
          </p>
          {sections.map(({ id, Component }) => <Component key={id} />)}
        </main>
      </div>
    </div>
  );
}
