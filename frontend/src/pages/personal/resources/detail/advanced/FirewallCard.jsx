/**
 * FirewallCard — 這台 VM 的防火牆
 * 上半是以這台 VM 為中心的迷你拓撲，下半是 Proxmox 原始規則表。
 * SkyLab: 開頭的受管規則上鎖（由連線對話框／拓撲頁管理），其餘可自行新增、停用、刪除。
 * 「新增規則」開的是共用的 ConnectionDialog（預設停在「自訂規則」分頁，也能切到「連線」做對外發布）。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "../ResourceDetailPage.module.scss";
import MIcon from "../../../../../components/MIcon";
import LoadingState from "../../../../../components/LoadingState/LoadingState";
import ConnectionDialog from "../../../../../components/ConnectionDialog/ConnectionDialog";
import useDialogPresence from "../../../../../hooks/useDialogPresence";
import { useToast } from "../../../../../hooks/useToast";
import { useConfirm } from "../../../../../components/ConfirmDialog/ConfirmProvider";
import {
  deleteVmRule,
  getVmOptions,
  getVmRules,
  getVmTopology,
  updateVmRule,
} from "../../../../../services/firewall";
import MiniTopology from "./MiniTopology";

export default function FirewallCard({ vmid, canManage }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const [topology, setTopology] = useState(null);
  const [rules, setRules] = useState([]);
  const [options, setOptions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const addPresence = useDialogPresence(showAdd);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [topo, ruleList, opts] = await Promise.all([
        getVmTopology(vmid).catch(() => null),
        getVmRules(vmid),
        getVmOptions(vmid).catch(() => null),
      ]);
      setTopology(topo);
      setRules(ruleList ?? []);
      setOptions(opts);
    } catch (err) {
      toast.error(err?.message ?? t("FirewallCard.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [vmid, toast, t]);

  useEffect(() => {
    load();
  }, [load]);

  const thisVmName = topology?.nodes?.find((n) => n.vmid === vmid)?.name;

  /* 對話框可能建了自訂規則，也可能建了連線（含對外發布），兩種都重載規則表與迷你拓撲 */
  function handleDialogDone(result) {
    toast.success(result?.kind === "rule" ? t("FirewallCard.ruleAdded") : t("FirewallCard.connectionAdded"));
    setShowAdd(false);
    load();
  }

  async function handleToggle(rule) {
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
      message: t("FirewallCard.deleteRuleMessage", { pos: rule.pos }),
      danger: true,
    });
    if (!ok) return;
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

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="security" size={18} />
            {t("FirewallCard.title")}
          </h2>
          <p className={styles.cardDesc}>{t("FirewallCard.desc")}</p>
        </div>
        <div className={styles.headerActions}>
          {options && (
            <>
              <span className={`${styles.badge} ${options.enable ? styles.badge_ok : styles.badge_muted}`}>
                {options.enable ? t("FirewallCard.enabled") : t("FirewallCard.disabled")}
              </span>
              <span className={`${styles.badge} ${styles.badge_muted}`} title={t("FirewallCard.policyHint")}>
                IN {options.policy_in} · OUT {options.policy_out}
              </span>
            </>
          )}
          {canManage && (
            <button type="button" className={styles.btnSecondary} onClick={() => setShowAdd(true)}>
              <MIcon name="add" size={16} />
              {t("FirewallCard.addRule")}
            </button>
          )}
        </div>
      </div>
      <div className={styles.cardBody}>
        {loading ? (
          <LoadingState text={t("FirewallCard.loading")} />
        ) : (
          <>
            {topology && (
              <>
                <MiniTopology topology={topology} />
                <div className={styles.flowLegend}>
                  <span><i className={`${styles.legendDot} ${styles.legendIn}`} />{t("FirewallCard.legendInbound")}</span>
                  <span><i className={`${styles.legendDot} ${styles.legendOut}`} />{t("FirewallCard.legendOutbound")}</span>
                  <span><i className={`${styles.legendDot} ${styles.legendPeer}`} />{t("FirewallCard.legendPeer")}</span>
                </div>
              </>
            )}

            {rules.length === 0 ? (
              <p className={styles.mutedText}>{t("FirewallCard.noRules")}</p>
            ) : (
              <div className={styles.tableScroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.th}>#</th>
                      <th className={styles.th}>{t("FirewallCard.direction")}</th>
                      <th className={styles.th}>{t("FirewallCard.protocol")}</th>
                      <th className={styles.th}>{t("FirewallCard.port")}</th>
                      <th className={styles.th}>{t("FirewallCard.sourceCol")}</th>
                      <th className={styles.th}>{t("FirewallCard.action")}</th>
                      <th className={styles.th}>{t("FirewallCard.noteCol")}</th>
                      {canManage && <th className={`${styles.th} ${styles.thRight}`}>{t("FirewallCard.actionsCol")}</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((rule) => (
                      <tr key={rule.pos} className={`${styles.tr} ${rule.is_managed ? styles.lockedRow : ""}`}>
                        <td className={`${styles.td} ${styles.mutedCell}`}>{rule.pos}</td>
                        <td className={styles.td}>
                          <span className={`${styles.badge} ${rule.type === "in" ? styles.badge_info : styles.badge_muted}`}>
                            {rule.type === "in" ? t("FirewallCard.directionIn") : t("FirewallCard.directionOut")}
                          </span>
                        </td>
                        <td className={`${styles.td} ${styles.nowrapCell}`}>{rule.proto ? rule.proto.toUpperCase() : t("FirewallCard.any")}</td>
                        <td className={`${styles.td} ${styles.nowrapCell}`}>{rule.dport ?? t("FirewallCard.any")}</td>
                        <td className={`${styles.td} ${styles.monoText}`}>
                          {rule.type === "in" ? (rule.source ?? t("FirewallCard.anySource")) : (rule.dest ?? t("FirewallCard.anySource"))}
                        </td>
                        <td className={styles.td}>
                          <span className={`${styles.badge} ${rule.action === "ACCEPT" ? styles.badge_ok : styles.badge_err}`}>
                            {rule.action}
                          </span>
                          {rule.enable === 0 && (
                            <span className={`${styles.badge} ${styles.badge_muted}`}>{t("FirewallCard.ruleDisabled")}</span>
                          )}
                        </td>
                        <td className={`${styles.td} ${styles.detailCell}`}>
                          {rule.is_managed ? (
                            <span className={styles.hintLine} title={rule.comment ?? ""}>
                              <MIcon name="lock" size={12} />
                              {t("FirewallCard.managedByService")}
                            </span>
                          ) : (
                            rule.comment ?? "—"
                          )}
                        </td>
                        {canManage && (
                          <td className={`${styles.td} ${styles.tdRight}`}>
                            {rule.is_managed ? (
                              <span className={styles.mutedText}>{t("FirewallCard.locked")}</span>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  className={styles.rpIconBtn}
                                  disabled={busy}
                                  title={rule.enable === 0 ? t("FirewallCard.enableRule") : t("FirewallCard.disableRule")}
                                  onClick={() => handleToggle(rule)}
                                >
                                  <MIcon name={rule.enable === 0 ? "toggle_off" : "toggle_on"} size={18} />
                                </button>
                                <button
                                  type="button"
                                  className={`${styles.rpIconBtn} ${styles.rpIconBtnDanger}`}
                                  disabled={busy}
                                  title={t("FirewallCard.deleteRule")}
                                  onClick={() => handleDelete(rule)}
                                >
                                  <MIcon name="delete" size={16} />
                                </button>
                              </>
                            )}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>

      {/* 共用對話框自己 portal 到 body，不會被卡片的 overflow:hidden 困住 */}
      {addPresence.open && (
        <ConnectionDialog
          fixedVmid={vmid}
          fixedName={thisVmName}
          initialTab="rule"
          closing={addPresence.closing}
          onClose={() => setShowAdd(false)}
          onDone={handleDialogDone}
          onChanged={load}
        />
      )}
    </div>
  );
}
