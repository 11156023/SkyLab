/**
 * RulesPanel
 * 拓撲頁點選 VM 後滑入的防火牆工作面板：
 * 顯示防火牆選項與規則清單，自訂規則可就地停用／刪除，
 * 「新增規則」開共用的 ConnectionDialog（鎖定這台 VM）。
 * SkyLab: 受管規則上鎖，只能由連線／拓撲管理（與資源詳情的 FirewallCard 同規則）。
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  deleteVmRule,
  getVmRules,
  getVmOptions,
  updateVmRule,
} from "../../services/firewall";
import styles from "./RulesPanel.module.scss";
import MIcon from "../MIcon";
import LoadingState from "../LoadingState/LoadingState";
import ConnectionDialog from "../ConnectionDialog/ConnectionDialog";
import useDialogPresence from "../../hooks/useDialogPresence";
import { useToast } from "../../hooks/useToast";
import { useConfirm } from "../ConfirmDialog/ConfirmProvider";
import { useAuth } from "../../contexts/AuthContext";

function Badge({ label, variant }) {
  return <span className={`${styles.badge} ${styles[`badge_${variant}`]}`}>{label}</span>;
}

export default function RulesPanel({ node, onClose, onChanged, closing = false }) {
  const { t } = useTranslation("components");
  const toast = useToast();
  const confirm = useConfirm();
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_superuser || user?.role === "admin");
  const [rules,   setRules]   = useState([]);
  const [options, setOptions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [busy,    setBusy]    = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const addPresence = useDialogPresence(showAdd);

  const load = useCallback(async (silent = false) => {
    if (!node?.vmid) return;
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const [r, o] = await Promise.all([getVmRules(node.vmid), getVmOptions(node.vmid)]);
      setRules(r ?? []);
      setOptions(o);
    } catch (err) {
      if (!silent) setError(err?.message ?? t("RulesPanel.loadFailed"));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [node?.vmid, t]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleToggle(rule) {
    setBusy(true);
    try {
      await updateVmRule(node.vmid, rule.pos, { enable: rule.enable === 0 ? 1 : 0 });
      await load(true);
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
    setBusy(true);
    try {
      await deleteVmRule(node.vmid, rule.pos);
      toast.success(t("RulesPanel.ruleDeleted"));
      await load(true);
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
    load(true);
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
          <button
            type="button"
            className={styles.addBtn}
            onClick={() => setShowAdd(true)}
            disabled={busy}
          >
            <MIcon name="add" size={14} />
            {t("RulesPanel.addRule")}
          </button>
          <button type="button" className={styles.closeBtn} onClick={onClose} aria-label={t("RulesPanel.closeAriaLabel")}>
            <MIcon name="close" size={20} />
          </button>
        </div>
      </div>

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
                    <Badge label={rule.action ?? "—"} variant={rule.action === "ACCEPT" ? "success" : "danger"} />
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
                      {rule.is_managed ? (
                        <span className={styles.lockedTag} title={t("RulesPanel.managedHint")}>
                          <MIcon name="lock" size={12} />
                        </span>
                      ) : (
                        <>
                          <button
                            type="button"
                            className={styles.ruleBtn}
                            disabled={busy}
                            title={rule.enable === 0 ? t("RulesPanel.enableRule") : t("RulesPanel.disableRule")}
                            onClick={() => handleToggle(rule)}
                          >
                            <MIcon name={rule.enable === 0 ? "toggle_off" : "toggle_on"} size={16} />
                          </button>
                          <button
                            type="button"
                            className={`${styles.ruleBtn} ${styles.ruleBtnDanger}`}
                            disabled={busy}
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

          {/* 單機深度資訊（迷你拓撲、對外服務）在資源詳情的進階設定 */}
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
