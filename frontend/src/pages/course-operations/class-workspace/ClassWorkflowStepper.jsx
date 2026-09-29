import { useTranslation } from "react-i18next";
import Stepper from "../../../components/Stepper/Stepper";
import { CLASS_WORKFLOW_TABS, POST_ACTIVE_TABS } from "./classWorkflowTabs";

/**
 * 班級頁的流程步驟列：設定步驟走圓點連線；上課進度、AI 不算步驟，班級啟用後才接在分隔線後面。
 *
 * @param {object}   item      班級（students、weeks、course_environment、nodes）
 * @param {string}   activeKey 目前所在的分頁 key
 * @param {Function} onSelect  (key) => void
 */
export default function ClassWorkflowStepper({ item, activeKey, onSelect }) {
  const { t } = useTranslation("teaching");
  const setupTabs = CLASS_WORKFLOW_TABS.filter(([key]) => !POST_ACTIVE_TABS.includes(key));
  const postActiveTabs = item.status === "active"
    ? CLASS_WORKFLOW_TABS.filter(([key]) => POST_ACTIVE_TABS.includes(key))
    : [];
  const nodes = item.nodes ?? [];

  function stepDone(key) {
    if (key === "students") return item.students.length > 0;
    if (key === "weekly") return item.weeks.some((week) => String(week.title ?? "").trim());
    if (key === "machines") return Boolean(item.course_environment) && nodes.length > 0;
    return false;
  }

  return (
    <Stepper
      ariaLabel={t("ClassWorkspacePage.workflowAriaLabel")}
      steps={setupTabs.map(([key, , labelKey]) => ({ key, label: t(labelKey), done: stepDone(key) }))}
      extras={postActiveTabs.map(([key, icon, labelKey]) => ({ key, icon, label: t(labelKey) }))}
      activeKey={activeKey}
      onSelect={onSelect}
    />
  );
}
