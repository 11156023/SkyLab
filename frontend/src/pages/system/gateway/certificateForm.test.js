import { describe, expect, test } from "vitest";
import {
  daysUntil,
  isCertificateFormDirty,
  isValidCertPath,
  toCertificateForm,
  toCertificatePayload,
  validateCertificateForm,
} from "./certificateForm";

const saved = {
  ssl_certificate_path: "/etc/ssl/skylab/fullchain.pem",
  ssl_certificate_key_path: "/etc/ssl/skylab/privkey.pem",
  configured: true,
};

describe("HTTPS 憑證表單", () => {
  test("沒有設定時兩個路徑都是空字串", () => {
    expect(toCertificateForm(null)).toEqual({
      ssl_certificate_path: "",
      ssl_certificate_key_path: "",
    });
  });

  test("路徑只收安全的絕對路徑", () => {
    expect(isValidCertPath("/etc/ssl/skylab/fullchain.pem")).toBe(true);
    expect(isValidCertPath("/etc/letsencrypt/live/example.com/privkey.pem")).toBe(true);
    expect(isValidCertPath("etc/ssl/a.pem")).toBe(false);
    expect(isValidCertPath("/etc/ssl/my cert.pem")).toBe(false);
    expect(isValidCertPath("/etc/ssl/a.pem;")).toBe(false);
    expect(isValidCertPath("/etc/ssl/../shadow")).toBe(false);
    expect(isValidCertPath("/etc/ssl/*.pem")).toBe(false);
    expect(isValidCertPath(`/${"a".repeat(600)}`)).toBe(false);
  });

  test("兩個都空代表不使用；只填一個或格式錯要擋", () => {
    expect(validateCertificateForm({ ssl_certificate_path: "", ssl_certificate_key_path: "" })).toBeNull();
    expect(validateCertificateForm({ ssl_certificate_path: "/a.pem", ssl_certificate_key_path: "" })).toBe("certErrorPair");
    expect(validateCertificateForm({ ssl_certificate_path: "/a.pem", ssl_certificate_key_path: "b.key" })).toBe("certErrorPath");
    expect(validateCertificateForm(toCertificateForm(saved))).toBeNull();
  });

  test("送出前去掉前後空白，只送兩個路徑", () => {
    expect(toCertificatePayload({ ssl_certificate_path: " /a.pem ", ssl_certificate_key_path: "/b.key\n" })).toEqual({
      ssl_certificate_path: "/a.pem",
      ssl_certificate_key_path: "/b.key",
    });
  });

  test("只有空白差異不算改過", () => {
    const form = toCertificateForm(saved);
    expect(isCertificateFormDirty(form, saved)).toBe(false);
    expect(isCertificateFormDirty({ ...form, ssl_certificate_path: ` ${form.ssl_certificate_path} ` }, saved)).toBe(false);
    expect(isCertificateFormDirty({ ...form, ssl_certificate_path: "/etc/ssl/other.pem" }, saved)).toBe(true);
  });

  test("daysUntil 算剩幾天，過期為負數", () => {
    const now = new Date("2026-10-04T00:00:00Z");
    expect(daysUntil("2026-10-14T12:00:00Z", now)).toBe(10);
    expect(daysUntil("2026-10-03T00:00:00Z", now)).toBe(-1);
    expect(daysUntil(null, now)).toBeNull();
    expect(daysUntil("not a date", now)).toBeNull();
  });
});
