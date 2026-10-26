/**
 * 樣式對照頁的資料來源：直接讀 _themes.scss 與 _variables.scss 的原始碼（Vite ?raw），
 * 解析出 CSS 自訂屬性與 SCSS 變數的名稱、分組與原始值。
 * 不另外手抄一份清單，加了新 token 這頁自動跟上；實際顏色在頁面上用 getComputedStyle 讀。
 */
import themesSource from "../../assets/styles/_themes.scss?raw";
import variablesSource from "../../assets/styles/_variables.scss?raw";

/* 區塊註解長這樣：/* ----- Backgrounds ----- *\/、/* ----- Machine kind（機器來源徽章）----- */
const GROUP_HEADER = /^\/\*\s*-{3,}\s*(.+?)\s*-{3,}/;

/** 分組標題只留「：」「（」之前的短名，其餘當說明 */
function splitHeader(text) {
  const match = text.match(/^([^（：:(]+)(.*)$/);
  if (!match) return { title: text.trim(), note: "" };
  return { title: match[1].trim(), note: match[2].replace(/^[（：:(]|[）)]$/g, "").trim() };
}

/**
 * 解析 _themes.scss 的 :root 區塊（亮色預設值）與緊接的 body 區塊（淺色陰影）。
 * 深色與風格變體只覆寫其中一部分，名稱集合以 :root 為準。
 * @returns {Array<{ title: string, note: string, tokens: Array<{ name: string, value: string }> }>}
 */
export function parseThemeTokens(source = themesSource) {
  const groups = [];
  let group = null;
  let inBlock = false;
  let blocksSeen = 0;

  const pushGroup = (title, note = "") => {
    group = { title, note, tokens: [] };
    groups.push(group);
  };

  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!inBlock) {
      if (/^(:root|body)\s*\{/.test(line)) {
        inBlock = true;
        blocksSeen += 1;
        if (blocksSeen === 2) pushGroup("Shadows", "淺色模式的陰影定義在 body 區塊，色調跟著主色走");
      }
      continue;
    }
    if (line.startsWith("}")) {
      inBlock = false;
      if (blocksSeen >= 2) break;
      continue;
    }
    const header = line.match(GROUP_HEADER);
    if (header) {
      const { title, note } = splitHeader(header[1]);
      pushGroup(title, note);
      continue;
    }
    const token = line.match(/^(--[a-z0-9-]+)\s*:\s*([^;]+);/i);
    if (token) {
      if (!group) pushGroup("Tokens");
      group.tokens.push({ name: token[1], value: token[2].trim() });
    }
  }
  return groups.filter((g) => g.tokens.length > 0);
}

/**
 * 解析 _variables.scss：每行 `$name: value;` 一筆，依區塊註解分組。
 * @returns {Array<{ group: string, name: string, value: string }>}
 */
export function parseScssVariables(source = variablesSource) {
  const vars = [];
  let group = "Variables";
  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim();
    const header = line.match(GROUP_HEADER);
    if (header) {
      group = splitHeader(header[1]).title;
      continue;
    }
    const variable = line.match(/^\$([a-z0-9-]+)\s*:\s*([^;]+);/i);
    if (variable) vars.push({ group, name: `$${variable[1]}`, value: variable[2].trim() });
  }
  return vars;
}

export const THEME_GROUPS = parseThemeTokens();
export const SCSS_VARS = parseScssVariables();

/** 取某一組 SCSS 變數（依 _variables.scss 的區塊註解名稱） */
export function scssGroup(title) {
  return SCSS_VARS.filter((v) => v.group === title);
}

/** 依名稱前綴取 SCSS 變數，例如 "$font-size-" */
export function scssByPrefix(prefix) {
  return SCSS_VARS.filter((v) => v.name.startsWith(prefix));
}

/** 名稱→原始值，給需要直接套用數值的示範用（例如字級、間距的長度） */
export function scssValue(name) {
  return SCSS_VARS.find((v) => v.name === name)?.value ?? "";
}
