/**
 * intents.test.js
 * 三個入口帶進來的初始值，都要能推導成正確的意圖與機器。
 */

import { describe, expect, test } from "vitest";
import { INTENT, INTERNET_KEY, deriveInitialState, endsOf, isVmKey } from "./intents";

describe("isVmKey", () => {
  test("網際網路與空值都不是機器", () => {
    expect(isVmKey(INTERNET_KEY)).toBe(false);
    expect(isVmKey("")).toBe(false);
    expect(isVmKey(undefined)).toBe(false);
    expect(isVmKey("101")).toBe(true);
  });
});

describe("deriveInitialState：拓撲頁拉線", () => {
  test("從網際網路拉到機器就是開放服務，機器已填好", () => {
    const s = deriveInitialState({ initialSource: INTERNET_KEY, initialTarget: "101" });
    expect(s.intent).toBe(INTENT.PUBLISH);
    expect(s.vmKey).toBe("101");
  });

  test("從機器拉到網際網路就是上網", () => {
    const s = deriveInitialState({ initialSource: "101", initialTarget: INTERNET_KEY });
    expect(s.intent).toBe(INTENT.OUTBOUND);
    expect(s.vmKey).toBe("101");
  });

  test("機器拉到機器就是互通，方向照拉線", () => {
    const s = deriveInitialState({ initialSource: "101", initialTarget: "102" });
    expect(s.intent).toBe(INTENT.PEER);
    expect(s.peerSourceKey).toBe("101");
    expect(s.peerTargetKey).toBe("102");
  });

  test("兩端相同不算互通，停在選意圖", () => {
    const s = deriveInitialState({ initialSource: "101", initialTarget: "101" });
    expect(s.intent).toBeNull();
    expect(s.vmKey).toBe("101");
  });
});

describe("deriveInitialState：什麼都沒帶", () => {
  test("停在選意圖，欄位全空", () => {
    const s = deriveInitialState({});
    expect(s).toEqual({ intent: null, vmKey: "", peerSourceKey: "", peerTargetKey: "" });
  });

  test("只給目標也先填好機器，等使用者選意圖", () => {
    const s = deriveInitialState({ initialTarget: "102" });
    expect(s.intent).toBeNull();
    expect(s.vmKey).toBe("102");
    expect(s.peerTargetKey).toBe("102");
  });
});

describe("deriveInitialState：鎖定機器的入口", () => {
  test("規則面板：預選自己寫規則，機器鎖定", () => {
    const s = deriveInitialState({ initialTab: "rule", fixedKey: "101" });
    expect(s.intent).toBe(INTENT.RULE);
    expect(s.vmKey).toBe("101");
    expect(s.peerSourceKey).toBe("101");
    expect(s.peerTargetKey).toBe("");
  });

  test("鎖定機器時互通一律由它當來源", () => {
    const s = deriveInitialState({ initialSource: "102", initialTarget: "101", fixedKey: "101" });
    expect(s.intent).toBe(INTENT.PEER);
    expect(s.peerSourceKey).toBe("101");
    expect(s.peerTargetKey).toBe("102");
  });

  test("編輯既有發布：鎖定為開放服務", () => {
    const s = deriveInitialState({ fixedKey: "101", editing: true, initialTab: "rule" });
    expect(s.intent).toBe(INTENT.PUBLISH);
    expect(s.vmKey).toBe("101");
  });
});

describe("endsOf", () => {
  const keys = { vmKey: "101", peerSourceKey: "102", peerTargetKey: "103" };

  test("開放服務：來源是網際網路", () => {
    expect(endsOf(INTENT.PUBLISH, keys)).toEqual({ sourceKey: INTERNET_KEY, targetKey: "101" });
  });

  test("上網：目標是網際網路", () => {
    expect(endsOf(INTENT.OUTBOUND, keys)).toEqual({ sourceKey: "101", targetKey: INTERNET_KEY });
  });

  test("互通：用互通專屬的兩端", () => {
    expect(endsOf(INTENT.PEER, keys)).toEqual({ sourceKey: "102", targetKey: "103" });
  });

  test("規則或尚未選意圖：沒有兩端", () => {
    expect(endsOf(INTENT.RULE, keys)).toEqual({ sourceKey: "", targetKey: "" });
    expect(endsOf(null, keys)).toEqual({ sourceKey: "", targetKey: "" });
  });
});
