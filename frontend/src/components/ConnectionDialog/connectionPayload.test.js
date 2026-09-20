/**
 * connectionPayload.test.js
 * 蓋住 ConnectionDialog 的 payload 組裝與驗證規則。
 * 這些規則原本埋在 903 行的元件裡沒有測試，對話框改寫成意圖優先時
 * 就是靠這裡確認行為沒有跑掉。
 */

import { describe, expect, test } from "vitest";
import {
  buildInboundPayload,
  buildOutboundPorts,
  buildPeerPortsPayload,
  buildRulePayload,
  isPortless,
  previewTemplateHostname,
  validPort,
} from "./connectionPayload";

const forwardRow = (external, internal, protocol = "tcp") => ({
  externalPort: String(external),
  internalPort: String(internal),
  protocol,
});
const portRow = (port, protocol = "tcp") => ({ port: port === "" ? "" : String(port), protocol });

describe("helpers", () => {
  test("icmp 類視為無 port 協定", () => {
    expect(isPortless("icmp")).toBe(true);
    expect(isPortless("icmpv6")).toBe(true);
    expect(isPortless("tcp")).toBe(false);
  });

  test("port 範圍是 1..65535 的整數", () => {
    expect(validPort(1)).toBe(true);
    expect(validPort(65535)).toBe(true);
    expect(validPort(0)).toBe(false);
    expect(validPort(65536)).toBe(false);
    expect(validPort(80.5)).toBe(false);
    expect(validPort(Number("abc"))).toBe(false);
  });

  test("出站不限 port", () => {
    expect(buildOutboundPorts()).toEqual([{ port: 0, protocol: "tcp" }]);
  });
});

describe("buildInboundPayload：網址模式", () => {
  const base = { mode: "domain", domainPort: "443", fullDomain: "lab.example.edu", enableHttps: true };

  test("組出單筆發布，協定固定 tcp", () => {
    expect(buildInboundPayload(base)).toEqual({
      publish: [{
        port: 443,
        protocol: "tcp",
        mode: "domain",
        domain: "lab.example.edu",
        enable_https: true,
      }],
      raw: [],
    });
  });

  test("port 不合法就擋下", () => {
    expect(buildInboundPayload({ ...base, domainPort: "0" }).error.key)
      .toBe("ConnectionDialog.portRangeError");
  });

  test("沒有網域就擋下", () => {
    expect(buildInboundPayload({ ...base, fullDomain: "" }).error.key)
      .toBe("ConnectionDialog.domainRequired");
  });

  test("網域被占用時優先顯示後端給的原因", () => {
    const { error } = buildInboundPayload({
      ...base,
      domainTaken: true,
      domainTakenText: "Cloudflare 上已有這筆紀錄",
    });
    expect(error.key).toBe("ConnectionDialog.domainTaken");
    expect(error.text).toBe("Cloudflare 上已有這筆紀錄");
  });

  test("後端沒給原因時只回 key", () => {
    const { error } = buildInboundPayload({ ...base, domainTaken: true, domainTakenText: null });
    expect(error).toEqual({ key: "ConnectionDialog.domainTaken" });
  });

  test("enable_https 照實帶出", () => {
    const { publish } = buildInboundPayload({ ...base, enableHttps: false });
    expect(publish[0].enable_https) .toBe(false);
  });
});

