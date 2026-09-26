import { useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import useAnchoredMenu from "../../hooks/useAnchoredMenu";
import { COMMON_PORTS } from "../ReverseProxyRuleModal/ReverseProxyRuleModal";
import styles from "./ConnectionDialog.module.scss";

const MENU_MIN_WIDTH = 280;

/**
 * Port 輸入框，可選擇帶常用 port 下拉。
 * 取代原生 <input type="number" list>：數字框會把 ↑↓ 拿去加減數值（打不開建議、還會改到 port），
 * datalist 又依目前的值過濾選項（值是 80 時只剩 80／8000／8080），各瀏覽器外觀也不一致。
 * 這裡用只收數字的文字框，搭配自訂選單：永遠列出全部常用 port，↑↓ 選、Enter 套用、Esc 收起。
 *
 * @param {boolean} suggestions 是否帶常用 port 下拉（外部 port 這類自訂號碼的欄位傳 false）
 */
export default function PortInput({
  id,
  value,
  onChange,
  placeholder,
  disabled = false,
  invalid = false,
  suggestions = true,
  className = "",
}) {
  const { t } = useTranslation("components");
  const wrapRef = useRef(null);
  const inputRef = useRef(null);
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  const openMenu = () => {
    if (disabled || !suggestions) return;
    const current = COMMON_PORTS.findIndex((p) => p.value === String(value));
    setActive(current >= 0 ? current : 0);
    setOpen(true);
  };
  const closeMenu = () => setOpen(false);

  const pick = (port) => {
    onChange(port);
    closeMenu();
    inputRef.current?.focus();
  };

  const handleKeyDown = (event) => {
    if (!suggestions) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openMenu();
        return;
      }
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((i) => (i + step + COMMON_PORTS.length) % COMMON_PORTS.length);
    } else if (event.key === "Enter" && open && active >= 0) {
      /* 選單開著時 Enter 是「套用這個 port」，不是送出整張表單 */
      event.preventDefault();
      pick(COMMON_PORTS[active].value);
    } else if (event.key === "Escape" && open) {
      /* 只收起選單，不讓對話框的 Esc 監聽把整個對話框關掉 */
      event.stopPropagation();
      closeMenu();
    }
  };

  return (
    <div ref={wrapRef} className={`${styles.portCombo} ${className}`}>
      <input
        ref={inputRef}
        id={id}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        data-port-input=""
        className={`${styles.portInput} ${suggestions ? styles.portInputWithToggle : ""} ${invalid ? styles.portInputInvalid : ""}`}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        role={suggestions ? "combobox" : undefined}
        aria-expanded={suggestions ? open : undefined}
        aria-controls={suggestions && open ? listId : undefined}
        aria-autocomplete={suggestions ? "list" : undefined}
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        /* 先濾掉非數字再截 5 位：用 maxLength 的話瀏覽器會先截原始文字，
           貼上「port 8080」會只剩「port 」、濾完變空白 */
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 5))}
        onKeyDown={handleKeyDown}
        onBlur={closeMenu}
      />
      {suggestions && (
        <button
          type="button"
          className={styles.portToggle}
          tabIndex={-1}
          disabled={disabled}
          aria-label={t("ConnectionDialog.commonPorts")}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            if (open) {
              closeMenu();
              return;
            }
            /* 焦點放進輸入框：之後 ↑↓ 可以選、點外面會收起 */
            inputRef.current?.focus();
            openMenu();
          }}
        >
          <MIcon name={open ? "expand_less" : "expand_more"} size={18} />
        </button>
      )}
      {open && (
        <PortMenu
          anchorRef={wrapRef}
          listId={listId}
          active={active}
          value={String(value)}
          onHover={setActive}
          onPick={pick}
          onClose={closeMenu}
        />
      )}
    </div>
  );
}

function PortMenu({ anchorRef, listId, active, value, onHover, onPick, onClose }) {
  const { t } = useTranslation("components");
  const width = Math.max(anchorRef.current?.offsetWidth ?? 0, MENU_MIN_WIDTH);
  const { ref, pos } = useAnchoredMenu({ anchorRef, onClose, width, align: "left" });

  /* portal 到 body：對話框有 backdrop-filter，fixed 選單留在裡面會被困住 */
  return createPortal(
    <ul
      ref={ref}
      id={listId}
      role="listbox"
      className={styles.portMenu}
      style={pos ? { top: pos.top, left: pos.left, width } : { top: 0, left: 0, width, visibility: "hidden" }}
    >
      {COMMON_PORTS.map((port, index) => (
        <li
          key={port.value}
          id={`${listId}-${index}`}
          role="option"
          aria-selected={port.value === value}
          className={`${styles.portMenuItem} ${index === active ? styles.portMenuItemActive : ""}`}
          onMouseDown={(e) => e.preventDefault()}
          onMouseEnter={() => onHover(index)}
          onClick={() => onPick(port.value)}
        >
          {t(port.labelKey)}
          {port.value === value && <MIcon name="check" size={16} />}
        </li>
      ))}
    </ul>,
    document.body,
  );
}
