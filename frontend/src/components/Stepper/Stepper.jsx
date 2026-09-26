import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import styles from "./Stepper.module.scss";

/**
 * 全站共用的步驟列（圓點連線 stepper）。
 * 每一步是可點的圓點＋標籤，步與步之間以連線相接；兩端都「走到了」（已完成或目前）的連線以主色標示。
 * 不算步驟的分頁（例如班級啟用後的上課進度、AI）放 extras：接在分隔線後面，圓點改放圖示、不連線。
 *
 * @param {Array}    steps     [{ key, label, done?, disabled? }]，圓點依序顯示 1、2、3…，done 時換成 ✓；
 *                             disabled 用在精靈式流程「還不能跳過去」的步驟（例如只能往回跳）
 * @param {Array}    extras    [{ key, label, icon }]，選填，icon 為 MIcon 名稱
 * @param {string}   activeKey 目前所在的 key（steps 或 extras 皆可）
 * @param {Function} onSelect  (key) => void
 * @param {string}   ariaLabel 導覽區塊的無障礙名稱
 * @param {string}   className 額外樣式
 */
export default function Stepper({ steps, extras = [], activeKey, onSelect, ariaLabel, className }) {
  const { t } = useTranslation("components");
  const reached = (step) => step.done || step.key === activeKey;

  return (
    <nav className={`${styles.stepper}${className ? ` ${className}` : ""}`} aria-label={ariaLabel}>
      <ol className={styles.track}>
        {steps.map((step, index) => {
          const active = step.key === activeKey;
          return (
            <Fragment key={step.key}>
              {index > 0 && (
                <li
                  aria-hidden="true"
                  className={`${styles.connector} ${reached(steps[index - 1]) && reached(step) ? styles.connectorReached : ""}`}
                />
              )}
              <li className={styles.item}>
                <button
                  type="button"
                  className={`${styles.step} ${active ? styles.stepActive : ""} ${step.done ? styles.stepDone : ""}`}
                  aria-current={active ? "step" : undefined}
                  disabled={step.disabled}
                  onClick={() => onSelect(step.key)}
                >
                  <span className={styles.dot} aria-hidden="true">
                    {step.done ? <MIcon name="check" size={15} /> : index + 1}
                  </span>
                  <span className={styles.label}>
                    {step.label}
                    {step.done && <span className={styles.srOnly}>{t("Stepper.doneSuffix")}</span>}
                  </span>
                </button>
              </li>
            </Fragment>
          );
        })}
        {extras.length > 0 && <li aria-hidden="true" className={styles.divider} />}
        {extras.map((extra) => {
          const active = extra.key === activeKey;
          return (
            <li key={extra.key} className={styles.item}>
              <button
                type="button"
                className={`${styles.step} ${active ? styles.stepActive : ""}`}
                aria-current={active ? "page" : undefined}
                onClick={() => onSelect(extra.key)}
              >
                <span className={`${styles.dot} ${styles.dotIcon}`} aria-hidden="true">
                  <MIcon name={extra.icon} size={15} />
                </span>
                <span className={styles.label}>{extra.label}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
