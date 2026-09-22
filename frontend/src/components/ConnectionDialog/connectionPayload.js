/**
 * connectionPayload.js
 * ConnectionDialog 的 payload 組裝與驗證。
 *
 * 全部是純函式：不碰 React state、不做翻譯。錯誤只回傳 i18n key，
 * API 回來的動態訊息放在 text，呼叫端自己決定怎麼顯示。
 * 抽出來是為了讓這些規則能被測試蓋住，對話框改寫成意圖優先時行為不會跟著跑掉。
 */

/** Proxmox 的 dport 允許 8000:8010 這種範圍 */
export const RULE_PORT_RE = /^\d{1,5}(?::\d{1,5})?$/;

export const isPortless = (proto) => proto === "icmp" || proto === "icmpv6";
/** 課程環境主機名樣板裡代表每位學生的占位符；後端 course_env 驗證同一個 */
export const STUDENT_PLACEHOLDER = "{student}";
export const CLASS_PLACEHOLDER = "{class}";

/** 給老師看的示範網址：課堂代號與匿名學生識別碼都用範例值代入 */
export function previewTemplateHostname(prefix, zoneName) {
  const host = String(prefix ?? "")
    .trim()
    .toLowerCase()
    .replace(CLASS_PLACEHOLDER, "linux101-a1b2c3")
    .replace(STUDENT_PLACEHOLDER, "s8f21c4a2");
  return zoneName ? `${host}.${zoneName}` : host;
}
export const validPort = (n) => Number.isInteger(n) && n >= 1 && n <= 65535;

const fail = (key, text) => ({ error: text ? { key, text } : { key } });
const failInvalid = (key) => ({ invalid: true, error: { key } });

/** 出站不限 port，port=0 表示整條協定放行 */
export function buildOutboundPorts() {
  return [{ port: 0, protocol: "tcp" }];
}

/**
 * 入站：拆成走 publishService 的清單，與（無 port 協定）走 createConnection 的清單。
 *
 * @param {object} input
 * @param {"domain"|"port_forward"|"firewall_only"} input.mode
 * @param {string|number} input.domainPort     mode=domain 的對外 port
 * @param {string} input.fullDomain            mode=domain 的完整網域
 * @param {boolean} input.enableHttps
 * @param {boolean} input.domainTaken          即時檢查判定網域已被占用
 * @param {string|null} input.domainTakenText  佔用原因（API 給的訊息，優先顯示）
 * @param {Array} input.forwardRows            mode=port_forward 的列
 * @param {Array} input.firewallRows           mode=firewall_only 的列
 * @param {boolean} input.templateMode         課程環境模板：網址是樣板、對外 port 開課時才配
 * @param {string} input.hostnamePrefix        templateMode 的主機名樣板（須含 {student}）
 * @param {string} input.zoneId                templateMode 的網域 zone
 * @param {Array} input.templateForwardRows    templateMode 的 port_forward 列（只有內部 port + 協定）
 */
