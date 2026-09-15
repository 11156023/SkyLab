import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";
import MIcon from "../../../../components/MIcon";
import NodeHandles from "./NodeHandles";
import MachineKindBadge from "../../../../components/MachineKindBadge/MachineKindBadge";

const STATUS_COLOR = { running: "var(--color-success)", stopped: "var(--color-danger)" };

const formatPorts = (ports) =>
  (ports ?? []).map((p) => (p.port === 0 ? p.protocol : `${p.port}/${p.protocol}`)).join(", ");

/* 歸屬徽章的說明：老師看學生機器、學生看自己的課堂機、學生看老師開放的機器、
   管理員看別人的個人機 */
function originHint(t, data, peer) {
  if (peer) {
    return t("VMNode.originPeer", {
      owner: data.owner_name ?? "",
      cls: data.teaching_class_name ?? "",
      ports: formatPorts(data.allowed_ports),
    });
  }
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
  /* 老師開放給班級的機器：不能管、但可以當連線目標 */
  const peer = readOnly && data.can_connect !== false;
  const nodeClass = [
    styles.vmNode,
    selected ? styles.nodeSelected : "",
    peer ? styles.nodePeer : readOnly ? styles.nodeReadOnly : "",
  ].join(" ");

  return (
    <div className={nodeClass}>
      <NodeHandles />
      {/* 機器來源徽章與我的資源同一套：學生機器帶學生名、老師開放帶老師名、
          唯讀的課堂機掛鎖 */}
      <MachineKindBadge
        kind={peer ? "teacher_open" : data.machine_kind}
        classRelation={data.class_relation}
        ownerName={data.owner_name}
        teachingClassName={data.teaching_class_name}
        readOnly={readOnly && !peer}
        solid
        size="sm"
        className={styles.originBadge}
        title={originHint(t, data, peer)}
      />
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
