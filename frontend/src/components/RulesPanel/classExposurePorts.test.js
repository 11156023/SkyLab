/**
 * classExposurePorts.test.js
 * 老師輸入的埠清單文字要能寬鬆解析、嚴格驗證，格式化要能反過來還原。
 */

import { describe, expect, test } from "vitest";
import { formatPortList, parsePortList } from "./classExposurePorts";

describe("parsePortList", () => {
  test("逗號、空白、全形頓號都能分隔，沒寫協定就是 tcp", () => {
    expect(parsePortList("80, 443/tcp 53/udp、3306")).toEqual({
      ports: [
        { port: 80, protocol: "tcp" },
        { port: 443, protocol: "tcp" },
        { port: 53, protocol: "udp" },
        { port: 3306, protocol: "tcp" },
      ],
    });
  });

  test("重複的埠只留一個，大小寫不分", () => {
    expect(parsePortList("80/TCP, 80, 80/tcp")).toEqual({ ports: [{ port: 80, protocol: "tcp" }] });
  });

  test("icmp 這種無埠協定可以直接寫協定名", () => {
    expect(parsePortList("icmp, 0/icmpv6")).toEqual({
      ports: [{ port: 0, protocol: "icmp" }, { port: 0, protocol: "icmpv6" }],
    });
  });

  test("空字串是缺埠，不是格式錯", () => {
    expect(parsePortList("  ")).toEqual({ error: "ClassExposure.portsRequired" });
    expect(parsePortList(undefined)).toEqual({ error: "ClassExposure.portsRequired" });
  });

  test("超出範圍、未知協定、多餘斜線都算格式錯並帶回那個 token", () => {
    expect(parsePortList("70000")).toEqual({ error: "ClassExposure.portInvalid", token: "70000" });
    expect(parsePortList("80/http")).toEqual({ error: "ClassExposure.portInvalid", token: "80/http" });
    expect(parsePortList("80/tcp/x")).toEqual({ error: "ClassExposure.portInvalid", token: "80/tcp/x" });
    expect(parsePortList("abc")).toEqual({ error: "ClassExposure.portInvalid", token: "abc" });
  });
});

describe("formatPortList", () => {
  test("與 parsePortList 互為反向", () => {
    const text = "80/tcp, 53/udp, icmp";
    expect(formatPortList(parsePortList(text).ports)).toBe(text);
    expect(formatPortList([])).toBe("");
  });
});
