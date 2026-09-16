import { Handle, Position } from "@xyflow/react";
import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";
import MIcon from "../../../../components/MIcon";

export default function GatewayNode({ selected }) {
  const { t } = useTranslation("network");
  return (
    <div className={`${styles.gwNode} ${selected ? styles.nodeSelected : ""}`}>
      <Handle type="source" position={Position.Right} className={styles.handleOut} data-firewall-handle="source" />
      <Handle
        type="target"
        position={Position.Left}
        className={styles.handleIn}
        data-firewall-handle="target"
        data-guide="firewall-drag-end"
      />
      <MIcon name="public" size={30} />
      <span className={styles.gwLabel}>{t("GatewayNode.internet")}</span>
    </div>
  );
}
