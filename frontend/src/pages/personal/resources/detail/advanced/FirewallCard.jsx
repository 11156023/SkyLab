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

/**
 * @param {string[]} publicUrls 這台機器的對外網址（來自 ResourcePublic.public_urls），唯讀顯示；
 *   要改網址從「新增規則 › 連線」或拓撲頁做。
 * @param {() => void} [onChanged] 對話框建了連線後通知上層重載資源（網址才會更新）。
 */
export default function FirewallCard({ vmid, canManage, publicUrls = [], onChanged }) {
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
      toast.error(err?.message ?? t("Error.generic", { ns: "common" }));
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
    if (result?.kind !== "rule") onChanged?.();
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
      message: t("FirewallCard.deleteRuleMessage", { pos: rule.pos + 1 }),
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

  /* 全部規則都是服務管理的鎖定規則時，操作欄整欄是空的，不顯示 */
  const showActionsCol = canManage && rules.some((rule) => !rule.is_managed);

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
            <button type="button" className={styles.btnSecondary} onClick={() => setShowAdd(true)}>
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
                <MiniTopology topology={topology} />
                <div className={styles.flowLegend}>
                  <span><i className={`${styles.legendDot} ${styles.legendIn}`} />{t("FirewallCard.legendInbound")}</span>
                  <span><i className={`${styles.legendDot} ${styles.legendOut}`} />{t("FirewallCard.legendOutbound")}</span>
                  <span><i className={`${styles.legendDot} ${styles.legendPeer}`} />{t("FirewallCard.legendPeer")}</span>
                </div>
              </div>
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
                      {showActionsCol && <th className={styles.th}>{t("FirewallCard.actionsCol")}</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {rules.map((rule) => (
                      <tr key={rule.pos} className={`${styles.tr} ${rule.is_managed ? styles.lockedRow : ""}`}>
                        {/* rule.pos 是 Proxmox 的 0 起算位置，顯示給人看從 1 開始 */}
                        <td className={`${styles.td} ${styles.mutedCell}`}>{rule.pos + 1}</td>
                        <td className={styles.td}>
                          <span className={`${styles.badge} ${rule.type === "in" ? styles.badge_info : styles.badge_muted}`}>
                            {rule.type === "in" ? t("FirewallCard.directionIn") : t("FirewallCard.directionOut")}
                          </span>
                        </td>
                        <td className={`${styles.td} ${styles.nowrapCell}`}>{rule.proto ? rule.proto.toUpperCase() : t("FirewallCard.any")}</td>
                        <td className={`${styles.td} ${styles.nowrapCell}`}>{formatPorts(rule.dport, t)}</td>
                        <td className={`${styles.td} ${styles.monoText}`}>
                          {(rule.type === "in" ? rule.source : rule.dest) ?? t("FirewallCard.any")}
                        </td>
                        <td className={styles.td}>
                          <span className={`${styles.badge} ${rule.action === "ACCEPT" ? styles.badge_success : styles.badge_danger}`}>
                            {policyLabel(rule.action, t)}
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
                        {showActionsCol && (
                          <td className={`${styles.td} ${styles.tdActions}`}>
                            {/* 鎖定規則的備註欄已標「由連線管理」，操作欄留空不再重複 */}
                            {rule.is_managed ? null : (
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
