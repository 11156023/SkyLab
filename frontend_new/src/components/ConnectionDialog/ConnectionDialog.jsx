/**
 * ConnectionDialog
 * 建立防火牆連線的 Modal。
 *
 * 模式：
 * - 網際網路 → VM：入站（Port Forwarding 或 Firewall Only）
 * - VM → 網際網路：出站（直接確認，無需設定 port）
 * - VM → VM：指定 port + 方向
 */

import { useState } from "react";
import styles from "./ConnectionDialog.module.scss";
import MIcon from "../MIcon";

const PROTOCOLS = ["tcp", "udp", "icmp", "icmpv6", "sctp"];

const GATEWAY_LABEL = "網際網路";

let _uid = 0;
function uid() { return ++_uid; }

function newPortRow() {
  return { id: uid(), port: "", protocol: "tcp" };
}

function newForwardRow() {
  return { id: uid(), externalPort: "", internalPort: "", protocol: "tcp" };
}

/* ── 入站模式：純防火牆 ── */
function FirewallOnlyForm({ rows, setRows }) {
  const add = () => setRows((r) => [...r, newPortRow()]);
  const remove = (id) => setRows((r) => r.filter((x) => x.id !== id));
  const update = (id, key, val) =>
    setRows((r) => r.map((x) => (x.id === id ? { ...x, [key]: val } : x)));

  return (
    <div className={styles.portSection}>
      <p className={styles.modeDesc}>直接開放指定 port，不進行外部路由對應</p>
      {rows.map((row) => (
        <div key={row.id} className={styles.portRow}>
          <input
            type="number"
            min="1"
            max="65535"
            placeholder="Port"
            value={row.port}
            onChange={(e) => update(row.id, "port", e.target.value)}
            className={styles.portInput}
          />
          <select
            value={row.protocol}
            onChange={(e) => update(row.id, "protocol", e.target.value)}
            className={styles.protoSelect}
          >
            {PROTOCOLS.map((p) => <option key={p}>{p}</option>)}
          </select>
          <button type="button" className={styles.removeBtn} onClick={() => remove(row.id)}>
            <MIcon name="remove" size={16} />
          </button>
        </div>
      ))}
      <button type="button" className={styles.addBtn} onClick={add}>
        <MIcon name="add" size={16} />
        新增 Port
      </button>
    </div>
  );
}

/* ── 入站模式：Port Forwarding ── */
function PortForwardForm({ rows, setRows }) {
  const add = () => setRows((r) => [...r, newForwardRow()]);
  const remove = (id) => setRows((r) => r.filter((x) => x.id !== id));
  const update = (id, key, val) =>
    setRows((r) => r.map((x) => (x.id === id ? { ...x, [key]: val } : x)));

  return (
    <div className={styles.portSection}>
      <p className={styles.modeDesc}>將外部 Port 對應到 VM 內部 Port（需 Gateway VM 設定）</p>
      <div className={styles.portRowHeader}>
        <span>外部 Port</span>
        <span>內部 Port</span>
        <span>協定</span>
        <span />
      </div>
      {rows.map((row) => (
        <div key={row.id} className={styles.portRow}>
          <input
            type="number" min="1" max="65535" placeholder="外部"
            value={row.externalPort}
            onChange={(e) => update(row.id, "externalPort", e.target.value)}
            className={styles.portInput}
          />
          <input
            type="number" min="1" max="65535" placeholder="內部"
            value={row.internalPort}
            onChange={(e) => update(row.id, "internalPort", e.target.value)}
            className={styles.portInput}
          />
          <select
            value={row.protocol}
            onChange={(e) => update(row.id, "protocol", e.target.value)}
            className={styles.protoSelect}
          >
            {PROTOCOLS.map((p) => <option key={p}>{p}</option>)}
          </select>
          <button type="button" className={styles.removeBtn} onClick={() => remove(row.id)}>
            <MIcon name="remove" size={16} />
          </button>
        </div>
      ))}
      <button type="button" className={styles.addBtn} onClick={add}>
        <MIcon name="add" size={16} />
        新增對應
      </button>
    </div>
  );
}

