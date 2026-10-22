import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./AiApiPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import DataTable, { DataTableCells } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { AiApiService } from "../../../services/aiApi";
import { useAuth } from "../../../contexts/AuthContext";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useLoadError } from "../../../hooks/useQueryLoadState";
import {
  useAiApiCacheUpdater,
  useAiCredentialQuery,
  useMyAiCredentialsQuery,
  useMyAiRequestsQuery,
  useMyAiUsageQuery,
  useMyAiUsageRecordsInfiniteQuery,
} from "../../../queries/aiApi";
import { LIST_REFRESH_INTERVAL } from "../../../queries/queryClient";
import { focusInvalidField } from "../../../utils/focusField";
import PageHeader from "../../../components/PageHeader/PageHeader";
import RrdChart from "../../../components/RrdChart/RrdChart";
import { formatDate, formatDateTime, formatMonthDay } from "../../../utils/formatDate";
import useAnchoredMenu from "../../../hooks/useAnchoredMenu";
import { formatModelDisplay, formatTokens, isOkStatus, presetToRange } from "../aiFormat";

const ReadOnlyCode = lazy(() => import("../../../components/ReadOnlyCode/ReadOnlyCode"));
const AiApiChatTab = lazy(() => import("./AiApiChatTab"));

/* ── helpers ── */

const EMPTY = Object.freeze([]);

/* listMyRequests 的回應裡有沒有待審的申請（決定要不要背景輪詢） */
function hasPendingRequest(response) {
  return (response?.data ?? []).some((item) => item.status === "pending");
}

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
  /* 輪替剛拿到的明文直接顯示；其餘可用的金鑰開視窗時才向擁有者端點取回完整金鑰
     （查詢 gcTime 0，視窗一關就從快取丟掉）。已失效的不取回、也不給複製。 */
  const needsFetch = usable && !apiKey;
  const keyQuery = useAiCredentialQuery(credential.id, { enabled: needsFetch && !closing });
  const { cancelCredential } = useAiApiCacheUpdater();
  const loadedKey = keyQuery.data?.api_key || "";
  const keyFetching = keyQuery.isFetching;
  const keyLoading = needsFetch && !loadedKey && (keyQuery.isPending || keyFetching);
  /* 回應裡沒有金鑰也算失敗：不能拿前綴當金鑰給人複製 */
  const keyError = needsFetch && !keyFetching
    && (keyQuery.isError || (keyQuery.data !== undefined && !keyQuery.data?.api_key));
  const displayApiKey = usable ? apiKey || loadedKey : "";
  const baseUrl = buildAiProxyBaseUrl(credential.base_url);

  /* 一按關閉（離場動畫期間）就取消請求，晚回來的金鑰不會再出現在畫面上 */
  useEffect(() => {
    if (closing && needsFetch) cancelCredential(credential.id);
  }, [closing, needsFetch, cancelCredential, credential.id]);

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
                <button type="button" className={styles.btnSecondary} onClick={() => keyQuery.refetch()}>
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

/* ── 金鑰與申請紀錄的表格（TanStack Table）──
   欄位定義放模組層級：v9 的 FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會讓整格重新掛載
   （改名輸入框的焦點、選單都會掉）。表頭文字取 meta.headerKey 的翻譯。
   資料沒變時表格的列不會重繪，切換語言只會重繪有用 useTranslation 的元件，
   所以依語系格式化日期的格子（formatDateTime 讀 i18n.language）自己訂閱語系 */
const columnHelper = createColumnHelper();

function HeaderLabel({ column }) {
  const { t } = useTranslation("ai");
  return t(column.columnDef.meta.headerKey);
}

function DateTimeCell({ getValue }) {
  useTranslation("ai");
  return formatDateTime(getValue());
}

function columnMeta(headerKey, extra) {
  return { headerKey, thClassName: styles.th, tdClassName: styles.td, ...extra };
}

