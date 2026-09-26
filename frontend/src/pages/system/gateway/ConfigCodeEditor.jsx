import { Fragment, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import * as monaco from "monaco-editor";
import Editor, { loader } from "@monaco-editor/react";
import styles from "./ConfigCodeEditor.module.scss";
import MIcon from "../../../components/MIcon";

/* ── Monaco 本地打包（不走 CDN，離線環境可用）──────────
   worker 由 monaco 0.5x ESM 內建的 new URL(..., import.meta.url)
   交給 vite 打包，毋須手動設定 MonacoEnvironment */
loader.config({ monaco });

/* Monaco 沒有內建 TOML；用 ini 高亮會把行首「;」誤標成合法註解
   （TOML 只接受「#」），因此註冊一個極簡 TOML Monarch tokenizer */
if (!monaco.languages.getLanguages().some((lang) => lang.id === "toml")) {
  monaco.languages.register({ id: "toml" });
  monaco.languages.setLanguageConfiguration("toml", {
    comments: { lineComment: "#" },
    brackets: [["[", "]"], ["{", "}"]],
  });
  monaco.languages.setMonarchTokensProvider("toml", {
    defaultToken: "",
    tokenizer: {
      root: [
        [/^\s*#.*$/, "comment"],
        [/^\s*\[\[?[^\]]*\]?\]?/, "type"],
        [/^(\s*)([\w.-]+)(\s*=)/, ["white", "key", "delimiter"]],
        [/"""/, { token: "string", next: "@mstring" }],
        [/"(?:[^"\\]|\\.)*"/, "string"],
        [/'[^']*'/, "string"],
        [/\b(?:true|false)\b/, "keyword"],
        [/\d{4}-\d{2}-\d{2}[Tt ]?[\d:.Zz+-]*/, "number"],
        [/[+-]?\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?/, "number"],
        [/#.*$/, "comment"],
      ],
      mstring: [
        [/"""/, { token: "string", next: "@pop" }],
        [/[^"]+/, "string"],
        [/"/, "string"],
      ],
    },
  });
}

/* haproxy 沒有內建語言，註冊一個極簡 Monarch tokenizer */
if (!monaco.languages.getLanguages().some((lang) => lang.id === "haproxy")) {
  monaco.languages.register({ id: "haproxy" });
  monaco.languages.setLanguageConfiguration("haproxy", {
    comments: { lineComment: "#" },
  });
  monaco.languages.setMonarchTokensProvider("haproxy", {
    defaultToken: "",
    tokenizer: {
      root: [
        [/#.*$/, "comment"],
        [
          /^(?:global|defaults|frontend|backend|listen|peers|resolvers|userlist|mailers|program|ring|cache|http-errors|fcgi-app)\b/,
          "type",
        ],
        [
          /^[ \t]+(?:bind|server|default-server|mode|balance|option|timeout|maxconn|log|retries|acl|use_backend|default_backend|http-request|http-response|tcp-request|tcp-response|redirect|stats|monitor-uri|errorfile|cookie|compression|filter|stick-table|stick|http-check|tcp-check|description|user|group|daemon|chroot|pidfile|hash-type|source|capture|nbthread|cpu-map)\b/,
          "keyword",
        ],
        [/"(?:[^"\\]|\\.)*"/, "string"],
        [/'[^']*'/, "string"],
        [/\b\d+(?:\.\d+){3}(?::\d+)?\b/, "number"],
        [/\b\d+(?:ms|us|s|m|h|d)?\b/, "number"],
      ],
    },
  });
}

const LANG_LABEL = {
  haproxy: "HAProxy",
  yaml: "YAML",
  toml: "TOML",
};

const TAB_ICON_CLASS = {
  haproxy: "tabIcon_haproxy",
  yaml: "tabIcon_yaml",
  toml: "tabIcon_toml",
};

const EDITOR_OPTIONS = {
  minimap: { enabled: false },
  fontSize: 13,
  fontFamily: '"Cascadia Code", Consolas, "Courier New", monospace',
  lineNumbers: "on",
  scrollBeyondLastLine: false,
  wordWrap: "off",
  tabSize: 2,
  automaticLayout: true,
  padding: { top: 12, bottom: 12 },
  scrollbar: { verticalScrollbarSize: 12, horizontalScrollbarSize: 12 },
};

/* ── 類 VSCode 設定檔編輯器（Monaco 核心）───────────── */

export default function ConfigCodeEditor({
  fileName,
  filePath,
  language,
  value,
  onChange,
  dirty,
  saving,
  busy = false,
  loadFailed = false,
  host,
  onSave,
  onReload,
}) {
  const { t } = useTranslation("system");
  const [cursor, setCursor] = useState({ line: 1, col: 1 });
  const [indent, setIndent] = useState("Spaces: 2");
  const saveRef = useRef(() => {});

  useEffect(() => {
    saveRef.current = () => {
      if (dirty && !saving && !loadFailed) onSave();
    };
  }, [dirty, saving, loadFailed, onSave]);

  // Ctrl+S 掛在 window：焦點在編輯器外也能寫入（且擋掉瀏覽器另存網頁）。
  // Monaco 未註冊 Ctrl+S，編輯器內按下會 bubble 到這裡，不會雙重觸發。
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  function readIndent(editor) {
    const opts = editor.getModel()?.getOptions();
    if (!opts) return;
    setIndent(opts.insertSpaces ? `Spaces: ${opts.tabSize}` : `Tab Size: ${opts.tabSize}`);
  }

  function handleMount(editor) {
    editor.onDidChangeCursorPosition((e) => {
      setCursor({ line: e.position.lineNumber, col: e.position.column });
    });
    // Monaco 預設會偵測檔案實際縮排（detectIndentation），狀態列跟著 model 顯示
    readIndent(editor);
    editor.onDidChangeModelOptions(() => readIndent(editor));
    editor.onDidChangeModel(() => readIndent(editor));
  }

  const crumbs = filePath.split("/").filter(Boolean);

  return (
    <div className={styles.window}>
      <div className={styles.tabbar}>
        <div className={styles.tab}>
          <MIcon
            name="description"
            size={15}
            className={styles[TAB_ICON_CLASS[language]] ?? ""}
          />
          <span className={styles.tabName}>{fileName}</span>
          {dirty && <span className={styles.dirtyDot} title={t("ConfigCodeEditor.unwrittenTitle")} />}
        </div>
        <div className={styles.tabbarActions}>
          <button
            type="button"
            className={styles.saveBtn}
            onClick={onSave}
            disabled={saving || !dirty || loadFailed}
            title={t("ConfigCodeEditor.saveTitle")}
          >
            <MIcon name="save" size={15} />
            {saving ? t("ConfigCodeEditor.saving") : t("ConfigCodeEditor.saveConfig")}
          </button>
        </div>
      </div>

      <div className={styles.breadcrumbs}>
        {crumbs.map((crumb, i) => (
          <Fragment key={i}>
            {i > 0 && (
              <span className={styles.crumbSep}>
                <MIcon name="chevron_right" size={14} />
              </span>
            )}
            <span className={i === crumbs.length - 1 ? styles.crumbFile : ""}>{crumb}</span>
          </Fragment>
        ))}
      </div>

      <div className={styles.body}>
        {loadFailed ? (
          <div className={styles.loadError}>
            <MIcon name="cloud_off" size={40} />
            <p className={styles.loadErrorTitle}>{t("ConfigCodeEditor.loadErrorTitle")}</p>
            <p className={styles.loadErrorHint}>
              {t("ConfigCodeEditor.loadErrorHint")}
            </p>
            <button type="button" className={styles.saveBtn} onClick={onReload} disabled={busy}>
              <MIcon name="refresh" size={15} />
              {t("ConfigCodeEditor.reload")}
            </button>
          </div>
        ) : (
          <Editor
            height="100%"
            language={language}
            theme="vs-dark"
            value={value}
            onChange={(v) => onChange(v ?? "")}
            onMount={handleMount}
            options={EDITOR_OPTIONS}
            loading={<div className={styles.editorLoading}>{t("ConfigCodeEditor.loadingEditor")}</div>}
          />
        )}
      </div>

      <div className={styles.statusbar}>
        <span className={styles.statusRemote}>
          <MIcon name="cloud" size={13} />
          SSH: {host || "gateway"}
        </span>
        {loadFailed ? (
          <span className={`${styles.statusItem} ${styles.statusAlert}`}>
            <MIcon name="error_outline" size={13} />
            {t("Error.title", { ns: "common" })}
          </span>
        ) : (
          <span className={styles.statusItem}>{dirty ? t("ConfigCodeEditor.unwritten") : t("ConfigCodeEditor.synced")}</span>
        )}
        <div className={styles.statusRight}>
          <span className={styles.statusItem}>
            {t("ConfigCodeEditor.lineCol", { line: cursor.line, col: cursor.col })}
          </span>
          <span className={styles.statusItem}>{indent}</span>
          <span className={styles.statusItem}>UTF-8</span>
          <span className={styles.statusItem}>{LANG_LABEL[language] ?? language}</span>
        </div>
      </div>
    </div>
  );
}
