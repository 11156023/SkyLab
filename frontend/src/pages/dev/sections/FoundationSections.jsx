import { useEffect, useState } from "react";
import { useTheme } from "../../../contexts/ThemeContext";
import { Demo, RuleList, Section } from "../DevSection";
import { THEME_GROUPS, scssByPrefix, scssGroup, scssValue } from "../tokens";
import styles from "../DevStylePage.module.scss";

const ALL_TOKEN_NAMES = THEME_GROUPS.flatMap((g) => g.tokens.map((t) => t.name));

/**
 * 讀 body 上每個 CSS 自訂屬性目前的 computed 值。
 * ThemeProvider 在 effect 裡切 body.dark／data-style，子元件的 effect 先跑，
 * 所以延到下一個 frame 再讀，才拿得到切換後的值。
 */
function useComputedTokens() {
  const { theme, style, primaryColor } = useTheme();
  const [values, setValues] = useState({});
  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const computed = getComputedStyle(document.body);
      const next = {};
      for (const name of ALL_TOKEN_NAMES) next[name] = computed.getPropertyValue(name).trim();
      setValues(next);
    });
    return () => cancelAnimationFrame(id);
  }, [theme, style, primaryColor]);
  return values;
}

function Swatch({ name, value }) {
  const isShadow = name.startsWith("--shadow-") && !name.startsWith("--shadow-color");
  const isFilter = name.endsWith("-backdrop-filter");
  const isNumber = /^[\d.]+$/.test(value);
  let preview;
  if (isShadow) {
    preview = <i className={styles.swatchShadow} style={{ boxShadow: `var(${name})` }} aria-hidden="true" />;
  } else if (isFilter || isNumber) {
    preview = <i className={styles.swatchColor} style={{ background: "var(--color-hover)" }} aria-hidden="true" />;
  } else {
    preview = <i className={styles.swatchColor} style={{ background: `var(${name})` }} aria-hidden="true" />;
  }
  return (
    <div className={styles.swatch}>
      {preview}
      <div className={styles.swatchText}>
        <span className={styles.swatchName}>{name}</span>
        <span className={styles.swatchValue} title={value}>{value || "—"}</span>
      </div>
    </div>
  );
}

export function ColorsSection() {
  const values = useComputedTokens();
  return (
    <Section
      id="colors"
      title="顏色 Colors"
      refs={["frontend/src/assets/styles/_themes.scss", "var(--color-*)", "body.dark", "body[data-style]"]}
      intro="名稱與分組直接從 _themes.scss 解析出來，色值是此刻 body 上的 computed 值：切換頂欄的亮暗、風格會即時換；主色換過時 ThemeContext 注入的覆寫也會反映在這裡。元件 SCSS 一律用 var(--color-*)，不寫死 HEX。"
    >
      {THEME_GROUPS.map((group) => (
        <div key={group.title} className={styles.swatchGroup}>
          <div className={styles.swatchGroupHead}>
            <h3 className={styles.swatchGroupTitle}>{group.title}</h3>
            {group.note && <span className={styles.caption}>{group.note}</span>}
          </div>
          <div className={styles.swatchGrid}>
            {group.tokens.map((token) => (
              <Swatch key={token.name} name={token.name} value={values[token.name] ?? token.value} />
            ))}
          </div>
        </div>
      ))}
      <RuleList
        items={[
          "狀態色五種：success／info／pending／danger／status-neutral；黃橙只給「待審核」語意，警示一律紅色 --color-danger。",
          "--color-primary-dark 只能當底色（深色模式仍是深色）；品牌色文字／邊框用 --color-primary-on-surface。",
          "終端機、程式碼區塊、404／斷線插圖的純白雲等固定色是規範明列的例外，其餘不得寫死色碼。",
        ]}
      />
    </Section>
  );
}

const SAMPLE = "SkyLab 校園私有雲 Campus Cloud 0123456789";

