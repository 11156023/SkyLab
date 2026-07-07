import { Handle, Position } from "@xyflow/react";
import styles from "../FirewallPage.module.scss";
import MIcon from "../../../../components/MIcon";

export default function GatewayNode({ selected }) {
  return (
    <div className={`${styles.gwNode} ${selected ? styles.nodeSelected : ""}`}>
      <Handle type="source" position={Position.Right} className={styles.handleOut} />
      <Handle type="target" position={Position.Left}  className={styles.handleIn} />
      <MIcon name="public" size={30} />
      <span className={styles.gwLabel}>網際網路</span>
    </div>
  );
}