describe("buildInboundPayload：對外 port 轉發", () => {
  const build = (rows) => buildInboundPayload({ mode: "port_forward", forwardRows: rows });

  test("每列組成一筆發布，對外與內部 port 分開帶", () => {
    expect(build([forwardRow(18080, 8080), forwardRow(10022, 22, "udp")])).toEqual({
      publish: [
        { port: 8080, protocol: "tcp", mode: "port_forward", external_port: 18080 },
        { port: 22, protocol: "udp", mode: "port_forward", external_port: 10022 },
      ],
      raw: [],
    });
  });

  test("完全空白的列不算數，全空就擋下並標記欄位", () => {
    const res = build([forwardRow("", "")]);
    expect(res.invalid).toBe(true);
    expect(res.error.key).toBe("ConnectionDialog.portsRequired");
  });

  test("只填一半的列會被驗證擋下", () => {
    expect(build([forwardRow(18080, "")]).error.key).toBe("ConnectionDialog.portRangeError");
    expect(build([forwardRow("", 8080)]).error.key).toBe("ConnectionDialog.portRangeError");
  });

  test("超出範圍的 port 會被擋下", () => {
    expect(build([forwardRow(70000, 8080)]).error.key).toBe("ConnectionDialog.portRangeError");
  });
});

describe("buildInboundPayload：僅開放防火牆", () => {
  const build = (rows) => buildInboundPayload({ mode: "firewall_only", firewallRows: rows });

  test("有 port 的走發布", () => {
    expect(build([portRow(22)])).toEqual({
      publish: [{ port: 22, protocol: "tcp", mode: "firewall_only" }],
      raw: [],
    });
  });

  test("無 port 協定沒有對外服務可發布，改走一般防火牆規則", () => {
    expect(build([portRow("", "icmp")])).toEqual({
      publish: [],
      raw: [{ port: 0, protocol: "icmp" }],
    });
  });

  test("兩種混在一起時各走各的路", () => {
    const res = build([portRow(22), portRow("", "icmp"), portRow(443)]);
    expect(res.publish).toEqual([
      { port: 22, protocol: "tcp", mode: "firewall_only" },
      { port: 443, protocol: "tcp", mode: "firewall_only" },
    ]);
    expect(res.raw).toEqual([{ port: 0, protocol: "icmp" }]);
  });

  test("一列都沒填就擋下", () => {
    const res = build([portRow("")]);
    expect(res.invalid).toBe(true);
    expect(res.error.key).toBe("ConnectionDialog.portsRequired");
  });
});

describe("buildPeerPortsPayload", () => {
  test("一列一個 port", () => {
    expect(buildPeerPortsPayload([portRow(5432), portRow(6379, "udp")])).toEqual({
      ports: [
        { port: 5432, protocol: "tcp" },
        { port: 6379, protocol: "udp" },
      ],
    });
  });

  test("無 port 協定用 port=0 表示", () => {
    expect(buildPeerPortsPayload([portRow("", "icmp")])).toEqual({
      ports: [{ port: 0, protocol: "icmp" }],
    });
  });

  test("空白列會被忽略，全空就擋下", () => {
    const res = buildPeerPortsPayload([portRow(""), portRow("")]);
    expect(res.invalid).toBe(true);
    expect(res.error.key).toBe("ConnectionDialog.portsRequired");
  });

  test("超出範圍的 port 會被擋下", () => {
    expect(buildPeerPortsPayload([portRow(99999)]).error.key)
      .toBe("ConnectionDialog.portRangeError");
  });
});

