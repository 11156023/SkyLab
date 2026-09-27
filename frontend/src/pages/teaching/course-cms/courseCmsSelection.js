/** 重新載入清單後，選取項目仍存在才保留，否則清掉（例如剛被刪除的路徑） */
export function pruneSelection(rows, cur) {
  if (cur == null) return null;
  return (rows ?? []).some((row) => row.id === cur) ? cur : null;
}
