/**
 * ConnectionDetailPanel
 * 點選拓撲圖上的連線後滑入的細節面板：講清楚這條線「開了什麼、往哪個方向」，
 * 並把刪除收在這裡，取代原本掛在線上的小叉叉。
 * 與 RulesPanel 互斥顯示（同一個右側位置）。
 * 課程環境編輯器也用它看模板上的線：那裡的網址是樣板（allowOpen=false），
 * 已發布的版本不能改（不給 onDelete 就沒有刪除鈕）。
 * 資源詳情的迷你拓撲也用它（疊在那張小圖上）。
 *
 * 不能刪的時候傳 lockedHint：在原本放刪除鈕的位置用鎖頭講清楚為什麼
 * （沒有管理權、環境已發布…），不讓人以為是按鈕不見了。
 */

import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import { describePort, portMode, PORT_MODE } from "./utils/buildFlow";
import styles from "./ConnectionDetailPanel.module.scss";

const MODE_META = {
  [PORT_MODE.DOMAIN]:   { icon: "language",   labelKey: "ConnectionPanel.modeDomain" },
  [PORT_MODE.FORWARD]:  { icon: "swap_horiz", labelKey: "ConnectionPanel.modePortForward" },
  [PORT_MODE.FIREWALL]: { icon: "shield",     labelKey: "ConnectionPanel.modeFirewallOnly" },
};

export default function ConnectionDetailPanel({ edge, resolveName, onClose, onDelete, lockedHint = "", closing = false, allowOpen = true }) {
  const { t } = useTranslation("network");
  if (!edge) return null;

  const isInbound  = edge.source_vmid === null;
  const isOutbound = edge.target_vmid === null;
  const kindKey = isInbound
    ? "ConnectionPanel.inbound"
    : isOutbound
      ? "ConnectionPanel.outbound"
      : "ConnectionPanel.internal";
  const kindTone = isInbound ? styles.toneInfo : isOutbound ? styles.toneSuccess : styles.toneNeutral;
  const bidirectional = edge.direction === "bidirectional";
  const ports = edge.ports ?? [];

  return (
    <div className={`${styles.panel} ${closing ? styles.panelOut : ""}`}>
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <MIcon name="lan" size={18} />
          <span className={styles.title}>{t("ConnectionPanel.title")}</span>
        </div>
        <button
          type="button"
          className={styles.closeBtn}
          onClick={onClose}
          aria-label={t("ConnectionPanel.close")}
        >
          <MIcon name="close" size={20} />
        </button>
      </div>

      {/* 標題列與底部的刪除鈕固定，只有中間這段捲動：畫布很矮（資源詳情的迷你拓撲）時刪除鈕不會被捲到看不見 */}
      <div className={styles.body}>
      {/* 兩端與方向：一眼看出資料往哪走 */}
      <div className={styles.section}>
        <div className={styles.endpoints}>
          <span className={styles.endpoint}>{resolveName(edge.source_vmid)}</span>
          <MIcon name={bidirectional ? "sync_alt" : "arrow_forward"} size={16} />
          <span className={styles.endpoint}>{resolveName(edge.target_vmid)}</span>
        </div>
        <div className={styles.badgeRow}>
          <span className={`${styles.badge} ${kindTone}`}>{t(kindKey)}</span>
          <span className={styles.badge}>
            {t(bidirectional ? "ConnectionPanel.bidirectional" : "ConnectionPanel.oneWay")}
          </span>
          {edge.topology_managed && (
            <span className={styles.badge}>{t("ConnectionPanel.topologyManaged")}</span>
          )}
        </div>
      </div>

      {/* 開放內容：逐條講用途，而不是只有埠號 */}
      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>
          {t("ConnectionPanel.portsTitle", { count: ports.length })}
        </h3>
        {ports.length === 0 ? (
          <p className={styles.hint}>{t("ConnectionPanel.noPorts")}</p>
        ) : (
          <ul className={styles.portList}>
            {ports.map((port, i) => {
              const mode = portMode(port);
              const meta = MODE_META[mode];
              return (
                <li key={`${port.protocol}-${port.port}-${i}`} className={styles.portRow}>
                  <MIcon name={meta.icon} size={14} />
                  <div className={styles.portInfo}>
                    <span className={styles.portText}>{describePort(port)}</span>
                    <span className={styles.portMode}>
                      {t(meta.labelKey)}
                      {port.protocol ? ` · ${port.protocol}` : ""}
                    </span>
                  </div>
                  {mode === PORT_MODE.DOMAIN && allowOpen && (
                    <a
                      className={styles.openLink}
                      href={`https://${port.domain}`}
                      target="_blank"
                      rel="noreferrer"
                      title={t("ConnectionPanel.openUrl")}
                    >
                      <MIcon name="open_in_new" size={14} />
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      </div>

      {onDelete && !edge.topology_managed ? (
        <div className={styles.footer}>
          <button type="button" className={styles.deleteBtn} onClick={() => onDelete(edge)}>
            <MIcon name="delete" size={15} />
            {t("ConnectionPanel.delete")}
          </button>
        </div>
      ) : lockedHint ? (
        <div className={styles.footer}>
          <p className={styles.lockedHint}>
            <MIcon name="lock" size={14} />
            <span>{lockedHint}</span>
          </p>
        </div>
      ) : null}
    </div>
  );
}
