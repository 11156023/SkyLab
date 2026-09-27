/* 反向代理／對外連線共用的網域與連接埠小工具（純函式，無 React 依賴）。
   ReverseProxyRuleModal、ConnectionDialog 與其 PortInput 共用。 */

/* label 是模組層級常數，無法呼叫 hook，改存 labelKey，實際 render 處再 t() */
export const COMMON_PORTS = [
  { value: "80", labelKey: "ReverseProxyRuleModal.port80" },
  { value: "443", labelKey: "ReverseProxyRuleModal.port443" },
  { value: "3000", labelKey: "ReverseProxyRuleModal.port3000" },
  { value: "5000", labelKey: "ReverseProxyRuleModal.port5000" },
  { value: "8000", labelKey: "ReverseProxyRuleModal.port8000" },
  { value: "8080", labelKey: "ReverseProxyRuleModal.port8080" },
  { value: "8888", labelKey: "ReverseProxyRuleModal.port8888" },
];

/** 找出網域所屬的 zone；有多個候選時取名稱最長（最精確）的那個 */
export function findZoneByDomain(domain, zones = []) {
  return [...zones]
    .sort((a, b) => b.name.length - a.name.length)
    .find((zone) => domain === zone.name || domain.endsWith(`.${zone.name}`));
}

/** 去掉 zone 後綴取得主機名前綴；網域就是 zone 本身時回空字串，不屬於該 zone 則原樣回傳 */
export function extractHostnamePrefix(domain, zoneName) {
  if (domain === zoneName) return "";
  const suffix = `.${zoneName}`;
  return domain.endsWith(suffix) ? domain.slice(0, -suffix.length) : domain;
}
