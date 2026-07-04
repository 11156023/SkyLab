/**
 * RrdChart.jsx
 * recharts AreaChart 包裝，供資源監控頁、資源詳情監控分頁、教學熱圖共用。
 *
 * Props:
 *   - data:   [{ time: "14:05", <seriesKey>: number|null, ... }]
 *   - series: [{ key, label, color }] — color 可傳 "--css-var" 名稱（執行期解析主題色）或色碼
 *   - unit:   y 軸與 tooltip 的單位字串（如 "%"）
 *   - height: 圖高，預設 200
 *   - title:  圖表上方小標（選填）
 */

import { useId } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import styles from "./RrdChart.module.scss";

/** 解析主題 CSS 變數為實際色值（SVG 屬性不支援 var()），從 body 讀以支援 body.dark 覆蓋 */
function resolveColor(color) {
  if (typeof color === "string" && color.startsWith("--")) {
    const value = getComputedStyle(document.body).getPropertyValue(color).trim();
    return value || "#2b4d98";
  }
  return color ?? "#2b4d98";
}

export default function RrdChart({ data, series, unit = "", height = 200, title }) {
  // useId 可能含冒號等非法字元，清掉才能安全用於 SVG url(#...)
  const gradientId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const axisColor = resolveColor("--color-text-muted");
  const gridColor = resolveColor("--color-border");

  const tooltipStyle = {
    borderRadius: 8,
    border: `1px solid ${gridColor}`,
    background: resolveColor("--color-surface"),
    color: resolveColor("--color-text"),
    fontSize: 12,
  };

  return (
    <div className={styles.chart}>
      {title && <p className={styles.chartTitle}>{title}</p>}
      {!data || data.length === 0 ? (
        <div className={styles.empty} style={{ height }}>
          尚無趨勢資料
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <defs>
              {series.map((s) => (
                <linearGradient
                  key={s.key}
                  id={`${gradientId}-${s.key}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop offset="5%" stopColor={resolveColor(s.color)} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={resolveColor(s.color)} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
            <XAxis
              dataKey="time"
              tick={{ fontSize: 11, fill: axisColor }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[0, (max) => Math.max(Math.ceil(max * 1.3), 1)]}
              tickFormatter={(v) => `${v}${unit}`}
              tick={{ fontSize: 11, fill: axisColor }}
              axisLine={false}
              tickLine={false}
              width={48}
            />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(value, name) => [`${Number(value).toFixed(2)}${unit}`, name]}
              cursor={{ stroke: gridColor, strokeDasharray: "4 4" }}
            />
            {series.map((s) => (
              <Area
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={resolveColor(s.color)}
                strokeWidth={2}
                fill={`url(#${gradientId}-${s.key})`}
                dot={false}
                activeDot={{ r: 4 }}
                connectNulls
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
