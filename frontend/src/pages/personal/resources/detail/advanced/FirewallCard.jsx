/**
 * FirewallCard — 這台 VM 的防火牆
 * 上半是以這台 VM 為中心的迷你拓撲，下半是 Proxmox 原始規則表。
 * SkyLab: 開頭的受管規則上鎖（由連線對話框／拓撲頁管理），其餘可自行新增、停用、刪除。
 * 「新增規則」開的是共用的 ConnectionDialog（預設停在「自訂規則」分頁，也能切到「連線」做對外發布）。
 *
 * 迷你拓撲跟防火牆拓撲頁同一套操作：拖機器、從機器邊緣拉線（帶入兩端開同一個表單）、
 * 點連線開連線內容面板（疊在圖上）看開放內容或刪除。
 * 權限：沒有這台的管理權（學生的課堂機）整張圖只能看；另一端是別人的機器時那條線不能刪、
 * 也不能再往它拉線（連線要同時寫兩端的規則）。
 */

import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "../ResourceDetailPage.module.scss";
import MIcon from "../../../../../components/MIcon";
import DataTable, { DataTableCells } from "../../../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../../../components/DataTable/features";
import Switch from "../../../../../components/Switch/Switch";
import LoadingState from "../../../../../components/LoadingState/LoadingState";
import ConnectionDialog from "../../../../../components/ConnectionDialog/ConnectionDialog";
import useDialogPresence from "../../../../../hooks/useDialogPresence";
import { useToast } from "../../../../../hooks/useToast";
import { useConfirm } from "../../../../../components/ConfirmDialog/ConfirmProvider";
import { deleteConnection, deleteVmRule, updateVmRule } from "../../../../../services/firewall";
import ConnectionDetailPanel from "../../../../network/firewall/ConnectionDetailPanel";
import { GATEWAY_KEY, portLabel } from "../../../../network/firewall/utils/buildFlow";
import { canManageNode, toDialogNodes } from "../../../../../components/ConnectionDialog/topologyNodes";
import {
  useFirewallCacheUpdater,
  useVmFirewallOptionsQuery,
  useVmFirewallRulesQuery,
  useVmFirewallTopologyQuery,
} from "../../../../../queries/firewall";
import { useQueryErrorEffect } from "../../../../../queries/resourceDetail";
import MiniTopology from "./MiniTopology";

const EMPTY = Object.freeze([]);

const POLICY_LABEL_KEYS = {
  ACCEPT: "FirewallCard.policyAccept",
  DROP: "FirewallCard.policyDrop",
  REJECT: "FirewallCard.policyReject",
};

/** Proxmox 的策略／動作（ACCEPT、DROP…）換成使用者看得懂的字；不認得的值原樣顯示 */
function policyLabel(value, t) {
  const key = POLICY_LABEL_KEYS[String(value ?? "").toUpperCase()];
  return key ? t(key) : value;
}

/** Proxmox 的 port 寫法（80,443、1000:2000、1:65535）轉成一般寫法；涵蓋全部 port 等同「任意」 */
function formatPorts(dport, t) {
  if (!dport) return t("FirewallCard.any");
  const parts = String(dport).split(",").map((part) => {
    const [from, to] = part.split(":");
    if (to === undefined) return from;
    if (Number(from) <= 1 && Number(to) >= 65535) return null;
    return `${from}–${to}`;
  });
  return parts.includes(null) ? t("FirewallCard.any") : parts.join(", ");
}

/* ── 規則表的欄位（模組層級：cell／header 會被當成元件掛載，render 裡新建會整格重掛） ── */
function RuleHeader({ column }) {
  const { t } = useTranslation("personal");
  return t(column.columnDef.meta.labelKey);
}

function RuleDirectionCell({ row }) {
  const { t } = useTranslation("personal");
  const inbound = row.original.type === "in";
  return (
    <span className={`${styles.badge} ${inbound ? styles.badge_info : styles.badge_muted}`}>
      {inbound ? t("FirewallCard.directionIn") : t("FirewallCard.directionOut")}
    </span>
  );
}

