/**
 * HTTPS 憑證表單的共用邏輯：閘道頁的「HTTPS 憑證」分頁與初始化精靈共用。
 * 系統不簽發憑證，只記管理員放在 Gateway 上的憑證（fullchain）與私鑰路徑。
 * 路徑規則與後端 gateway_certificate_service.normalize_cert_path 一致（改一邊要改另一邊）。
 */

/* 建議存放位置（Gateway 安裝腳本會建好 /etc/ssl/skylab） */
export const SUGGESTED_CERT_PATH = "/etc/ssl/skylab/fullchain.pem";
export const SUGGESTED_KEY_PATH = "/etc/ssl/skylab/privkey.pem";

/* 路徑會原樣寫進 nginx 設定：只收絕對路徑與檔名常見字元（不含空白、分號、引號） */
const CERT_PATH_PATTERN = /^\/[A-Za-z0-9._@+=,-]+(?:\/[A-Za-z0-9._@+=,-]+)*$/;
const CERT_PATH_MAX_LENGTH = 512;
/* 憑證剩不到這麼多天就提醒換新（與後端健康監控的門檻相同） */
export const CERT_WARN_DAYS = 14;

export function isValidCertPath(value) {
  const path = String(value ?? "").trim();
  if (!path || path.length > CERT_PATH_MAX_LENGTH || !CERT_PATH_PATTERN.test(path)) return false;
  return !path.split("/").slice(1).some((segment) => segment === "." || segment === "..");
}

export function toCertificateForm(config) {
  return {
    ssl_certificate_path: config?.ssl_certificate_path ?? "",
    ssl_certificate_key_path: config?.ssl_certificate_key_path ?? "",
  };
}

export function toCertificatePayload(form) {
  return {
    ssl_certificate_path: String(form.ssl_certificate_path ?? "").trim(),
    ssl_certificate_key_path: String(form.ssl_certificate_key_path ?? "").trim(),
  };
}

/** 回傳錯誤訊息的 key 尾碼（certErrorXxx），沒問題回 null。兩個都留空代表不使用憑證。 */
export function validateCertificateForm(form) {
  const { ssl_certificate_path: cert, ssl_certificate_key_path: key } = toCertificatePayload(form);
  if (!cert && !key) return null;
  if (!cert || !key) return "certErrorPair";
  if (!isValidCertPath(cert) || !isValidCertPath(key)) return "certErrorPath";
  return null;
}

export function isCertificateFormDirty(form, config) {
  return JSON.stringify(toCertificatePayload(form)) !== JSON.stringify(toCertificatePayload(toCertificateForm(config)));
}

/** 距離到期還有幾天（無條件捨去）；沒有到期日回 null */
export function daysUntil(isoDate, now = new Date()) {
  if (!isoDate) return null;
  const end = new Date(isoDate);
  if (Number.isNaN(end.getTime())) return null;
  return Math.floor((end.getTime() - now.getTime()) / 86_400_000);
}
