/**
 * ConnectionDialog — 網路連線／防火牆規則的統一對話框
 * 兩個入口共用同一份：拓撲頁「新增連線」、資源頁防火牆卡片「新增規則」。
 * （`service` 編輯模式目前沒有入口在用，保留給日後需要就地編輯既有發布的頁面。）
 *
 * 「連線」分頁依來源／目標的組合決定表單：
 * - 網際網路 → VM：入站發布（用網址／用對外 port／僅開放防火牆），逐 port 走 publishService，
 *   有重複 port 與網域撞名保護；無 port 協定（icmp）走 createConnection。
 * - VM → 網際網路：出站上網、不限 port，走 createConnection。
 * - VM → VM：指定 port 與單向／雙向，走 createConnection。
 * 「自訂規則」分頁直接寫一條 Proxmox 原始規則（方向／動作／協定／來源／備註），走 createVmRule；
 * 這類規則不帶 SkyLab: 標記，不會出現在拓撲圖上。
 *
 * props：
 * - nodes            可選，[{ key, vmid, name }]；沒給就自己抓 getTopology()
 * - fixedVmid        鎖定一端為這台 VM（資源詳情頁用），另一端自由選；fixedName 為顯示名稱備援
 * - initialSource / initialTarget  預設兩端（"internet" 或 vmid 字串）
 * - initialTab       "connection"（預設）| "rule"
 * - initialMode      入站預設發布方式 "domain" | "port_forward" | "firewall_only"（網址不可用時退回對外 port）
 * - service          編輯既有對外服務時傳入（鎖定入站、單一 port，改走 replacePublishedService）
 * - onDone(result)   全部成功後回呼（呼叫端負責關閉與重新載入）
 * - onChanged()      可選；多筆發布途中失敗時，已成功的部分會先通知一次
 * - onClose / closing
 *
 * 對話框自己 portal 到 body：呼叫端可能在有 overflow:hidden + backdrop-filter 的卡片裡。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./ConnectionDialog.module.scss";
import MIcon from "../MIcon";
import { focusInvalidField } from "../../utils/focusField";
import {
  createConnection,
  createVmRule,
  getTopology,
  publishService,
  replacePublishedService,
} from "../../services/firewall";
import { ReverseProxyService } from "../../services/reverseProxy";
import {
  COMMON_PORTS,
  extractHostnamePrefix,
  findZoneByDomain,
} from "../ReverseProxyRuleModal/ReverseProxyRuleModal";

export const INTERNET_KEY = "internet";

const INBOUND_MODES = ["domain", "port_forward", "firewall_only"];
const CONNECTION_PROTOCOLS = ["tcp", "udp", "icmp", "icmpv6", "sctp"];
const FORWARD_PROTOCOLS = ["tcp", "udp"];
const RULE_PROTOCOLS = ["tcp", "udp", "icmp"];
const AVAILABILITY_DEBOUNCE_MS = 500;
const RULE_PORT_RE = /^\d{1,5}(?::\d{1,5})?$/; // 自訂規則允許 8000:8010 這種範圍
const COMMON_PORTS_LIST_ID = "connection-dialog-common-ports";
const EMPTY = [];

const isPortless = (proto) => proto === "icmp" || proto === "icmpv6";
const validPort = (n) => Number.isInteger(n) && n >= 1 && n <= 65535;
const isVmKey = (key) => Boolean(key) && key !== INTERNET_KEY;

let _uid = 0;
const uid = () => ++_uid;
const newPortRow = (init = {}) => ({ id: uid(), port: "", protocol: "tcp", ...init });
const newForwardRow = (init = {}) => ({ id: uid(), externalPort: "", internalPort: "", protocol: "tcp", ...init });

function modeMeta(mode) {
  if (mode === "domain") return { icon: "language", labelKey: "ConnectionDialog.modeDomain", descKey: "ConnectionDialog.modeDomainDesc" };
  if (mode === "port_forward") return { icon: "swap_horiz", labelKey: "ConnectionDialog.modePortForward", descKey: "ConnectionDialog.modePortForwardDesc" };
  return { icon: "shield", labelKey: "ConnectionDialog.modeFirewallOnly", descKey: "ConnectionDialog.modeFirewallOnlyDesc" };
}

/* ── 一列一個 port：僅開放防火牆、VM→VM 共用 ── */
function PortRows({ rows, setRows, protocols, invalid, single }) {
  const { t } = useTranslation("components");
  const add = () => setRows((r) => [...r, newPortRow()]);
  const remove = (id) => setRows((r) => (r.length > 1 ? r.filter((x) => x.id !== id) : r));
  const update = (id, key, val) =>
    setRows((r) => r.map((x) => (x.id === id ? { ...x, [key]: val } : x)));

  return (
    <div className={styles.portSection}>
      {rows.map((row) => {
        const portless = isPortless(row.protocol);
        const missing = invalid && !portless && !row.port;
        return (
          <div key={row.id} className={styles.portRow}>
            <input
              type="number"
              min="1"
              max="65535"
              list={COMMON_PORTS_LIST_ID}
              placeholder={portless ? t("ConnectionDialog.portlessPlaceholder") : t("ConnectionDialog.portPlaceholder")}
              value={portless ? "" : row.port}
              disabled={portless}
              onChange={(e) => update(row.id, "port", e.target.value)}
              aria-invalid={missing}
              className={`${styles.portInput} ${missing ? styles.portInputInvalid : ""}`}
            />
            <select
              value={row.protocol}
              onChange={(e) => update(row.id, "protocol", e.target.value)}
              className={styles.protoSelect}
            >
              {protocols.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            {!single && (
              <button type="button" className={styles.removeBtn} onClick={() => remove(row.id)} disabled={rows.length === 1}>
                <MIcon name="remove" size={16} />
              </button>
            )}
          </div>
        );
      })}
      {!single && (
        <button type="button" className={styles.addBtn} onClick={add}>
          <MIcon name="add" size={16} />
          {t("ConnectionDialog.addPort")}
        </button>
      )}
    </div>
  );
}

/* ── 一列一組對外 port → 內部 port ── */
function ForwardRows({ rows, setRows, invalid, single }) {
  const { t } = useTranslation("components");
  const add = () => setRows((r) => [...r, newForwardRow()]);
  const remove = (id) => setRows((r) => (r.length > 1 ? r.filter((x) => x.id !== id) : r));
  const update = (id, key, val) =>
    setRows((r) => r.map((x) => (x.id === id ? { ...x, [key]: val } : x)));

  return (
    <div className={styles.portSection}>
      <div className={styles.forwardRowHeader}>
        <span>{t("ConnectionDialog.externalPort")}</span>
        <span>{t("ConnectionDialog.internalPort")}</span>
        <span>{t("ConnectionDialog.protocol")}</span>
        <span />
      </div>
      {rows.map((row) => (
        <div key={row.id} className={styles.forwardRow}>
          <input
            type="number" min="1" max="65535"
            placeholder={t("ConnectionDialog.externalPlaceholder")}
            value={row.externalPort}
            onChange={(e) => update(row.id, "externalPort", e.target.value)}
            aria-invalid={Boolean(invalid && !row.externalPort)}
            className={`${styles.portInput} ${invalid && !row.externalPort ? styles.portInputInvalid : ""}`}
          />
          <input
            type="number" min="1" max="65535"
            list={COMMON_PORTS_LIST_ID}
            placeholder={t("ConnectionDialog.internalPlaceholder")}
            value={row.internalPort}
            onChange={(e) => update(row.id, "internalPort", e.target.value)}
            aria-invalid={Boolean(invalid && !row.internalPort)}
            className={`${styles.portInput} ${invalid && !row.internalPort ? styles.portInputInvalid : ""}`}
          />
          <select
            value={row.protocol}
            onChange={(e) => update(row.id, "protocol", e.target.value)}
            className={styles.protoSelect}
          >
            {FORWARD_PROTOCOLS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          {!single && (
            <button type="button" className={styles.removeBtn} onClick={() => remove(row.id)} disabled={rows.length === 1}>
              <MIcon name="remove" size={16} />
            </button>
          )}
        </div>
      ))}
      {!single && (
        <button type="button" className={styles.addBtn} onClick={add}>
          <MIcon name="add" size={16} />
          {t("ConnectionDialog.addMapping")}
        </button>
      )}
      <p className={styles.fieldHint}>{t("ConnectionDialog.portForwardHint")}</p>
    </div>
  );
}

/* ── 主元件 ── */
export default function ConnectionDialog({
  nodes,
  fixedVmid,
  fixedName,
  initialSource,
  initialTarget,
  initialTab = "connection",
  initialMode,
  service,
  onDone,
  onChanged,
  onClose,
  closing = false,
}) {
  const { t } = useTranslation("components");
  const fixedKey = fixedVmid != null ? String(fixedVmid) : null;
  const editing = Boolean(service);

  /* ── 分頁 ── */
  const [tab, setTab] = useState(editing ? "connection" : initialTab);

  /* ── 節點清單：沒給就自己抓 ── */
  const [fetchedNodes, setFetchedNodes] = useState(null);
  useEffect(() => {
    if (nodes) return undefined;
    let cancelled = false;
    getTopology()
      .then((topo) => {
        if (cancelled) return;
        setFetchedNodes(
          (topo?.nodes ?? [])
            .filter((n) => n.node_type !== "gateway" && n.vmid != null)
            .map((n) => ({ key: String(n.vmid), vmid: n.vmid, name: n.name })),
        );
      })
      .catch(() => !cancelled && setFetchedNodes([]));
    return () => { cancelled = true; };
  }, [nodes]);

  const nodesLoading = !nodes && fetchedNodes === null;
  const vmNodes = useMemo(() => {
    const list = nodes ?? fetchedNodes ?? EMPTY;
    if (fixedKey && !list.some((n) => n.key === fixedKey)) {
      return [{ key: fixedKey, vmid: fixedVmid, name: fixedName ?? `VM ${fixedVmid}` }, ...list];
    }
    return list;
  }, [nodes, fetchedNodes, fixedKey, fixedVmid, fixedName]);

  const nodeOptions = useMemo(
    () => [{ key: INTERNET_KEY, label: t("ConnectionDialog.gatewayLabel") }, ...vmNodes.map((n) => ({ key: n.key, label: n.name }))],
    [vmNodes, t],
  );
  const labelOf = (key) => nodeOptions.find((n) => n.key === key)?.label ?? key;
  const getVmid = (key) => (key === INTERNET_KEY ? null : (vmNodes.find((n) => n.key === key)?.vmid ?? null));

  /* ── 兩端 ── */
  const [sourceKey, setSourceKey] = useState(() => {
    if (editing) return INTERNET_KEY;
    return initialSource ?? INTERNET_KEY;
  });
  const [targetKey, setTargetKey] = useState(() => {
    if (editing) return fixedKey ?? initialTarget ?? "";
    if (initialTarget) return initialTarget;
    if (fixedKey) return initialSource === fixedKey ? INTERNET_KEY : fixedKey;
    return "";
  });

  /* 清單載入後修正無效的端點（拉線帶入的 key 不存在、或還沒選到 VM） */
  useEffect(() => {
    if (nodesLoading) return;
    const known = (k) => k === INTERNET_KEY || vmNodes.some((n) => n.key === k);
    setSourceKey((s) => (known(s) ? s : INTERNET_KEY));
    setTargetKey((tk) => {
      if (known(tk)) return tk;
      return fixedKey ?? vmNodes[0]?.key ?? "";
    });
  }, [nodesLoading, vmNodes, fixedKey]);

  /* 兩端不能相同；鎖定模式下必須有一端是這台 VM */
  const applyEnds = (s, tk) => {
    if (fixedKey && s !== fixedKey && tk !== fixedKey) tk = fixedKey;
    setSourceKey(s);
    setTargetKey(tk);
  };
  const pickSource = (key) => applyEnds(key, key === targetKey ? sourceKey : targetKey);
  const pickTarget = (key) => applyEnds(key === sourceKey ? targetKey : sourceKey, key);
  const swapEnds = () => applyEnds(targetKey, sourceKey);

  const isInternetSrc = sourceKey === INTERNET_KEY;
  const isInternetTgt = targetKey === INTERNET_KEY;
  const isInbound = isInternetSrc && isVmKey(targetKey);
  const isOutbound = isVmKey(sourceKey) && isInternetTgt;
  const isVmToVm = isVmKey(sourceKey) && isVmKey(targetKey);

  /* ── 入站：發布方式 ── */
  const [setupContext, setSetupContext] = useState(null);
  useEffect(() => {
    let cancelled = false;
    ReverseProxyService.setupContext()
      .then((ctx) => !cancelled && setSetupContext(ctx ?? { enabled: false, zones: [] }))
      .catch(() => !cancelled && setSetupContext({ enabled: false, zones: [] }));
    return () => { cancelled = true; };
  }, []);
  const zones = useMemo(() => setupContext?.zones ?? EMPTY, [setupContext]);
  const domainReady = Boolean(setupContext) && setupContext.enabled !== false && zones.length > 0;

  const [mode, setModeState] = useState(service?.mode ?? initialMode ?? "port_forward");
  const modeTouched = useRef(editing || Boolean(initialMode));
  const setMode = (m) => { modeTouched.current = true; setModeState(m); };
  /* 網址可用時預設用網址（使用者或呼叫端還沒指定過才改）；呼叫端指定網址但環境不支援就退回對外 port */
  useEffect(() => {
    if (!setupContext) return;
    if (domainReady && !modeTouched.current) setModeState("domain");
    if (!domainReady && !editing) setModeState((m) => (m === "domain" ? "port_forward" : m));
  }, [setupContext, domainReady, editing]);
  const modeCards = INBOUND_MODES.filter((m) => m !== "domain" || domainReady || service?.mode === "domain");

  /* 網址模式 */
  const matchedCommon = editing ? COMMON_PORTS.find((p) => p.value === String(service.port)) : null;
  const [commonPort, setCommonPort] = useState(matchedCommon?.value ?? "80");
  const [customPort, setCustomPort] = useState(editing && !matchedCommon ? String(service.port) : "");
  const [useCustomPort, setUseCustomPort] = useState(Boolean(editing && !matchedCommon));
  const domainPort = useCustomPort ? customPort : commonPort;
  const [zoneId, setZoneId] = useState("");
  const [prefix, setPrefix] = useState(service?.domain ?? "");
  const [enableHttps, setEnableHttps] = useState(service?.enable_https ?? true);
  const [availability, setAvailability] = useState(null); // { available, reason, message, checking }

  /* zones 抓回來後：編輯時還原 zone + 開頭，新增時預設第一個 zone */
  useEffect(() => {
    if (!zones.length) return;
    if (service?.domain) {
      const z = findZoneByDomain(service.domain, zones);
      if (z) {
        setZoneId(z.id);
        setPrefix(extractHostnamePrefix(service.domain, z.name));
        return;
      }
    }
    setZoneId((cur) => cur || zones[0].id);
  }, [zones, service?.domain]);

  const selectedZone = zones.find((z) => z.id === zoneId);
  const cleanPrefix = prefix.trim().toLowerCase().replace(/^\.+|\.+$/g, "");
  const fullDomain = selectedZone ? (cleanPrefix ? `${cleanPrefix}.${selectedZone.name}` : selectedZone.name) : "";
  const domainUnchanged = Boolean(service?.domain) && fullDomain === service.domain;

  /* 網域即時檢查：本系統建的或 Cloudflare 上原本就有的，撞名都提醒 */
  useEffect(() => {
    if (tab !== "connection" || !isInbound || mode !== "domain" || !fullDomain || domainUnchanged) {
      setAvailability(null);
      return undefined;
    }
    let cancelled = false;
    setAvailability({ checking: true });
    const timer = setTimeout(() => {
      ReverseProxyService.checkDomainAvailability(fullDomain)
        .then((res) => !cancelled && setAvailability(res))
        .catch(() => !cancelled && setAvailability(null));
    }, AVAILABILITY_DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [tab, isInbound, mode, fullDomain, domainUnchanged]);

  /* port 列 */
  const [fwdRows, setFwdRows] = useState(() => [
    newForwardRow(service?.mode === "port_forward"
      ? { externalPort: String(service.external_port ?? ""), internalPort: String(service.port), protocol: service.protocol }
      : {}),
  ]);
  const [fwRows, setFwRows] = useState(() => [
    newPortRow(service?.mode === "firewall_only" ? { port: String(service.port), protocol: service.protocol } : {}),
  ]);
  const [vmRows, setVmRows] = useState(() => [newPortRow()]);
  const [direction, setDirection] = useState("one_way");

  /* ── 自訂規則 ── */
  const [ruleVmKey, setRuleVmKey] = useState(
    fixedKey ?? (isVmKey(initialTarget) ? initialTarget : isVmKey(initialSource) ? initialSource : ""),
  );
  useEffect(() => {
    if (nodesLoading) return;
    setRuleVmKey((k) => (vmNodes.some((n) => n.key === k) ? k : (fixedKey ?? vmNodes[0]?.key ?? "")));
  }, [nodesLoading, vmNodes, fixedKey]);
  const [rule, setRule] = useState({ type: "in", action: "ACCEPT", proto: "tcp", dport: "", source: "", comment: "" });
  const setRuleField = (k, v) => setRule((prev) => ({ ...prev, [k]: v }));
  /* Proxmox 的 dport 一定要搭配協定；icmp 類沒有 port */
  const rulePortDisabled = !rule.proto || isPortless(rule.proto);
  const ruleOverlapsPublish = rule.type === "in" && rule.action === "ACCEPT" && !rule.source.trim() && !rulePortDisabled;

  /* ── 送出 ── */
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [portsInvalid, setPortsInvalid] = useState(false);
  const editRows = (setter) => (updater) => { setPortsInvalid(false); setError(""); setter(updater); };

  /** 入站：拆成走 publishService 的清單與（無 port 協定）走 createConnection 的清單 */
  function buildInbound() {
    if (mode === "domain") {
      const port = Number(domainPort);
      if (!validPort(port)) return { error: t("ConnectionDialog.portRangeError") };
      if (!fullDomain) return { error: t("ConnectionDialog.domainRequired") };
      if (availability?.available === false) return { error: availability.message ?? t("ConnectionDialog.domainTaken") };
      return { publish: [{ port, protocol: "tcp", mode, domain: fullDomain, enable_https: enableHttps }], raw: [] };
    }
    if (mode === "port_forward") {
      const rows = fwdRows.filter((r) => r.externalPort || r.internalPort);
      if (rows.length === 0) return { invalid: true, error: t("ConnectionDialog.portsRequired") };
      const publish = [];
      for (const r of rows) {
        const ext = Number(r.externalPort);
        const inn = Number(r.internalPort);
        if (!validPort(ext) || !validPort(inn)) return { invalid: true, error: t("ConnectionDialog.portRangeError") };
        publish.push({ port: inn, protocol: r.protocol, mode, external_port: ext });
      }
      return { publish, raw: [] };
    }
    const rows = fwRows.filter((r) => r.port || isPortless(r.protocol));
    if (rows.length === 0) return { invalid: true, error: t("ConnectionDialog.portsRequired") };
    const publish = [];
    const raw = [];
    for (const r of rows) {
      if (isPortless(r.protocol)) { raw.push({ port: 0, protocol: r.protocol }); continue; }
      const port = Number(r.port);
      if (!validPort(port)) return { invalid: true, error: t("ConnectionDialog.portRangeError") };
      publish.push({ port, protocol: r.protocol, mode });
    }
    return { publish, raw };
  }

  /** VM→VM：一列一個 port，icmp 類不需 port */
  function buildPeerPorts() {
    const rows = vmRows.filter((r) => r.port || isPortless(r.protocol));
    if (rows.length === 0) return { invalid: true, error: t("ConnectionDialog.portsRequired") };
    const ports = [];
    for (const r of rows) {
      if (isPortless(r.protocol)) { ports.push({ port: 0, protocol: r.protocol }); continue; }
      const port = Number(r.port);
      if (!validPort(port)) return { invalid: true, error: t("ConnectionDialog.portRangeError") };
      ports.push({ port, protocol: r.protocol });
    }
    return { ports };
  }

  function buildRule() {
    const body = { type: rule.type, action: rule.action, enable: 1 };
    if (rule.proto) body.proto = rule.proto;
    const dport = rule.dport.trim();
    if (dport && !rulePortDisabled) {
      const ok = RULE_PORT_RE.test(dport) && dport.split(":").every((p) => validPort(Number(p)));
      if (!ok) return { error: t("ConnectionDialog.portRangeFormatError") };
      body.dport = dport;
    }
    const addr = rule.source.trim();
    if (addr) body[rule.type === "in" ? "source" : "dest"] = addr;
    const comment = rule.comment.trim();
    if (comment) body.comment = comment;
    return { body };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const form = e.currentTarget;
    setError("");

    if (tab === "rule") {
      const vmid = getVmid(ruleVmKey);
      if (vmid == null) { setError(t("ConnectionDialog.noNodes")); return; }
      const built = buildRule();
      if (built.error) { setError(built.error); return; }
      setSubmitting(true);
      try {
        await createVmRule(vmid, built.body);
        onDone?.({ kind: "rule", vmid });
      } catch (err) {
        setError(err?.message ?? t("ConnectionDialog.createFailed"));
      } finally {
        setSubmitting(false);
      }
      return;
    }

    if (isInbound) {
      const vmid = getVmid(targetKey);
      const built = buildInbound();
      if (built.error) {
        setError(built.error);
        if (built.invalid) { setPortsInvalid(true); focusInvalidField(form.querySelector('input[type="number"]')); }
        return;
      }
      setSubmitting(true);
      let done = 0;
      try {
        if (editing) {
          await replacePublishedService(vmid, { port: service.port, protocol: service.protocol }, built.publish[0]);
          onDone?.({ kind: "replace", vmid });
          return;
        }
        for (const payload of built.publish) {
          try {
            await publishService(vmid, payload);
          } catch (err) {
            if (done > 0) onChanged?.();
            setError(t("ConnectionDialog.partialFailed", {
              done, port: `${payload.port}/${payload.protocol}`, message: err?.message ?? t("ConnectionDialog.createFailed"),
            }));
            return;
          }
          done += 1;
        }
        if (built.raw.length > 0) {
          await createConnection({ source_vmid: null, target_vmid: vmid, ports: built.raw, direction: "one_way" });
        }
        onDone?.({ kind: "publish", vmid, count: done + built.raw.length });
      } catch (err) {
        if (done > 0) onChanged?.();
        setError(err?.message ?? t("ConnectionDialog.createFailed"));
      } finally {
        setSubmitting(false);
      }
      return;
    }

    let ports;
    if (isOutbound) {
      ports = [{ port: 0, protocol: "tcp" }]; // 出站不限 port
    } else if (isVmToVm) {
      const built = buildPeerPorts();
      if (built.error) {
        setError(built.error);
        setPortsInvalid(true);
        focusInvalidField(form.querySelector('input[type="number"]'));
        return;
      }
      ports = built.ports;
    } else {
      setError(t("ConnectionDialog.noNodes"));
      return;
    }
    setSubmitting(true);
    try {
      await createConnection({
        source_vmid: getVmid(sourceKey),
        target_vmid: getVmid(targetKey),
        ports,
        direction: isVmToVm ? direction : "one_way",
      });
      onDone?.({ kind: "connection", source_vmid: getVmid(sourceKey), target_vmid: getVmid(targetKey) });
    } catch (err) {
      setError(err?.message ?? t("ConnectionDialog.createFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  /* ── 文案 ── */
  const title = editing
    ? t("ConnectionDialog.titleEditService")
    : tab === "rule" ? t("ConnectionDialog.titleRule") : t("ConnectionDialog.title");
  const submitLabel = submitting
    ? t("ConnectionDialog.working")
    : tab === "rule"
      ? t("ConnectionDialog.addRule")
      : editing
        ? t("ConnectionDialog.saveChanges")
        : isInbound
          ? t("ConnectionDialog.publish")
          : t("ConnectionDialog.createConnection");
  const submitDisabled = submitting || nodesLoading
    || (tab === "connection" && isInbound && mode === "domain" && (availability?.checking || availability?.available === false));

  const availabilityTone = availability?.checking
    ? ""
    : availability?.available === false
      ? styles.hintBad
      : availability?.reason === "unverified"
        ? styles.hintWarn
        : availability?.available
          ? styles.hintOk
          : "";
  const availabilityIcon = availability?.checking
    ? "hourglass_empty"
    : availability?.available === false
      ? "error"
      : availability?.available
        ? "check_circle"
        : "language";
  const availabilityText = availability?.checking
    ? t("ConnectionDialog.checkingDomain", { domain: fullDomain })
    : availability?.message
      ? availability.message
      : availability?.available
        ? t("ConnectionDialog.domainAvailable", { domain: fullDomain })
        : domainUnchanged
          ? t("ConnectionDialog.domainUnchanged", { domain: fullDomain })
          : fullDomain;

  const endSelect = (label, value, onPick) => (
    <div className={styles.nodeSelect}>
      <label className={styles.nodeLabel}>{label}</label>
      <select value={value} onChange={(e) => onPick(e.target.value)} className={styles.select} disabled={editing || nodesLoading}>
        {nodeOptions.map((n) => <option key={n.key} value={n.key}>{n.label}</option>)}
      </select>
    </div>
  );

  return createPortal(
    <div
      className={`${styles.overlay} ${closing ? styles.overlayOut : ""}`}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-label={title}>
        <div className={styles.dialogHeader}>
          <h2 className={styles.dialogTitle}>{title}</h2>
          <button type="button" className={styles.closeBtn} onClick={onClose} aria-label={t("ConnectionDialog.cancel")}>
            <MIcon name="close" size={20} />
          </button>
        </div>

        {!editing && (
          <div className={styles.tabs} role="tablist">
            <button
              type="button" role="tab" aria-selected={tab === "connection"}
              className={`${styles.tab} ${tab === "connection" ? styles.tabActive : ""}`}
              onClick={() => { setTab("connection"); setError(""); }}
            >
              <MIcon name="hub" size={16} />
              {t("ConnectionDialog.tabConnection")}
            </button>
            <button
              type="button" role="tab" aria-selected={tab === "rule"}
              className={`${styles.tab} ${tab === "rule" ? styles.tabActive : ""}`}
              onClick={() => { setTab("rule"); setError(""); }}
            >
              <MIcon name="tune" size={16} />
              {t("ConnectionDialog.tabRule")}
            </button>
          </div>
        )}

        <form className={styles.dialogBody} onSubmit={handleSubmit}>
          <datalist id={COMMON_PORTS_LIST_ID}>
            {COMMON_PORTS.map((p) => <option key={p.value} value={p.value}>{t(p.labelKey)}</option>)}
          </datalist>

          {tab === "connection" ? (
            <>
              <p className={styles.tabDesc}>{t("ConnectionDialog.tabConnectionDesc")}</p>

              {/* 來源 ⇄ 目標 */}
              <div className={styles.nodeRow}>
                {endSelect(t("ConnectionDialog.source"), sourceKey, pickSource)}
                <button
                  type="button"
                  className={styles.swapBtn}
                  onClick={swapEnds}
                  disabled={editing || nodesLoading}
                  title={t("ConnectionDialog.swapAriaLabel")}
                  aria-label={t("ConnectionDialog.swapAriaLabel")}
                >
                  <MIcon name="swap_horiz" size={20} />
                </button>
                {endSelect(t("ConnectionDialog.target"), targetKey, pickTarget)}
              </div>
              {nodesLoading && <p className={styles.fieldHint}>{t("ConnectionDialog.loadingNodes")}</p>}
              {!nodesLoading && vmNodes.length === 0 && <p className={styles.fieldHint}>{t("ConnectionDialog.noNodes")}</p>}

              {/* 出站：只需確認 */}
              {isOutbound && (
                <p className={styles.infoBox}>
                  <MIcon name="info" size={16} />
                  {t("ConnectionDialog.outboundMessage", { source: labelOf(sourceKey) })}
                </p>
              )}

              {/* 入站：發布方式 */}
              {isInbound && (
                <>
                  <div className={styles.field}>
                    <label className={styles.fieldLabel}>{t("ConnectionDialog.publishMethod")}</label>
                    <div className={styles.modeCards}>
                      {modeCards.map((m) => {
                        const meta = modeMeta(m);
                        return (
                          <button
                            key={m}
                            type="button"
                            className={`${styles.modeCard} ${mode === m ? styles.modeCardActive : ""}`}
                            onClick={() => setMode(m)}
                          >
                            <strong><MIcon name={meta.icon} size={14} /> {t(meta.labelKey)}</strong>
                            <span>{t(meta.descKey)}</span>
                          </button>
                        );
                      })}
                    </div>
                    {setupContext && !domainReady && (
                      <span className={styles.fieldHint}>
                        {setupContext?.reasons?.[0] ?? t("ConnectionDialog.domainUnavailable")}
                      </span>
                    )}
                  </div>

                  {mode === "domain" && (
                    <>
                      <div className={styles.formGrid}>
                        <div className={styles.field}>
                          <label className={styles.fieldLabel} htmlFor="cd-domain-port">{t("ConnectionDialog.portLabel")}</label>
                          {useCustomPort ? (
                            <input
                              id="cd-domain-port"
                              type="number" min="1" max="65535"
                              className={styles.textInput}
                              value={customPort}
                              onChange={(e) => { setError(""); setCustomPort(e.target.value); }}
                              placeholder={t("ConnectionDialog.customPortPlaceholder")}
                            />
                          ) : (
                            <select id="cd-domain-port" className={styles.select} value={commonPort} onChange={(e) => setCommonPort(e.target.value)}>
                              {COMMON_PORTS.map((p) => <option key={p.value} value={p.value}>{t(p.labelKey)}</option>)}
                            </select>
                          )}
                          <button type="button" className={styles.ghostBtn} onClick={() => setUseCustomPort((v) => !v)}>
                            {useCustomPort ? t("ConnectionDialog.backToCommonPorts") : t("ConnectionDialog.portNotListed")}
                          </button>
                        </div>
                        <div className={styles.field}>
                          <label className={styles.fieldLabel}>{t("ConnectionDialog.protocol")}</label>
                          <select className={styles.select} value="tcp" disabled>
                            <option value="tcp">tcp</option>
                          </select>
                          <span className={styles.fieldHint}>{t("ConnectionDialog.domainTcpOnly")}</span>
                        </div>
                      </div>
                      <div className={styles.formGrid}>
                        <div className={styles.field}>
                          <label className={styles.fieldLabel} htmlFor="cd-prefix">{t("ConnectionDialog.prefixLabel")}</label>
                          <input
                            id="cd-prefix"
                            className={styles.textInput}
                            value={prefix}
                            onChange={(e) => { setError(""); setPrefix(e.target.value); }}
                            placeholder={t("ConnectionDialog.prefixPlaceholder")}
                          />
                        </div>
                        <div className={styles.field}>
                          <label className={styles.fieldLabel} htmlFor="cd-zone">{t("ConnectionDialog.zoneLabel")}</label>
                          <select id="cd-zone" className={styles.select} value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
                            {zones.map((z) => <option key={z.id} value={z.id}>.{z.name}</option>)}
                          </select>
                        </div>
                      </div>
                      {fullDomain && (
                        <span className={`${styles.hintLine} ${availabilityTone}`}>
                          <MIcon name={availabilityIcon} size={14} />
                          {availabilityText}
                        </span>
                      )}
                      <label className={styles.checkRow}>
                        <input type="checkbox" checked={enableHttps} onChange={(e) => setEnableHttps(e.target.checked)} />
                        <span>{t("ConnectionDialog.enableHttps")}</span>
                      </label>
                    </>
                  )}

                  {mode === "port_forward" && (
                    <ForwardRows rows={fwdRows} setRows={editRows(setFwdRows)} invalid={portsInvalid} single={editing} />
                  )}

                  {mode === "firewall_only" && (
                    <>
                      <p className={styles.fieldHint}>{t("ConnectionDialog.firewallOnlyHint")}</p>
                      <PortRows
                        rows={fwRows}
                        setRows={editRows(setFwRows)}
                        protocols={editing ? FORWARD_PROTOCOLS : CONNECTION_PROTOCOLS}
                        invalid={portsInvalid}
                        single={editing}
                      />
                    </>
                  )}
                </>
              )}

              {/* VM → VM */}
              {isVmToVm && (
                <>
                  <div className={styles.field}>
                    <label className={styles.fieldLabel}>{t("ConnectionDialog.direction")}</label>
                    <div className={styles.modeToggle}>
                      <button
                        type="button"
                        className={`${styles.modeBtn} ${direction === "one_way" ? styles.modeBtnActive : ""}`}
                        onClick={() => setDirection("one_way")}
                      >
                        {labelOf(sourceKey)} → {labelOf(targetKey)}
                      </button>
                      <button
                        type="button"
                        className={`${styles.modeBtn} ${direction === "bidirectional" ? styles.modeBtnActive : ""}`}
                        onClick={() => setDirection("bidirectional")}
                      >
                        {t("ConnectionDialog.bidirectional")}
                      </button>
                    </div>
                  </div>
                  <p className={styles.fieldHint}>
                    {t("ConnectionDialog.vmToVmHint", { source: labelOf(sourceKey), target: labelOf(targetKey) })}
                  </p>
                  <PortRows rows={vmRows} setRows={editRows(setVmRows)} protocols={CONNECTION_PROTOCOLS} invalid={portsInvalid} />
                </>
              )}
            </>
          ) : (
            <>
              <p className={styles.tabDesc}>{t("ConnectionDialog.tabRuleDesc")}</p>

              {!fixedKey && (
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="cd-rule-vm">{t("ConnectionDialog.ruleVm")}</label>
                  <select id="cd-rule-vm" className={styles.select} value={ruleVmKey} onChange={(e) => setRuleVmKey(e.target.value)} disabled={nodesLoading}>
                    {vmNodes.map((n) => <option key={n.key} value={n.key}>{n.name}</option>)}
                  </select>
                  {nodesLoading && <span className={styles.fieldHint}>{t("ConnectionDialog.loadingNodes")}</span>}
                </div>
              )}

              <div className={styles.formGrid}>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="cd-rule-type">{t("ConnectionDialog.ruleDirection")}</label>
                  <select id="cd-rule-type" className={styles.select} value={rule.type} onChange={(e) => setRuleField("type", e.target.value)}>
                    <option value="in">{t("ConnectionDialog.ruleIn")}</option>
                    <option value="out">{t("ConnectionDialog.ruleOut")}</option>
                  </select>
                </div>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="cd-rule-action">{t("ConnectionDialog.ruleAction")}</label>
                  <select id="cd-rule-action" className={styles.select} value={rule.action} onChange={(e) => setRuleField("action", e.target.value)}>
                    <option value="ACCEPT">{t("ConnectionDialog.actionAccept")}</option>
                    <option value="DROP">{t("ConnectionDialog.actionDrop")}</option>
                    <option value="REJECT">{t("ConnectionDialog.actionReject")}</option>
                  </select>
                </div>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="cd-rule-proto">{t("ConnectionDialog.protocol")}</label>
                  <select id="cd-rule-proto" className={styles.select} value={rule.proto} onChange={(e) => setRuleField("proto", e.target.value)}>
                    <option value="">{t("ConnectionDialog.anyProtocol")}</option>
                    {RULE_PROTOCOLS.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </div>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="cd-rule-dport">{t("ConnectionDialog.rulePort")}</label>
                  <input
                    id="cd-rule-dport"
                    className={styles.textInput}
                    value={rulePortDisabled ? "" : rule.dport}
                    disabled={rulePortDisabled}
                    onChange={(e) => { setError(""); setRuleField("dport", e.target.value); }}
                    placeholder={rulePortDisabled ? t("ConnectionDialog.portlessPlaceholder") : t("ConnectionDialog.rulePortPlaceholder")}
                  />
                </div>
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="cd-rule-addr">
                  {rule.type === "in" ? t("ConnectionDialog.ruleSource") : t("ConnectionDialog.ruleDest")}
                </label>
                <input
                  id="cd-rule-addr"
                  className={styles.textInput}
                  value={rule.source}
                  onChange={(e) => setRuleField("source", e.target.value)}
                  placeholder={t("ConnectionDialog.ruleSourcePlaceholder")}
                />
                <span className={styles.fieldHint}>{t("ConnectionDialog.ruleSourceHint")}</span>
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="cd-rule-comment">{t("ConnectionDialog.ruleComment")}</label>
                <input
                  id="cd-rule-comment"
                  className={styles.textInput}
                  value={rule.comment}
                  onChange={(e) => setRuleField("comment", e.target.value)}
                  placeholder={t("ConnectionDialog.ruleCommentPlaceholder")}
                />
              </div>

              {ruleOverlapsPublish && (
                <p className={styles.infoBox}>
                  <MIcon name="lightbulb" size={16} />
                  <span>
                    {t("ConnectionDialog.ruleOverlapHint")}{" "}
                    <button type="button" className={styles.linkBtn} onClick={() => { setTab("connection"); setError(""); }}>
                      {t("ConnectionDialog.ruleOverlapAction")}
                    </button>
                  </span>
                </p>
              )}
            </>
          )}

          {error && <p className={styles.errorMsg}>{error}</p>}

          <div className={styles.actions}>
            <button type="button" className={styles.cancelBtn} onClick={onClose} disabled={submitting}>
              {t("ConnectionDialog.cancel")}
            </button>
            <button type="submit" className={styles.confirmBtn} disabled={submitDisabled}>
              {submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
