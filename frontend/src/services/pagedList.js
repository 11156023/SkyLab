/**
 * 後端清單端點單次上限（le=100、le=200）比呼叫端要的筆數小時，用 skip 分頁補齊。
 *
 * fetchPage({ skip, limit }) 回 { data, count }（count 為符合條件的總數）。
 * - limit ≤ pageSize：只打一次，原樣回傳，行為跟直接呼叫一樣
 * - limit > pageSize：依 skip 逐頁抓，拿滿 limit、抓到 count、或某頁不足一頁就停；
 *   最多 maxPages 頁，避免資料異常時無限迴圈
 * 翻頁期間若有新資料插到最前面，offset 會位移造成重複，因此依 id 去重。
 */
export async function fetchAllPages(fetchPage, { limit, skip = 0, pageSize, maxPages = 50 }) {
  if (limit <= pageSize) return fetchPage({ skip, limit });

  const data = [];
  const seen = new Set();
  let count = null;
  let offset = skip;
  for (let page = 0; page < maxPages && data.length < limit; page += 1) {
    const size = Math.min(pageSize, limit - data.length);
    const res = await fetchPage({ skip: offset, limit: size });
    const rows = res?.data ?? [];
    if (Number.isFinite(res?.count)) count = res.count;
    for (const row of rows) {
      const key = row?.id;
      if (key != null) {
        if (seen.has(key)) continue;
        seen.add(key);
      }
      data.push(row);
    }
    offset += rows.length;
    if (rows.length < size || (count !== null && offset >= count)) break;
  }
  return { data: data.slice(0, limit), count: count ?? data.length };
}