describe("buildRulePayload", () => {
  const rule = (over = {}) => ({
    type: "in", action: "ACCEPT", proto: "tcp", dport: "", source: "", comment: "", ...over,
  });

  test("最小規則只帶方向、動作與啟用", () => {
    expect(buildRulePayload(rule({ proto: "" })).body).toEqual({
      type: "in", action: "ACCEPT", enable: 1,
    });
  });

  test("入站的位址寫進 source，出站寫進 dest", () => {
    expect(buildRulePayload(rule({ source: "10.0.0.0/24" })).body.source).toBe("10.0.0.0/24");
    expect(buildRulePayload(rule({ type: "out", source: "10.0.0.0/24" })).body.dest)
      .toBe("10.0.0.0/24");
  });

  test("接受單一 port 與範圍", () => {
    expect(buildRulePayload(rule({ dport: "22" })).body.dport).toBe("22");
    expect(buildRulePayload(rule({ dport: "8000:8010" })).body.dport).toBe("8000:8010");
  });

  test("格式錯誤或超出範圍的 port 會被擋下", () => {
    expect(buildRulePayload(rule({ dport: "22-80" })).error.key)
      .toBe("ConnectionDialog.portRangeFormatError");
    expect(buildRulePayload(rule({ dport: "70000" })).error.key)
      .toBe("ConnectionDialog.portRangeFormatError");
    expect(buildRulePayload(rule({ dport: "8000:99999" })).error.key)
      .toBe("ConnectionDialog.portRangeFormatError");
  });

  test("沒有協定或 icmp 時忽略 port，不當成錯誤", () => {
    expect(buildRulePayload(rule({ proto: "", dport: "22" })).body.dport).toBeUndefined();
    expect(buildRulePayload(rule({ proto: "icmp", dport: "22" })).body.dport).toBeUndefined();
  });

  test("前後空白會被去掉，空字串不寫進 body", () => {
    const { body } = buildRulePayload(rule({ source: "  10.0.0.1  ", comment: "  note  " }));
    expect(body.source).toBe("10.0.0.1");
    expect(body.comment).toBe("note");
    expect(buildRulePayload(rule({ comment: "   " })).body.comment).toBeUndefined();
  });
});

/* ── 課程環境模板：網址是樣板、對外 port 開課時才配 ── */
describe("buildInboundPayload：templateMode", () => {
  test("網址模式送出主機名樣板與 zone，不組完整網域", () => {
    const out = buildInboundPayload({
      mode: "domain", domainPort: "5678", enableHttps: true,
      templateMode: true, hostnamePrefix: " {Class}-{student}-N8N ", zoneId: "zone-1",
    });
    expect(out.publish).toEqual([
      { port: 5678, protocol: "tcp", mode: "domain", hostname_prefix: "{class}-{student}-n8n", zone_id: "zone-1", enable_https: true },
    ]);
    expect(out.raw).toEqual([]);
  });

  test("樣板少了 {student} 會被擋下：全班會搶同一個網址", () => {
    const out = buildInboundPayload({
      mode: "domain", domainPort: "80", enableHttps: true,
      templateMode: true, hostnamePrefix: "n8n", zoneId: "zone-1",
    });
    expect(out.error.key).toBe("ConnectionDialog.templateStudentPlaceholder");
  });

  test("沒有 zone 就不能用網址", () => {
    const out = buildInboundPayload({
      mode: "domain", domainPort: "80", enableHttps: true,
      templateMode: true, hostnamePrefix: "{student}-app", zoneId: "",
    });
    expect(out.error.key).toBe("ConnectionDialog.domainRequired");
  });

  test("對外 port 模式只帶內部 port 與協定，對外 port 留給開課時配號", () => {
    const out = buildInboundPayload({
      mode: "port_forward", templateMode: true,
      templateForwardRows: [{ port: "22", protocol: "tcp" }, { port: "", protocol: "udp" }, { port: "53", protocol: "udp" }],
    });
    expect(out.publish).toEqual([
      { port: 22, protocol: "tcp", mode: "port_forward" },
      { port: 53, protocol: "udp", mode: "port_forward" },
    ]);
    expect(out.publish.every((p) => !("external_port" in p))).toBe(true);
  });

  test("對外 port 模式一列都沒填就是錯", () => {
    const out = buildInboundPayload({ mode: "port_forward", templateMode: true, templateForwardRows: [{ port: "", protocol: "tcp" }] });
    expect(out.invalid).toBe(true);
    expect(out.error.key).toBe("ConnectionDialog.portsRequired");
  });

  test("預覽網址把兩個占位符換成範例值", () => {
    expect(previewTemplateHostname("{class}-{student}-app", "lab.example.edu")).toBe("linux101-a1b2c3-s8f21c4a2-app.lab.example.edu");
    expect(previewTemplateHostname("{student}", undefined)).toBe("s8f21c4a2");
  });
});
