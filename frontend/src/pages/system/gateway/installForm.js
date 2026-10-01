/**
 * Gateway 一鍵安裝表單的共用邏輯：閘道頁的「安裝服務」分頁與初始化精靈共用。
 */

const IPV4_PATTERN = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
const SOURCE_PATTERN = /^[0-9A-Fa-f:.]+(\/\d{1,3})?$/;

export function toInstallForm(defaults) {
  return {
    ingress_interface: defaults?.ingress_interface ?? "eth0",
    vm_interface: defaults?.vm_interface ?? "eth1",
    snat_address: defaults?.snat_address ?? "",
    listen_port: String(defaults?.listen_port ?? 51821),
    forward_port_start: String(defaults?.forward_port_start ?? 30000),
    forward_port_end: String(defaults?.forward_port_end ?? 39999),
    monitoring_allow_from: (defaults?.monitoring_allow_from ?? []).join(", "),
  };
}

export function parseSources(text) {
  return text.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
}

export function parsePort(value) {
  const port = Number(value);
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? port : null;
}

/* 後端也會驗，但 422 的錯誤訊息是陣列，前端只看得到「HTTP 422」，所以送出前先擋。
   回傳錯誤訊息的 key 尾碼（installErrorXxx），沒問題回 null */
export function validateInstallForm(form) {
  if (!form.ingress_interface || !form.vm_interface) return "installErrorInterface";
  if (form.ingress_interface === form.vm_interface) return "installErrorSameInterface";
  if (!IPV4_PATTERN.test(form.snat_address.trim())) return "installErrorSnat";
  const listen = parsePort(form.listen_port);
  const start = parsePort(form.forward_port_start);
  const end = parsePort(form.forward_port_end);
  if (listen === null || start === null || end === null) return "installErrorPort";
  if (start > end) return "installErrorRange";
  if (listen >= start && listen <= end) return "installErrorListenInRange";
  if (!parseSources(form.monitoring_allow_from).every((item) => SOURCE_PATTERN.test(item))) {
    return "installErrorSources";
  }
  return null;
}

export function toInstallPayload(form) {
  return {
    ingress_interface: form.ingress_interface,
    vm_interface: form.vm_interface,
    snat_address: form.snat_address.trim(),
    listen_port: parsePort(form.listen_port),
    forward_port_start: parsePort(form.forward_port_start),
    forward_port_end: parsePort(form.forward_port_end),
    monitoring_allow_from: parseSources(form.monitoring_allow_from),
  };
}

/* UFW、certbot 可能是系統原本就有的，只有 nginx／WireGuard 在才算裝過 */
export function hasCoreServices(status) {
  return Boolean(status?.components?.nginx || status?.components?.wireguard);
}

export function firstIpv4(iface) {
  const cidr = iface?.addresses?.[0];
  return cidr ? cidr.split("/")[0] : null;
}
