import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import styles from "./CourseOperations.module.scss";
import { environmentSpecs } from "./nodeSpecs";

/** 課程環境的一個選項。班級頁與一鍵建立精靈共用，避免兩邊各長一套。
 *  disabled：送出中不讓人再改選，避免選到一半的環境被送出去。 */
export default function EnvironmentChoice({ candidate, selected, suggested, onSelect, disabled = false }) {
  const { t } = useTranslation("teaching");
  const specs = environmentSpecs(candidate.nodes);
  return <button
    type="button"
    className={`${selected ? styles.envChoiceSelected : ""}${suggested ? ` ${styles.envChoiceSuggested}` : ""}`}
    disabled={disabled}
    onClick={onSelect}
  >
    <span className={styles.envChoiceIcon}><MIcon name="account_tree" size={20} /></span>
    <div>
      <strong><span>{candidate.name}</span><i>v{candidate.version}</i></strong>
      <p>{candidate.description || t("ClassWorkspacePage.noDescriptionFallback")}</p>
    </div>
    <div className={styles.envChoiceSpecs}>
      <span><MIcon name="dns" size={13} />{t("ClassWorkspacePage.perStudentUnit", { count: candidate.nodes.length })}</span>
      <span>{specs.cpu} CPU</span>
      <span>{specs.memory} GB RAM</span>
      <span>{specs.disk} GB</span>
    </div>
    <em>{selected ? <><MIcon name="check" size={14} />{t("ClassWorkspacePage.selectedLabel")}</> : t("ClassWorkspacePage.selectLabel")}</em>
  </button>;
}
