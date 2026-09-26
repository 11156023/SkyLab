import { useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import { folderBackPath, folderFrontPath, folderSheets, folderTotals, sheetRole } from "./templateFolder";
import styles from "./QuickTemplateFolder.module.scss";

/* 外框要跟著實際尺寸重畫，所以量元素大小 */
function useSize(ref) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const update = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    update();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

/**
 * 快速練習的資料夾卡：資料夾是環境，裡面一張紙一台機器（露出名稱、VM/LXC、角色），
 * 前蓋寫環境名稱、說明與每人合計資源。資料夾高度固定，機器越多紙疊得越高。
 * 造型沿用復刻的 folder-card；滑入只做小預覽（前蓋微傾、紙上探），點擊進確認頁。
 */
export default function QuickTemplateFolder({ template, onOpen }) {
  const { t, i18n } = useTranslation("personal");
  const folderRef = useRef(null);
  const frontRef = useRef(null);
  const folder = useSize(folderRef);
  const front = useSize(frontRef);
  /* useId 帶冒號，SVG 的 url(#…) 參照不吃，濾掉 */
  const uid = `folder${useId().replace(/[^\w-]/g, "")}`;
  const nodes = template.nodes ?? [];
  const sheets = folderSheets(nodes);
  const { cpu, memoryGb } = folderTotals(template);
  const machines = new Intl.ListFormat(i18n.language, { type: "conjunction" }).format(nodes.map((node) => node.name));
  const meta = [
    cpu > 0 && { icon: "developer_board", text: t("QuickTemplateFolder.cpu", { count: cpu }) },
    memoryGb > 0 && { icon: "memory", text: t("QuickTemplateFolder.memory", { amount: memoryGb }) },
    template.duration_hours > 0 && { icon: "schedule", text: t("QuickTemplateFormPage.durationHours", { count: template.duration_hours }) },
  ].filter(Boolean);

  return (
    <button ref={folderRef} type="button" className={styles.folder} onClick={onOpen}
      aria-label={t("QuickTemplateFolder.aria", { name: template.name, count: nodes.length, machines })}
      aria-describedby={template.description ? `${uid}-desc` : undefined}>
      <svg className={styles.back} aria-hidden="true" viewBox={`0 0 ${folder.width || 1} ${folder.height || 1}`}>
        <defs>
          <linearGradient id={`${uid}-back`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" style={{ stopColor: "var(--color-folder-back-top)" }} />
            <stop offset="1" style={{ stopColor: "var(--color-folder-back-bottom)" }} />
          </linearGradient>
        </defs>
        {folder.width > 0 && <path fill={`url(#${uid}-back)`} d={folderBackPath(folder.width, folder.height)} />}
      </svg>

      {sheets.map((sheet) => (
        <span key={sheet.key} className={`${styles.sheet} ${sheet.more ? styles.sheetMore : ""}`}
          style={{ "--depth": sheet.depth }} aria-hidden="true">
          {sheet.more ? `+${t("HomeOverview.machineCount", { count: sheet.more })}` : <>
            <MIcon name={sheet.node.type === "lxc" || sheet.node.resource_type === "lxc" ? "terminal" : "desktop_windows"} size={15} />
            <span className={styles.sheetName} title={sheet.node.name}>{sheet.node.name}</span>
            {sheetRole(sheet.node) && <span className={styles.sheetRole}>{sheetRole(sheet.node)}</span>}
          </>}
        </span>
      ))}

      <span className={styles.shade} aria-hidden="true" />

      <span ref={frontRef} className={styles.front}>
        <svg aria-hidden="true" viewBox={`0 0 ${front.width || 1} ${front.height || 1}`}>
          <defs>
            <linearGradient id={`${uid}-front`} x1="0.2" y1="0" x2="0.8" y2="1">
              <stop offset="0.3" style={{ stopColor: "var(--color-folder-front-light)" }} />
              <stop offset="1" style={{ stopColor: "var(--color-folder-front-dark)" }} />
            </linearGradient>
            <linearGradient id={`${uid}-glint`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#fff" stopOpacity="0.22" />
              <stop offset="0.7" stopColor="#fff" stopOpacity="0" />
            </linearGradient>
          </defs>
          {front.width > 0 && <>
            <path fill={`url(#${uid}-front)`} d={folderFrontPath(front.width, front.height)} />
            <path className={styles.glint} fill={`url(#${uid}-glint)`} d={folderFrontPath(front.width, front.height)} />
          </>}
        </svg>
        <span className={styles.label}>
          <strong className={styles.name}>{template.name}</strong>
          {template.description && <span id={`${uid}-desc`} className={styles.description}>{template.description}</span>}
          {meta.length > 0 && <span className={styles.meta}>
            {meta.map((item) => <span key={item.icon}><MIcon name={item.icon} size={15} />{item.text}</span>)}
          </span>}
        </span>
      </span>
    </button>
  );
}
