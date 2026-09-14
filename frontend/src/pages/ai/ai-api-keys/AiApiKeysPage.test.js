import { describe, expect, test } from "vitest";
import { buildCredentialListParams } from "./AiApiKeysPage";

describe("AiApiKeysPage query contract", () => {
  test("預設以啟用中狀態查詢第一頁", () => {
    expect(buildCredentialListParams({ status: "active" })).toEqual({
      status: "active",
      query: undefined,
      user_role: [],
      created_after: undefined,
      skip: 0,
      limit: 50,
    });
  });

  test("全部狀態與空白搜尋不會送出多餘條件", () => {
    expect(buildCredentialListParams({ status: "all", query: "  ", page: 2 })).toMatchObject({
      status: undefined,
      query: undefined,
      skip: 100,
      limit: 50,
    });
  });

  test("角色與建立時間篩選會帶入 server-side 查詢", () => {
    const params = buildCredentialListParams({
      status: "inactive",
      query: "ccai_abc",
      roles: ["teacher", "admin"],
      createdRange: "30d",
      page: 1,
      limit: 25,
    });

    expect(params).toMatchObject({
      status: "inactive",
      query: "ccai_abc",
      user_role: ["teacher", "admin"],
      skip: 25,
      limit: 25,
    });
    expect(params.created_after).toEqual(expect.any(String));
  });
});
