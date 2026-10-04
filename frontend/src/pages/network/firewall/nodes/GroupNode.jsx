import { useTranslation } from "react-i18next";
import MIcon from "../../../../components/MIcon";
import NodeHandles from "./NodeHandles";
import styles from "../FirewallPage.module.scss";

/**
 * GroupNode — 拓撲的群組框（班級或機器類型）。
 * - 展開：虛線框，標題列＋框內格狀排的機器（機器是 ReactFlow 的子節點，畫在框裡）
 * - 收合：一張摘要卡（台數、開機數、對外開放數），群組內往外的連線併成一條接到這張卡
 * 標題列的按鈕切換收合（nodrag，點了不會被當成拖曳）；框的其他地方可以拖動整個群組。
 */
export default function GroupNode({ data }) {
  const { t } = useTranslation("network");
  const { t: tc } = useTranslation("components");
  const name = data.label.type === "class" ? data.label.name : tc(data.label.labelKey);
  const icon = data.label.icon;
  const toggleLabel = data.collapsed
    ? t("GroupNode.expand", { name })
    : t("GroupNode.collapse", { name });

  return (
    <div className={`${styles.groupNode} ${data.collapsed ? styles.groupNodeCollapsed : ""}`}>
      {/* 收合時連線接在這張卡上；不能從群組拉線，要建連線請展開後拉單台機器 */}
      {data.collapsed && <NodeHandles connectable={false} />}
      <div className={styles.groupHeader}>
        <button
          type="button"
          className={`${styles.groupToggle} nodrag`}
          onClick={() => data.onToggle?.(data.groupKey)}
          /* 搜尋中群組被強制展開、不能收合（applyView 不給 onToggle） */
          disabled={!data.onToggle}
          aria-expanded={!data.collapsed}
          aria-label={toggleLabel}
          title={toggleLabel}
        >
          <MIcon name={icon} size={18} />
          <span className={styles.groupName}>{name}</span>
          {/* 收合箭頭比照側邊欄分組標題：放在名稱右端，展開時旋轉 90° */}
          <span className={`${styles.groupChevron} ${data.collapsed ? "" : styles.groupChevronOpen}`} aria-hidden="true">
            <MIcon name="chevron_right" size={18} />
          </span>
        </button>
        <span className={styles.groupCount}>{t("GroupNode.count", { count: data.total })}</span>
        {data.exposed > 0 && (
          <span className={styles.groupExposed} title={t("GroupNode.exposedHint", { count: data.exposed })}>
            <MIcon name="public" size={11} />
            {data.exposed}
          </span>
        )}
      </div>
      {data.collapsed && (
        <p className={styles.groupStats}>
          {t("GroupNode.running", { count: data.running })}
          {" · "}{t("GroupNode.rules", { count: data.rules ?? 0 })}
          {data.exposed > 0 && <> · {t("GroupNode.exposed", { count: data.exposed })}</>}
        </p>
      )}
    </div>
  );
}