/* 金鑰列的各欄：改名、選單、操作中等狀態由 CredentialRow 擁有，經 rowState 傳進來 */
function CredentialNameCell({ row, rowState }) {
  const { t } = useTranslation("ai");
  const item = row.original;
  const { deprecated, busy, editing, nameInput, nameInvalid, onNameInput, onRename, onCancelRename, onShowDetails } = rowState;
  return (
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
            onChange={(e) => onNameInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onRename();
              if (e.key === "Escape") onCancelRename();
            }}
            autoFocus
          />
          <button type="button" className={styles.iconBtn} onClick={onRename} disabled={busy} aria-label={t("AiApiPage.actionRename")}>
            <MIcon name="check" size={16} />
          </button>
          <button type="button" className={styles.iconBtn} onClick={onCancelRename} aria-label={t("AiApiPage.cancel")}>
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
  );
}

function CredentialStatusCell({ rowState }) {
  const { t } = useTranslation("ai");
  const { state, deprecated } = rowState;
  return (
    <span className={`${styles.badge} ${deprecated ? styles.badge_muted : styles.badge_active}`}>
      <span className={styles.dot} />
      {t(CREDENTIAL_STATE_LABEL_KEYS[state])}
    </span>
  );
}

function CredentialExpiryCell({ row, rowState }) {
  const { t } = useTranslation("ai");
  const item = row.original;

  function fmtExpiry(value) {
    if (!value) return t("AiApiPage.durationOptionNever");
    const label = formatDateTime(value);
    return isExpired(value) ? t("AiApiPage.expiredFormat", { date: label }) : label;
  }

  return (
    <>
      <span className={rowState.state === "expired" ? styles.textDanger : ""}>{fmtExpiry(item.expires_at)}</span>
      {item.revoked_at && (
        <div className={styles.cellSubline}>{t("AiApiPage.metaRevoked", { value: formatDateTime(item.revoked_at) })}</div>
      )}
    </>
  );
}

function CredentialActionsCell({ rowState }) {
  const { t } = useTranslation("ai");
  const {
    state, busy, hasMenu, menuOpen, menuMounted, menuClosing, menuBtnRef,
    onToggleMenu, onCloseMenu, onStartRename, onRotate, onDelete,
  } = rowState;
  if (!hasMenu) return null;
  return (
    <div className={styles.rowActions} data-guide="ai-key-actions">
      {menuMounted && (
        <KeyMenu
          state={state}
          busy={busy}
          onRename={onStartRename}
          onRotate={onRotate}
          onDelete={onDelete}
          onClose={onCloseMenu}
          anchorRef={menuBtnRef}
          closing={menuClosing}
        />
      )}
      <button
        ref={menuBtnRef}
        type="button"
        className={`${styles.menuBtn} ${menuOpen ? styles.menuBtnActive : ""}`}
        onClick={onToggleMenu}
        title={t("AiApiPage.moreActions")}
        aria-label={t("AiApiPage.moreActions")}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        <MIcon name="more_vert" size={18} />
      </button>
    </div>
  );
}

const credentialColumns = columnHelper.columns([
  columnHelper.display({ id: "key", header: HeaderLabel, cell: CredentialNameCell, meta: columnMeta("AiApiPage.colKey") }),
  columnHelper.display({ id: "status", header: HeaderLabel, cell: CredentialStatusCell, meta: columnMeta("AiApiPage.colStatus") }),
  columnHelper.accessor("created_at", { header: HeaderLabel, cell: DateTimeCell, meta: columnMeta("AiApiPage.colCreated") }),
  columnHelper.display({ id: "expiry", header: HeaderLabel, cell: CredentialExpiryCell, meta: columnMeta("AiApiPage.colExpiry") }),
  columnHelper.display({
    id: "actions",
    header: HeaderLabel,
    cell: CredentialActionsCell,
    meta: columnMeta("AiApiPage.colActions", { thClassName: `${styles.th} ${styles.tdActions}`, tdClassName: `${styles.td} ${styles.tdActions}` }),
  }),
]);

/* ── Credential row：一把金鑰一列，名稱開詳細視窗，其餘動作收進 ⋮ ──
   列自己畫 <tr> 並擁有改名、選單、操作中等狀態，儲存格由欄位定義畫（DataTableCells） */
