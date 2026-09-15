/**
 * ConnectionDialog — 網路連線／防火牆規則的統一對話框（意圖優先）
 * 三個入口共用：拓撲頁「新增連線」（可由拉線帶入兩端）、資源頁防火牆卡片與
 * 拓撲頁規則面板的「新增規則」（鎖定這台機器）。
 *
 * 使用者先選「要做什麼」，方向由意圖決定，不再自己排來源與目標：
 * - 開放服務給外部（網際網路 → VM）：網址／對外 port／僅開放防火牆三選一，
 *   逐 port 走 publishService，有網域撞名保護；無 port 協定（icmp）走 createConnection。
 * - 讓機器能上網（VM → 網際網路）：不限 port，走 createConnection。
 * - 兩台機器互通（VM → VM）：指定 port 與單向／雙向，走 createConnection。
 * - 自己寫規則：直接寫一條 Proxmox 原始規則，走 createVmRule；不帶 SkyLab: 標記，不上拓撲圖。
 *
 * payload 組裝與驗證在 connectionPayload.js、送出在 submitConnection.js、
 * 意圖推導在 intents.js，三者都是純邏輯且有測試；這個檔案只管表單狀態與呈現。
 *
 * props：
 * - nodes            可選，[{ key, vmid, name }]；沒給就自己抓 getTopology()
 * - fixedVmid        鎖定機器為這台 VM（資源詳情頁用）；fixedName 為顯示名稱備援
 * - initialSource / initialTarget  拉線帶入的兩端（"internet" 或 vmid 字串），能推導出意圖就直接跳過選意圖
 * - initialTab       "rule" 時預選「自己寫規則」（仍可更改）
 * - initialMode      入站預設發布方式 "domain" | "port_forward" | "firewall_only"（網址不可用時退回對外 port）
 * - service          編輯既有對外服務時傳入（鎖定意圖與機器、單一 port，改走 replacePublishedService）
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
import { getTopology } from "../../services/firewall";
import { toDialogNodes } from "./topologyNodes";
import { ReverseProxyService } from "../../services/reverseProxy";
import {
  COMMON_PORTS,
  extractHostnamePrefix,
  findZoneByDomain,
} from "../ReverseProxyRuleModal/ReverseProxyRuleModal";
import {
  buildInboundPayload,
  buildOutboundPorts,
  buildPeerPortsPayload,
  buildRulePayload,
  isPortless,
} from "./connectionPayload";
import { submitEdge, submitInbound, submitRule } from "./submitConnection";
import { INTENT, INTERNET_KEY, deriveInitialState, endsOf, isVmKey } from "./intents";
import IntentPicker from "./IntentPicker";

export { INTERNET_KEY };

const INBOUND_MODES = ["domain", "port_forward", "firewall_only"];
const CONNECTION_PROTOCOLS = ["tcp", "udp", "icmp", "icmpv6", "sctp"];
const FORWARD_PROTOCOLS = ["tcp", "udp"];
const RULE_PROTOCOLS = ["tcp", "udp", "icmp"];
const AVAILABILITY_DEBOUNCE_MS = 500;
const COMMON_PORTS_LIST_ID = "connection-dialog-common-ports";
const EMPTY = [];

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
              <button
                type="button"
                className={styles.removeBtn}
                onClick={() => remove(row.id)}
                disabled={rows.length === 1}
                aria-label={t("ConnectionDialog.removeRow")}
              >
                <MIcon name="close" size={16} />
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
            <button
              type="button"
              className={styles.removeBtn}
              onClick={() => remove(row.id)}
              disabled={rows.length === 1}
              aria-label={t("ConnectionDialog.removeRow")}
            >
              <MIcon name="close" size={16} />
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

  /* ── 送出狀態（放前面，換意圖時要一起清） ── */
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [portsInvalid, setPortsInvalid] = useState(false);
  const editRows = (setter) => (updater) => { setPortsInvalid(false); setError(""); setter(updater); };

  /* ── 意圖與機器 ── */
  const [initial] = useState(() =>
    deriveInitialState({ initialSource, initialTarget, initialTab, fixedKey, editing }),
  );
  const [intent, setIntentState] = useState(initial.intent);
  const [vmKey, setVmKey] = useState(initial.vmKey);
  const [peerSourceKey, setPeerSourceKey] = useState(initial.peerSourceKey);
  const [peerTargetKey, setPeerTargetKey] = useState(initial.peerTargetKey);
  const setIntent = (next) => { setIntentState(next); setError(""); setPortsInvalid(false); };

  const isInbound  = intent === INTENT.PUBLISH;
  const isOutbound = intent === INTENT.OUTBOUND;
  const isVmToVm   = intent === INTENT.PEER;
  const isRule     = intent === INTENT.RULE;

  /* ── 節點清單：沒給就自己抓 ── */
  const [fetchedNodes, setFetchedNodes] = useState(null);
  useEffect(() => {
    if (nodes) return undefined;
    let cancelled = false;
    getTopology()
      .then((topo) => {
        if (cancelled) return;
        setFetchedNodes(toDialogNodes(topo?.nodes));
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

  const labelOf = (key) =>
    key === INTERNET_KEY
      ? t("ConnectionDialog.gatewayLabel")
      : (vmNodes.find((n) => n.key === key)?.name ?? key);
  const getVmid = (key) => (key === INTERNET_KEY ? null : (vmNodes.find((n) => n.key === key)?.vmid ?? null));

  /* 清單載入後修正無效的機器（拉線帶入的 key 不存在、或還沒選） */
  useEffect(() => {
    if (nodesLoading) return;
    const known = (k) => isVmKey(k) && vmNodes.some((n) => n.key === k);
    const fallback = fixedKey ?? vmNodes[0]?.key ?? "";
    setVmKey((k) => (known(k) ? k : fallback));
    setPeerSourceKey((k) => (known(k) ? k : fallback));
  }, [nodesLoading, vmNodes, fixedKey]);

  /* 互通的另一端必須是另一台已知的機器；來源改成跟目標同一台時目標自動讓位 */
  useEffect(() => {
    if (nodesLoading) return;
    setPeerTargetKey((k) => {
      const ok = isVmKey(k) && k !== peerSourceKey && vmNodes.some((n) => n.key === k);
      return ok ? k : (vmNodes.find((n) => n.key !== peerSourceKey)?.key ?? "");
    });
  }, [nodesLoading, vmNodes, peerSourceKey]);

  const { sourceKey, targetKey } = endsOf(intent, { vmKey, peerSourceKey, peerTargetKey });

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

  /* 網址模式：port 直接輸入，常用埠由 datalist 提示 */
  const [domainPort, setDomainPort] = useState(editing ? String(service.port) : "80");
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
    if (!isInbound || mode !== "domain" || !fullDomain || domainUnchanged) {
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
  }, [isInbound, mode, fullDomain, domainUnchanged]);

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
  const [rule, setRule] = useState({ type: "in", action: "ACCEPT", proto: "tcp", dport: "", source: "", comment: "" });
  const setRuleField = (k, v) => setRule((prev) => ({ ...prev, [k]: v }));
  /* Proxmox 的 dport 一定要搭配協定；icmp 類沒有 port */
  const rulePortDisabled = !rule.proto || isPortless(rule.proto);
  const ruleOverlapsPublish = rule.type === "in" && rule.action === "ACCEPT" && !rule.source.trim() && !rulePortDisabled;

  /* ── 送出 ── */
  /** 錯誤優先顯示後端訊息，沒有才用翻譯；partialFailed 的巢狀訊息也在這裡補齊 */
  const describeError = (err) => {
    if (!err) return "";
    if (err.text) return err.text;
    if (!err.key) return t("ConnectionDialog.createFailed");
    const params = { ...(err.params ?? {}) };
    if ("message" in params && !params.message) {
      params.message = t("ConnectionDialog.createFailed");
    }
    return t(err.key, params);
  };

  async function handleSubmit(e) {
    e.preventDefault();
    const form = e.currentTarget;
    setError("");
    if (!intent) return;

    if (isRule) {
      const vmid = getVmid(vmKey);
      if (vmid == null) { setError(t("ConnectionDialog.noNodes")); return; }
      const built = buildRulePayload(rule);
      if (built.error) { setError(describeError(built.error)); return; }
      setSubmitting(true);
      const res = await submitRule({ vmid, body: built.body });
      setSubmitting(false);
      if (res.ok) onDone?.(res.result);
      else setError(describeError(res.error));
      return;
    }

    if (isInbound) {
      const vmid = getVmid(vmKey);
      if (vmid == null) { setError(t("ConnectionDialog.noNodes")); return; }
      const built = buildInboundPayload({
        mode,
        domainPort,
        fullDomain,
        enableHttps,
        domainTaken: availability?.available === false,
        domainTakenText: availability?.message ?? null,
        forwardRows: fwdRows,
        firewallRows: fwRows,
      });
      if (built.error) {
        setError(describeError(built.error));
        if (built.invalid) {
          setPortsInvalid(true);
          focusInvalidField(form.querySelector('input[type="number"]'));
        }
        return;
      }
      setSubmitting(true);
      const res = await submitInbound({ vmid, publish: built.publish, raw: built.raw, service });
      setSubmitting(false);
      if (res.ok) { onDone?.(res.result); return; }
      /* 已經成功的那幾條要先讓呼叫端刷新，否則畫面上看不到它們 */
      if (res.partialDone > 0) onChanged?.();
      setError(describeError(res.error));
      return;
    }

    let ports;
    if (isOutbound) {
      ports = buildOutboundPorts();
    } else if (isVmToVm) {
      const built = buildPeerPortsPayload(vmRows);
      if (built.error) {
        setError(describeError(built.error));
        setPortsInvalid(true);
        focusInvalidField(form.querySelector('input[type="number"]'));
        return;
      }
      ports = built.ports;
    } else {
      return;
    }
    const sourceVmid = getVmid(sourceKey);
    const targetVmid = getVmid(targetKey);
    if ((isVmKey(sourceKey) && sourceVmid == null) || (isVmKey(targetKey) && targetVmid == null)) {
      setError(t("ConnectionDialog.noNodes"));
      return;
    }

    setSubmitting(true);
    const res = await submitEdge({
      sourceVmid,
      targetVmid,
      ports,
      direction: isVmToVm ? direction : "one_way",
    });
    setSubmitting(false);
    if (res.ok) onDone?.(res.result);
    else setError(describeError(res.error));
  }

  /* ── 文案 ── */
  const title = editing
    ? t("ConnectionDialog.titleEditService")
    : isRule ? t("ConnectionDialog.titleRule") : t("ConnectionDialog.title");
  const submitLabel = submitting
    ? t("ConnectionDialog.working")
    : isRule
      ? t("ConnectionDialog.addRule")
      : editing
        ? t("ConnectionDialog.saveChanges")
        : isInbound
          ? t("ConnectionDialog.publish")
          : t("ConnectionDialog.createConnection");
  const machineReady = isVmToVm
    ? isVmKey(peerSourceKey) && isVmKey(peerTargetKey)
    : isVmKey(vmKey);
  const submitDisabled = submitting || nodesLoading || !intent || !machineReady
    || (isInbound && mode === "domain" && (availability?.checking || availability?.available === false));

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

  /* 機器欄位：鎖定（資源頁入口、編輯）就顯示名稱，否則下拉 */
  const machineField = (id, label, value, onPick, { exclude } = {}) => {
    const locked = editing || (fixedKey !== null && value === fixedKey);
    const options = vmNodes.filter((n) => n.key !== exclude);
    return (
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={id}>{label}</label>
        {locked ? (
          <div className={styles.lockedMachine} id={id}>
            <MIcon name="dns" size={16} />
            <span>{labelOf(value)}</span>
          </div>
        ) : (
          <select
            id={id}
            className={styles.select}
            value={value}
            onChange={(e) => onPick(e.target.value)}
            disabled={nodesLoading}
          >
            {options.map((n) => <option key={n.key} value={n.key}>{n.name}</option>)}
          </select>
        )}
        {nodesLoading && <span className={styles.fieldHint}>{t("ConnectionDialog.loadingNodes")}</span>}
        {!nodesLoading && !locked && options.length === 0 && (
          <span className={styles.fieldHint}>{t("ConnectionDialog.noNodes")}</span>
        )}
      </div>
    );
  };

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

        <form className={styles.dialogBody} onSubmit={handleSubmit}>
          <datalist id={COMMON_PORTS_LIST_ID}>
            {COMMON_PORTS.map((p) => <option key={p.value} value={p.value}>{t(p.labelKey)}</option>)}
          </datalist>

          <IntentPicker value={intent} onChange={setIntent} locked={editing} />

          {/* 讓機器能上網：選好機器就能送 */}
          {isOutbound && (
            <>
              {machineField("cd-vm", t("ConnectionDialog.machine"), vmKey, setVmKey)}
              <p className={styles.infoBox}>
                <MIcon name="info" size={16} />
                {t("ConnectionDialog.outboundMessage", { source: labelOf(vmKey) })}
              </p>
            </>
          )}

          {/* 開放服務給外部 */}
          {isInbound && (
            <>
              {machineField("cd-vm", t("ConnectionDialog.machine"), vmKey, setVmKey)}

              <div className={styles.field}>
                <label className={styles.fieldLabel}>{t("ConnectionDialog.publishMethod")}</label>
                <div className={styles.modeCards}>
                  {modeCards.map((m) => {
                    const meta = modeMeta(m);
                    const active = mode === m;
                    return (
                      <button
                        key={m}
                        type="button"
                        className={`${styles.modeCard} ${active ? styles.modeCardActive : ""}`}
                        onClick={() => setMode(m)}
                        aria-pressed={active}
                      >
                        <strong><MIcon name={meta.icon} size={14} /> {t(meta.labelKey)}</strong>
                        {/* 只有選中的那張展開說明，其餘留標題就好 */}
                        {active && <span>{t(meta.descKey)}</span>}
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
                      <input
                        id="cd-domain-port"
                        type="number" min="1" max="65535"
                        list={COMMON_PORTS_LIST_ID}
                        className={styles.textInput}
                        value={domainPort}
                        onChange={(e) => { setError(""); setDomainPort(e.target.value); }}
                        placeholder="80"
                      />
                    </div>
                    <div className={`${styles.field} ${styles.fieldAlignEnd}`}>
                      <label className={styles.checkRow}>
                        <input type="checkbox" checked={enableHttps} onChange={(e) => setEnableHttps(e.target.checked)} />
                        <span>{t("ConnectionDialog.enableHttps")}</span>
                      </label>
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

          {/* 兩台機器互通 */}
          {isVmToVm && (
            <>
              <div className={styles.formGrid}>
                {machineField("cd-peer-source", t("ConnectionDialog.peerSource"), peerSourceKey, setPeerSourceKey)}
                {machineField("cd-peer-target", t("ConnectionDialog.peerTarget"), peerTargetKey, setPeerTargetKey, { exclude: peerSourceKey })}
              </div>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>{t("ConnectionDialog.direction")}</label>
                <div className={styles.modeToggle}>
                  <button
                    type="button"
                    className={`${styles.modeBtn} ${direction === "one_way" ? styles.modeBtnActive : ""}`}
                    onClick={() => setDirection("one_way")}
                  >
                    {labelOf(peerSourceKey)} → {labelOf(peerTargetKey)}
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
                {t("ConnectionDialog.vmToVmHint", { source: labelOf(peerSourceKey), target: labelOf(peerTargetKey) })}
              </p>
              <PortRows rows={vmRows} setRows={editRows(setVmRows)} protocols={CONNECTION_PROTOCOLS} invalid={portsInvalid} />
            </>
          )}

          {/* 自己寫規則 */}
          {isRule && (
            <>
              {machineField("cd-vm", t("ConnectionDialog.machine"), vmKey, setVmKey)}

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
                    <button
                      type="button"
                      className={styles.linkBtn}
                      onClick={() => { setIntent(INTENT.PUBLISH); setMode("firewall_only"); }}
                    >
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
