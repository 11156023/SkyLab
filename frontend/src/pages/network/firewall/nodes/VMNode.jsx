import { Handle, Position } from "@xyflow/react";
import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";
import MIcon from "../../../../components/MIcon";

const STATUS_COLOR = { running: "var(--color-success)", stopped: "var(--color-danger)" };

export default function VMNode({ data, selected }) {
  const { t } = useTranslation("network");
  const statusColor = STATUS_COLOR[data.status] ?? "var(--color-status-neutral)";
  const exposed = data.exposed_count ?? 0;

  return (
    <div className={`${styles.vmNode} ${selected ? styles.nodeSelected : ""}`}>
      <Handle type="target" position={Position.Left}  className={styles.handleIn} />
      <div className={styles.vmStatus} style={{ background: statusColor }} />
      <div className={styles.vmInfo}>
        <span className={styles.vmName}>{data.name}</span>
        <span className={styles.vmMeta}>{data.ip_address ?? `vmid:${data.vmid}`}</span>
      </div>
      <MIcon name={data.firewall_enabled ? "security" : "shield"} size={15} />
      {/* 有對外開放的機器一眼可辨，不必逐條點線確認暴露面 */}
      {exposed > 0 && (
        <span
          className={styles.exposedBadge}
          title={t("VMNode.exposedHint", { count: exposed })}
        >
          <MIcon name="public" size={11} />
          {exposed}
        </span>
      )}
      <Handle type="source" position={Position.Right} className={styles.handleOut} />
    </div>
  );
}
