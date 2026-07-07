/**
 * RulesPanel
 * 顯示選取 VM 的防火牆選項與規則清單。
 * 從右側滑入，點 × 關閉。
 */

import { useState, useEffect } from "react";
import { getVmRules, getVmOptions } from "../../services/firewall";
import styles from "./RulesPanel.module.scss";
import MIcon from "../MIcon";

function Badge({ label, variant }) {
  return <span className={`${styles.badge} ${styles[`badge_${variant}`]}`}>{label}</span>;
}

export default function RulesPanel({ node, onClose }) {
  const [rules,   setRules]   = useState([]);
  const [options, setOptions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");

  useEffect(() => {
    if (!node?.vmid) return;
    setLoading(true);
    setError("");

    Promise.all([getVmRules(node.vmid), getVmOptions(node.vmid)])
      .then(([r, o]) => { setRules(r ?? []); setOptions(o); })
      .catch((err) => setError(err?.message ?? "載入失敗"))
      .finally(() => setLoading(false));
  }, [node?.vmid]);

  if (!node) return null;

  return (
    <div className={styles.panel}>
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <MIcon name="security" size={18} />
          <span className={styles.vmName}>{node.name}</span>
        </div>
        <button type="button" className={styles.closeBtn} onClick={onClose} aria-label="關閉">
          <MIcon name="close" size={20} />
        </button>
      </div>

      {loading && <p className={styles.hint}>載入中…</p>}
      {error   && <p className={styles.errorMsg}>{error}</p>}

      {!loading && !error && (
        <>
          {/* Options */}
          {options && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>防火牆設定</h3>
              <div className={styles.optionRow}>
                <span className={styles.optionLabel}>狀態</span>
                <Badge
                  label={options.enable ? "已啟用" : "已停用"}
                  variant={options.enable ? "success" : "muted"}
                />
              </div>
              <div className={styles.optionRow}>
                <span className={styles.optionLabel}>預設入站</span>
                <Badge label={options.policy_in  ?? "—"} variant="neutral" />
              </div>
              <div className={styles.optionRow}>
                <span className={styles.optionLabel}>預設出站</span>
                <Badge label={options.policy_out ?? "—"} variant="neutral" />
              </div>
            </div>
          )}

          {/* Rules */}
          <div className={styles.section}>
            <h3 className={styles.sectionTitle}>規則清單（{rules.length}）</h3>
            {rules.length === 0 ? (
              <p className={styles.hint}>目前沒有防火牆規則</p>
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
                        <span className={styles.ruleComment}>
                          {rule.is_managed ? "🔒 " : ""}{rule.comment}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