export function TypeSection() {
  const sizes = scssByPrefix("$font-size-");
  const weights = scssByPrefix("$font-weight-");
  const family = scssValue("$font-family-base");
  const lineHeight = scssValue("$line-height-base");
  return (
    <Section
      id="type"
      title="字體 Type"
      refs={["frontend/src/assets/styles/_variables.scss", "$font-size-*", "$font-weight-*", "$line-height-base"]}
      intro={`字族 ${family}；全站行高 ${lineHeight}。數值從 _variables.scss 解析。`}
    >
      <div className={styles.demoGrid}>
        <Demo label="字級" refs={["$font-size-10 … 32"]} note="$font-size-10 僅限資料密集區的次要標籤；一般內文最小 12。">
          {sizes.map((v) => (
            <div key={v.name} className={styles.typeRow}>
              <span className={styles.typeMeta}>{v.name} = {v.value}</span>
              <span className={styles.typeSample} style={{ fontSize: v.value }}>{SAMPLE}</span>
            </div>
          ))}
        </Demo>
        <Demo label="字重" refs={["$font-weight-400 / 500 / 700"]}>
          {weights.map((v) => (
            <div key={v.name} className={styles.typeRow}>
              <span className={styles.typeMeta}>{v.name} = {v.value}</span>
              <span className={styles.typeSample} style={{ fontWeight: v.value, fontSize: scssValue("$font-size-16") }}>{SAMPLE}</span>
            </div>
          ))}
        </Demo>
        <Demo label="文字色階" refs={["--color-text-*"]} note="標題用 text-primary，一般文字 text，次要 text-secondary，輔助說明與 placeholder 用 text-muted。">
          {["--color-text-primary", "--color-text", "--color-text-secondary", "--color-text-muted", "--color-primary-on-surface"].map((name) => (
            <div key={name} className={styles.textTone}>
              <span className={styles.typeMeta}>{name}</span>
              <span style={{ color: `var(${name})` }}>SkyLab 校園私有雲</span>
            </div>
          ))}
        </Demo>
      </div>
    </Section>
  );
}

function useViewportWidth() {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return width;
}

export function DimsSection() {
  const width = useViewportWidth();
  const spacing = scssByPrefix("$spacing-");
  const radius = scssByPrefix("$radius-");
  const breakpoints = scssByPrefix("$breakpoint-");
  const layout = scssGroup("Layout Sizes");
  const motion = scssGroup("Motion");
  const form = scssGroup("Form");
  return (
    <Section
      id="dims"
      title="尺寸 Dims"
      refs={["frontend/src/assets/styles/_variables.scss", "$spacing-*", "$radius-*", "$breakpoint-*", "@include respond-to()"]}
      intro="間距、圓角、斷點、版面尺寸與動畫時間都是 SCSS 結構變數，元件 SCSS 不得自行新增變數。"
    >
      <div className={styles.demoGrid}>
        <Demo label="間距" refs={["$spacing-4 … 48"]}>
          {spacing.map((v) => (
            <div key={v.name} className={styles.dimRow}>
              <span className={styles.dimName}>{v.name}</span>
              <span className={styles.dimValue}>{v.value}</span>
              <i className={styles.dimBar} style={{ width: v.value }} aria-hidden="true" />
            </div>
          ))}
        </Demo>
        <Demo label="圓角" refs={["$radius-8 / 12 / 16 / pill"]} note="卡片 16、小區塊與按鈕 8、徽章 pill。">
          {radius.map((v) => (
            <div key={v.name} className={styles.dimRow}>
              <span className={styles.dimName}>{v.name}</span>
              <span className={styles.dimValue}>{v.value}</span>
              <i className={styles.dimRadius} style={{ borderRadius: v.value }} aria-hidden="true" />
            </div>
          ))}
        </Demo>
        <Demo label="斷點" refs={["respond-to(sm|md|lg|xl)", "respond-below()"]} note={`目前視窗寬 ${width}px；綠色的是現在已達到的斷點。側欄固定／抽屜切換點是 $breakpoint-sidebar。`}>
          {breakpoints.map((v) => {
            const hit = width >= parseInt(v.value, 10);
            return (
              <div key={v.name} className={styles.dimRow}>
                <span className={styles.dimName}>{v.name}</span>
                <span className={styles.dimValue}>{v.value}</span>
                <span className={hit ? styles.dimBreakpointHit : styles.dimBreakpoint}>{hit ? "已達到" : "未達到"}</span>
              </div>
            );
          })}
        </Demo>
        <Demo label="版面與表單尺寸" refs={["$header-height", "$sidebar-width", "$form-control-padding-x"]}>
          {[...layout, ...form].map((v) => (
            <div key={v.name} className={styles.dimRow}>
              <span className={styles.dimName}>{v.name}</span>
              <span className={styles.dimValue}>{v.value}</span>
              <span />
            </div>
          ))}
        </Demo>
        <Demo label="動畫時間" refs={["$transition-base", "$transition-slow"]} note="進場 slideUp 0.18s／fadeIn 0.15s；離場用 setTimeout＋transition，不用 onAnimationEnd。">
          {motion.map((v) => (
            <div key={v.name} className={styles.dimRow}>
              <span className={styles.dimName}>{v.name}</span>
              <span className={styles.dimValue}>{v.value}</span>
              <span />
            </div>
          ))}
        </Demo>
      </div>
    </Section>
  );
}

