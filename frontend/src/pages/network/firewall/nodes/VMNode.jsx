import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";
import MIcon from "../../../../components/MIcon";
import NodeHandles from "./NodeHandles";

const STATUS_COLOR = { running: "var(--color-success)", stopped: "var(--color-danger)" };

/* 歸屬徽章的說明：老師看學生機器、學生看自己的課堂機、管理員看別人的個人機 */
function originHint(t, data) {
  if (data.teaching_class_name && data.owner_name) {
    return t("VMNode.originStudent", { owner: data.owner_name, cls: data.teaching_class_name });
  }
  if (data.teaching_class_name) {
    return t("VMNode.originClassReadOnly", { cls: data.teaching_class_name });
  }
  return t("VMNode.originOwner", { owner: data.owner_name });
}

export default function VMNode({ data, selected }) {
  const { t } = useTranslation("network");
  const statusColor = STATUS_COLOR[data.status] ?? "var(--color-status-neutral)";
  const exposed = data.exposed_count ?? 0;
  const readOnly = data.can_manage === false;
  const origin = data.teaching_class_name || data.owner_name;

  return (
    <div className={`${styles.vmNode} ${selected ? styles.nodeSelected : ""} ${readOnly ? styles.nodeReadOnly : ""}`}>
      <NodeHandles />
      {/* 師生關係一眼可辨：不是自己的機器標班級／擁有者，唯讀的課堂機掛鎖 */}
      {origin && (
        <span
          className={`${styles.originBadge} ${readOnly ? styles.originReadOnly : ""}`}
          title={originHint(t, data)}
        >
          <MIcon name={readOnly ? "lock" : "school"} size={11} />
          {origin}
        </span>
      )}
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
    </div>
  );
}
