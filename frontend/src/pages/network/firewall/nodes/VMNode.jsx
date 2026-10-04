import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";
import MIcon from "../../../../components/MIcon";
import NodeHandles from "./NodeHandles";
import MachineKindBadge from "../../../../components/MachineKindBadge/MachineKindBadge";

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
  /* 同一班的主機名都是 cls-xxxx-1-1，截斷後分不出誰是誰：老師視角主標改學生名，主機名放滑過提示 */
  const title = data.teaching_class_name && data.owner_name ? data.owner_name : data.name;
  /* 群組框內：框標題已寫明班級／類型，徽章只在唯讀（要掛鎖頭）時保留 */
  const showBadge = !data.inGroup || readOnly;

  return (
    <div className={`${styles.vmNode} ${selected ? styles.nodeSelected : ""} ${readOnly ? styles.nodeReadOnly : ""}`}>
      <NodeHandles dragStartSide="right" />
      {/* 機器來源徽章與我的資源同一套：學生機器帶學生名，唯讀的課堂機掛鎖 */}
      {showBadge && <MachineKindBadge
        kind={data.machine_kind}
        classRelation={data.class_relation}
        ownerName={data.owner_name}
        teachingClassName={data.teaching_class_name}
        readOnly={readOnly}
        solid
        className={styles.originBadge}
        title={originHint(t, data)}
      />}

      <div className={styles.vmStatus} style={{ background: statusColor }} />
      <div className={styles.vmInfo}>
        <span className={styles.vmName} title={title !== data.name ? data.name : undefined}>{title}</span>
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
