import MIcon from "../MIcon";
import styles from "./AiPveChat.module.scss";

/* 後端 _SYSTEM_PROMPT 規定診斷回覆的狀態標記只有這四個，這裡照著收斂成徽章。
   ⚠ 沒有 variation selector 的版本也收，部分模型不會補 U+FE0F。 */
const STATUS_MARKERS = [
  { marker: "✅", tone: "ok", icon: "check_circle" },
  { marker: "⚠️", tone: "warn", icon: "warning" },
  { marker: "⚠", tone: "warn", icon: "warning" },
  { marker: "❌", tone: "bad", icon: "cancel" },
  { marker: "❓", tone: "unknown", icon: "help" },
];

/* 分層標籤同樣是 prompt 固定的五層，順序即由外而內。 */
const LAYER_TAGS = ["PVE", "VM", "OS", "Service", "Application"];

/* 固定診斷格式的段落標題，配一個能一眼分辨段落用途的圖示。 */
const SECTION_ICONS = {
  診斷結論: "fact_check",
  分層結果: "layers",
  主要證據: "plagiarism",
  建議下一步: "lightbulb",
  資料缺口: "help_center",
  建議操作: "lightbulb",
};

/** 取出 React children 的純文字；含非文字節點（連結、粗體等）時回傳 null 交還原樣渲染。 */
export function toPlainText(children) {
  const parts = Array.isArray(children) ? children : [children];
  let text = "";
  for (const part of parts) {
    if (part === null || part === undefined || part === false) continue;
    if (typeof part === "string" || typeof part === "number") {
      text += String(part);
      continue;
    }
    return null;
  }
  return text;
}

/** 「✅ 正常」→ 狀態徽章；標記後面的說明文字保留。 */
export function parseStatusCell(text) {
  const value = String(text ?? "").trim();
  for (const entry of STATUS_MARKERS) {
    if (!value.startsWith(entry.marker)) continue;
    return { ...entry, label: value.slice(entry.marker.length).trim() };
  }
  return null;
}

/** 「[PVE]」→ 分層晶片。整格只有標籤時才算，避免動到句子裡的方括號。 */
export function parseLayerCell(text) {
  const value = String(text ?? "").trim();
  const match = /^\[([A-Za-z]+)\]$/.exec(value);
  if (!match) return null;
  const layer = LAYER_TAGS.find((tag) => tag.toLowerCase() === match[1].toLowerCase());
  return layer ? { layer } : null;
}

/** 「87.3%」→ 量表。整格只有百分比時才算，句子裡的百分比不動。 */
export function parsePercentCell(text) {
  const match = /^(\d+(?:\.\d+)?)\s*%$/.exec(String(text ?? "").trim());
  if (!match) return null;
  const percent = Number(match[1]);
  if (!Number.isFinite(percent) || percent < 0 || percent > 100) return null;
  return { percent, label: `${match[1]}%` };
}

/** 「## 診斷結論」→ 段落圖示。標題可能帶編號或冒號，取得到就配圖。 */
export function sectionIcon(text) {
  const value = String(text ?? "").trim();
  const hit = Object.keys(SECTION_ICONS).find((name) => value.includes(name));
  return hit ? SECTION_ICONS[hit] : null;
}

function StatusBadge({ tone, icon, label }) {
  return (
    <span className={`${styles.cellStatus} ${styles[`cellStatus_${tone}`]}`}>
      <MIcon name={icon} size={15} />
      {label && <span>{label}</span>}
    </span>
  );
}

function LayerChip({ layer }) {
  return (
    <span className={`${styles.cellLayer} ${styles[`cellLayer_${layer.toLowerCase()}`]}`}>
      {layer}
    </span>
  );
}

/* 量表只做視覺化，不上語意色：門檻由監控設定決定，對話這端拿不到，
   自己編一組紅黃綠會和後端的異常判定打架。語意一律留給 AI 給的狀態標記。 */
function PercentMeter({ percent, label }) {
  return (
    <span className={styles.cellMeter}>
      <span className={styles.cellMeterTrack}>
        <span className={styles.cellMeterFill} style={{ width: `${percent}%` }} />
      </span>
      <span className={styles.cellMeterValue}>{label}</span>
    </span>
  );
}

/** 把一格表格內容轉成對應的 UI；認不出來的回傳 null，由呼叫端沿用原本文字。 */
export function renderRichCell(text) {
  const status = parseStatusCell(text);
  if (status) return <StatusBadge {...status} />;
  const layer = parseLayerCell(text);
  if (layer) return <LayerChip {...layer} />;
  const percent = parsePercentCell(text);
  if (percent) return <PercentMeter {...percent} />;
  return null;
}

/** ReactMarkdown 的 td/th：純文字格才嘗試轉 UI，其餘原樣渲染。 */
function cellRenderer(Tag) {
  return function Cell({ children, node: _node, ...rest }) {
    const text = toPlainText(children);
    const rich = text === null ? null : renderRichCell(text);
    return <Tag {...rest}>{rich ?? children}</Tag>;
  };
}

function headingRenderer(Tag) {
  return function Heading({ children, node: _node, className, ...rest }) {
    const icon = sectionIcon(toPlainText(children) ?? "");
    if (!icon) return <Tag {...rest} className={className}>{children}</Tag>;
    return (
      <Tag {...rest} className={[className, styles.mdHeadingWithIcon].filter(Boolean).join(" ")}>
        <MIcon name={icon} size={16} />
        {children}
      </Tag>
    );
  };
}

/** 傳給 ReactMarkdown 的 components，模組層建好避免每次 render 產生新物件。 */
export const AI_PVE_MARKDOWN_COMPONENTS = {
  td: cellRenderer("td"),
  th: cellRenderer("th"),
  h1: headingRenderer("h1"),
  h2: headingRenderer("h2"),
  h3: headingRenderer("h3"),
};