/* ── 主元件 ── */
export default function ConnectionDialog({ nodes, onConfirm, onClose }) {
  // 節點選擇
  const [sourceKey, setSourceKey] = useState("internet");
  const [targetKey, setTargetKey] = useState(nodes[0]?.key ?? "");

  // 方向（VM→VM 用）
  const [direction, setDirection] = useState("one_way");

  // 入站模式
  const [inboundMode, setInboundMode] = useState("port"); // "port" | "firewall"

  // Port rows
  const [fwRows,  setFwRows]  = useState([newPortRow()]);
  const [fwdRows, setFwdRows] = useState([newForwardRow()]);
  const [vmRows,  setVmRows]  = useState([newPortRow()]);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const isInternetSrc = sourceKey === "internet";
  const isInternetTgt = targetKey === "internet";
  const isVmToVm = !isInternetSrc && !isInternetTgt;
  const isInbound = isInternetSrc && !isInternetTgt;
  const isOutbound = !isInternetSrc && isInternetTgt;

  const nodeOptions = [
    { key: "internet", label: GATEWAY_LABEL },
    ...nodes.map((n) => ({ key: n.key, label: n.name })),
  ];

  const sourceName = nodeOptions.find((n) => n.key === sourceKey)?.label ?? sourceKey;
  const targetName = nodeOptions.find((n) => n.key === targetKey)?.label ?? targetKey;

  const getVmid = (key) => {
    if (key === "internet") return null;
    const n = nodes.find((x) => x.key === key);
    return n?.vmid ?? null;
  };

  const buildPorts = () => {
    if (isOutbound) return [{ port: 0, protocol: "tcp" }]; // 出站不限 port

    if (isInbound) {
      if (inboundMode === "firewall") {
        return fwRows
          .filter((r) => r.port)
          .map((r) => ({ port: Number(r.port), protocol: r.protocol }));
      }
      // port forwarding
      return fwdRows
        .filter((r) => r.externalPort && r.internalPort)
        .map((r) => ({
          port: Number(r.internalPort),
          protocol: r.protocol,
          external_port: Number(r.externalPort),
        }));
    }

    // VM→VM
    return vmRows
      .filter((r) => r.port)
      .map((r) => ({ port: Number(r.port), protocol: r.protocol }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const ports = buildPorts();
    if (!isOutbound && ports.length === 0) {
      setError("請至少填寫一個 Port");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await onConfirm({
        source_vmid: getVmid(sourceKey),
        target_vmid: getVmid(targetKey),
        ports,
        direction: isVmToVm ? direction : "one_way",
      });
    } catch (err) {
      setError(err?.message ?? "建立失敗");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.dialog}>
        {/* Title */}
        <div className={styles.dialogHeader}>
          <h2 className={styles.dialogTitle}>新增連線</h2>
          <button type="button" className={styles.closeBtn} onClick={onClose}>
            <MIcon name="close" size={20} />
          </button>
        </div>

        <form className={styles.dialogBody} onSubmit={handleSubmit}>
          {/* Source / Target 選擇 */}
          <div className={styles.nodeRow}>
            <div className={styles.nodeSelect}>
              <label className={styles.nodeLabel}>來源</label>
              <select
                value={sourceKey}
                onChange={(e) => setSourceKey(e.target.value)}
                className={styles.select}
              >
                {nodeOptions
                  .filter((n) => n.key !== targetKey)
                  .map((n) => <option key={n.key} value={n.key}>{n.label}</option>)}
              </select>
            </div>

            <div className={styles.arrowIcon}>
              <MIcon name="arrow_forward" size={20} />
            </div>

            <div className={styles.nodeSelect}>
              <label className={styles.nodeLabel}>目標</label>
              <select
                value={targetKey}
                onChange={(e) => setTargetKey(e.target.value)}
                className={styles.select}
              >
                {nodeOptions
                  .filter((n) => n.key !== sourceKey)
                  .map((n) => <option key={n.key} value={n.key}>{n.label}</option>)}
              </select>
            </div>
          </div>

          {/* 出站：只需確認 */}
          {isOutbound && (
            <p className={styles.outboundMsg}>
              <MIcon name="info" size={16} />
              開放 <strong>{sourceName}</strong> 存取網際網路的出站連線
            </p>
          )}

          {/* 入站：模式選擇 */}
          {isInbound && (
            <>
              <div className={styles.modeToggle}>
                <button
                  type="button"
                  className={`${styles.modeBtn} ${inboundMode === "port" ? styles.modeBtnActive : ""}`}
                  onClick={() => setInboundMode("port")}
                >
                  Port Forwarding
                </button>
                <button
                  type="button"
                  className={`${styles.modeBtn} ${inboundMode === "firewall" ? styles.modeBtnActive : ""}`}
                  onClick={() => setInboundMode("firewall")}
                >
                  Firewall Only
                </button>
              </div>
              {inboundMode === "port"
                ? <PortForwardForm rows={fwdRows} setRows={setFwdRows} />
                : <FirewallOnlyForm rows={fwRows} setRows={setFwRows} />
              }
            </>
          )}

          {/* VM→VM */}
          {isVmToVm && (
            <>
              <div className={styles.directionRow}>
                <label className={styles.nodeLabel}>方向</label>
                <div className={styles.modeToggle}>
                  <button
                    type="button"
                    className={`${styles.modeBtn} ${direction === "one_way" ? styles.modeBtnActive : ""}`}
                    onClick={() => setDirection("one_way")}
                  >
                    {sourceName} → {targetName}
                  </button>
                  <button
                    type="button"
                    className={`${styles.modeBtn} ${direction === "bidirectional" ? styles.modeBtnActive : ""}`}
                    onClick={() => setDirection("bidirectional")}
                  >
                    雙向
                  </button>
                </div>
              </div>
              <FirewallOnlyForm rows={vmRows} setRows={setVmRows} />
            </>
          )}

          {error && <p className={styles.errorMsg}>{error}</p>}

          {/* Actions */}
          <div className={styles.actions}>
            <button type="button" className={styles.cancelBtn} onClick={onClose}>取消</button>
            <button type="submit" className={styles.confirmBtn} disabled={submitting}>
              {submitting ? "建立中…" : "建立連線"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
