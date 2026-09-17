/**
 * classExposurePorts.js
 * 「開放給班級」的埠清單是一行文字輸入（80, 443/tcp, 53/udp），
 * 這裡負責文字 ⇄ PortSpec 陣列的轉換與驗證。純函式，方便測試。
 *
 * 錯誤只回 i18n key，呼叫端自己翻譯。
 */

const PROTOCOLS = new Set(["tcp", "udp", "icmp", "icmpv6", "sctp"]);
const PORTLESS = new Set(["icmp", "icmpv6"]);

/** "80, 443/tcp, 53/udp, icmp" → { ports: [{port, protocol}] } 或 { error } */
export function parsePortList(text) {
  const tokens = String(text ?? "")
    .split(/[,\s，、]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  if (tokens.length === 0) return { error: "ClassExposure.portsRequired" };

  const seen = new Set();
  const ports = [];
  for (const token of tokens) {
    let port;
    let protocol;
    if (PORTLESS.has(token)) {
      /* 純 "icmp" 這種無埠協定 */
      port = 0;
      protocol = token;
    } else {
      const [p, proto = "tcp", extra] = token.split("/");
      if (extra !== undefined) return { error: "ClassExposure.portInvalid", token };
      protocol = proto;
      if (!PROTOCOLS.has(protocol)) return { error: "ClassExposure.portInvalid", token };
      if (PORTLESS.has(protocol)) {
        port = 0;
      } else {
        port = Number(p);
        if (!/^\d{1,5}$/.test(p) || port < 1 || port > 65535) {
          return { error: "ClassExposure.portInvalid", token };
        }
      }
    }
    const key = `${port}/${protocol}`;
    if (seen.has(key)) continue;
    seen.add(key);
    ports.push({ port, protocol });
  }
  return { ports };
}

/** [{port, protocol}] → "80/tcp, 443/tcp"；無埠協定只印協定名 */
export function formatPortList(ports) {
  return (ports ?? [])
    .map((p) => (p.port === 0 ? p.protocol : `${p.port}/${p.protocol}`))
    .join(", ");
}