function RuleProtocolCell({ row }) {
  const { t } = useTranslation("personal");
  const { proto } = row.original;
  return proto ? proto.toUpperCase() : t("FirewallCard.any");
}

function RulePortCell({ row }) {
  const { t } = useTranslation("personal");
  return formatPorts(row.original.dport, t);
}

function RuleSourceCell({ row }) {
  const { t } = useTranslation("personal");
  const rule = row.original;
  return (rule.type === "in" ? rule.source : rule.dest) ?? t("FirewallCard.any");
}

function RuleActionCell({ row }) {
  const { t } = useTranslation("personal");
  const rule = row.original;
  /* 停用的規則不生效，動作欄直接換成紅色「停用」，不再並排「允許」＋「已停用」兩顆徽章 */
  return rule.enable === 0 ? (
    <span className={`${styles.badge} ${styles.badge_danger}`}>{t("FirewallCard.ruleDisabled")}</span>
  ) : (
    <span className={`${styles.badge} ${rule.action === "ACCEPT" ? styles.badge_success : styles.badge_danger}`}>
      {policyLabel(rule.action, t)}
    </span>
  );
}

function RuleNoteCell({ row }) {
  const { t } = useTranslation("personal");
  const rule = row.original;
  return rule.is_managed ? (
    <span className={styles.hintLine} title={rule.comment ?? ""}>
      <MIcon name="lock" size={12} />
      {t("FirewallCard.managedByService")}
    </span>
  ) : (
    rule.comment ?? "—"
  );
}

/* 操作中狀態與處理函式是卡片層級的，經 RuleRow 的 rowState 傳進來 */
function RuleActionsCell({ row, rowState }) {
  const { t } = useTranslation("personal");
  const rule = row.original;
  const { busy, onToggle, onDelete } = rowState;
  /* 鎖定規則的備註欄已標「由連線管理」，操作欄留空不再重複 */
  if (rule.is_managed) return null;
  return (
    <>
      <Switch
        checked={rule.enable !== 0}
        onChange={onToggle}
        disabled={busy}
        ariaLabel={t("FirewallCard.enableSwitch")}
        title={t("FirewallCard.enableSwitch")}
      />
      <button
        type="button"
        className={`${styles.rpIconBtn} ${styles.rpIconBtnDanger}`}
        disabled={busy}
        title={t("FirewallCard.deleteRule")}
        onClick={onDelete}
      >
        <MIcon name="delete" size={16} />
      </button>
    </>
  );
}

