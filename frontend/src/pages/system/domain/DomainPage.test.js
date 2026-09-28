import { describe, expect, test } from "vitest";
import { DNS_TYPES, buildRecordBody, pickSelectedZone, recordTypeOptions } from "./DomainPage";

const baseForm = {
  type: "A",
  name: " www ",
  content: " 1.1.1.1 ",
  ttl: "1",
  proxied: false,
  comment: "",
  priority: 10,
};

describe("DomainPage zone 選取", () => {
  test("重新載入後原 zone 還在就沿用（換成新物件）", () => {
    const prev = { id: "b", name: "old-b" };
    const items = [{ id: "a" }, { id: "b", name: "new-b" }];
    expect(pickSelectedZone(prev, items)).toBe(items[1]);
  });

  test("換了帳號、原 zone 不在新清單時改選第一個", () => {
    expect(pickSelectedZone({ id: "old" }, [{ id: "x" }, { id: "y" }])).toEqual({ id: "x" });
  });

  test("沒有 zone 時清空選取", () => {
    expect(pickSelectedZone({ id: "old" }, [])).toBeNull();
    expect(pickSelectedZone(null, [])).toBeNull();
  });
});

describe("DomainPage DNS record 表單", () => {
  test("不提供表單無法送出的 SRV", () => {
    expect(DNS_TYPES).not.toContain("SRV");
    expect(DNS_TYPES).toContain("MX");
  });

  test("編輯外部建立的 SRV 紀錄時，類型下拉仍列出原類型", () => {
    expect(recordTypeOptions("SRV")).toContain("SRV");
    expect(recordTypeOptions("A")).toEqual(DNS_TYPES);
    expect(recordTypeOptions(undefined)).toEqual(DNS_TYPES);
  });

  test("MX 一定帶 priority", () => {
    const body = buildRecordBody({ ...baseForm, type: "MX", content: "mail.example.edu", priority: "20" }, false);
    expect(body.priority).toBe(20);
    const fallback = buildRecordBody({ ...baseForm, type: "MX", priority: "" }, false);
    expect(fallback.priority).toBe(10);
  });

  test("非 MX 不帶 priority", () => {
    expect(buildRecordBody(baseForm, false)).not.toHaveProperty("priority");
  });

  test("編輯時清空備註送空字串（代表清除）", () => {
    const body = buildRecordBody({ ...baseForm, comment: "  " }, true);
    expect(body.comment).toBe("");
  });

  test("新增時空備註不送，有值則去掉空白", () => {
    expect(buildRecordBody(baseForm, false)).not.toHaveProperty("comment");
    expect(buildRecordBody({ ...baseForm, comment: " note " }, false).comment).toBe("note");
    expect(buildRecordBody(baseForm, false)).toMatchObject({ name: "www", content: "1.1.1.1", ttl: 1 });
  });
});