/* 規範文件的層級表；$z-* 兩個值從 _variables.scss 讀 */
const Z_LAYERS = [
  { label: "基礎卡片", value: "1", note: "一般卡片" },
  { label: "卡片 hover／選單", value: "50", note: "容器內的 Dropdown" },
  { label: "浮動求助鈕", value: "90", note: "UserGuide 入口，介於選單與 Sticky Header 之間" },
  { label: "Sticky Header", value: "100", note: "頁面頂部導覽列、本頁頂欄" },
  { label: "Portal 浮層選單", value: "150", note: "components/PowerMenu：portal 到 body、position: fixed" },
  { label: "Dialog／Modal 遮罩", value: "300", note: "components/Modal 的 .overlay" },
  { label: "Toast／Tooltip", value: "400", note: "sonner Toaster" },
  { label: "AI 助手", value: "$z-ai-assistant", note: "浮在所有一般對話框之上；新遮罩要 @include dialog-overlay-avoid-ai" },
  { label: "畫面型對話框", value: "$z-screen-dialog", note: "VNC、終端機、課堂觀看：AI 讀不到的畫面，直接蓋過助手（Modal layer=\"screen\"）" },
  { label: "導覽 UserGuide", value: "3000–3199", note: "聚光層 3100、示範視窗 3099；保留給 UserGuide" },
];

export function ZIndexSection() {
  const rows = Z_LAYERS.map((layer) => ({
    ...layer,
    resolved: layer.value.startsWith("$") ? `${scssValue(layer.value)}（${layer.value}）` : layer.value,
  }));
  return (
    <Section
      id="zindex"
      title="層級 z-index"
      refs={["$z-ai-assistant", "$z-screen-dialog", "@include dialog-overlay-avoid-ai", "createPortal(document.body)"]}
      intro="backdrop-filter 與 transform 會建立新的 stacking context：浮層若可能超出玻璃容器，一律 createPortal 到 body 並用 position: fixed（PowerMenu、Modal 都是）。"
    >
      <div className={styles.demoGrid}>
        <Demo label="層級表" wide>
          <table className={styles.zTable}>
            <thead>
              <tr><th>層級</th><th>值</th><th>用途</th></tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label}>
                  <td>{row.label}</td>
                  <td><span className={styles.zValue}>{row.resolved}</span></td>
                  <td>{row.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Demo>
        <Demo label="疊層示意" wide note="由左到右越上層；數值只是示意，實際層級以上表為準。">
          <div className={styles.zStack} aria-hidden="true">
            {rows.slice(0, 7).map((row, index) => (
              <div
                key={row.label}
                className={styles.zLayer}
                style={{ left: index * 96, top: index * 8, zIndex: index + 1 }}
              >
                {row.value} {row.label}
              </div>
            ))}
          </div>
        </Demo>
      </div>
    </Section>
  );
}