const columnHelper = createColumnHelper();
const RULE_COLUMNS = columnHelper.columns([
  /* rule.pos 是 Proxmox 的 0 起算位置，顯示給人看從 1 開始 */
  columnHelper.accessor("pos", {
    header: "#",
    cell: (info) => info.getValue() + 1,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.display({
    id: "direction",
    header: RuleHeader,
    cell: RuleDirectionCell,
    meta: { labelKey: "FirewallCard.direction", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "protocol",
    header: RuleHeader,
    cell: RuleProtocolCell,
    meta: { labelKey: "FirewallCard.protocol", thClassName: styles.th, tdClassName: `${styles.td} ${styles.nowrapCell}` },
  }),
  columnHelper.display({
    id: "port",
    header: RuleHeader,
    cell: RulePortCell,
    meta: { labelKey: "FirewallCard.port", thClassName: styles.th, tdClassName: `${styles.td} ${styles.nowrapCell}` },
  }),
  columnHelper.display({
    id: "source",
    header: RuleHeader,
    cell: RuleSourceCell,
    meta: { labelKey: "FirewallCard.sourceCol", thClassName: styles.th, tdClassName: `${styles.td} ${styles.monoText}` },
  }),
  columnHelper.display({
    id: "action",
    header: RuleHeader,
    cell: RuleActionCell,
    meta: { labelKey: "FirewallCard.action", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "note",
    header: RuleHeader,
    cell: RuleNoteCell,
    meta: { labelKey: "FirewallCard.noteCol", thClassName: styles.th, tdClassName: `${styles.td} ${styles.detailCell}` },
  }),
]);
/* 全部規則都是服務管理的鎖定規則時操作欄整欄不顯示：兩份欄位定義都放模組層級，依情況挑一份 */
const RULE_COLUMNS_WITH_ACTIONS = columnHelper.columns([
  ...RULE_COLUMNS,
  columnHelper.display({
    id: "actions",
    header: RuleHeader,
    cell: RuleActionsCell,
    meta: { labelKey: "FirewallCard.actionsCol", thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdActions}` },
  }),
]);

const getRuleId = (rule) => String(rule.pos);

/* 與 buildFlow 給邊的 id 同一個規則（只由兩端決定）：拓撲重抓後用它找回選取中的那條線 */
const edgeIdOf = (edge) => `edge-${edge.source_vmid ?? GATEWAY_KEY}-${edge.target_vmid ?? GATEWAY_KEY}`;

/* busy 與操作欄顯示與否是卡片層級的、規則資料沒變也會變：經列元件的 props 進 rowState，
   儲存格才會跟著重繪（showActions 本身 cell 用不到，是讓操作欄出現／消失時整列重畫） */
function RuleRow({ row, rule, busy, showActions, onToggle, onDelete }) {
  return (
    <tr className={`${styles.tr} ${rule.is_managed ? styles.lockedRow : ""} ${rule.enable === 0 ? styles.ruleOffRow : ""}`}>
      <DataTableCells
        row={row}
        rowState={{ busy, showActions, onToggle: () => onToggle(rule), onDelete: () => onDelete(rule) }}
      />
    </tr>
  );
}

/**
 * @param {string[]} publicUrls 這台機器的對外網址（來自 ResourcePublic.public_urls），唯讀顯示；
 *   要改網址從「新增規則 › 連線」或拓撲頁做。
 * @param {() => void} [onChanged] 對話框建了連線後通知上層重載資源（網址才會更新）。
 */
export default function FirewallCard({ vmid, canManage, publicUrls = [], onChanged }) {
  const { t } = useTranslation("personal");
  const { t: tn } = useTranslation("network");
  const toast = useToast();
  const confirm = useConfirm();
  const firewallCache = useFirewallCacheUpdater();
  const [busy, setBusy] = useState(false);
  /* 連線表單：{ mode: "rule" }＝「新增規則」鈕（鎖定這台），{ mode: "connect", source, target }＝圖上拉線帶入兩端 */
  const [dialog, setDialog] = useState(null);
  const dialogPresence = useDialogPresence(dialog);
  /* 圖上選取中的連線（存 id：拓撲重抓後用它找回同一條，線不見了面板就關） */
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);
  /* 刪除連線進行中：確認後又被按第二次不會送出兩筆 */
  const deletingEdgeRef = useRef(false);

  /* 迷你拓撲與防火牆選項查不到就不顯示；規則表查不到時提示，已有清單沿用、從沒載到就是空的 */
  const topologyQuery = useVmFirewallTopologyQuery(vmid);
  const rulesQuery = useVmFirewallRulesQuery(vmid);
  const optionsQuery = useVmFirewallOptionsQuery(vmid);
  useQueryErrorEffect(rulesQuery, (err) => {
    toast.error(err?.message ?? t("Error.generic", { ns: "common" }));
  });
  const topology = topologyQuery.isError ? null : topologyQuery.data ?? null;
  const rules = rulesQuery.data ?? EMPTY;
  const options = optionsQuery.isError ? null : optionsQuery.data ?? null;
  /* 三個都第一次載完（成功或失敗）才畫內容；之後的重抓沿用舊資料，不再閃轉圈圈 */
  const firstLoading = (q) => q.data === undefined && !q.isError && (q.isPending || q.isFetching);
  const loading = firstLoading(topologyQuery) || firstLoading(rulesQuery) || firstLoading(optionsQuery);
  /* 規則、選項、迷你拓撲一起重抓（在途的舊請求會被取消） */
  const load = () => firewallCache.invalidateVm(vmid);
  /* 停用／刪除是用規則的位置（pos）指定的：清單重抓中畫面上的位置可能已經位移（例如剛建了連線、
     受管規則插在前面），等新清單回來才能操作，不然會改到或刪到別條規則 */
  const rulesRefreshing = rulesQuery.isFetching;

  const thisVmName = topology?.nodes?.find((n) => n.vmid === vmid)?.name;

  /* ── 迷你拓撲上的連線 ── */
  const topologyNodes = topology?.nodes ?? EMPTY;
  const selectedEdge = selectedEdgeId
    ? (topology?.edges ?? EMPTY).find((edge) => edgeIdOf(edge) === selectedEdgeId) ?? null
    : null;
  /* useDialogPresence 依值是否換了物件來判斷開關，這裡要給穩定的物件，不然每次 render 都被當成新開 */
  const selectedEdgeView = useMemo(
    () => (selectedEdge ? { id: selectedEdgeId, edge: selectedEdge } : null),
    [selectedEdge, selectedEdgeId],
  );
  /* 關閉時先播 0.22s 滑出動畫再卸載，時長與連線內容面板的 panelOut 一致 */
  const edgePanel = useDialogPresence(selectedEdgeView, 220);

  const nameOf = (edgeVmid) => {
    if (edgeVmid === null || edgeVmid === undefined) return tn("GatewayNode.internet");
    return topologyNodes.find((n) => n.vmid === edgeVmid)?.name ?? `vmid:${edgeVmid}`;
  };
  /* 連線另一端（不是這台）裡第一台不能管的機器：有的話這條線不能刪，也不能再往它拉線 */
  const unmanageablePeerOf = (edge) => [edge.source_vmid, edge.target_vmid]
    .filter((id) => id !== null && id !== undefined && id !== vmid)
    .map((id) => topologyNodes.find((n) => n.vmid === id))
    .find((node) => node && !canManageNode(node));

  /* 這條線為什麼不能刪（能刪回空字串）：講清楚原因，不讓人以為是按鈕不見了 */
  function edgeLockedHint(edge) {
    if (!edge) return "";
    if (!canManage) return t("FirewallCard.topologyReadOnlyHint");
    if (edge.topology_managed) return t("FirewallCard.edgeLockedManaged");
    const peer = unmanageablePeerOf(edge);
    return peer ? t("FirewallCard.edgeLockedPeer", { name: peer.name }) : "";
  }

  /* 拉線被擋：這台不能改，或另一端是不能管的機器 */
  function handleConnectBlocked(node) {
    toast.error(!canManage || node?.vmid === vmid
      ? t("FirewallCard.topologyReadOnlyHint")
      : t("FirewallCard.blockedPeer", { name: node?.name ?? "" }));
  }

  /* 連線增刪後：這台的規則／選項／迷你拓撲、另一端機器的規則、整張拓撲都可能變（受管規則插在前面，
     後面的位置跟著位移），全部標成過期重抓；對外網址也可能變，通知上層重載資源 */
  function refreshAfterConnectionChange() {
    firewallCache.invalidate();
    onChanged?.();
  }

  async function handleDeleteEdge(edge) {
    if (!edge || deletingEdgeRef.current) return;
    const ports = edge.ports?.length > 0 ? portLabel(edge.ports) : "";
    const ok = await confirm({
      title: tn("FirewallPage.deleteConnectionTitle"),
      message: ports
        ? `${tn("FirewallPage.deleteConnectionConfirm")}\n${nameOf(edge.source_vmid)} → ${nameOf(edge.target_vmid)}：${ports}`
        : `${tn("FirewallPage.deleteConnectionConfirm")}\n${nameOf(edge.source_vmid)} → ${nameOf(edge.target_vmid)}`,
      confirmText: tn("FirewallPage.delete"),
      cancelText: tn("FirewallPage.cancel"),
      danger: true,
    });
    if (!ok) return;
    deletingEdgeRef.current = true;
    try {
      await deleteConnection({ source_vmid: edge.source_vmid, target_vmid: edge.target_vmid, ports: null });
      toast.success(t("FirewallCard.connectionDeleted"));
      setSelectedEdgeId(null);
      refreshAfterConnectionChange();
    } catch (err) {
      toast.error(err?.message ?? tn("FirewallPage.deleteFailed"));
    } finally {
      deletingEdgeRef.current = false;
    }
  }

  /* 對話框可能建了自訂規則，也可能建了連線（含對外發布），兩種都重載規則表與迷你拓撲 */
  function handleDialogDone(result) {
    toast.success(result?.kind === "rule" ? t("FirewallCard.ruleAdded") : t("FirewallCard.connectionAdded"));
    setDialog(null);
    if (result?.kind === "rule") load();
    else refreshAfterConnectionChange();
  }

  async function handleToggle(rule) {
    if (rulesRefreshing) return;
    setBusy(true);
    try {
      await updateVmRule(vmid, rule.pos, { enable: rule.enable === 0 ? 1 : 0 });
      await load();
    } catch (err) {
      toast.error(err?.message ?? t("FirewallCard.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(rule) {
    const ok = await confirm({
      title: t("FirewallCard.deleteRuleTitle"),
      message: t("FirewallCard.deleteRuleMessage", { pos: rule.pos + 1 }),
      danger: true,
    });
    if (!ok) return;
    /* 確認視窗開著時清單可能已重抓，同一個位置已不是同一條：按下確認時再對一次 */
    const latest = firewallCache.getVmRules(vmid) ?? [];
    const current = latest.find((item) => item.pos === rule.pos);
    if (!current || JSON.stringify(current) !== JSON.stringify(rule)) {
      toast.error(t("FirewallCard.rulesChanged"));
      return;
    }
    setBusy(true);
    try {
      await deleteVmRule(vmid, rule.pos);
      toast.success(t("FirewallCard.ruleDeleted"));
      await load();
    } catch (err) {
      toast.error(err?.message ?? t("FirewallCard.deleteFailed"));
    } finally {
      setBusy(false);
    }
  }

  /* 全部規則都是服務管理的鎖定規則時，操作欄整欄是空的，不顯示 */
  const showActionsCol = canManage && rules.some((rule) => !rule.is_managed);
  const ruleTable = useTable({
    features: baseFeatures,
    columns: showActionsCol ? RULE_COLUMNS_WITH_ACTIONS : RULE_COLUMNS,
    data: rules,
    getRowId: getRuleId,
  });

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="security" size={18} />
            {t("FirewallCard.title")}
          </h2>
        </div>
        <div className={styles.headerActions}>
          {options && (
            <>
              <span className={`${styles.badge} ${options.enable ? styles.badge_success : styles.badge_muted}`}>
                {options.enable ? t("FirewallCard.enabled") : t("FirewallCard.disabled")}
              </span>
              <span className={`${styles.badge} ${styles.badge_muted}`}>
                {t("FirewallCard.policySummary", {
                  in: policyLabel(options.policy_in, t),
                  out: policyLabel(options.policy_out, t),
                })}
              </span>
            </>
          )}
          {canManage && (
            <button type="button" className={styles.btnSecondary} onClick={() => setDialog({ mode: "rule" })}>
              <MIcon name="add" size={16} />
              {t("FirewallCard.addRule")}
            </button>
          )}
        </div>
      </div>
      <div className={styles.cardBody}>
        {publicUrls.length > 0 && (
          <div className={styles.publicUrlBlock}>
            <span className={styles.publicUrlLabel}>
              <MIcon name="language" size={14} />
              {t("FirewallCard.publicUrls")}
            </span>
            <div className={styles.linkRow}>
              {publicUrls.map((url) => (
                <a key={url} className={styles.linkBtn} href={url} target="_blank" rel="noreferrer" title={url}>
                  {url.replace(/^https?:\/\//, "")}
                  <MIcon name="open_in_new" size={13} />
                </a>
              ))}
            </div>
          </div>
        )}
        {loading ? (
          <LoadingState text={t("FirewallCard.loading")} />
        ) : (
          <>
            {/* 拓撲圖與圖例包成一組、組內間距較小：圖例緊貼著圖，不會被讀成下方規則表的說明 */}
            {topology && (
              <div className={styles.topologyBlock}>
                <MiniTopology
                  topology={topology}
                  vmid={vmid}
                  canManage={canManage}
                  selectedEdgeId={selectedEdgeId}
                  onSelectEdge={(_edge, id) => setSelectedEdgeId((prev) => (prev === id ? null : id))}
                  onConnectRequest={(preset) => setDialog({ mode: "connect", ...preset })}
                  onBlocked={handleConnectBlocked}
                >
                  {/* 連線內容面板疊在圖上（跟拓撲頁同一個元件）；不能刪的時候在刪除鈕的位置講原因 */}
                  {edgePanel.item && (
                    <ConnectionDetailPanel
                      edge={edgePanel.item.edge}
                      resolveName={nameOf}
                      closing={edgePanel.closing}
                      onClose={() => setSelectedEdgeId(null)}
                      onDelete={edgeLockedHint(edgePanel.item.edge) ? undefined : handleDeleteEdge}
                      lockedHint={edgeLockedHint(edgePanel.item.edge)}
                    />
                  )}
                </MiniTopology>
                <div className={styles.flowFooter}>
                  <div className={styles.flowLegend}>
                    <span><i className={`${styles.legendDot} ${styles.legendIn}`} />{t("FirewallCard.legendInbound")}</span>
                    <span><i className={`${styles.legendDot} ${styles.legendOut}`} />{t("FirewallCard.legendOutbound")}</span>
                    <span><i className={`${styles.legendDot} ${styles.legendPeer}`} />{t("FirewallCard.legendPeer")}</span>
                  </div>
                  {/* 這張圖能做什麼：有管理權講怎麼拉線、刪線；沒有就講只能看 */}
                  <p className={styles.flowHint}>
                    {!canManage && <MIcon name="lock" size={13} />}
                    {canManage ? t("FirewallCard.topologyHint") : t("FirewallCard.topologyReadOnlyHint")}
                  </p>
                </div>
              </div>
            )}

            {rules.length === 0 ? (
              <p className={styles.mutedText}>{t("FirewallCard.noRules")}</p>
            ) : (
              <div className={styles.tableScroll}>
                <DataTable
                  table={ruleTable}
                  className={styles.table}
                  renderRow={(row) => (
                    <RuleRow
                      key={row.id}
                      row={row}
                      rule={row.original}
                      busy={busy || rulesRefreshing}
                      showActions={showActionsCol}
                      onToggle={handleToggle}
                      onDelete={handleDelete}
                    />
                  )}
                />
              </div>
            )}
          </>
        )}
      </div>

      {/* 共用對話框自己 portal 到 body，不會被卡片的 overflow:hidden 困住 */}
      {dialogPresence.item?.mode === "rule" && (
        <ConnectionDialog
          fixedVmid={vmid}
          fixedName={thisVmName}
          initialTab="rule"
          closing={dialogPresence.closing}
          onClose={() => setDialog(null)}
          onDone={handleDialogDone}
          onChanged={load}
        />
      )}
      {/* 圖上拉線：兩端照拉的方向帶入（不鎖定這台，否則「別台 → 這台」會被翻成「這台 → 別台」）；
          可選的機器只有圖上能管的那幾台 */}
      {dialogPresence.item?.mode === "connect" && (
        <ConnectionDialog
          key={`${dialogPresence.item.source}->${dialogPresence.item.target}`}
          nodes={toDialogNodes(topologyNodes)}
          initialSource={dialogPresence.item.source}
          initialTarget={dialogPresence.item.target}
          closing={dialogPresence.closing}
          onClose={() => setDialog(null)}
          onDone={handleDialogDone}
          onChanged={refreshAfterConnectionChange}
        />
      )}
    </div>
  );
}
