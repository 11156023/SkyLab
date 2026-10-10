import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./AiApiPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { AiApiService } from "../../../services/aiApi";
import { useAuth } from "../../../contexts/AuthContext";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import { focusInvalidField } from "../../../utils/focusField";
import PageHeader from "../../../components/PageHeader/PageHeader";
import RrdChart from "../../../components/RrdChart/RrdChart";
import { formatDate, formatDateTime, formatMonthDay } from "../../../utils/formatDate";
import useAnchoredMenu from "../../../hooks/useAnchoredMenu";
import { formatModelDisplay, formatTokens, isOkStatus, presetToRange } from "../aiFormat";

const ReadOnlyCode = lazy(() => import("../../../components/ReadOnlyCode/ReadOnlyCode"));
const AiApiChatTab = lazy(() => import("./AiApiChatTab"));

/* ── helpers ── */

function isExpired(value) {
  if (!value) return false;
  return new Date(value) < new Date();
}

/* 清單只顯示前綴；完整金鑰在開啟詳細視窗後取得。 */
function maskPrefix(prefix) {
  return `${prefix ?? ""}••••••`;
}

/* 複製到剪貼簿並以 toast 回報結果；label 是訊息裡顯示的項目名稱 */
function useCopyToClipboard() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  return useCallback(async (label, value) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t("AiApiPage.copiedSuccess", { label }));
    } catch {
      toast.error(t("AiApiPage.copiedError", { label }));
    }
  }, [t, toast]);
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

/* 使用者刪除的金鑰由後端排除，不會再進入這份清單；revoked_at 在這裡代表
   重新產生後被替換的舊金鑰或其他後端撤銷紀錄。 */
export function getCredentialState(item, credentials = []) {
  if (item?.revoked_at) {
    const revokedAt = new Date(item.revoked_at).getTime();
    const replaced = credentials.some((other) => other.id !== item.id
      && other.request_id === item.request_id
      && new Date(other.created_at).getTime() >= revokedAt - 1000);
    return replaced ? "replaced" : "revoked";
  }
  if (isExpired(item?.expires_at)) return "expired";
  return "active";
}

const CREDENTIAL_STATE_LABEL_KEYS = {
  active: "AiApiPage.credStatusActive",
  expired: "AiApiPage.credStatusExpired",
  replaced: "AiApiPage.credStatusReplaced",
  revoked: "AiApiPage.credStatusRevoked",
};

/* 範例程式碼的語言：畫面上的名稱與編輯器語法（cmd 用 Monaco 的 bat） */
const CODE_LANGUAGES = [
  { key: "javascript", labelKey: "AiApiPage.docsLangJavascript", editor: "javascript" },
  { key: "python", labelKey: "AiApiPage.docsLangPython", editor: "python" },
  { key: "bash", labelKey: "AiApiPage.docsLangBash", editor: "bash" },
  { key: "cmd", labelKey: "AiApiPage.docsLangCmd", editor: "bat" },
];

/* ── Empty：外層帶 data-guide，導覽才找得到這一塊 ── */
function EmptyState({ icon, title, description, action, guideId }) {
  return (
    <div data-guide={guideId}>
      <SharedEmptyState icon={icon} title={title} description={description} action={action} />
    </div>
  );
}


/* ── Credential 列的「⋮」操作選單 ──
   portal 到 body 並用 fixed 定位，做法同範本管理頁的 RowMenu */
const KEY_MENU_WIDTH = 220;

function KeyMenu({ state, busy, onRename, onRotate, onDelete, onClose, anchorRef, closing = false }) {
  const { t } = useTranslation("ai");
  const { ref, pos } = useAnchoredMenu({ anchorRef, onClose, width: KEY_MENU_WIDTH });
  const active = state === "active";

  return createPortal(
    <div
      ref={ref}
      role="menu"
      className={`${styles.keyMenu} ${closing ? styles.keyMenuOut : ""}`}
      style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: "hidden" }}
    >
      {active && (
        <>
          <button type="button" role="menuitem" className={styles.keyMenuItem} onClick={() => { onClose(); onRename(); }}>
            <MIcon name="edit" size={15} />
            {t("AiApiPage.actionRename")}
          </button>
          <div className={styles.keyMenuDivider} />
        </>
      )}
      {/* 重新產生金鑰是破壞性動作（舊金鑰立即失效），不叫「刷新」也不長得像刷新 */}
      <button
        type="button"
        role="menuitem"
        className={`${styles.keyMenuItem} ${styles.keyMenuItemDanger}`}
        disabled={!active || busy}
        onClick={() => { onClose(); onRotate(); }}
      >
        <MIcon name="autorenew" size={15} />
        <span className={styles.keyMenuLabel}>
          {t("AiApiPage.actionRotate")}
          {/* 停用要說原因：新金鑰會沿用舊的到期日，過期的輪替出來也不能用 */}
          {state === "expired" && <small>{t("AiApiPage.rotateExpiredHint")}</small>}
        </span>
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

/* 完整金鑰只留在開啟中的詳細視窗，清單維持前綴；已失效的金鑰不再取回明文，也不給複製。
   外框交給共用 Modal（Esc、焦點、Tab 鎖定、捲動鎖都由它處理） */
