import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./AiApiPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { AiApiService } from "../../../services/aiApi";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { focusInvalidField } from "../../../utils/focusField";
import PageHeader from "../../../components/PageHeader/PageHeader";
import RrdChart from "../../../components/RrdChart/RrdChart";
import { formatDateTime, formatMonthDay } from "../../../utils/formatDate";
import { computePosition, isAnchorOffscreen } from "../../../components/PowerMenu/position";

const ReadOnlyCode = lazy(() => import("../../../components/ReadOnlyCode/ReadOnlyCode"));

/* ── helpers ── */

function isExpired(value) {
  if (!value) return false;
  return new Date(value) < new Date();
}

function maskKey(value) {
  if (!value || value.length <= 14) return value ?? "";
  return `${value.slice(0, 8)}••••••${value.slice(-6)}`;
}

function formatTokens(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function buildAiProxyBaseUrl(baseUrl) {
  const root = String(baseUrl ?? "").trim().replace(/\/+$/, "");
  if (!root) return "";
  if (root.endsWith("/api/v1/ai-proxy")) return root;
  if (root.endsWith("/api/v1")) return `${root}/ai-proxy`;
  return `${root}/api/v1/ai-proxy`;
}

/* endpoint："responses"（預設）或 "chat"（Chat Completions）。代理兩種都支援，
   網路上多數教學用的是 Chat Completions，所以兩種範例都給 */
export function buildApiExample(language, baseUrl, endpoint = "responses") {
  const proxyBaseUrl = buildAiProxyBaseUrl(baseUrl) || "BASE_URL";
  const chat = endpoint === "chat";
  const url = `${proxyBaseUrl}/${chat ? "chat/completions" : "responses"}`;

  if (language === "python") {
    return chat
      ? `from openai import OpenAI

client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="${proxyBaseUrl}",
)

response = client.chat.completions.create(
    model="MODEL_NAME",
    messages=[{"role": "user", "content": "INPUT"}],
)

print(response.choices[0].message.content)`
      : `from openai import OpenAI

client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="${proxyBaseUrl}",
)

response = client.responses.create(
    model="MODEL_NAME",
    input="INPUT",
)

print(response.output_text)`;
  }

  if (language === "bash") {
    const body = JSON.stringify(chat
      ? { model: "MODEL_NAME", messages: [{ role: "user", content: "INPUT" }] }
      : { model: "MODEL_NAME", input: "INPUT" }, null, 2);
    return [
      `curl "${url}" \\`,
      '  -H "Authorization: Bearer YOUR_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      `  -d '${body}'`,
    ].join("\n");
  }

  if (language === "cmd") {
    /* CMD 的 JSON 內層引號要寫成 \"，模板字串裡就得是 \\" */
    const body = chat
      ? `{\\"model\\":\\"MODEL_NAME\\",\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"INPUT\\"}]}`
      : `{\\"model\\":\\"MODEL_NAME\\",\\"input\\":\\"INPUT\\"}`;
    return `curl -X POST "${url}" ^
  -H "Authorization: Bearer YOUR_API_KEY" ^
  -H "Content-Type: application/json" ^
  -d "${body}"`;
  }

  return chat
    ? `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "YOUR_API_KEY",
  baseURL: "${proxyBaseUrl}",
});

const response = await client.chat.completions.create({
  model: "MODEL_NAME",
  messages: [{ role: "user", content: "INPUT" }],
});

console.log(response.choices[0].message.content);`
    : `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "YOUR_API_KEY",
  baseURL: "${proxyBaseUrl}",
});

const response = await client.responses.create({
  model: "MODEL_NAME",
  input: "INPUT",
});

console.log(response.output_text);`;
}

/* 查可用模型的指令；回傳清單的 id 就是範例裡的 MODEL_NAME */
export function buildModelsCommand(baseUrl) {
  const proxyBaseUrl = buildAiProxyBaseUrl(baseUrl) || "BASE_URL";
  return `curl "${proxyBaseUrl}/models" -H "Authorization: Bearer YOUR_API_KEY"`;
}

function statusStyle(status) {
  if (status === "approved") return "approved";
  if (status === "rejected") return "rejected";
  return "pending";
}

/* ── Empty ── */
function EmptyState({ icon, title, guideId }) {
  return (
    <div data-guide={guideId}>
      <SharedEmptyState icon={icon} title={title} />
    </div>
  );
}


/* ── Credential 列的「⋮」操作選單 ──
   portal 到 body 並用 fixed 定位，做法同範本管理頁的 RowMenu */
const KEY_MENU_WIDTH = 200;

function KeyMenu({ rotateDisabled, busy, onRename, onRotate, onDelete, onClose, anchorRef, closing = false }) {
  const { t } = useTranslation("ai");
  const ref = useRef(null);
  const [pos, setPos] = useState(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });

  const reposition = useCallback(() => {
    const anchor = anchorRef?.current;
    const menu = ref.current;
    if (!anchor || !menu) return;
    const rect = anchor.getBoundingClientRect();
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    if (isAnchorOffscreen(rect, viewport)) {
      onCloseRef.current();
      return;
    }
    setPos(computePosition(rect, menu.offsetHeight, viewport, KEY_MENU_WIDTH));
  }, [anchorRef]);

  useLayoutEffect(() => { reposition(); }, [reposition]);

  useEffect(() => {
    const opts = { passive: true, capture: true };
    window.addEventListener("scroll", reposition, opts);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, opts);
      window.removeEventListener("resize", reposition);
    };
  }, [reposition]);

  useEffect(() => {
    const handler = (e) => {
      if (!ref.current?.contains(e.target) && !anchorRef?.current?.contains(e.target)) onClose();
    };
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose, anchorRef]);

  return createPortal(
    <div
      ref={ref}
      role="menu"
      className={`${styles.keyMenu} ${closing ? styles.keyMenuOut : ""}`}
      style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: "hidden" }}
    >
      <button type="button" role="menuitem" className={styles.keyMenuItem} onClick={() => { onClose(); onRename(); }}>
        <MIcon name="edit" size={15} />
        {t("AiApiPage.actionRename")}
      </button>
      <div className={styles.keyMenuDivider} />
      {/* 重新產生金鑰是破壞性動作（舊金鑰立即失效），不叫「刷新」也不長得像刷新 */}
      <button
        type="button"
        role="menuitem"
        className={`${styles.keyMenuItem} ${styles.keyMenuItemDanger}`}
        disabled={rotateDisabled || busy}
        onClick={() => { onClose(); onRotate(); }}
      >
        <MIcon name="autorenew" size={15} />
        {t("AiApiPage.actionRotate")}
      </button>
      <button
        type="button"
        role="menuitem"
        className={`${styles.keyMenuItem} ${styles.keyMenuItemDanger}`}
        disabled={busy}
        onClick={() => { onClose(); onDelete(); }}
      >
        <MIcon name="delete" size={15} />
        {t("AiApiPage.actionDelete")}
      </button>
    </div>,
    document.body,
  );
}

