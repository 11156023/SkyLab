/**
 * responseMessage.test.js
 * 錯誤訊息解析共用後：api.js（request／apiPostForm）與 auth.js（loginLdap 等）
 * 對 FastAPI 422 的 detail 陣列、物件 detail 都要給出字串，不能把陣列塞進 err.message。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { readResponseMessage } from "./responseMessage";
import { apiPost, apiPostForm } from "./api";
import { loginLdap } from "./auth";

function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const jsonRes = (status, body = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

const VALIDATION_ERROR = {
  detail: [{ loc: ["body", "password"], msg: "String should have at most 255 characters", type: "string_too_long" }],
};

let fetchMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

describe("readResponseMessage", () => {
  test("字串 detail 或 message 原樣回傳", async () => {
    expect(await readResponseMessage(jsonRes(400, { detail: "壞掉了" }))).toBe("壞掉了");
    expect(await readResponseMessage(jsonRes(400, { message: "也壞了" }))).toBe("也壞了");
  });

  test("422 的 detail 陣列取第一筆 msg", async () => {
    expect(await readResponseMessage(jsonRes(422, VALIDATION_ERROR)))
      .toBe("String should have at most 255 characters");
  });

  test("物件 detail 取其 message；看不懂的形狀退回 HTTP 狀態", async () => {
    expect(await readResponseMessage(jsonRes(409, { detail: { message: "衝突", code: "x" } }))).toBe("衝突");
    expect(await readResponseMessage(jsonRes(422, { detail: [{ loc: [] }] }))).toBe("HTTP 422");
    expect(await readResponseMessage(jsonRes(500, { detail: { code: 1 } }))).toBe("HTTP 500");
  });

  test("body 不是 JSON 時退回 HTTP 狀態", async () => {
    const res = { status: 502, json: async () => { throw new SyntaxError("bad json"); } };
    expect(await readResponseMessage(res)).toBe("HTTP 502");
  });
});

describe("呼叫端一律拿到字串 message", () => {
  test("apiPostForm 遇到 422 detail 陣列", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(422, VALIDATION_ERROR));
    const err = await apiPostForm("/api/v1/login/access-token", { username: "a", password: "b" })
      .catch((e) => e);
    expect(err.status).toBe(422);
    expect(typeof err.message).toBe("string");
    expect(err.message).toBe("String should have at most 255 characters");
  });

  test("loginLdap 遇到 422 detail 陣列", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(422, VALIDATION_ERROR));
    const err = await loginLdap("s1234567", "x".repeat(300)).catch((e) => e);
    expect(err.status).toBe(422);
    expect(typeof err.message).toBe("string");
  });

  test("apiPost 遇到 422 detail 陣列", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(422, VALIDATION_ERROR));
    const err = await apiPost("/api/v1/anything", {}).catch((e) => e);
    expect(err).toMatchObject({ status: 422, message: "String should have at most 255 characters" });
  });
});
