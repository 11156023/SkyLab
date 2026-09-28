import { expect, test, vi } from "vitest";
import { fetchAllPages } from "./pagedList";

test("limit 不超過一頁時只打一次並原樣回傳", async () => {
  const response = { data: [{ id: 1 }], count: 42 };
  const fetchPage = vi.fn().mockResolvedValue(response);
  await expect(fetchAllPages(fetchPage, { limit: 50, pageSize: 100 })).resolves.toBe(response);
  expect(fetchPage).toHaveBeenCalledWith({ skip: 0, limit: 50 });
});

test("翻頁時新資料插入造成的重複以 id 去掉", async () => {
  const fetchPage = vi.fn()
    .mockResolvedValueOnce({ data: [{ id: "a" }, { id: "b" }], count: 3 })
    .mockResolvedValueOnce({ data: [{ id: "b" }], count: 3 });
  const res = await fetchAllPages(fetchPage, { limit: 10, pageSize: 2 });
  expect(res.data.map((row) => row.id)).toEqual(["a", "b"]);
  expect(fetchPage).toHaveBeenCalledTimes(2);
});

test("最多抓 maxPages 頁，後端一直回滿頁也不會無限迴圈", async () => {
  let n = 0;
  const fetchPage = vi.fn(async ({ limit }) => ({
    data: Array.from({ length: limit }, () => ({ id: n++ })),
    count: 1_000_000,
  }));
  const res = await fetchAllPages(fetchPage, { limit: 1_000_000, pageSize: 10, maxPages: 3 });
  expect(fetchPage).toHaveBeenCalledTimes(3);
  expect(res.data).toHaveLength(30);
});