/* ── Credential row：一把金鑰一列，常用的複製／顯示放圖示，其餘收進 ⋮ ── */
function CredentialRow({ item, onRefresh }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const confirm = useConfirm();
  const [showKey, setShowKey] = useState(false);
  const [editing, setEditing] = useState(false);
  const [nameInput, setNameInput] = useState(item.api_key_name);
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useDialogPresence(menuOpen, 130);
  const menuBtnRef = useRef(null);

  function fmtExpiry(value) {
    if (!value) return t("AiApiPage.durationOptionNever");
    const label = formatDateTime(value);
    return isExpired(value) ? t("AiApiPage.expiredFormat", { date: label }) : label;
  }

  function credStatusInfo(it) {
    if (it.revoked_at) return { label: t("AiApiPage.credStatusReplaced"), cls: "inactive" };
    if (isExpired(it.expires_at)) return { label: t("AiApiPage.credStatusExpired"), cls: "expired" };
    return { label: t("AiApiPage.credStatusActive"), cls: "active" };
  }

  const info = credStatusInfo(item);
  const inactive = Boolean(item.revoked_at);
  const expired = isExpired(item.expires_at);
  const deprecated = inactive || expired;

  const copy = async (label, value) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t("AiApiPage.copiedSuccess", { label }));
    } catch {
      toast.error(t("AiApiPage.copiedError", { label }));
    }
  };

  const doRotate = async () => {
    const ok = await confirm({
      title: t("AiApiPage.rotateDialogTitle"),
      message: t("AiApiPage.rotateDialogMessage"),
      confirmText: t("AiApiPage.rotateDialogConfirm"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await AiApiService.rotateCredential(item.id);
      toast.success(t("AiApiPage.rotateSuccess"));
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.rotateError"));
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    const ok = await confirm({
      title: t("AiApiPage.deleteDialogTitle"),
      message: t("AiApiPage.deleteDialogMessage"),
      confirmText: t("AiApiPage.deleteDialogConfirm"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await AiApiService.revokeCredential(item.id);
      toast.success(t("AiApiPage.deleteSuccess"));
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.deleteError"));
    } finally {
      setBusy(false);
    }
  };

  const doRename = async () => {
    if (!nameInput.trim()) return;
    setBusy(true);
    try {
      await AiApiService.updateCredential(item.id, { api_key_name: nameInput.trim() });
      toast.success(t("AiApiPage.renameSuccess"));
      setEditing(false);
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.renameError"));
    } finally {
      setBusy(false);
    }
  };

  const startRename = () => { setNameInput(item.api_key_name); setEditing(true); };
  const cancelRename = () => { setNameInput(item.api_key_name); setEditing(false); };

  return (
    <tr className={`${styles.tr} ${deprecated ? styles.trDeprecated : ""}`}>
      <td className={styles.td}>
        <div className={styles.nameCell}>
          <div className={styles.rowIcon}>
            <MIcon name="vpn_key" size={20} />
          </div>
          <div className={styles.rowMain}>
            {editing ? (
              <div className={styles.renameRow}>
                <input
                  type="text"
                  className={styles.renameInput}
                  value={nameInput}
                  maxLength={20}
                  onChange={(e) => setNameInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") doRename();
                    if (e.key === "Escape") cancelRename();
                  }}
                  autoFocus
                />
                <button type="button" className={styles.iconBtn} onClick={doRename} disabled={busy} aria-label={t("AiApiPage.actionRename")}>
                  <MIcon name="check" size={16} />
                </button>
                <button type="button" className={styles.iconBtn} onClick={cancelRename} aria-label={t("AiApiPage.cancel")}>
                  <MIcon name="close" size={16} />
                </button>
              </div>
            ) : (
              <span className={`${styles.rowName} ${deprecated ? styles.rowNameDeprecated : ""}`} title={item.api_key_name || undefined}>
                {item.api_key_name}
              </span>
            )}
            <span className={`${styles.rowKey} ${deprecated ? styles.rowKeyDeprecated : ""}`}>
              {showKey ? item.api_key : maskKey(item.api_key)}
            </span>
          </div>
        </div>
      </td>
      <td className={styles.td}>
        <span className={`${styles.badge} ${styles[`badge_${info.cls}`]}`}>
          <span className={styles.dot} />
          {info.label}
        </span>
      </td>
      <td className={styles.td}>{formatDateTime(item.created_at)}</td>
      <td className={styles.td}>
        <span className={expired ? styles.textDanger : ""}>{fmtExpiry(item.expires_at)}</span>
        {item.revoked_at && (
          <div className={styles.cellSubline}>{t("AiApiPage.metaRevoked", { value: formatDateTime(item.revoked_at) })}</div>
        )}
      </td>
      <td className={`${styles.td} ${styles.tdActions}`}>
        <div className={styles.rowActions} data-guide="ai-key-actions">
          <button
            type="button"
            className={styles.iconBtn}
            onClick={() => copy("API Key", item.api_key)}
            aria-label={t("AiApiPage.actionCopyKey")}
            title={t("AiApiPage.actionCopyKey")}
          >
            <MIcon name="content_copy" size={16} />
          </button>
          <button
            type="button"
            className={styles.iconBtn}
            onClick={() => setShowKey((v) => !v)}
            aria-label={showKey ? t("AiApiPage.actionHide") : t("AiApiPage.actionShow")}
            title={showKey ? t("AiApiPage.actionHide") : t("AiApiPage.actionShow")}
            aria-pressed={showKey}
          >
            <MIcon name={showKey ? "visibility_off" : "visibility"} size={16} />
          </button>
          {menu.open && (
            <KeyMenu
              rotateDisabled={inactive}
              busy={busy}
              onRename={startRename}
              onRotate={doRotate}
              onDelete={doDelete}
              onClose={() => setMenuOpen(false)}
              anchorRef={menuBtnRef}
              closing={menu.closing}
            />
          )}
          <button
            ref={menuBtnRef}
            type="button"
            className={`${styles.menuBtn} ${menuOpen ? styles.menuBtnActive : ""}`}
            onClick={() => setMenuOpen((v) => !v)}
            title={t("AiApiPage.moreActions")}
            aria-label={t("AiApiPage.moreActions")}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            <MIcon name="more_vert" size={18} />
          </button>
        </div>
      </td>
    </tr>
  );
}

/* ── API 快速開始的內容：對象是學生，只留「複製連線資訊 → 查模型 → 貼範例執行」三步，
   出錯才需要的對照表收在最下面 ── */
function ApiDocsContent({ credentials }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [language, setLanguage] = useState("javascript");
  const [endpointKind, setEndpointKind] = useState("responses");
  const usable = credentials.filter((item) => !item.revoked_at && !isExpired(item.expires_at));
  const [credentialId, setCredentialId] = useState(null);
  const credential = usable.find((item) => item.id === credentialId) ?? usable[0] ?? null;
  /* 沒有可用金鑰時仍拿得到 Base URL（舊金鑰上也帶著），範例才不會整段變成 BASE_URL */
  const baseUrl = buildAiProxyBaseUrl((credential ?? credentials[0])?.base_url);
  const code = buildApiExample(language, baseUrl, endpointKind);
  const modelsCommand = buildModelsCommand(baseUrl);
  const languages = [
    { key: "javascript", label: "JavaScript" },
    { key: "python", label: "Python" },
    { key: "bash", label: "Bash" },
  ];
  const endpointKinds = [
    { key: "responses", label: "Responses" },
    { key: "chat", label: "Chat Completions" },
  ];

  const copy = async (label, value) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t("AiApiPage.copiedSuccess", { label }));
    } catch {
      toast.error(t("AiApiPage.copiedError", { label }));
    }
  };

  return (
    <div className={styles.docsLayout}>
      {/* 1. 連線資訊：呼叫 API 只需要這兩個值，寬螢幕並排 */}
      <section className={styles.docsStep}>
        <span className={styles.docsStepNumber}>1</span>
        <div className={styles.docsStepBody}>
          <h3 className={styles.docsStepTitle}>{t("AiApiPage.docsConnTitle")}</h3>
          <div className={styles.docsConnGrid}>
            <div className={styles.docsField}>
              <span className={styles.docsFieldLabel}>Base URL</span>
              <div className={styles.docsEndpointRow}>
                <code title={baseUrl || undefined}>{baseUrl || t("AiApiPage.docsBaseUrlUnavailable")}</code>
                <button type="button" className={styles.docsCopyButton} onClick={() => copy("Base URL", baseUrl)} disabled={!baseUrl} aria-label={t("AiApiPage.copyBaseUrl")} title={t("AiApiPage.copyBaseUrl")}>
                  <MIcon name="content_copy" size={16} />
                </button>
              </div>
            </div>
            <div className={styles.docsField}>
              <span className={styles.docsFieldLabel}>API Key</span>
              {credential ? (
                <div className={styles.docsEndpointRow}>
                  {usable.length > 1 && (
                    <select
                      className={styles.docsKeySelect}
                      value={credential.id}
                      onChange={(event) => setCredentialId(event.target.value)}
                      aria-label={t("AiApiPage.docsKeySelectLabel")}
                    >
                      {usable.map((item) => <option key={item.id} value={item.id}>{item.api_key_name}</option>)}
                    </select>
                  )}
                  <code>{maskKey(credential.api_key)}</code>
                  <button type="button" className={styles.docsCopyButton} onClick={() => copy("API Key", credential.api_key)} aria-label={t("AiApiPage.actionCopyKey")} title={t("AiApiPage.actionCopyKey")}>
                    <MIcon name="content_copy" size={16} />
                  </button>
                </div>
              ) : (
                <p className={styles.docsNotice}>
                  <span>{t("AiApiPage.docsNoActiveKey")}</span>
                </p>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* 2. 查模型：MODEL_NAME 從這裡拿 */}
      <section className={styles.docsStep}>
        <span className={styles.docsStepNumber}>2</span>
        <div className={styles.docsStepBody}>
          <h3 className={styles.docsStepTitle}>
            {t("AiApiPage.docsModelsTitle")}
          </h3>
          <div className={styles.docsEndpointRow}>
            <code title={modelsCommand}>{modelsCommand}</code>
            <button type="button" className={styles.docsCopyButton} onClick={() => copy(t("AiApiPage.docsCommand"), modelsCommand)} aria-label={t("AiApiPage.copy")} title={t("AiApiPage.copy")}>
              <MIcon name="content_copy" size={16} />
            </button>
          </div>
        </div>
      </section>

      {/* 3. 送出第一個請求 */}
      <section className={styles.docsStep}>
        <span className={styles.docsStepNumber}>3</span>
        <div className={styles.docsStepBody}>
          <h3 className={styles.docsStepTitle}>{t("AiApiPage.docsExampleTitle")}</h3>
          <div className={styles.codeToolbar}>
            <div className={styles.codeTabs} role="group" aria-label={t("AiApiPage.docsEndpointKindLabel")}>
              {endpointKinds.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={endpointKind === item.key}
                  className={endpointKind === item.key ? styles.codeTabActive : styles.codeTab}
                  onClick={() => setEndpointKind(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className={styles.codeTabs} role="group" aria-label={t("AiApiPage.docsLanguageLabel")}>
              {languages.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={language === item.key}
                  className={language === item.key ? styles.codeTabActive : styles.codeTab}
                  onClick={() => setLanguage(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <div className={styles.codeCard}>
            <div className={styles.codePanelHeader}>
              <span>{language}</span>
              <button type="button" className={styles.codeCopyButton} onClick={() => copy(t("AiApiPage.docsCode"), code)} aria-label={t("AiApiPage.copyCode")} title={t("AiApiPage.copyCode")}>
                <MIcon name="content_copy" size={16} />
              </button>
            </div>
            <div className={styles.codeViewport}>
              <Suspense fallback={<pre className={styles.codeBlock}><code>{code}</code></pre>}>
                <ReadOnlyCode
                  code={code}
                  language={language}
                  height="100%"
                  label={`${t("AiApiPage.docsCode")} (${language})`}
                  fallback={<pre className={styles.codeBlock}><code>{code}</code></pre>}
                />
              </Suspense>
            </div>
          </div>
        </div>
      </section>

      {/* 出錯時才需要看，預設收合，不佔主流程的版面 */}
      <details className={styles.docsErrors}>
        <summary>
          {t("AiApiPage.docsLimitsTitle")}
          <MIcon name="expand_more" size={18} className={styles.docsErrorsChevron} />
        </summary>
        <div className={styles.docsErrorList}>
          <div><span className={styles.statusCode}>401</span><span>{t("AiApiPage.docsErr401")}</span></div>
          <div>
            <span className={styles.statusCode}>429</span>
            <span>
              {credential?.rate_limit
                ? t("AiApiPage.docsErr429WithLimit", { limit: credential.rate_limit })
                : t("AiApiPage.docsErr429")}
            </span>
          </div>
          <div><span className={styles.statusCode}>413</span><span>{t("AiApiPage.docsErr413")}</span></div>
          <div><span className={styles.statusCode}>502</span><span>{t("AiApiPage.docsErr502")}</span></div>
        </div>
      </details>

    </div>
  );
}

/* ── API 快速開始彈窗 ── */
function QuickStartModal({ closing = false, credentials, onClose }) {
  const { t } = useTranslation("ai");

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div
      className={`${styles.dialogOverlay} ${closing ? styles.dialogOverlayOut : ""}`}
      role="presentation"
      onMouseDown={onClose}
    >
      <div
        className={`${styles.dialog} ${styles.quickStartDialog}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-quick-start-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className={styles.quickStartHeader}>
          <div className={styles.quickStartHeading}>
            <h2 id="ai-quick-start-title" className={styles.dialogTitle}>{t("AiApiPage.quickStartButton")}</h2>
          </div>
          <button type="button" className={styles.dialogClose} onClick={onClose} aria-label={t("AiApiPage.close")}>
            <MIcon name="close" size={18} />
          </button>
        </div>
        <div className={styles.quickStartBody}>
          <ApiDocsContent credentials={credentials} />
        </div>
      </div>
    </div>
  );
}

/* ── Request row ── */
function RequestRow({ item }) {
  const { t } = useTranslation("ai");

  function statusLabel(status) {
    if (status === "approved") return t("AiApiPage.statusApproved");
    if (status === "rejected") return t("AiApiPage.statusRejected");
    return t("AiApiPage.statusPending");
  }

  const st = statusStyle(item.status);
  return (
    <tr className={styles.tr}>
      <td className={styles.td}>
        <div className={styles.nameCell}>
          <div className={`${styles.rowIcon} ${styles[`rowIcon_${st}`] ?? ""}`}>
            <MIcon name="assignment" size={20} />
          </div>
          <div className={styles.rowMain}>
            <span className={styles.rowName} title={item.api_key_name || undefined}>{item.api_key_name || "—"}</span>
          </div>
        </div>
      </td>
      <td className={styles.td}>
        <span className={`${styles.cellText} ${styles.cellTextWide}`} title={item.purpose || undefined}>{item.purpose || "—"}</span>
      </td>
      <td className={styles.td}>
        <span className={`${styles.badge} ${styles[`badge_${st}`]}`}>
          <span className={styles.dot} />
          {statusLabel(item.status)}
        </span>
      </td>
      <td className={styles.td}>{formatDateTime(item.created_at)}</td>
      <td className={styles.td}>
        {item.reviewed_at
          ? formatDateTime(item.reviewed_at)
          : <span className={styles.cellMuted}>{t("AiApiPage.requestNotReviewed")}</span>}
      </td>
      <td className={styles.td}>
        {item.review_comment
          ? <span className={`${styles.cellText} ${st === "rejected" ? styles.textDanger : ""}`} title={item.review_comment}>{item.review_comment}</span>
          : <span className={styles.cellMuted}>—</span>}
      </td>
    </tr>
  );
}

/* ── Usage stat card ── */
function UsageStatCard({ label, value }) {
  return (
    <div className={styles.usageStatCard}>
      <span className={styles.usageStatLabel}>{label}</span>
      <span className={styles.usageStatValue}>{value}</span>
    </div>
  );
}

/* ── Usage: 逐日 Tokens 折線圖（#7）── */
function DailyUsageChart({ daily }) {
  const { t } = useTranslation("ai");
  const data = useMemo(
    () => (daily ?? []).map((point) => ({
      time: formatMonthDay(point.date),
      input: point.input_tokens,
      output: point.output_tokens,
    })),
    [daily],
  );
  /* 區間內完全沒用量就不畫（統計卡已是 0），有值才值得佔版面 */
  if (data.length === 0 || data.every((point) => !point.input && !point.output)) return null;
  return (
    <RrdChart
      title={t("AiApiPage.usageDailyTitle")}
      data={data}
      series={[
        { key: "input", label: t("AiApiPage.usageStatInputTokens"), color: "--color-info" },
        { key: "output", label: t("AiApiPage.usageStatOutputTokens"), color: "--color-success" },
      ]}
      height={180}
    />
  );
}

/* ── Usage: by-model / by-call-type breakdown ── */
function UsageBreakdown({ icon, title, entries, formatter }) {
  const { t } = useTranslation("ai");
  if (!entries || Object.keys(entries).length === 0) return null;
  return (
    <div className={styles.usageBreakdown}>
      <div className={styles.usageBreakdownTitle}>
        <MIcon name={icon} size={14} /> {title}
      </div>
      <div className={styles.usageBreakdownList}>
        {Object.entries(entries).map(([key, stats]) => (
          <div key={key} className={styles.usageBreakdownRow}>
            <span className={styles.usageBreakdownKey}>{formatter ? formatter(key) : key}</span>
            <span>{t("AiApiPage.callCount", { count: stats.calls ?? stats.requests ?? 0 })}</span>
            <span>↑ {formatTokens(stats.input_tokens)}</span>
            <span>↓ {formatTokens(stats.output_tokens)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatModelDisplay(modelName) {
  if (!modelName) return "-";
  const trimmed = modelName.trim();
  if (!trimmed) return "-";
  const match = trimmed.match(/models--([^/]+)--([^/]+)/);
  if (!match) return trimmed;
  return `${match[1]}/${match[2]}`;
}

/* ── Usage record row ── */
function UsageRecordRow({ item }) {
  const { t } = useTranslation("ai");

  const succeeded = ["success", "ok", "200", 200].includes(item.status);
  const statusCls = succeeded ? "success" : "danger";
  const statusLabel =
    succeeded
      ? t("AiApiPage.recordStatusSuccess")
      : t("AiApiPage.recordStatusError");

  const callTypeLabels = {
    chat: "AiApiPage.callTypeChat",
    recommend: "AiApiPage.callTypeRecommend",
    chat_completion: "AiApiPage.callTypeChatCompletion",
  };
  const callTypeKey = callTypeLabels[item.call_type];

  return (
    <div className={styles.usageRecordRow}>
      <div className={styles.usageRecordTop}>
        <div className={styles.usageRecordHeading}>
          <span className={styles.usageRecordTime}>{formatDateTime(item.created_at)}</span>
          <span className={styles.usageRecordId}>ID {item.id}</span>
        </div>
        <span className={`${styles.badge} ${styles[`badge_${statusCls}`]}`}>
          <span className={styles.dot} />
          {statusLabel}
        </span>
      </div>
      <div className={styles.usageRecordDetails}>
        <div>
          <span>{t("AiApiPage.recordKey")}</span>
          <strong>{item.api_key_name} <small>{item.api_key_prefix}…</small></strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordModel")}</span>
          <strong className={styles.usageRecordModel}>{formatModelDisplay(item.model_name)}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordRequestType")}</span>
          <strong className={styles.usageRecordType}>{callTypeKey ? t(callTypeKey) : item.call_type || "—"}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordInputTokens")}</span>
          <strong>{formatTokens(item.input_tokens)}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordOutputTokens")}</span>
          <strong>{formatTokens(item.output_tokens)}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordTotalTokens")}</span>
          <strong>{formatTokens(item.total_tokens ?? ((item.input_tokens ?? 0) + (item.output_tokens ?? 0)))}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordLatency")}</span>
          <strong>{item.request_duration_ms == null ? "—" : t("AiApiPage.recordDuration", { seconds: (item.request_duration_ms / 1000).toFixed(1) })}</strong>
        </div>
      </div>
      {item.error_message && (
        <div className={styles.usageRecordError}>
          <MIcon name="error_outline" size={15} />
          <span>{item.error_message}</span>
        </div>
      )}
    </div>
  );
}

/* ── My Usage Tab ── */
const USAGE_RECORD_PAGE_SIZE = 20;

function MyUsageTab() {
  const { t } = useTranslation("ai");
  const [preset, setPreset] = useState("30d");
  const [usageData, setUsageData] = useState(null);
  const [usageError, setUsageError] = useState(false);
  const [records, setRecords] = useState([]);
  const [recordsCount, setRecordsCount] = useState(0);
  const [recordsError, setRecordsError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const { start, end } = useMemo(() => {
    const now = new Date();
    const s = new Date(now);
    if (preset === "7d") s.setDate(s.getDate() - 7);
    else if (preset === "30d") s.setDate(s.getDate() - 30);
    else s.setDate(s.getDate() - 90);
    return { start: s.toISOString(), end: now.toISOString() };
  }, [preset]);

  const load = useCallback(async () => {
    setLoading(true);
    setUsageError(false);
    setRecordsError(false);
    const [usageRes, recRes] = await Promise.allSettled([
      AiApiService.getMyUsage({ start_date: start, end_date: end }),
      AiApiService.getMyUsageRecords({ start_date: start, end_date: end, limit: USAGE_RECORD_PAGE_SIZE }),
    ]);
    if (usageRes.status === "fulfilled") setUsageData(usageRes.value);
    else setUsageError(true);
    if (recRes.status === "fulfilled") {
      setRecords(recRes.value?.data ?? []);
      setRecordsCount(recRes.value?.count ?? 0);
    } else {
      setRecords([]);
      setRecordsCount(0);
      setRecordsError(true);
    }
    setLoading(false);
  }, [start, end]);

  useEffect(() => { load(); }, [load]);

  const hasMoreRecords = !recordsError && records.length > 0 && records.length < recordsCount;

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const res = await AiApiService.getMyUsageRecords({
        start_date: start,
        end_date: end,
        skip: records.length,
        limit: USAGE_RECORD_PAGE_SIZE,
      });
      setRecords((prev) => [...prev, ...(res?.data ?? [])]);
      setRecordsCount(res?.count ?? 0);
    } catch {
      /* 維持現有清單，不覆蓋成功資料 */
    } finally {
      setLoadingMore(false);
    }
  };

  const PRESETS = [
    { value: "7d", label: t("AiApiPage.preset7d") },
    { value: "30d", label: t("AiApiPage.preset30d") },
    { value: "90d", label: t("AiApiPage.preset90d") },
  ];

  const hasAnyUsage = (usageData?.total_requests ?? 0) > 0;

  return (
    <div className={styles.usageTab}>
      <div className={styles.usageDateRow} data-guide="ai-usage-panel">
        <SegmentedControl
          options={PRESETS}
          value={preset}
          onChange={setPreset}
          ariaLabel={t("AiApiPage.usageRangeLabel")}
        />
        <span className={styles.usageDateRange}>{start.slice(0, 10)} ~ {end.slice(0, 10)}</span>
      </div>

      {loading ? (
        <LoadingState />
      ) : (
        <>
          {/* ── 申請金鑰 API 用量總覽 ── */}
          <div className={styles.usagePanel} data-guide="ai-route-usage">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageTitle")}</h3>
            </div>
            {usageError ? (
              <p className={styles.textDanger}>{t("AiApiPage.usageError")}</p>
            ) : (
              <>
                <div className={styles.usageStatsGrid}>
                  <UsageStatCard label={t("AiApiPage.usageStatTotalCalls")} value={usageData?.total_requests ?? 0} />
                  <UsageStatCard label={t("AiApiPage.usageStatInputTokens")} value={formatTokens(usageData?.total_input_tokens)} />
                  <UsageStatCard label={t("AiApiPage.usageStatOutputTokens")} value={formatTokens(usageData?.total_output_tokens)} />
                </div>
                <DailyUsageChart daily={usageData?.daily} />
                <UsageBreakdown
                  icon="bar_chart"
                  title={t("AiApiPage.usageBreakdownByModel")}
                  entries={usageData?.by_model}
                  formatter={formatModelDisplay}
                />
                {!usageError && !hasAnyUsage && (
                  <p className={styles.noData}>{t("AiApiPage.usageEmpty")}</p>
                )}
              </>
            )}
          </div>

          {/* ── 細項呼叫紀錄 ── */}
          <div className={styles.usagePanel} data-guide="ai-usage-records">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageRecordsTitle")}</h3>
            </div>
            {recordsError ? (
              <p className={styles.textDanger}>{t("AiApiPage.usageRecordsError")}</p>
            ) : records.length === 0 ? (
              <p className={styles.noData}>{t("AiApiPage.usageRecordsEmpty")}</p>
            ) : (
              <>
                <div className={styles.usageRecordList}>
                  {records.map((item) => (
                    <UsageRecordRow key={`${item.route}-${item.id}`} item={item} />
                  ))}
                </div>
                {hasMoreRecords && (
                  <div className={styles.usageRecordsMoreRow}>
                    <button type="button" className={styles.btnOutline} onClick={loadMore} disabled={loadingMore}>
                      {loadingMore ? (
                        <>
                          <MIcon name="hourglass_empty" size={16} />
                          {t("AiApiPage.recordsLoadingMore")}
                        </>
                      ) : (
                        <>
                          <MIcon name="expand_more" size={16} />
                          {t("AiApiPage.recordsLoadMore")}
                        </>
                      )}
                    </button>
                    <span className={styles.usageDateRange}>
                      {t("AiApiPage.recordsShownCount", {
                        shown: records.length,
                        total: recordsCount,
                      })}
                    </span>
                  </div>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ── Apply key dialog ── */
function ApplyKeyModal({
  closing = false,
  busy = false,
  apiKeyName,
  onApiKeyNameChange,
  purpose,
  onPurposeChange,
  purposeInvalid,
  purposeInputRef,
  duration,
  onDurationChange,
  durationOptions,
  onClose,
  onSubmit,
}) {
  const { t } = useTranslation("ai");

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [busy, onClose]);

  return (
    <div
      className={`${styles.dialogOverlay} ${closing ? styles.dialogOverlayOut : ""}`}
      role="presentation"
      onMouseDown={() => { if (!busy) onClose(); }}
    >
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-apply-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className={styles.dialogHeader}>
          <div>
            <h2 id="ai-apply-dialog-title" className={styles.dialogTitle} data-guide="ai-form">{t("AiApiPage.applyPanelTitle")}</h2>
          </div>
          <button
            type="button"
            className={styles.dialogClose}
            onClick={onClose}
            disabled={busy}
            aria-label={t("AiApiPage.close")}
            data-guide="ai-apply-close"
          >
            <MIcon name="close" size={18} />
          </button>
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel} htmlFor="ai-key-name">{t("AiApiPage.formLabelKeyName")}</label>
          <input
            id="ai-key-name"
            type="text"
            className={styles.formInput}
            value={apiKeyName}
            onChange={(e) => onApiKeyNameChange(e.target.value)}
            placeholder={t("AiApiPage.formPlaceholderKeyName")}
            maxLength={20}
            data-guide="ai-apply-name"
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel} htmlFor="ai-purpose">{t("AiApiPage.formLabelPurpose")}</label>
          <textarea
            id="ai-purpose"
            ref={purposeInputRef}
            className={`${styles.formTextarea} ${purposeInvalid ? styles.fieldInvalid : ""}`}
            value={purpose}
            onChange={(e) => onPurposeChange(e.target.value)}
            placeholder={t("AiApiPage.formPlaceholderPurpose")}
            rows={5}
            data-guide="ai-apply-purpose"
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel} htmlFor="ai-duration">{t("AiApiPage.formLabelDuration")}</label>
          <select
            id="ai-duration"
            className={styles.formSelect}
            value={duration}
            onChange={(e) => onDurationChange(e.target.value)}
            data-guide="ai-apply-duration"
          >
            {durationOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        <div className={styles.dialogFooter}>
          <span className={styles.formHint}>{t("AiApiPage.formHintPurpose")}</span>
          <div className={styles.dialogActions}>
            <button type="button" className={styles.btnOutline} onClick={onClose} disabled={busy}>
              {t("AiApiPage.cancel")}
            </button>
            <button type="button" className={styles.btnPrimary} onClick={onSubmit} disabled={busy} data-guide="ai-submit">
              <MIcon name="send" size={16} />
              {busy ? t("AiApiPage.submitButtonSubmitting") : t("AiApiPage.submitButton")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ───────────────────────────── Main ───────────────────────────── */

export default function AiApiPage() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("keys");

  const DURATION_OPTIONS = [
    { value: "1h", label: t("AiApiPage.durationOption1h") },
    { value: "1d", label: t("AiApiPage.durationOption1d") },
    { value: "7d", label: t("AiApiPage.durationOption7d") },
    { value: "30d", label: t("AiApiPage.durationOption30d") },
    { value: "never", label: t("AiApiPage.durationOptionNever") },
  ];

  const TABS = [
    { key: "keys",    label: "API Keys" },
    { key: "records", label: t("AiApiPage.tabRecords") },
    { key: "usage",   label: t("AiApiPage.tabUsage") },
  ];

  /* ── Form state ── */
  const [apiKeyName, setApiKeyName] = useState("test");
  const [purpose, setPurpose] = useState("");
  const [duration, setDuration] = useState("never");
  const [submitting, setSubmitting] = useState(false);
  const [purposeInvalid, setPurposeInvalid] = useState(false);
  const purposeInputRef = useRef(null);
  const [showApplyModal, setShowApplyModal] = useState(false);
  const applyDialog = useDialogPresence(showApplyModal);
  const [showQuickStart, setShowQuickStart] = useState(false);
  const quickStartDialog = useDialogPresence(showQuickStart);

  /* ── Data ── */
  const [credentials, setCredentials] = useState([]);
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [credRes, reqRes] = await Promise.all([
        AiApiService.listMyCredentials(),
        AiApiService.listMyRequests(),
      ]);
      setCredentials(credRes?.data ?? []);
      setRequests(reqRes?.data ?? []);
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.loadError"));
    } finally {
      setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => { load(); }, [load]);

  const activeCredentials = credentials.filter((c) => !c.revoked_at && !isExpired(c.expires_at));

  /* ── Submit request ── */
  const handleSubmit = async () => {
    if (purpose.trim().length < 10) {
      setPurposeInvalid(true);
      focusInvalidField(purposeInputRef.current);
      return;
    }
    setSubmitting(true);
    try {
      await AiApiService.createRequest({
        purpose: purpose.trim(),
        api_key_name: apiKeyName.trim(),
        duration,
      });
      setPurpose("");
      setApiKeyName("test");
      setDuration("never");
      setShowApplyModal(false);
      toast.success(t("AiApiPage.submitSuccess"));
      load();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.submitError"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <PageHeader
        title="AI API"
      />

      {/* ── 控制列：左側分頁切換、右側動作（排版比照 AI 金鑰管理） ── */}
      <div className={styles.controlsRow}>
        <div data-guide="ai-tabs">
          <SegmentedControl
            className={styles.pageTabs}
            ariaLabel={t("AiApiPage.tabsAriaLabel")}
            value={activeTab}
            onChange={setActiveTab}
            options={TABS.map((tab) => ({
              value: tab.key,
              label: tab.label,
              badge: tab.key === "keys" ? activeCredentials.length : tab.key === "records" ? requests.length : undefined,
              buttonProps: {
                "data-guide-tab": tab.key,
                "data-guide-has-content": tab.key !== "keys" || credentials.length > 0 ? "true" : "false",
              },
            }))}
          />
        </div>
        <div className={styles.headerActions}>
          <button
            type="button"
            className={styles.btnQuickStart}
            onClick={() => setShowQuickStart(true)}
            data-guide="ai-quick-start"
          >
            <MIcon name="rocket_launch" size={16} />
            {t("AiApiPage.quickStartButton")}
          </button>
          <button
            type="button"
            className={styles.btnAddKey}
            onClick={() => setShowApplyModal(true)}
            data-guide="ai-add-key"
          >
            <MIcon name="add" size={16} />
            {t("AiApiPage.addKeyButton")}
          </button>
        </div>
      </div>

      {/* ── Content ── */}
      <div className={styles.content}>
        {/* ---- Tab: API Keys ---- */}
        {activeTab === "keys" && (
          loading ? (
            <LoadingState />
          ) : credentials.length === 0 ? (
            <EmptyState
              icon="vpn_key"
              title={t("AiApiPage.keysEmptyTitle")}
              guideId="ai-keys-content"
            />
          ) : (
            <div className={styles.tableWrap} data-guide="ai-keys-content">
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th className={styles.th}>{t("AiApiPage.colKey")}</th>
                    <th className={styles.th}>{t("AiApiPage.colStatus")}</th>
                    <th className={styles.th}>{t("AiApiPage.colCreated")}</th>
                    <th className={styles.th}>{t("AiApiPage.colExpiry")}</th>
                    <th className={`${styles.th} ${styles.tdActions}`}>{t("AiApiPage.colActions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {credentials.map((item) => (
                    <CredentialRow key={item.id} item={item} onRefresh={load} />
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}

        {/* ---- Tab: 申請紀錄 ---- */}
        {activeTab === "records" && (
          loading ? (
            <LoadingState />
          ) : requests.length === 0 ? (
            <EmptyState
              icon="history"
              title={t("AiApiPage.recordsEmptyTitle")}
              guideId="ai-records-content"
            />
          ) : (
            <div className={styles.tableWrap} data-guide="ai-records-content">
              <table className={`${styles.table} ${styles.tableRecords}`}>
                <thead>
                  <tr>
                    <th className={styles.th}>{t("AiApiPage.colKeyName")}</th>
                    <th className={styles.th}>{t("AiApiPage.colPurpose")}</th>
                    <th className={styles.th}>{t("AiApiPage.colStatus")}</th>
                    <th className={styles.th}>{t("AiApiPage.colAppliedAt")}</th>
                    <th className={styles.th}>{t("AiApiPage.colReviewedAt")}</th>
                    <th className={styles.th}>{t("AiApiPage.colComment")}</th>
                  </tr>
                </thead>
                <tbody>
                  {requests.map((item) => (
                    <RequestRow key={item.id} item={item} />
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}

        {/* ---- Tab: 我的用量 ---- */}
        {activeTab === "usage" && <MyUsageTab />}
      </div>

      {/* ── API 快速開始彈窗（原「API 文件」分頁） ── */}
      {quickStartDialog.open && (
        <QuickStartModal
          closing={quickStartDialog.closing}
          credentials={credentials}
          onClose={() => setShowQuickStart(false)}
        />
      )}

      {/* ── 新增金鑰彈窗 ── */}
      {applyDialog.open && (
        <ApplyKeyModal
          closing={applyDialog.closing}
          busy={submitting}
          apiKeyName={apiKeyName}
          onApiKeyNameChange={setApiKeyName}
          purpose={purpose}
          onPurposeChange={(value) => { setPurpose(value); setPurposeInvalid(false); }}
          purposeInvalid={purposeInvalid}
          purposeInputRef={purposeInputRef}
          duration={duration}
          onDurationChange={setDuration}
          durationOptions={DURATION_OPTIONS}
          onClose={() => setShowApplyModal(false)}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}
