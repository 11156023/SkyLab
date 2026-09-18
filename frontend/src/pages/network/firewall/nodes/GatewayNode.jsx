import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";
import MIcon from "../../../../components/MIcon";
import NodeHandles from "./NodeHandles";

export default function GatewayNode({ selected }) {
  const { t } = useTranslation("network");
  return (
    <div className={`${styles.gwNode} ${selected ? styles.nodeSelected : ""}`}>
      <NodeHandles dragEndSide="left" />
      <MIcon name="public" size={30} />
      <span className={styles.gwLabel}>{t("GatewayNode.internet")}</span>
    </div>
  );
}
