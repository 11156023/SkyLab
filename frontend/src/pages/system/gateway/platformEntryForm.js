/**
 * 平台入口表單的共用邏輯：閘道頁的「平台入口」分頁與初始化精靈共用。
 * 後端也會驗，但表單在送出前先擋，錯誤才能指到欄位而不是只有一句訊息。
 */

export const DEFAULT_UPSTREAM_PORT = 8082;

const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
/* 完整網域：至少兩段 */
const DOMAIN_PATTERN = new RegExp(`^${LABEL}(?:\\.${LABEL})+$`, "i");
/* 上游可以是單段主機名稱 */
const HOSTNAME_PATTERN = new RegExp(`^${LABEL}(?:\\.${LABEL})*$`, "i");
const IPV4_PATTERN = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
/* 四段都是數字卻不是合法 IPv4（例如 300.1.1.1）：別當成主機名稱放行 */
const NUMERIC_DOTTED_PATTERN = /^\d+(\.\d+)+$/;

function cleanHost(value) {
  return String(value ?? "").trim().toLowerCase().replace(/\.$/, "");
}

export function toPlatformForm(config) {
  return {
    enabled: Boolean(config?.enabled),
    domain: config?.domain ?? "",
    upstream_host: config?.upstream_host ?? "",
    upstream_port: String(config?.upstream_port ?? DEFAULT_UPSTREAM_PORT),
    enable_https: config?.enable_https ?? true,
  };
}

function parsePort(value) {
  const port = Number(value);
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? port : null;
}

export function isValidDomain(value) {
  const domain = cleanHost(value);
  return domain.length <= 255 && DOMAIN_PATTERN.test(domain);
}

export function isValidUpstreamHost(value) {
  const host = cleanHost(value);
  if (IPV4_PATTERN.test(host)) return true;
  if (NUMERIC_DOTTED_PATTERN.test(host)) return false;
  return host.length <= 255 && HOSTNAME_PATTERN.test(host);
}

/** 回傳錯誤訊息的 key 尾碼（platformErrorXxx），沒問題回 null。
 *  停用時網域與上游可以留空（先存草稿），有填就要合法。 */
export function validatePlatformForm(form) {
  const domain = cleanHost(form.domain);
  const host = cleanHost(form.upstream_host);
  if (form.enabled && !domain) return "platformErrorDomainRequired";
  if (domain && !isValidDomain(domain)) return "platformErrorDomain";
  if (form.enabled && !host) return "platformErrorUpstreamRequired";
  if (host && !isValidUpstreamHost(host)) return "platformErrorUpstream";
  if (parsePort(form.upstream_port) === null) return "platformErrorPort";
  return null;
}

export function toPlatformPayload(form) {
  return {
    enabled: Boolean(form.enabled),
    domain: cleanHost(form.domain),
    upstream_host: cleanHost(form.upstream_host),
    upstream_port: parsePort(form.upstream_port) ?? DEFAULT_UPSTREAM_PORT,
    enable_https: Boolean(form.enable_https),
  };
}

/** 表單和已儲存的設定有沒有差異（決定要不要提示「尚未儲存」） */
export function isPlatformFormDirty(form, config) {
  return JSON.stringify(toPlatformPayload(form)) !== JSON.stringify(toPlatformPayload(toPlatformForm(config)));
}

/** 測試上游用的參數；上游還沒填好就回 null（按鈕停用） */
export function toUpstreamTarget(form) {
  const host = cleanHost(form.upstream_host);
  const port = parsePort(form.upstream_port);
  if (!host || !isValidUpstreamHost(host) || port === null) return null;
  return { upstream_host: host, upstream_port: port };
}
