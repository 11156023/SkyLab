import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import HomeCard from "./HomeCard";
import card from "./HomeCard.module.scss";
import styles from "./MachineCard.module.scss";

const KNOWN_STATUSES = ["running", "starting", "stopped", "provisioning", "failed", "expired"];
const STATUS_DOT = { running: card.dotSuccess, starting: card.dotPending, provisioning: card.dotPending, failed: card.dotDanger };

/**
 * 首頁「最近使用機器」卡：標籤列放狀態與類型，內頁放名稱、VMID 與進入動作。
 */
export default function MachineCard({ machine, openingMachineId, onOpen, onInfo }) {
  const { t } = useTranslation("personal");
  const isLxc = machine.type === "lxc";
  const status = KNOWN_STATUSES.includes(machine.status) ? machine.status : "unknown";
  /* 開機 task 還沒跑完（starting）時跟「剛按下開機」一樣顯示開機中，按鈕保持停用 */
  const opening = openingMachineId === machine.vmid || machine.status === "starting";
  const launchable = ["running", "stopped"].includes(machine.status);
  const actionKey = opening
    ? "StudentHomePage.actionStarting"
    : machine.status === "running"
      ? "StudentHomePage.actionEnter"
      : machine.status === "stopped" ? "StudentHomePage.actionStartAndEnter" : "HomeOverview.unavailable";

  return (
    <HomeCard
      icon={isLxc ? "terminal" : "desktop_windows"}
      band={<>
        <span className={`${card.dot} ${STATUS_DOT[status] ?? ""}`}>{t(`HomeOverview.machineStatus.${status}`)}</span>
        <span>{isLxc ? "LXC" : "VM"}</span>
      </>}
    >
      <h3 className={card.name}>{machine.name}</h3>
      <p className={card.sub}>#{machine.vmid}</p>
      <div className={card.actions}>
        <button type="button" className={styles.launchButton} onClick={() => onOpen(machine)}
          disabled={openingMachineId !== null || !launchable}>
          <MIcon name={opening ? "hourglass_top" : "play_arrow"} size={18} />
          {t(actionKey)}
        </button>
        <button type="button" className={styles.infoButton} onClick={() => onInfo(machine)}
          aria-label={t("StudentHomePage.machineInfoAria", { name: machine.name })}
          title={t("StudentHomePage.machineInfoAria", { name: machine.name })}>
          <MIcon name="info" size={18} />
        </button>
      </div>
    </HomeCard>
  );
}
