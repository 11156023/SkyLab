import { forwardRef, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import styles from "./PasswordInput.module.scss";

/**
 * 密碼欄：右側附顯示／隱藏的眼睛鈕。
 *
 * input 長相由呼叫端的 className 決定（各頁表單樣式不同），這裡只負責
 * 定位眼睛鈕與讓出右側內距。眼睛鈕不進 Tab 鍵序（tabIndex={-1}），
 * 免得打斷「密碼 → 下一欄」的填寫節奏。
 *
 * ref 會轉給內層的 input，呼叫端才能做聚焦（例如送出時跳到未填欄位）。
 */
const PasswordInput = forwardRef(function PasswordInput({ className, ...props }, ref) {
  const { t } = useTranslation("common");
  const [show, setShow] = useState(false);

  return (
    <span className={styles.wrap}>
      <input
        {...props}
        ref={ref}
        type={show ? "text" : "password"}
        className={`${className ?? ""} ${styles.input}`.trim()}
      />
      <button
        type="button"
        className={styles.eyeBtn}
        onClick={() => setShow((v) => !v)}
        disabled={props.disabled}
        tabIndex={-1}
        aria-label={show ? t("PasswordInput.hide") : t("PasswordInput.show")}
        title={show ? t("PasswordInput.hide") : t("PasswordInput.show")}
        aria-pressed={show}
      >
        <MIcon name={show ? "visibility_off" : "visibility"} size={18} />
      </button>
    </span>
  );
});

export default PasswordInput;
