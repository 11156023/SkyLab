import { useTranslation } from "react-i18next";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import Stepper from "../../../components/Stepper/Stepper";
import { CLASS_WORKFLOW_TABS, POST_ACTIVE_TABS } from "./classWorkflowTabs";
import styles from "./ClassWorkflowStepper.module.scss";

/**
 * 班級頁的分頁切換，依班級狀態換樣式：
 * - 設定中（未啟用）：圓點連線的流程步驟列，打勾表示該步驟已設好
 * - 可以上課（已啟用）：設定已走完，不再需要「步驟」的暗示，改成一般的分段切換，
 *   六個分頁（含上課進度、AI 檢查）平行排開、純文字
 *
 * @param {object}   item      班級（status、students、weeks、course_environment、nodes）
 * @param {string}   activeKey 目前所在的分頁 key
 * @param {Function} onSelect  (key) => void
 */
export default function ClassWorkflowStepper({ item, activeKey, onSelect }) {
  const { t } = useTranslation("teaching");
  const ariaLabel = t("ClassWorkspacePage.workflowAriaLabel");

  if (item.status === "active") {
    return (
      <SegmentedControl
        className={styles.segment}
        ariaLabel={ariaLabel}
        value={activeKey}
        onChange={onSelect}
        options={CLASS_WORKFLOW_TABS.map(([key, , labelKey]) => ({ value: key, label: t(labelKey) }))}
      />
    );
  }

  const setupTabs = CLASS_WORKFLOW_TABS.filter(([key]) => !POST_ACTIVE_TABS.includes(key));
  const nodes = item.nodes ?? [];

  function stepDone(key) {
    if (key === "students") return item.students.length > 0;
    if (key === "weekly") return item.weeks.some((week) => String(week.title ?? "").trim());
    if (key === "machines") return Boolean(item.course_environment) && nodes.length > 0;
    return false;
  }

  return (
    <Stepper
      ariaLabel={ariaLabel}
      steps={setupTabs.map(([key, , labelKey]) => ({ key, label: t(labelKey), done: stepDone(key) }))}
      activeKey={activeKey}
      onSelect={onSelect}
    />
  );
}
