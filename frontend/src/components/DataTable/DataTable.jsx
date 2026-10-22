import { flexRender } from "@tanstack/react-table";
import { useTranslation } from "react-i18next";

/**
 * TanStack Table 的共用渲染：表頭與列都從 table 實例來，樣式仍是各頁自己的 SCSS Module class
 * （全站表格 mixin 見 website/guides/frontend-style-guide.md 的 Tables）。輸出的 DOM 與手寫的
 * `<table><thead>…</thead><tbody>…</tbody></table>` 相同，換上來不改畫面。
 *
 * 欄位的樣式掛在 columnDef.meta：
 *   meta: { thClassName, tdClassName, thProps, tdProps }
 *   tdClassName／tdProps 可以是 (row) => …（每列不同的 class、title）
 *
 * 會影響畫面的頁面狀態（操作中、展開、勾選、目前時間…）建議經 renderRow 的 rowState 或放進 data：
 * DataTableCells／DataTableHead 已退出 React Compiler 快取，預設列在 meta／columns 改變時會重算；
 * 但 renderRow 的列元件是頁面自己的（會被編譯、依 props 快取），只改 meta 時它不會重畫，狀態要走它的 props／rowState。
 *
 * 列：
 *   - 預設每列一個 `<tr>`，class 由 `rowClassName(row)`、其他屬性由 `rowProps(row)` 決定
 *   - 列本身有互動狀態（選單、操作中…）時傳 `renderRow(row)`，由頁面的列元件自己畫 `<tr>`，
 *     列元件裡用 `<DataTableCells row={row} rowState={…} />` 把狀態交給各欄的 cell
 *
 * `children` 會放在 `<tbody>` 最前面（例如群組列）；`colgroup` 原樣放在表頭前；
 * `bodyProps` 給 `<tbody>`（虛擬化的 ref、data-* 屬性）；`before`／`after` 放在列的前後（虛擬化的空白列）。
 */
export default function DataTable({
  table,
  className,
  colgroup = null,
  headerRowClassName,
  rowClassName,
  rowProps,
  renderRow,
  bodyProps,
  children = null,
  before = null,
  after = null,
  rows,
  hideHeader = false,
  ...rest
}) {
  /* 同 DataTableCells：rows 陣列只在 data 變時換，Compiler 依它快取的話，只改 meta／columns 時整段列不會重畫 */
  "use no memo";
  const bodyRows = rows ?? table.getRowModel().rows;
  return (
    <table className={className} {...rest}>
      {colgroup}
      {!hideHeader && <DataTableHead table={table} rowClassName={headerRowClassName} />}
      <tbody {...bodyProps}>
        {children}
        {before}
        {bodyRows.map((row) => (renderRow
          ? renderRow(row)
          : (
            <tr key={row.id} className={rowClassName?.(row)} {...rowProps?.(row)}>
              <DataTableCells row={row} />
            </tr>
          )))}
        {after}
      </tbody>
    </table>
  );
}

/** 表頭：每個 header group 一列，`th` 的 class 取 columnDef.meta.thClassName；同 DataTableCells 訂閱語系 */
export function DataTableHead({ table, rowClassName }) {
  "use no memo"; // 同 DataTableCells：header 物件跨 render 不變，快取後 meta／columns 改了表頭不會更新
  const { i18n } = useTranslation();
  const lang = i18n?.language;
  return (
    <thead>
      {table.getHeaderGroups().map((headerGroup) => (
        <tr key={headerGroup.id} className={rowClassName}>
          {headerGroup.headers.map((header) => {
            const meta = header.column.columnDef.meta ?? {};
            return (
              <th
                key={header.id}
                className={meta.thClassName}
                colSpan={header.colSpan > 1 ? header.colSpan : undefined}
                {...meta.thProps}
              >
                {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, { ...header.getContext(), lang })}
              </th>
            );
          })}
        </tr>
      ))}
    </thead>
  );
}

/**
 * 一列的所有儲存格。`rowState` 會跟 cell context 一起傳進每欄的 cell（cell 是元件，可以直接用 hook）：
 *   cell: ({ row, rowState }) => …
 *
 * 語系：formatDate／formatNumber 這類 helper 是呼叫當下才讀 i18n.language，靠重繪換格式；
 * 但資料沒變時，編譯後的列與儲存格依 row 快取不會重繪，切換語言後日期會一直是舊格式。
 * 所以這裡訂閱語系，並把 `lang` 放進傳給 cell 的 props：語言一換，每格都拿到新的 props 而重繪。
 */
export function DataTableCells({ row, rowState }) {
  /* 不讓 React Compiler 依 row 快取：v9 的 row 物件跨 render 不變，快取後只改 useTable 的 meta 或 columns
     時儲存格不會重繪（已實測）。外層一重畫就整列重算，與手寫表格相同 */
  "use no memo";
  const { i18n } = useTranslation();
  const lang = i18n?.language;
  return row.getAllCells().map((cell) => {
    const meta = cell.column.columnDef.meta ?? {};
    const tdClassName = typeof meta.tdClassName === "function" ? meta.tdClassName(row) : meta.tdClassName;
    const tdProps = typeof meta.tdProps === "function" ? meta.tdProps(row) : meta.tdProps;
    return (
      <td key={cell.id} className={tdClassName} {...tdProps}>
        {flexRender(cell.column.columnDef.cell, { ...cell.getContext(), rowState, lang })}
      </td>
    );
  });
}