export function buildInboundPayload({
  mode,
  domainPort,
  fullDomain,
  enableHttps,
  domainTaken = false,
  domainTakenText = null,
  forwardRows = [],
  firewallRows = [],
  templateMode = false,
  hostnamePrefix = "",
  zoneId = "",
  templateForwardRows = [],
}) {
  if (mode === "domain" && templateMode) {
    const port = Number(domainPort);
    if (!validPort(port)) return fail("ConnectionDialog.portRangeError");
    const prefix = String(hostnamePrefix ?? "").trim().toLowerCase();
    if (!prefix || !zoneId) return fail("ConnectionDialog.domainRequired");
    /* 少了它，全班會搶同一個網址，只有第一位學生拿得到（後端也擋） */
    if (!prefix.includes(STUDENT_PLACEHOLDER)) return fail("ConnectionDialog.templateStudentPlaceholder");
    return {
      publish: [{ port, protocol: "tcp", mode, hostname_prefix: prefix, zone_id: zoneId, enable_https: enableHttps }],
      raw: [],
    };
  }

  if (mode === "port_forward" && templateMode) {
    /* 對外 port 是全域唯一資源，模板上只說「要一個」，開課時逐人配號 */
    const rows = templateForwardRows.filter((r) => r.port);
    if (rows.length === 0) return failInvalid("ConnectionDialog.portsRequired");
    const publish = [];
    for (const row of rows) {
      const port = Number(row.port);
      if (!validPort(port)) return failInvalid("ConnectionDialog.portRangeError");
      publish.push({ port, protocol: row.protocol, mode });
    }
    return { publish, raw: [] };
  }

  if (mode === "domain") {
    const port = Number(domainPort);
    if (!validPort(port)) return fail("ConnectionDialog.portRangeError");
    if (!fullDomain) return fail("ConnectionDialog.domainRequired");
    if (domainTaken) return fail("ConnectionDialog.domainTaken", domainTakenText);
    return {
      publish: [{ port, protocol: "tcp", mode, domain: fullDomain, enable_https: enableHttps }],
      raw: [],
    };
  }

  if (mode === "port_forward") {
    const rows = forwardRows.filter((r) => r.externalPort || r.internalPort);
    if (rows.length === 0) return failInvalid("ConnectionDialog.portsRequired");
    const publish = [];
    for (const row of rows) {
      const external = Number(row.externalPort);
      const internal = Number(row.internalPort);
      if (!validPort(external) || !validPort(internal)) {
        return failInvalid("ConnectionDialog.portRangeError");
      }
      publish.push({ port: internal, protocol: row.protocol, mode, external_port: external });
    }
    return { publish, raw: [] };
  }

  const rows = firewallRows.filter((r) => r.port || isPortless(r.protocol));
  if (rows.length === 0) return failInvalid("ConnectionDialog.portsRequired");
  const publish = [];
  const raw = [];
  for (const row of rows) {
    /* 無 port 協定沒有對外服務可發布，只能寫成一般防火牆規則 */
    if (isPortless(row.protocol)) {
      raw.push({ port: 0, protocol: row.protocol });
      continue;
    }
    const port = Number(row.port);
    if (!validPort(port)) return failInvalid("ConnectionDialog.portRangeError");
    publish.push({ port, protocol: row.protocol, mode });
  }
  return { publish, raw };
}

/** VM 對 VM：一列一個 port，icmp 類不需要 port */
export function buildPeerPortsPayload(rows = []) {
  const filled = rows.filter((r) => r.port || isPortless(r.protocol));
  if (filled.length === 0) return failInvalid("ConnectionDialog.portsRequired");
  const ports = [];
  for (const row of filled) {
    if (isPortless(row.protocol)) {
      ports.push({ port: 0, protocol: row.protocol });
      continue;
    }
    const port = Number(row.port);
    if (!validPort(port)) return failInvalid("ConnectionDialog.portRangeError");
    ports.push({ port, protocol: row.protocol });
  }
  return { ports };
}

/** 自訂規則：直接寫一條 Proxmox 原始規則 */
export function buildRulePayload(rule) {
  const body = { type: rule.type, action: rule.action, enable: 1 };
  if (rule.proto) body.proto = rule.proto;

  /* Proxmox 的 dport 一定要搭配協定；icmp 類沒有 port */
  const portDisabled = !rule.proto || isPortless(rule.proto);
  const dport = (rule.dport ?? "").trim();
  if (dport && !portDisabled) {
    const ok = RULE_PORT_RE.test(dport) && dport.split(":").every((p) => validPort(Number(p)));
    if (!ok) return fail("ConnectionDialog.portRangeFormatError");
    body.dport = dport;
  }

  const addr = (rule.source ?? "").trim();
  if (addr) body[rule.type === "in" ? "source" : "dest"] = addr;

  const comment = (rule.comment ?? "").trim();
  if (comment) body.comment = comment;

  return { body };
}