function CredentialRow({ row, item, state, onRefresh, onDeleted, onShowDetails, onRotated }) {
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
      /* 先從快取拿掉再重抓：背景重抓失敗也不會把刪掉的金鑰放回來 */
      await onDeleted(item.id);
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
      <DataTableCells
        row={row}
        rowState={{
          state,
          deprecated,
          busy,
          hasMenu,
          editing,
          nameInput,
          nameInvalid,
          onNameInput: (value) => { setNameInput(value); setNameInvalid(false); },
          onRename: doRename,
          onCancelRename: cancelRename,
          onShowDetails,
          menuOpen,
          menuMounted: menu.open,
          menuClosing: menu.closing,
          menuBtnRef,
          onToggleMenu: () => setMenuOpen((v) => !v),
          onCloseMenu: () => setMenuOpen(false),
          onStartRename: startRename,
          onRotate: doRotate,
          onDelete: doDelete,
        }}
      />
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

/* ── 申請紀錄表格的各欄 ── */
function RequestNameCell({ row }) {
  const item = row.original;
  return <span className={styles.rowName} title={item.api_key_name || undefined}>{item.api_key_name || "—"}</span>;
}

function RequestPurposeCell({ row }) {
  const item = row.original;
  return <span className={styles.cellClamp} title={item.purpose || undefined}>{item.purpose || "—"}</span>;
}

function RequestStatusCell({ row }) {
  const { t } = useTranslation("ai");
  const status = row.original.status;

  function statusLabel() {
    if (status === "approved") return t("AiApiPage.statusApproved");
    if (status === "rejected") return t("AiApiPage.statusRejected");
    return t("AiApiPage.statusPending");
  }

  return (
    <span className={`${styles.badge} ${styles[`badge_${statusStyle(status)}`]}`}>
      <span className={styles.dot} />
      {statusLabel()}
    </span>
  );
}

function RequestReviewedCell({ row }) {
  const { t } = useTranslation("ai");
  const reviewedAt = row.original.reviewed_at;
  return reviewedAt
    ? formatDateTime(reviewedAt)
    : <span className={styles.cellMuted}>{t("AiApiPage.requestNotReviewed")}</span>;
}

function RequestCommentCell({ row }) {
  const item = row.original;
  /* 審核意見整段顯示：被拒絕的原因是下一步的依據，觸控裝置看不到 title */
  return item.review_comment
    ? <span className={`${styles.cellWrap} ${statusStyle(item.status) === "rejected" ? styles.textDanger : ""}`}>{item.review_comment}</span>
    : <span className={styles.cellMuted}>—</span>;
}

const requestColumns = columnHelper.columns([
  columnHelper.display({ id: "name", header: HeaderLabel, cell: RequestNameCell, meta: columnMeta("AiApiPage.colKeyName") }),
  columnHelper.display({ id: "purpose", header: HeaderLabel, cell: RequestPurposeCell, meta: columnMeta("AiApiPage.colPurpose") }),
  columnHelper.display({ id: "status", header: HeaderLabel, cell: RequestStatusCell, meta: columnMeta("AiApiPage.colStatus") }),
  columnHelper.accessor("created_at", { header: HeaderLabel, cell: DateTimeCell, meta: columnMeta("AiApiPage.colAppliedAt") }),
  columnHelper.display({ id: "reviewed", header: HeaderLabel, cell: RequestReviewedCell, meta: columnMeta("AiApiPage.colReviewedAt") }),
  columnHelper.display({ id: "comment", header: HeaderLabel, cell: RequestCommentCell, meta: columnMeta("AiApiPage.colComment") }),
]);

const requestRowClassName = () => styles.tr;

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

  const { startDate: start, endDate: end } = useMemo(() => presetToRange(preset), [preset]);

  /* 統計與明細走 TanStack Query，以區間為 key：快速切換區間時較慢的舊區間回應只會進舊 key，
     不會蓋掉新區間；明細的「載入更多」是 infinite query，換區間後舊區間的下一頁也不會接到新清單後面。
     換區間時先沿用上一個區間的資料（不閃回載入動畫），背景刷新失敗保留畫面上的資料。 */
  const range = useMemo(() => ({ start_date: start, end_date: end }), [start, end]);
  const recordParams = useMemo(() => ({ ...range, limit: USAGE_RECORD_PAGE_SIZE }), [range]);
  const usageQuery = useMyAiUsageQuery(range);
  const recordsQuery = useMyAiUsageRecordsInfiniteQuery(recordParams);

  const usageData = usageQuery.data;
  /* 錯誤畫面在背景重試期間不閃回載入動畫，按重試才回到載入中（見 useLoadError）。
     換區間時沿用上一個區間的資料照常顯示；但這個區間失敗後，沒有資料時每次重抓 TanStack 都會把
     上一個區間的資料放回來當佔位資料，所以判斷錯誤時佔位資料不算資料，錯誤畫面才不會閃成舊區間的數字 */
  const usageLoad = useLoadError(usageQuery);
  const usageError = Boolean(usageLoad.error);
  const usageLoading = usageData === undefined && !usageError && (usageQuery.isPending || usageQuery.isFetching);
  const recordPages = recordsQuery.data?.pages;
  const recordsLoad = useLoadError(recordsQuery);
  const recordsError = Boolean(recordsLoad.error);
  const recordsLoading = recordPages === undefined && !recordsError && (recordsQuery.isPending || recordsQuery.isFetching);
  const records = useMemo(() => (recordPages ?? []).flatMap((page) => page?.data ?? []), [recordPages]);
  const recordsCount = recordPages?.length ? recordPages[recordPages.length - 1]?.count ?? 0 : 0;
  const loading = usageLoading || recordsLoading;
  const loadingMore = recordsQuery.isFetchingNextPage;

  /* 重試：兩份一起重抓（與原本相同），沒資料的回到載入中 */
  function retryLoad() {
    usageLoad.retry();
    recordsLoad.retry();
  }

  const hasMoreRecords = !recordsError && records.length > 0 && records.length < recordsCount;

  const loadMore = async () => {
    const result = await recordsQuery.fetchNextPage();
    /* 維持現有清單，不覆蓋成功資料；但要讓使用者知道沒載到 */
    if (result.isFetchNextPageError) toast.error(result.error?.message ?? t("AiApiPage.recordsLoadMoreError"));
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
        <ErrorState onRetry={retryLoad} />
      ) : (
        <>
          {/* ── 申請金鑰 API 用量總覽 ── */}
          <div className={styles.usagePanel} data-guide="ai-route-usage">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageTitle")}</h3>
              <p className={styles.usagePanelDesc}>{t("AiApiPage.usageDesc")}</p>
            </div>
            {usageError ? (
              <ErrorState onRetry={retryLoad} />
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
              <ErrorState onRetry={retryLoad} />
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
                    <button type="button" className={styles.btnSecondary} onClick={loadMore} disabled={loadingMore || recordsQuery.isPlaceholderData}>
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
  const [showInactive, setShowInactive] = useState(false);
  const [keyDetail, setKeyDetail] = useState(null);
  const keyDetailDialog = useDialogPresence(keyDetail);
  const closeKeyDetail = useCallback(() => setKeyDetail(null), []);
  const requestStatusRef = useRef(null);

  /* 我的申請與憑證走 TanStack Query。有待審申請時 30 秒背景刷新（審核結果不用整頁重新整理），
     沒有就不輪詢；背景刷新失敗保留畫面上的資料、不跳 toast（網路斷線時每 30 秒一則會轟炸）。
     重抓交錯時較早送出的舊清單會被取消，不會蓋掉新的，也不會讓已提醒過的審核結果再跳一次 */
  const requestsQuery = useMyAiRequestsQuery({
    refetchInterval: (query) => (hasPendingRequest(query.state.data) ? LIST_REFRESH_INTERVAL : false),
  });
  const requestsData = requestsQuery.data;
  const requests = requestsData?.data ?? EMPTY;
  const pendingCount = requests.filter((item) => item.status === "pending").length;
  const credentialsQuery = useMyAiCredentialsQuery({ refetchInterval: pendingCount > 0 ? LIST_REFRESH_INTERVAL : false });
  const credentialsData = credentialsQuery.data;
  const credentials = credentialsData?.data ?? EMPTY;
  const publicBaseUrl = credentialsData?.public_base_url ?? "";
  const aiCache = useAiApiCacheUpdater();
  /* 兩份都到齊才離開載入畫面；任一份首載失敗就顯示錯誤，按重試回到載入畫面。
     失敗看各自的 useLoadError：有待審申請時兩份都在輪詢，沒資料時 v5 每次重抓都清掉 error，
     看 isError／isFetching 的錯誤畫面會每 30 秒閃回載入中 */
  const credentialsLoad = useLoadError(credentialsQuery);
  const requestsLoad = useLoadError(requestsQuery);
  const noData = credentialsData === undefined || requestsData === undefined;
  const loadError = noData && Boolean(credentialsLoad.error || requestsLoad.error);
  const fetching = credentialsQuery.isFetching || requestsQuery.isFetching;
  const loading = noData && !loadError && (credentialsQuery.isPending || requestsQuery.isPending || fetching);

  // 待審的申請有了結果就提醒：學生常開著這頁等審核
  useEffect(() => {
    if (requestsData === undefined) return;
    const nextRequests = requestsData?.data ?? [];
    const previous = requestStatusRef.current;
    let approved = false;
    if (previous) {
      nextRequests.forEach((item) => {
        if (previous.get(item.id) !== "pending") return;
        if (item.status === "approved") {
          approved = true;
          toast.success(t("AiApiPage.requestApprovedToast", { name: item.api_key_name }));
        }
        if (item.status === "rejected") toast.error(t("AiApiPage.requestRejectedToast", { name: item.api_key_name }));
      });
    }
    requestStatusRef.current = new Map(nextRequests.map((item) => [item.id, item.status]));
    /* 核准會發新金鑰：金鑰的輪詢跟著待審數歸零而停，申請先抓回來時金鑰那份可能來不及重抓，這裡補抓一次 */
    if (approved) aiCache.refreshMyCredentials();
  }, [requestsData, toast, t, aiCache]);

  function retryLoad() {
    credentialsLoad.retry();
    requestsLoad.retry();
  }

  /* 操作後在背景重抓（不讓整張表閃成載入動畫），失敗時保留畫面上的資料並提醒 */
  function refreshAfterChange() {
    aiCache.invalidateMine({ throwOnError: true })
      .catch((e) => toast.error(e?.message ?? t("AiApiPage.loadError")));
  }

  const credentialStates = useMemo(
    () => new Map(credentials.map((item) => [item.id, getCredentialState(item, credentials)])),
    [credentials],
  );
  const activeCredentials = credentials.filter((item) => credentialStates.get(item.id) === "active");
  const inactiveCredentials = credentials.filter((item) => credentialStates.get(item.id) !== "active");
  // 失效的金鑰預設收起來：每重新產生一次就多一列同名的舊金鑰
  const visibleCredentials = showInactive ? [...activeCredentials, ...inactiveCredentials] : activeCredentials;
  const credentialsTable = useTable({
    features: baseFeatures,
    columns: credentialColumns,
    data: visibleCredentials,
    getRowId: (item) => String(item.id),
  });
  const requestsTable = useTable({
    features: baseFeatures,
    columns: requestColumns,
    data: requests,
    getRowId: (item) => String(item.id),
  });
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
      refreshAfterChange();
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
            <ErrorState onRetry={retryLoad} />
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
                  <DataTable
                    table={credentialsTable}
                    className={styles.table}
                    renderRow={(row) => (
                      <CredentialRow
                        key={row.id}
                        row={row}
                        item={row.original}
                        state={credentialStates.get(row.original.id)}
                        onRefresh={refreshAfterChange}
                        onDeleted={async (credentialId) => {
                          setKeyDetail((current) => current?.credential?.id === credentialId ? null : current);
                          await aiCache.removeMyCredential(credentialId);
                        }}
                        onShowDetails={(credential) => setKeyDetail({ credential, apiKey: null })}
                        onRotated={(credential) => setKeyDetail({ credential, apiKey: credential.api_key || null })}
                      />
                    )}
                  />
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
            <ErrorState onRetry={retryLoad} />
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
              <DataTable table={requestsTable} className={`${styles.table} ${styles.tableRecords}`} rowClassName={requestRowClassName} />
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
