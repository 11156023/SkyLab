/**
 * RulesPanel
 * 拓撲頁點選 VM 後滑入的防火牆工作面板：
 * 顯示防火牆選項與規則清單，自訂規則可就地停用／刪除，
 * 「新增規則」開共用的 ConnectionDialog（鎖定這台 VM）。
 * SkyLab: 受管規則上鎖，只能由連線／拓撲管理（與資源詳情的 FirewallCard 同規則）。
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { deleteVmRule, updateVmRule } from "../../services/firewall";
import {
  useFirewallCacheUpdater,
  useVmFirewallOptionsQuery,
  useVmFirewallRulesQuery,
} from "../../queries/firewall";
import styles from "./RulesPanel.module.scss";
import MIcon from "../MIcon";
import Switch from "../Switch/Switch";
import LoadingState from "../LoadingState/LoadingState";
import ConnectionDialog from "../ConnectionDialog/ConnectionDialog";
import useDialogPresence from "../../hooks/useDialogPresence";
import { useToast } from "../../hooks/useToast";
import { useConfirm } from "../ConfirmDialog/ConfirmProvider";
import { useAuth } from "../../contexts/AuthContext";
import { isAdminUser } from "../../utils/roles";

const EMPTY = Object.freeze([]);

function Badge({ label, variant }) {
  return <span className={`${styles.badge} ${styles[`badge_${variant}`]}`}>{label}</span>;
}

export default function RulesPanel({ node, onClose, onChanged, closing = false, canManage = true }) {
  const { t } = useTranslation("components");
  const toast = useToast();
  const confirm = useConfirm();
  const { user } = useAuth();
  const isAdmin = isAdminUser(user);
  const [busy,    setBusy]    = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const addPresence = useDialogPresence(showAdd);
  /* 規則與選項走 TanStack Query，以 vmid 為 key。面板在切換 VM 時不會重掛；前一台 VM 較慢回來的回應
     只會進那台的 key，不會蓋掉目前這台（否則清單顯示 A 的規則、刪除／停用卻打到 B 的同一個 pos）。
     刻意不沿用上一台的資料當佔位：換機器時先顯示載入中。不輪詢，寫入後才重抓。 */
  const vmid = node?.vmid ?? null;
  const rulesQuery = useVmFirewallRulesQuery(vmid);
  const optionsQuery = useVmFirewallOptionsQuery(vmid);
  const firewallCache = useFirewallCacheUpdater();
  const rules = rulesQuery.data ?? EMPTY;
  const options = optionsQuery.data ?? null;
  /* 兩份都到齊才顯示；首載失敗顯示錯誤，寫入後的背景重抓失敗保留畫面上的資料（與原本相同） */
  const noData = rulesQuery.data === undefined || optionsQuery.data === undefined;
  const fetching = rulesQuery.isFetching || optionsQuery.isFetching;
  const loading = noData && (rulesQuery.isPending || optionsQuery.isPending || fetching);
  const failed = noData && (rulesQuery.isError || optionsQuery.isError) && !fetching;
  const error = failed
    ? ((rulesQuery.error ?? optionsQuery.error)?.message ?? t("Error.generic", { ns: "common" }))
    : "";

  /* 寫入後重抓這台的規則、選項（與資源詳情的迷你拓撲）；重抓失敗不打斷操作 */
  const reload = () => firewallCache.invalidateVm(node.vmid);
  /* 停用／刪除是用規則的位置（pos）指定的：清單重抓中（例如打開時先顯示快取、連線變更後）畫面上的位置
     可能已經不是最新的，等新清單回來才能操作，不然會改到或刪到別條規則 */
  const rulesRefreshing = rulesQuery.isFetching;

  async function handleToggle(rule) {
    if (rulesRefreshing) return;
    setBusy(true);
    try {
      await updateVmRule(node.vmid, rule.pos, { enable: rule.enable === 0 ? 1 : 0 });
      await reload();
    } catch (err) {
      toast.error(err?.message ?? t("RulesPanel.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(rule) {
    const ok = await confirm({
      title: t("RulesPanel.deleteRuleTitle"),
      message: t("RulesPanel.deleteRuleMessage", { pos: rule.pos }),
      danger: true,
    });
    if (!ok) return;
    /* 確認視窗開著時清單可能已重抓（切回視窗、別人改了規則），同一個位置已不是同一條：按下確認時再對一次 */
    const latest = firewallCache.getVmRules(node.vmid) ?? [];
    const current = latest.find((item) => item.pos === rule.pos);
    if (!current || JSON.stringify(current) !== JSON.stringify(rule)) {
      toast.error(t("RulesPanel.rulesChanged"));
      return;
    }
    setBusy(true);
    try {
      await deleteVmRule(node.vmid, rule.pos);
      toast.success(t("RulesPanel.ruleDeleted"));
      await reload();
    } catch (err) {
      toast.error(err?.message ?? t("RulesPanel.deleteFailed"));
    } finally {
      setBusy(false);
    }
  }

  /* 自訂規則不影響拓撲邊，只重載面板；建了連線／發布才要求拓撲刷新 */
  function handleDialogDone(result) {
    toast.success(result?.kind === "rule" ? t("RulesPanel.ruleAdded") : t("RulesPanel.connectionAdded"));
    setShowAdd(false);
    reload();
    if (result?.kind !== "rule") onChanged?.();
  }

  if (!node) return null;

  return (
    <div className={`${styles.panel} ${closing ? styles.panelOut : ""}`}>
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <MIcon name="security" size={18} />
          <span className={styles.vmName}>{node.name}</span>
        </div>
        <div className={styles.headerActions}>
          {canManage && (
            <button
              type="button"
              className={styles.addBtn}
              onClick={() => setShowAdd(true)}
              disabled={busy}
            >
              <MIcon name="add" size={14} />
              {t("RulesPanel.addRule")}
            </button>
          )}
          <button type="button" className={styles.closeBtn} onClick={onClose} aria-label={t("RulesPanel.closeAriaLabel")}>
            <MIcon name="close" size={20} />
          </button>
        </div>
      </div>

      {/* 學生的課堂機：規則看得到，但只有班級老師能改 */}
      {!canManage && (
        <p className={styles.readOnlyHint}>
          <MIcon name="lock" size={14} />
          {t("RulesPanel.readOnlyHint")}
        </p>
      )}

      {loading && <LoadingState text={t("RulesPanel.loading")} />}
      {error   && <p className={styles.errorMsg}>{error}</p>}

      {!loading && !error && (
        <>
          {/* Options */}
          {options && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>{t("RulesPanel.firewallSettings")}</h3>
              <div className={styles.optionRow}>
                <span className={styles.optionLabel}>{t("RulesPanel.status")}</span>
                <Badge
                  label={options.enable ? t("RulesPanel.enabled") : t("RulesPanel.disabled")}
                  variant={options.enable ? "success" : "muted"}
                />
              </div>
              <div className={styles.optionRow}>
                <span className={styles.optionLabel}>{t("RulesPanel.defaultInbound")}</span>
                <Badge label={options.policy_in  ?? "—"} variant="neutral" />
              </div>
              <div className={styles.optionRow}>
                <span className={styles.optionLabel}>{t("RulesPanel.defaultOutbound")}</span>
                <Badge label={options.policy_out ?? "—"} variant="neutral" />
              </div>
            </div>
          )}

          {/* Rules */}
          <div className={styles.section}>
            <h3 className={styles.sectionTitle}>{t("RulesPanel.ruleListTitle", { count: rules.length })}</h3>
            {rules.length === 0 ? (
              <p className={styles.hint}>{t("RulesPanel.noRules")}</p>
            ) : (
              <div className={styles.ruleList}>
                {rules.map((rule) => (
                  <div
                    key={rule.pos}
                    className={`${styles.ruleRow} ${rule.enable === 0 ? styles.disabled : ""}`}
                  >
                    <span className={styles.rulePos}>#{rule.pos}</span>
                    <Badge label={rule.type?.toUpperCase() ?? "—"} variant={rule.type === "in" ? "blue" : "orange"} />
                    {rule.enable === 0
                      ? <Badge label={t("RulesPanel.ruleDisabled")} variant="danger" />
                      : <Badge label={rule.action ?? "—"} variant={rule.action === "ACCEPT" ? "success" : "danger"} />}
                    <div className={styles.ruleDetail}>
                      {rule.source && <span>{rule.source}</span>}
                      {rule.source && rule.dest && <MIcon name="arrow_forward" size={12} />}
                      {rule.dest   && <span>{rule.dest}</span>}
                      {rule.proto  && <span className={styles.ruleProto}>{rule.proto}{rule.dport ? `:${rule.dport}` : ""}</span>}
                      {rule.comment && (
                        <span className={styles.ruleComment}>{rule.comment}</span>
                      )}
                    </div>
                    <div className={styles.ruleActions}>
                      {!canManage ? null : rule.is_managed ? (
                        <span className={styles.lockedTag} title={t("RulesPanel.managedHint")}>
                          <MIcon name="lock" size={12} />
                        </span>
                      ) : (
                        <>
                          <Switch
                            checked={rule.enable !== 0}
                            onChange={() => handleToggle(rule)}
                            disabled={busy || rulesRefreshing}
                            ariaLabel={t("RulesPanel.enableSwitch")}
                            title={t("RulesPanel.enableSwitch")}
                          />
                          <button
                            type="button"
                            className={`${styles.ruleBtn} ${styles.ruleBtnDanger}`}
                            disabled={busy || rulesRefreshing}
                            title={t("RulesPanel.deleteRule")}
                            onClick={() => handleDelete(rule)}
                          >
                            <MIcon name="delete" size={14} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 單機深度資訊（迷你拓撲）在資源詳情的進階設定 */}
          <div className={styles.section}>
            <Link
              to={isAdmin ? `/resource-mgmt/${node.vmid}` : `/my-resources/${node.vmid}`}
              className={styles.detailLink}
            >
              {t("RulesPanel.detailLink")}
              <MIcon name="open_in_new" size={13} />
            </Link>
          </div>
        </>
      )}

      {/* 共用對話框自己 portal 到 body */}
      {addPresence.open && (
        <ConnectionDialog
          fixedVmid={node.vmid}
          fixedName={node.name}
          initialTab="rule"
          closing={addPresence.closing}
          onClose={() => setShowAdd(false)}
          onDone={handleDialogDone}
          onChanged={onChanged}
        />
      )}
    </div>
  );
}
