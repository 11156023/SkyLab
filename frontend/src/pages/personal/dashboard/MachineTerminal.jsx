import { useEffect, useState } from "react";
import { TERMINAL_ROWS, padKey } from "./terminalLines";
import styles from "./MachineCard.module.scss";

const BOOT_STEP_MS = 420;
const SYSTEMD_TAG = { OK: "  OK  ", FAIL: "FAILED" };

const prefersReducedMotion = () =>
  typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

/* 十格文字模式的用量條：一格 1ch，跟前後的字對齊；sweep 是沒有進度可讀時的來回掃描 */
function Cells({ share = 0, high = false, sweep = false }) {
  const lit = share > 0 ? Math.max(1, Math.round(share * 10)) : 0;
  const className = [styles.bar, high ? styles.barHigh : "", sweep ? styles.barSweep : ""].filter(Boolean).join(" ");
  return (
    <span className={className} aria-hidden="true">
      {Array.from({ length: 10 }, (_, index) => (
        <i key={index} className={index < lit ? styles.on : undefined} style={sweep ? { "--i": index } : undefined} />
      ))}
    </span>
  );
}

function Row({ row }) {
  const tone = row.tone ? styles[row.tone] : undefined;
  switch (row.kind) {
    case "comment":
      return <div className={`${styles.row} ${styles.dim}`}>{row.text}</div>;
    case "kv":
      return (
        <div className={`${styles.row} ${row.fresh ? styles.fresh : ""}`}>
          <span className={styles.key}>{padKey(row.key)}</span>
          <span className={tone}>{row.value}</span>
        </div>
      );
    case "meter":
      return (
        <div className={styles.row}>
          <span className={styles.key}>{padKey(row.key)}</span>
          <Cells share={row.share} high={row.high} />
          <span className={row.high ? styles.warn : undefined}>{` ${row.label}`}</span>
        </div>
      );
    case "log":
      return (
        <div className={styles.row}>
          [<span className={row.tag === "OK" ? styles.ok : styles.danger}>{SYSTEMD_TAG[row.tag]}</span>] {row.text}
        </div>
      );
    default:
      return <div className={`${styles.row} ${tone ?? ""}`}>{row.text}</div>;
  }
}

/**
 * 機器卡內的終端畫面：固定七行高，開機時把 boot 逐行印出（捲動保留最新幾行），
 * footer 貼齊底部（關機標記、游標、建立中的掃描條）。
 */
export default function MachineTerminal({ rows, boot, footer, off = false, label }) {
  const bootLength = boot?.length ?? 0;
  const [revealed, setRevealed] = useState(0);

  useEffect(() => {
    if (!bootLength) return undefined;
    if (prefersReducedMotion()) {
      setRevealed(bootLength);
      return undefined;
    }
    setRevealed(0);
    const timer = setInterval(() => {
      setRevealed((count) => (count >= bootLength ? count : count + 1));
    }, BOOT_STEP_MS);
    return () => clearInterval(timer);
  }, [bootLength]);

  /* 開機訊息只留得下「七行扣掉標頭與底列」的空間，超過就像終端一樣往上捲 */
  const bootRoom = TERMINAL_ROWS - rows.length - (footer ? 1 : 0);
  const bootStart = Math.max(0, revealed - bootRoom);
  const bootRows = boot ? boot.slice(bootStart, revealed) : [];

  return (
    <div className={`${styles.terminal} ${off ? styles.off : ""}`} role="group" aria-label={label}>
      {rows.map((row, index) => <Row key={`${row.kind}-${row.key ?? index}`} row={row} />)}
      {bootRows.map((row, index) => <Row key={`boot-${bootStart + index}`} row={row} />)}
      {footer && (
        <div className={`${styles.row} ${styles.foot} ${footer.tone ? styles[footer.tone] : ""}`}>
          {footer.progress && <Cells sweep />}
          {footer.text}
          {footer.cursor && <span className={styles.cursor} aria-hidden="true" />}
        </div>
      )}
    </div>
  );
}
