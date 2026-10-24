import {
  createSortedRowModel,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_datetime,
  sortFn_text,
  tableFeatures,
} from "@tanstack/react-table";

/**
 * TanStack Table v9 的 feature 組合（v9 規定要傳 `features`，且官方建議定義在元件外面）。
 *
 * - baseFeatures：只渲染欄位與列（核心 row model 是自動的），全站多數表格用這個
 * - sortableFeatures：需要前端排序的表格；`sortFn: "auto"` 會依值的型別解析成這三支，所以三支都要登記
 *
 * 伺服器分頁的表格（稽核日誌、AI 憑證…）頁碼狀態仍由頁面管、換頁改的是查詢參數，
 * 表格只負責渲染當頁的列，用 baseFeatures 即可。
 */
export const baseFeatures = tableFeatures({});

export const sortableFeatures = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: {
    alphanumeric: sortFn_alphanumeric,
    text: sortFn_text,
    datetime: sortFn_datetime,
  },
});
