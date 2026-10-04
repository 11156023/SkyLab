import { describe, expect, test } from "vitest";
import {
  isPlatformFormDirty,
  isValidDomain,
  isValidUpstreamHost,
  toPlatformForm,
  toPlatformPayload,
  toUpstreamTarget,
  validatePlatformForm,
} from "./platformEntryForm";

const saved = {
  enabled: true,
  domain: "skylab.example.com",
  upstream_host: "192.168.100.20",
  upstream_port: 8082,
  enable_https: true,
};

describe("平台入口表單", () => {
  test("沒有設定時帶入預設值：未啟用、8082、開 HTTPS", () => {
    expect(toPlatformForm(null)).toEqual({
      enabled: false,
      domain: "",
      upstream_host: "",
      upstream_port: "8082",
      enable_https: true,
    });
  });

  test("網域要是完整網域，上游收 IPv4 或主機名稱", () => {
    expect(isValidDomain("SkyLab.Example.com.")).toBe(true);
    expect(isValidDomain("skylab")).toBe(false);
    expect(isValidDomain("bad domain.example.com")).toBe(false);

    expect(isValidUpstreamHost("192.168.100.20")).toBe(true);
    expect(isValidUpstreamHost("deploy-host")).toBe(true);
    expect(isValidUpstreamHost("deploy.lab.internal")).toBe(true);
    expect(isValidUpstreamHost("300.1.1.1")).toBe(false);
    expect(isValidUpstreamHost("10.0.0.1:8082")).toBe(false);
    expect(isValidUpstreamHost("10.0.0.1; reboot")).toBe(false);
  });

  test("啟用時網域與上游必填，停用時可以留空當草稿", () => {
    const empty = toPlatformForm(null);
    expect(validatePlatformForm(empty)).toBeNull();
    expect(validatePlatformForm({ ...empty, enabled: true })).toBe("platformErrorDomainRequired");
    expect(validatePlatformForm({ ...empty, enabled: true, domain: "skylab.example.com" }))
      .toBe("platformErrorUpstreamRequired");
    expect(validatePlatformForm(toPlatformForm(saved))).toBeNull();
  });

  test("格式錯誤各自回對應的錯誤 key", () => {
    const form = toPlatformForm(saved);
    expect(validatePlatformForm({ ...form, domain: "skylab" })).toBe("platformErrorDomain");
    expect(validatePlatformForm({ ...form, upstream_host: "a b" })).toBe("platformErrorUpstream");
    expect(validatePlatformForm({ ...form, upstream_port: "70000" })).toBe("platformErrorPort");
    expect(validatePlatformForm({ ...form, upstream_port: "" })).toBe("platformErrorPort");
  });

  test("送出的內容會去空白、轉小寫、port 轉數字", () => {
    expect(
      toPlatformPayload({
        enabled: true,
        domain: " SkyLab.Example.com. ",
        upstream_host: " Deploy-Host ",
        upstream_port: "8082",
        enable_https: false,
      }),
    ).toEqual({
      enabled: true,
      domain: "skylab.example.com",
      upstream_host: "deploy-host",
      upstream_port: 8082,
      enable_https: false,
    });
  });

  test("dirty 只看實際會送出的內容", () => {
    const form = toPlatformForm(saved);
    expect(isPlatformFormDirty(form, saved)).toBe(false);
    expect(isPlatformFormDirty({ ...form, domain: " SKYLAB.example.com " }, saved)).toBe(false);
    expect(isPlatformFormDirty({ ...form, upstream_port: "8083" }, saved)).toBe(true);
    expect(isPlatformFormDirty({ ...form, enabled: false }, saved)).toBe(true);
  });

  test("上游沒填好就沒有測試目標", () => {
    expect(toUpstreamTarget(toPlatformForm(null))).toBeNull();
    expect(toUpstreamTarget({ ...toPlatformForm(saved), upstream_port: "0" })).toBeNull();
    expect(toUpstreamTarget(toPlatformForm(saved))).toEqual({
      upstream_host: "192.168.100.20",
      upstream_port: 8082,
    });
  });
});