function KeyDetailDialog({ credential, apiKey, state, closing = false, onClose }) {
  const { t } = useTranslation("ai");
  const copy = useCopyToClipboard();
  const usable = state === "active";
  const [loadedKey, setLoadedKey] = useState(apiKey || "");
  const [keyLoading, setKeyLoading] = useState(!apiKey && usable);
  const [keyError, setKeyError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const requestRef = useRef(null);
  const displayApiKey = usable ? apiKey || loadedKey : "";
  const baseUrl = buildAiProxyBaseUrl(credential.base_url);

  useEffect(() => {
    if (apiKey || !usable) return undefined;
    const controller = new AbortController();
    requestRef.current = controller;
    setKeyLoading(true);
    setKeyError(false);
    const loadKey = async () => {
      try {
        const detail = await AiApiService.getCredential(credential.id, { signal: controller.signal });
        if (!detail?.api_key) throw new Error("Missing API key");
        if (!controller.signal.aborted) setLoadedKey(detail.api_key);
      } catch {
        if (!controller.signal.aborted) setKeyError(true);
      } finally {
        if (!controller.signal.aborted) setKeyLoading(false);
      }
    };
    loadKey();
    return () => controller.abort();
  }, [credential.id, apiKey, usable, attempt]);

  /* 一按關閉（離場動畫期間）就取消請求，晚回來的金鑰不會再出現在畫面上 */
  useEffect(() => {
    if (closing) requestRef.current?.abort();
  }, [closing]);

  return (
    <Modal
      closing={closing}
      onClose={onClose}
      closeButton
      size="md"
      title={t("AiApiPage.keyDetailTitle")}
      actions={usable && baseUrl ? (
        <button
          type="button"
          className={styles.btnSecondary}
          disabled={!displayApiKey}
          onClick={() => copy(t("AiApiPage.curlCommand"), buildModelsCommand(baseUrl).replace("YOUR_API_KEY", displayApiKey))}
        >
          {t("AiApiPage.copyCurlQuickstart")}
        </button>
      ) : undefined}
    >
      {!usable && (
        <p className={styles.keyDetailNotice} role="status">
          <MIcon name="block" size={16} />
          <span>{t(`AiApiPage.keyInactiveNotice_${state}`)}</span>
        </p>
      )}
      <dl className={styles.keyDetailList}>
        <div className={styles.keyDetailField}>
          <dt>{t("AiApiPage.fieldApiKey")}</dt>
          <dd className={styles.keyDetailValue}>
            <code aria-live="polite">
              {!usable
                ? maskPrefix(credential.api_key_prefix)
                : keyLoading ? t("AiApiPage.keyDetailLoading") : displayApiKey || "—"}
            </code>
            {usable && (
              <button
                type="button"
                className={styles.copyBtn}
                disabled={!displayApiKey}
                onClick={() => copy(t("AiApiPage.fieldApiKey"), displayApiKey)}
                aria-label={t("AiApiPage.actionCopyKey")}
                title={t("AiApiPage.actionCopyKey")}
              >
                <MIcon name="content_copy" size={16} />
              </button>
            )}
          </dd>
          {keyError && (
            <>
              <dd role="alert" className={styles.keyDetailError}>{t("AiApiPage.keyDetailLoadError")}</dd>
              <dd>
                <button type="button" className={styles.btnSecondary} onClick={() => setAttempt((value) => value + 1)}>
                  <MIcon name="refresh" size={16} />
                  {t("AiApiPage.retry")}
                </button>
              </dd>
            </>
          )}
        </div>
        <div className={styles.keyDetailField}>
          <dt>{t("AiApiPage.colKeyName")}</dt>
          <dd className={styles.keyDetailValue}>
            <span>{credential.api_key_name}</span>
            <button type="button" className={styles.copyBtn} onClick={() => copy(t("AiApiPage.colKeyName"), credential.api_key_name)} aria-label={t("AiApiPage.copyKeyName")} title={t("AiApiPage.copyKeyName")}><MIcon name="content_copy" size={16} /></button>
          </dd>
        </div>
        <div className={styles.keyDetailField}>
          <dt>{t("AiApiPage.fieldBaseUrl")}</dt>
          <dd className={styles.keyDetailValue}>
            <code>{baseUrl || "—"}</code>
            {baseUrl && <button type="button" className={styles.copyBtn} onClick={() => copy(t("AiApiPage.fieldBaseUrl"), baseUrl)} aria-label={t("AiApiPage.copyBaseUrl")} title={t("AiApiPage.copyBaseUrl")}><MIcon name="content_copy" size={16} /></button>}
          </dd>
        </div>
        <div className={styles.keyDetailMeta}>
          <div><dt>{t("AiApiPage.colStatus")}</dt><dd>{t(CREDENTIAL_STATE_LABEL_KEYS[state])}</dd></div>
          <div><dt>{t("AiApiPage.colCreated")}</dt><dd>{formatDateTime(credential.created_at)}</dd></div>
          <div><dt>{t("AiApiPage.colExpiry")}</dt><dd>{credential.expires_at ? formatDateTime(credential.expires_at) : t("AiApiPage.durationOptionNever")}</dd></div>
          <div><dt>{t("AiApiPage.keyDetailRateLimit")}</dt><dd>{credential.rate_limit == null ? "—" : t("AiApiPage.keyDetailRateValue", { count: credential.rate_limit })}</dd></div>
          {credential.revoked_at && <div><dt>{t("AiApiPage.keyDetailRevoked")}</dt><dd>{formatDateTime(credential.revoked_at)}</dd></div>}
        </div>
      </dl>
    </Modal>
  );
}

/* ── Credential row：一把金鑰一列，名稱開詳細視窗，其餘動作收進 ⋮ ── */
function CredentialRow({ item, state, onRefresh, onDeleted, onShowDetails, onRotated }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState(false);
  const [nameInput, setNameInput] = useState(item.api_key_name);
  const [nameInvalid, setNameInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useDialogPresence(menuOpen, 130);
  const menuBtnRef = useRef(null);
  const deprecated = state !== "active";
  // 被替換的金鑰沒有任何可做的事；過期的仍可刪除（重新產生會說明為什麼不行）
  const hasMenu = state === "active" || state === "expired";

  function fmtExpiry(value) {
    if (!value) return t("AiApiPage.durationOptionNever");
    const label = formatDateTime(value);
    return isExpired(value) ? t("AiApiPage.expiredFormat", { date: label }) : label;
  }

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
      const created = await AiApiService.rotateCredential(item.id);
      toast.success(t("AiApiPage.rotateSuccess"));
      /* 頁面層保留新金鑰，列表重新載入時視窗仍會持續顯示。 */
      if (created?.id) onRotated(created);
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
      await AiApiService.deleteCredential(item.id);
      toast.success(t("AiApiPage.deleteSuccess"));
      onDeleted(item.id);
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.deleteError"));
    } finally {
      setBusy(false);
    }
  };

  const doRename = async () => {
    if (!nameInput.trim()) {
      setNameInvalid(true);
      return;
    }
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

  const startRename = () => { setNameInput(item.api_key_name); setNameInvalid(false); setEditing(true); };
  const cancelRename = () => { setNameInput(item.api_key_name); setNameInvalid(false); setEditing(false); };

  return (
    <tr className={`${styles.tr} ${deprecated ? styles.trDeprecated : ""}`}>
      <td className={styles.td}>
        <div className={styles.rowMain}>
          {editing ? (
            <div className={styles.renameRow}>
              <input
                type="text"
                className={`${styles.renameInput} ${nameInvalid ? styles.fieldInvalid : ""}`}
                value={nameInput}
                maxLength={20}
                aria-label={t("AiApiPage.formLabelKeyName")}
                aria-invalid={nameInvalid}
                title={nameInvalid ? t("AiApiPage.formErrorKeyName") : undefined}
                onChange={(e) => { setNameInput(e.target.value); setNameInvalid(false); }}
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
            <button
              type="button"
              className={`${styles.rowName} ${styles.keyDetailLink} ${deprecated ? styles.rowNameDeprecated : ""}`}
              onClick={() => onShowDetails(item)}
              title={t("AiApiPage.openKeyDetails", { name: item.api_key_name })}
              aria-label={t("AiApiPage.openKeyDetails", { name: item.api_key_name })}
            >{item.api_key_name}</button>
          )}
          <span
            className={`${styles.rowKey} ${deprecated ? styles.rowKeyDeprecated : ""}`}
            title={deprecated ? undefined : t("AiApiPage.keyHiddenHint")}
          >
            {maskPrefix(item.api_key_prefix)}
          </span>
        </div>
      </td>
      <td className={styles.td}>
        <span className={`${styles.badge} ${deprecated ? styles.badge_muted : styles.badge_active}`}>
          <span className={styles.dot} />
          {t(CREDENTIAL_STATE_LABEL_KEYS[state])}
        </span>
      </td>
      <td className={styles.td}>{formatDateTime(item.created_at)}</td>
      <td className={styles.td}>
        <span className={state === "expired" ? styles.textDanger : ""}>{fmtExpiry(item.expires_at)}</span>
        {item.revoked_at && (
          <div className={styles.cellSubline}>{t("AiApiPage.metaRevoked", { value: formatDateTime(item.revoked_at) })}</div>
        )}
      </td>
      <td className={`${styles.td} ${styles.tdActions}`}>
        {hasMenu && (
          <div className={styles.rowActions} data-guide="ai-key-actions">
            {menu.open && (
              <KeyMenu
                state={state}
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
        )}
      </td>
    </tr>
  );
}

/* ── API 快速開始的內容：對象是學生，只留「複製連線資訊 → 查模型 → 貼範例執行」三步，
   出錯才需要的對照表收在最下面 ── */
function ApiDocsContent({ credentials, publicBaseUrl }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const copy = useCopyToClipboard();
  const [language, setLanguage] = useState("javascript");
  const [endpointKind, setEndpointKind] = useState("responses");
  const [copyingKey, setCopyingKey] = useState(false);
  const usable = credentials.filter((item) => getCredentialState(item, credentials) === "active");
  const [credentialId, setCredentialId] = useState(null);
  const credential = usable.find((item) => item.id === credentialId) ?? usable[0] ?? null;
  /* Base URL 不是機密：還沒有可用金鑰時用後端回傳的公開位址，範例才不會整段變成 BASE_URL */
  const baseUrl = buildAiProxyBaseUrl(credential?.base_url || publicBaseUrl || credentials[0]?.base_url);
  const languageInfo = CODE_LANGUAGES.find((item) => item.key === language) ?? CODE_LANGUAGES[0];
  const code = buildApiExample(language, baseUrl, endpointKind);
  const modelsCommand = buildModelsCommand(baseUrl);
  const endpointKinds = [
    { value: "responses", label: "Responses" },
    { value: "chat", label: "Chat Completions" },
  ];

  /* 清單只有前綴；按複製時才取回完整金鑰，不用關掉視窗回清單找 */
  async function copyFullKey() {
    if (!credential || copyingKey) return;
    setCopyingKey(true);
    try {
      const detail = await AiApiService.getCredential(credential.id);
      if (!detail?.api_key) throw new Error("Missing API key");
      await copy(t("AiApiPage.fieldApiKey"), detail.api_key);
    } catch {
      toast.error(t("AiApiPage.copiedError", { label: t("AiApiPage.fieldApiKey") }));
    } finally {
      setCopyingKey(false);
    }
  }

  return (
    <div className={styles.docsLayout}>
      {/* 1. 連線資訊：呼叫 API 只需要這兩個值，寬螢幕並排 */}
      <section className={styles.docsStep}>
        <span className={styles.docsStepNumber}>1</span>
        <div className={styles.docsStepBody}>
          <h3 className={styles.docsStepTitle}>{t("AiApiPage.docsConnTitle")}</h3>
          <div className={styles.docsConnGrid}>
            <div className={styles.docsField}>
              <span className={styles.docsFieldLabel}>{t("AiApiPage.fieldBaseUrl")}</span>
              <div className={styles.docsEndpointRow}>
                <code title={baseUrl || undefined}>{baseUrl || t("AiApiPage.docsBaseUrlUnavailable")}</code>
                <button type="button" className={styles.copyBtn} onClick={() => copy(t("AiApiPage.fieldBaseUrl"), baseUrl)} disabled={!baseUrl} aria-label={t("AiApiPage.copyBaseUrl")} title={t("AiApiPage.copyBaseUrl")}>
                  <MIcon name="content_copy" size={16} />
                </button>
              </div>
            </div>
            <div className={styles.docsField}>
              <span className={styles.docsFieldLabel}>{t("AiApiPage.fieldApiKey")}</span>
              {credential ? (
                <>
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
                  <div className={styles.docsEndpointRow}>
                    <code>{maskPrefix(credential.api_key_prefix)}</code>
                    <button type="button" className={styles.copyBtn} onClick={copyFullKey} disabled={copyingKey} aria-label={t("AiApiPage.actionCopyKey")} title={t("AiApiPage.actionCopyKey")}>
                      <MIcon name={copyingKey ? "hourglass_empty" : "content_copy"} size={16} />
                    </button>
                  </div>
                </>
              ) : (
                <p className={styles.docsNotice}>
                  <MIcon name="info" size={16} />
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
          <h3 className={styles.docsStepTitle}>{t("AiApiPage.docsModelsTitle")}</h3>
          <div className={styles.docsEndpointRow}>
            <code title={modelsCommand}>{modelsCommand}</code>
            <button type="button" className={styles.copyBtn} onClick={() => copy(t("AiApiPage.docsCommand"), modelsCommand)} aria-label={t("AiApiPage.copy")} title={t("AiApiPage.copy")}>
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
            <SegmentedControl
              options={endpointKinds}
              value={endpointKind}
              onChange={setEndpointKind}
              ariaLabel={t("AiApiPage.docsEndpointKindLabel")}
              className={styles.codeTabs}
            />
            <SegmentedControl
              options={CODE_LANGUAGES.map((item) => ({ value: item.key, label: t(item.labelKey) }))}
              value={language}
              onChange={setLanguage}
              ariaLabel={t("AiApiPage.docsLanguageLabel")}
              className={styles.codeTabs}
            />
          </div>
          <div className={styles.codeCard}>
            <div className={styles.codePanelHeader}>
              <span>{t(languageInfo.labelKey)}</span>
              <button type="button" className={styles.codeCopyButton} onClick={() => copy(t("AiApiPage.docsCode"), code)} aria-label={t("AiApiPage.copyCode")} title={t("AiApiPage.copyCode")}>
                <MIcon name="content_copy" size={16} />
              </button>
            </div>
            <div className={styles.codeViewport}>
              <Suspense fallback={<pre className={styles.codeBlock}><code>{code}</code></pre>}>
                <ReadOnlyCode
                  code={code}
                  language={languageInfo.editor}
                  height="100%"
                  label={`${t("AiApiPage.docsCode")} (${t(languageInfo.labelKey)})`}
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
function QuickStartModal({ closing = false, credentials, publicBaseUrl, onClose }) {
  const { t } = useTranslation("ai");

  /* 標題列固定、文件內容自己捲；寬度用規範的一般級（程式碼一行放得下） */
  return (
    <Modal closing={closing} onClose={onClose} closeButton size="lg" title={t("AiApiPage.quickStartButton")}>
      <ApiDocsContent credentials={credentials} publicBaseUrl={publicBaseUrl} />
    </Modal>
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
        <span className={styles.rowName} title={item.api_key_name || undefined}>{item.api_key_name || "—"}</span>
      </td>
      <td className={styles.td}>
        <span className={styles.cellClamp} title={item.purpose || undefined}>{item.purpose || "—"}</span>
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
        {/* 審核意見整段顯示：被拒絕的原因是下一步的依據，觸控裝置看不到 title */}
        {item.review_comment
          ? <span className={`${styles.cellWrap} ${st === "rejected" ? styles.textDanger : ""}`}>{item.review_comment}</span>
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

/* ── Usage: 按模型統計；欄位要有標題，箭頭符號學生看不懂 ── */
function UsageBreakdown({ entries, formatter }) {
  const { t } = useTranslation("ai");
  if (!entries || Object.keys(entries).length === 0) return null;
  return (
    <div className={styles.usageBreakdownList}>
      <div className={`${styles.usageBreakdownRow} ${styles.usageBreakdownHead}`}>
        <span>{t("AiApiPage.recordModel")}</span>
        <span>{t("AiApiPage.usageStatTotalCalls")}</span>
        <span>{t("AiApiPage.usageStatInputTokens")}</span>
        <span>{t("AiApiPage.usageStatOutputTokens")}</span>
      </div>
      {Object.entries(entries).map(([key, stats]) => (
        <div key={key} className={styles.usageBreakdownRow}>
          <span className={styles.usageBreakdownKey}>{formatter ? formatter(key) : key}</span>
          <span>{t("AiApiPage.callCount", { count: stats.calls ?? stats.requests ?? 0 })}</span>
          <span>{formatTokens(stats.input_tokens)}</span>
          <span>{formatTokens(stats.output_tokens)}</span>
        </div>
      ))}
    </div>
  );
}

/* call_type 來自 AI 代理記錄的 request_type（backend services/llm_gateway/relay_service.GENERATION_ENDPOINTS）；
   沒列到的型別（completion、response…）直接顯示原字串。只放 locale 裡
   確實存在的 key，否則畫面會露出 key 字串本身。 */
const CALL_TYPE_LABELS = {
  chat: "AiApiPage.callTypeChat",
  recommend: "AiApiPage.callTypeRecommend",
  chat_completion: "AiApiPage.callTypeChatCompletion",
};

/* ── Usage record row ── */
function UsageRecordRow({ item }) {
  const { t } = useTranslation("ai");
  const [expanded, setExpanded] = useState(false);

  const succeeded = isOkStatus(item.status);
  const callTypeKey = CALL_TYPE_LABELS[item.call_type];
  const model = formatModelDisplay(item.model_name);
  const createdAt = formatDateTime(item.created_at);
  const detailId = `usage-record-${String(item.id).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const totalTokens = item.total_tokens ?? ((item.input_tokens ?? 0) + (item.output_tokens ?? 0));

  return (
    <div className={`${styles.usageRecordRow} ${expanded ? styles.usageRecordRowExpanded : ""}`}>
      <button
        type="button"
        className={styles.usageRecordSummary}
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={t(expanded ? "AiApiPage.recordCollapseAria" : "AiApiPage.recordExpandAria", {
          date: createdAt,
          model,
        })}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className={styles.usageRecordPrimary}>{createdAt}</span>
        <span className={`${styles.usageRecordPrimary} ${styles.usageRecordModel}`} title={model}>{model}</span>
        <span className={styles.usageRecordCell}>
          <span className={styles.usageRecordPrimary} title={item.api_key_name || undefined}>{item.api_key_name || "—"}</span>
          {item.api_key_prefix && <span className={styles.usageRecordMeta}>{item.api_key_prefix}…</span>}
        </span>
        <span className={styles.usageRecordToken}>{formatTokens(item.input_tokens)}</span>
        <span className={styles.usageRecordToken}>{formatTokens(item.output_tokens)}</span>
        <span>
          <span className={`${styles.badge} ${succeeded ? styles.badge_success : styles.badge_danger}`}>
            <span className={styles.dot} />
            {succeeded ? t("AiApiPage.recordStatusSuccess") : t("AiApiPage.recordStatusError")}
          </span>
        </span>
        <MIcon
          name="expand_more"
          size={20}
          className={styles.usageRecordChevron}
          aria-hidden="true"
        />
      </button>
      {expanded && (
        <div id={detailId} className={styles.usageRecordExpanded}>
          <dl className={styles.usageRecordDetails}>
            <div>
              <dt>{t("AiApiPage.recordRequestId")}</dt>
              <dd className={styles.usageRecordId}>{item.id}</dd>
            </div>
            <div>
              <dt>{t("AiApiPage.recordRequestType")}</dt>
              <dd>{callTypeKey ? t(callTypeKey) : item.call_type || "—"}</dd>
            </div>
            <div>
              <dt>{t("AiApiPage.recordTotalTokens")}</dt>
              <dd>{formatTokens(totalTokens)}</dd>
            </div>
            <div>
              <dt>{t("AiApiPage.recordLatency")}</dt>
              <dd>{item.request_duration_ms == null ? "—" : t("AiApiPage.recordDuration", { seconds: (item.request_duration_ms / 1000).toFixed(1) })}</dd>
            </div>
          </dl>
          {item.error_message && (
            <div className={styles.usageRecordError} role="alert">
              <MIcon name="error_outline" size={15} />
              <span>{item.error_message}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── My Usage Tab ── */
const USAGE_RECORD_PAGE_SIZE = 20;

function MyUsageTab() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [preset, setPreset] = useState("30d");
  const [usageData, setUsageData] = useState(null);
  const [usageError, setUsageError] = useState(false);
  const [records, setRecords] = useState([]);
  const [recordsCount, setRecordsCount] = useState(0);
  const [recordsError, setRecordsError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const { startDate: start, endDate: end } = useMemo(() => presetToRange(preset), [preset]);

  /* 快速切換區間時只採用最後一次 load 的結果；進行中的「載入更多」
     在區間改變後也要丟掉，不能把舊區間的紀錄接到新清單後面 */
  const loadSeqRef = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setUsageError(false);
    setRecordsError(false);
    const [usageRes, recRes] = await Promise.allSettled([
      AiApiService.getMyUsage({ start_date: start, end_date: end }),
      AiApiService.getMyUsageRecords({ start_date: start, end_date: end, limit: USAGE_RECORD_PAGE_SIZE }),
    ]);
    if (seq !== loadSeqRef.current) return;
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
    const seq = loadSeqRef.current;
    setLoadingMore(true);
    try {
      const res = await AiApiService.getMyUsageRecords({
        start_date: start,
        end_date: end,
        skip: records.length,
        limit: USAGE_RECORD_PAGE_SIZE,
      });
      if (seq !== loadSeqRef.current) return;
      setRecords((prev) => [...prev, ...(res?.data ?? [])]);
      setRecordsCount(res?.count ?? 0);
    } catch (e) {
      /* 維持現有清單，不覆蓋成功資料；但要讓使用者知道沒載到 */
      if (seq === loadSeqRef.current) toast.error(e?.message ?? t("AiApiPage.recordsLoadMoreError"));
    } finally {
      setLoadingMore(false);
    }
  };

  const PRESETS = [
    { value: "7d", label: t("AiApiPage.preset7d") },
    { value: "30d", label: t("AiApiPage.preset30d") },
    { value: "90d", label: t("AiApiPage.preset90d") },
  ];

  return (
    <div className={styles.usageTab}>
      <div className={styles.usageDateRow} data-guide="ai-usage-panel">
        <SegmentedControl
          options={PRESETS}
          value={preset}
          onChange={setPreset}
          ariaLabel={t("AiApiPage.usageRangeLabel")}
        />
        {/* 用本地日期顯示；toISOString 是 UTC，台灣凌晨會差一天 */}
        <span className={styles.usageDateRange}>{formatDate(start)} ~ {formatDate(end)}</span>
      </div>

      {loading ? (
        <LoadingState />
      ) : usageError && recordsError ? (
        <ErrorState onRetry={load} />
      ) : (
        <>
          {/* ── 申請金鑰 API 用量總覽 ── */}
          <div className={styles.usagePanel} data-guide="ai-route-usage">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageTitle")}</h3>
              <p className={styles.usagePanelDesc}>{t("AiApiPage.usageDesc")}</p>
            </div>
            {usageError ? (
              <ErrorState onRetry={load} />
            ) : (
              <>
                <div className={styles.usageStatsGrid}>
                  <UsageStatCard label={t("AiApiPage.usageStatTotalCalls")} value={usageData?.total_requests ?? 0} />
                  <UsageStatCard label={t("AiApiPage.usageStatInputTokens")} value={formatTokens(usageData?.total_input_tokens)} />
                  <UsageStatCard label={t("AiApiPage.usageStatOutputTokens")} value={formatTokens(usageData?.total_output_tokens)} />
                </div>
                <DailyUsageChart daily={usageData?.daily} />
                <UsageBreakdown entries={usageData?.by_model} formatter={formatModelDisplay} />
              </>
            )}
          </div>

          {/* ── 細項呼叫紀錄：沒有資料時只在這裡說一次 ── */}
          <div className={styles.usagePanel} data-guide="ai-usage-records">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageRecordsTitle")}</h3>
            </div>
            {recordsError ? (
              <ErrorState onRetry={load} />
            ) : records.length === 0 ? (
              <SharedEmptyState
                icon="receipt_long"
                title={t("AiApiPage.usageRecordsEmpty")}
                description={t("AiApiPage.usageRecordsEmptyDesc")}
              />
            ) : (
              <>
                {/* 窄螢幕維持表格、容器橫向捲動（表格規範不轉卡片） */}
                <div className={styles.usageRecordScroll}>
                  <div className={styles.usageRecordList}>
                    <div className={styles.usageRecordHeader} aria-hidden="true">
                      <span>{t("AiApiPage.recordDate")}</span>
                      <span>{t("AiApiPage.recordModel")}</span>
                      <span>{t("AiApiPage.recordKey")}</span>
                      <span>{t("AiApiPage.recordInputTokens")}</span>
                      <span>{t("AiApiPage.recordOutputTokens")}</span>
                      <span>{t("AiApiPage.recordStatus")}</span>
                      <span />
                    </div>
                    {records.map((item) => (
                      <UsageRecordRow key={`${item.route}-${item.id}`} item={item} />
                    ))}
                  </div>
                </div>
                {hasMoreRecords && (
                  <div className={styles.usageRecordsMoreRow}>
                    <button type="button" className={styles.btnSecondary} onClick={loadMore} disabled={loadingMore}>
                      {loadingMore ? (
                        <>
                          <MIcon name="hourglass_empty" size={16} spin />
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
  nameInvalid,
  nameInputRef,
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
  const purposeLength = purpose.trim().length;

  /* 外框（遮罩、標題列、Esc、焦點、捲動鎖）交給共用 Modal；送出中 Esc／點遮罩／× 都不關 */
  return (
    <Modal
      closing={closing}
      onClose={onClose}
      busy={busy}
      closeButton
      size="md"
      title={t("AiApiPage.applyPanelTitle")}
      data-guide="ai-form"
      closeProps={{ "data-guide": "ai-apply-close" }}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={busy}>
            {t("AiApiPage.cancel")}
          </button>
          <button type="button" className={styles.btnPrimary} onClick={onSubmit} disabled={busy} data-guide="ai-submit">
            <MIcon name="send" size={16} />
            {busy ? t("AiApiPage.submitButtonSubmitting") : t("AiApiPage.submitButton")}
          </button>
        </>
      }
    >
      <div className={styles.field}>
        <label htmlFor="ai-key-name">{t("AiApiPage.formLabelKeyName")}</label>
        <input
          id="ai-key-name"
          ref={nameInputRef}
          type="text"
          className={nameInvalid ? styles.fieldInvalid : undefined}
          value={apiKeyName}
          onChange={(e) => onApiKeyNameChange(e.target.value)}
          placeholder={t("AiApiPage.formPlaceholderKeyName")}
          maxLength={20}
          aria-invalid={nameInvalid}
          aria-describedby={nameInvalid ? "ai-key-name-error" : undefined}
          data-guide="ai-apply-name"
        />
        {nameInvalid && <small id="ai-key-name-error" className={styles.fieldError} role="alert">{t("AiApiPage.formErrorKeyName")}</small>}
      </div>

      <div className={styles.field}>
        <label htmlFor="ai-purpose">{t("AiApiPage.formLabelPurpose")}</label>
        <textarea
          id="ai-purpose"
          ref={purposeInputRef}
          className={purposeInvalid ? styles.fieldInvalid : undefined}
          value={purpose}
          onChange={(e) => onPurposeChange(e.target.value)}
          placeholder={t("AiApiPage.formPlaceholderPurpose")}
          rows={5}
          maxLength={2000}
          aria-invalid={purposeInvalid}
          aria-describedby="ai-purpose-hint"
          data-guide="ai-apply-purpose"
        />
        {/* 字數要求放在欄位正下方，不擠在按鈕列 */}
        <small
          id="ai-purpose-hint"
          className={purposeInvalid ? styles.fieldError : styles.fieldHint}
          role={purposeInvalid ? "alert" : undefined}
        >
          {t(purposeInvalid ? "AiApiPage.formErrorPurpose" : "AiApiPage.formHintPurpose", { count: purposeLength })}
        </small>
      </div>

      <div className={styles.field}>
        <label htmlFor="ai-duration">{t("AiApiPage.formLabelDuration")}</label>
        <select
          id="ai-duration"
          className={styles.durationSelect}
          value={duration}
          onChange={(e) => onDurationChange(e.target.value)}
          data-guide="ai-apply-duration"
        >
          {durationOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>
    </Modal>
  );
}

/* ───────────────────────────── Main ───────────────────────────── */

export default function AiApiPage() {
  const { t } = useTranslation("ai");
  const { user } = useAuth();
  const isStudent = user?.role !== "teacher" && user?.role !== "admin";
  const defaultDuration = isStudent ? "30d" : "never";
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("keys");

  const DURATION_OPTIONS = [
    { value: "1d", label: t("AiApiPage.durationOption1d") },
    { value: "7d", label: t("AiApiPage.durationOption7d") },
    { value: "30d", label: t("AiApiPage.durationOption30d") },
    isStudent
      ? { value: "90d", label: t("AiApiPage.durationOption90d") }
      : { value: "never", label: t("AiApiPage.durationOptionNever") },
  ];

  /* 「API 聊天」用登入者自己核准的金鑰（只在需要時向擁有者專用端點取回、留在記憶體），
     不再打包共用金鑰；絕不能改回讀 VITE_* 環境變數，那會公開在前端 bundle 裡 */
  const TABS = [
    { key: "keys", label: t("AiApiPage.tabKeys") },
    { key: "chat", label: t("AiApiPage.tabChat") },
    { key: "records", label: t("AiApiPage.tabRecords") },
    { key: "usage", label: t("AiApiPage.tabUsage") },
  ];

  /* ── Form state ── */
  const [apiKeyName, setApiKeyName] = useState("");
  const [nameInvalid, setNameInvalid] = useState(false);
  const nameInputRef = useRef(null);
  const [purpose, setPurpose] = useState("");
  const [duration, setDuration] = useState(defaultDuration);
  useEffect(() => { setDuration(defaultDuration); }, [defaultDuration]);
  const [submitting, setSubmitting] = useState(false);
  const [purposeInvalid, setPurposeInvalid] = useState(false);
  const purposeInputRef = useRef(null);
  const [showApplyModal, setShowApplyModal] = useState(false);
  const applyDialog = useDialogPresence(showApplyModal);
  const [showQuickStart, setShowQuickStart] = useState(false);
  const quickStartDialog = useDialogPresence(showQuickStart);

  /* ── Data ── */
  const [credentials, setCredentials] = useState([]);
  const [publicBaseUrl, setPublicBaseUrl] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [keyDetail, setKeyDetail] = useState(null);
  const keyDetailDialog = useDialogPresence(keyDetail);
  const closeKeyDetail = useCallback(() => setKeyDetail(null), []);
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const requestStatusRef = useRef(null);

  /* silent：操作後、自動更新時在背景重抓，不讓整張表閃成載入動畫，失敗時保留畫面上的資料；
     quiet：自動更新失敗不跳 toast（網路斷線時每 30 秒一則會轟炸） */
  const load = useCallback(async ({ silent = false, quiet = false } = {}) => {
    if (!silent) {
      setLoading(true);
      setLoadError(false);
    }
    try {
      const [credRes, reqRes] = await Promise.all([
        AiApiService.listMyCredentials(),
        AiApiService.listMyRequests(),
      ]);
      const nextRequests = reqRes?.data ?? [];
      // 待審的申請有了結果就提醒：學生常開著這頁等審核
      const previous = requestStatusRef.current;
      if (previous) {
        nextRequests.forEach((item) => {
          if (previous.get(item.id) !== "pending") return;
          if (item.status === "approved") toast.success(t("AiApiPage.requestApprovedToast", { name: item.api_key_name }));
          if (item.status === "rejected") toast.error(t("AiApiPage.requestRejectedToast", { name: item.api_key_name }));
        });
      }
      requestStatusRef.current = new Map(nextRequests.map((item) => [item.id, item.status]));
      setCredentials(credRes?.data ?? []);
      setPublicBaseUrl(credRes?.public_base_url ?? "");
      setRequests(nextRequests);
    } catch (e) {
      if (!silent) setLoadError(true);
      else if (!quiet) toast.error(e?.message ?? t("AiApiPage.loadError"));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => { load(); }, [load]);

  const pendingCount = requests.filter((item) => item.status === "pending").length;
  // 有待審申請時自動更新，審核結果不用整頁重新整理
  useAutoRefresh(() => {
    if (pendingCount > 0) load({ silent: true, quiet: true });
  });

  const credentialStates = useMemo(
    () => new Map(credentials.map((item) => [item.id, getCredentialState(item, credentials)])),
    [credentials],
  );
  const activeCredentials = credentials.filter((item) => credentialStates.get(item.id) === "active");
  const inactiveCredentials = credentials.filter((item) => credentialStates.get(item.id) !== "active");
  // 失效的金鑰預設收起來：每重新產生一次就多一列同名的舊金鑰
  const visibleCredentials = showInactive ? [...activeCredentials, ...inactiveCredentials] : activeCredentials;
  /* 目前分頁正顯示帶「新增金鑰」的空狀態時，頁首那顆先藏起來，不要上下兩顆一樣的鈕；
     導覽的 ai-add-key 標記改掛在空狀態那顆上。金鑰／紀錄分頁載入中也先不放，免得閃一下 */
  const listTab = activeTab === "keys" || activeTab === "records";
  const emptyWithAddKey = !loading && !loadError && (
    (activeTab === "keys" && (credentials.length === 0 ? pendingCount === 0 : visibleCredentials.length === 0))
    || (activeTab === "records" && requests.length === 0)
  );
  const showHeaderAddKey = !(listTab && (loading || emptyWithAddKey));

  /* ── Submit request ── */
  const handleSubmit = async () => {
    const name = apiKeyName.trim();
    const nameMissing = !name;
    const purposeTooShort = purpose.trim().length < 10;
    setNameInvalid(nameMissing);
    setPurposeInvalid(purposeTooShort);
    if (nameMissing || purposeTooShort) {
      focusInvalidField((nameMissing ? nameInputRef : purposeInputRef).current);
      return;
    }
    if (!DURATION_OPTIONS.some((option) => option.value === duration)) {
      toast.error(t("AiApiPage.formErrorDuration"));
      return;
    }
    setSubmitting(true);
    try {
      await AiApiService.createRequest({
        purpose: purpose.trim(),
        api_key_name: name,
        duration,
      });
      setPurpose("");
      setApiKeyName("");
      setDuration(defaultDuration);
      setShowApplyModal(false);
      // 申請要等審核，送出後帶到申請紀錄，才看得到它在等
      setActiveTab("records");
      toast.success(t("AiApiPage.submitSuccess"));
      load({ silent: true });
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.submitError"));
    } finally {
      setSubmitting(false);
    }
  };

  const detailItem = keyDetailDialog.item;
  const detailCredential = detailItem
    ? detailItem.apiKey
      ? detailItem.credential
      : credentials.find((item) => item.id === detailItem.credential.id) ?? detailItem.credential
    : null;

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <PageHeader title={t("AiApiPage.pageTitle")} />

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
              // 金鑰數跟清單一致（只算使用中）；申請紀錄只標待審的，0 就不顯示
              badge: tab.key === "keys"
                ? activeCredentials.length
                : tab.key === "records" && pendingCount > 0 ? pendingCount : undefined,
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
            className={styles.btnSecondary}
            onClick={() => setShowQuickStart(true)}
            data-guide="ai-quick-start"
          >
            <MIcon name="rocket_launch" size={16} />
            {t("AiApiPage.quickStartButton")}
          </button>
          {showHeaderAddKey && (
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => setShowApplyModal(true)}
              data-guide="ai-add-key"
            >
              <MIcon name="add" size={16} />
              {t("AiApiPage.addKeyButton")}
            </button>
          )}
        </div>
      </div>

      {/* ── Content ── */}
      <div className={styles.content}>
        {/* ---- Tab: API 金鑰 ---- */}
        {activeTab === "keys" && (
          loading ? (
            <LoadingState />
          ) : loadError ? (
            <ErrorState onRetry={() => load()} />
          ) : credentials.length === 0 ? (
            pendingCount > 0 ? (
              <EmptyState
                icon="hourglass_top"
                title={t("AiApiPage.keysPendingTitle")}
                description={t("AiApiPage.keysPendingDesc")}
                action={(
                  <button type="button" className={styles.btnSecondary} onClick={() => setActiveTab("records")}>
                    {t("AiApiPage.viewRecords")}
                  </button>
                )}
                guideId="ai-keys-content"
              />
            ) : (
              <EmptyState
                icon="vpn_key"
                title={t("AiApiPage.keysEmptyTitle")}
                description={t("AiApiPage.keysEmptyDesc")}
                action={(
                  <button type="button" className={styles.btnPrimary} onClick={() => setShowApplyModal(true)} data-guide="ai-add-key">
                    <MIcon name="add" size={16} />{t("AiApiPage.addKeyButton")}
                  </button>
                )}
                guideId="ai-keys-content"
              />
            )
          ) : (
            <div className={styles.keysSection} data-guide="ai-keys-content">
              {visibleCredentials.length === 0 ? (
                <SharedEmptyState
                  icon="key_off"
                  title={t("AiApiPage.keysNoActiveTitle")}
                  description={t("AiApiPage.keysNoActiveDesc")}
                  action={(
                  <button type="button" className={styles.btnPrimary} onClick={() => setShowApplyModal(true)} data-guide="ai-add-key">
                    <MIcon name="add" size={16} />{t("AiApiPage.addKeyButton")}
                  </button>
                )}
                />
              ) : (
                <div className={styles.tableWrap}>
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
                      {visibleCredentials.map((item) => (
                        <CredentialRow
                          key={item.id}
                          item={item}
                          state={credentialStates.get(item.id)}
                          onRefresh={() => load({ silent: true })}
                          onDeleted={(credentialId) => {
                            setCredentials((current) => current.filter((credential) => credential.id !== credentialId));
                            setKeyDetail((current) => current?.credential?.id === credentialId ? null : current);
                          }}
                          onShowDetails={(credential) => setKeyDetail({ credential, apiKey: null })}
                          onRotated={(credential) => setKeyDetail({ credential, apiKey: credential.api_key || null })}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {inactiveCredentials.length > 0 && (
                <button
                  type="button"
                  className={styles.btnGhost}
                  onClick={() => setShowInactive((value) => !value)}
                  aria-expanded={showInactive}
                >
                  <MIcon name={showInactive ? "expand_less" : "expand_more"} size={16} />
                  {showInactive
                    ? t("AiApiPage.hideInactive")
                    : t("AiApiPage.showInactive", { count: inactiveCredentials.length })}
                </button>
              )}
            </div>
          )
        )}

        {/* ---- Tab: 申請紀錄 ---- */}
        {activeTab === "records" && (
          loading ? (
            <LoadingState />
          ) : loadError ? (
            <ErrorState onRetry={() => load()} />
          ) : requests.length === 0 ? (
            <EmptyState
              icon="history"
              title={t("AiApiPage.recordsEmptyTitle")}
              description={t("AiApiPage.recordsEmptyDesc")}
              action={(
                  <button type="button" className={styles.btnPrimary} onClick={() => setShowApplyModal(true)} data-guide="ai-add-key">
                    <MIcon name="add" size={16} />{t("AiApiPage.addKeyButton")}
                  </button>
                )}
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
        {activeTab === "chat" && <Suspense fallback={<LoadingState />}><AiApiChatTab credentials={activeCredentials} credentialsLoading={loading} /></Suspense>}
      </div>

      {detailCredential && (
        <KeyDetailDialog
          key={detailCredential.id}
          credential={detailCredential}
          apiKey={detailItem.apiKey}
          state={detailItem.apiKey ? "active" : credentialStates.get(detailCredential.id) ?? getCredentialState(detailCredential, credentials)}
          closing={keyDetailDialog.closing}
          onClose={closeKeyDetail}
        />
      )}

      {/* ── API 快速開始彈窗（原「API 文件」分頁） ── */}
      {quickStartDialog.open && (
        <QuickStartModal
          closing={quickStartDialog.closing}
          credentials={credentials}
          publicBaseUrl={publicBaseUrl}
          onClose={() => setShowQuickStart(false)}
        />
      )}

      {/* ── 申請金鑰彈窗 ── */}
      {applyDialog.open && (
        <ApplyKeyModal
          closing={applyDialog.closing}
          busy={submitting}
          apiKeyName={apiKeyName}
          onApiKeyNameChange={(value) => { setApiKeyName(value); setNameInvalid(false); }}
          nameInvalid={nameInvalid}
          nameInputRef={nameInputRef}
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
