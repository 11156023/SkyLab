import { useCallback, useEffect, useState } from "react";
import styles from "./ReverseProxyPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { ReverseProxyService } from "../../../services/reverseProxy";
import { ResourcesService } from "../../../services/resources";

const COMMON_PORTS = [
  { value: "80", label: "80 — Nginx / Apache（網頁伺服器）" },
  { value: "443", label: "443 — HTTPS" },
  { value: "3000", label: "3000 — Node.js / React / Next.js" },
  { value: "5000", label: "5000 — Flask / Python" },
  { value: "8000", label: "8000 — FastAPI / Django" },
  { value: "8080", label: "8080 — 常見替代 Port" },
  { value: "8888", label: "8888 — Jupyter Notebook" },
];

function isAdminUser(user) {
  return user?.role === "admin" || user?.is_superuser === true;
}

function findZoneByDomain(domain, zones = []) {
  return [...zones]
    .sort((a, b) => b.name.length - a.name.length)
    .find((zone) => domain === zone.name || domain.endsWith(`.${zone.name}`));
}

function extractHostnamePrefix(domain, zoneName) {
  if (domain === zoneName) return "";
  const suffix = `.${zoneName}`;
  return domain.endsWith(suffix) ? domain.slice(0, -suffix.length) : domain;
}

/* ── How it works（靜態說明） ───────────────────────── */
function HowItWorks() {
  const [open, setOpen] = useState(false);

  const STEPS = [
    {
      num: "1",
      title: "設定網域",
      desc: "輸入主機名稱、選擇 Cloudflare Zone，並指定要綁定的 VM 和 Port。",
    },
    {
      num: "2",
      title: "系統自動設定",
      desc: "平台自動配置路由規則，開啟 HTTPS 時還會自動申請免費的 SSL 憑證。",
    },
    {
      num: "3",
      title: "直接訪問",
      desc: "任何人都可以透過這個網址直接訪問你 VM 裡跑的網站或 API。",
    },
  ];

  const PREREQS = [
    "你的 VM 裡需要有一個正在執行的網站或 API 服務",
    "你需要知道服務跑在哪個 Port（Node.js 預設 3000、Flask 預設 5000、Nginx 預設 80）",
    "管理員需要先在 Cloudflare 域名管理設定預設 A/CNAME 指向與可用 Zone",
  ];

  return (
    <div className={styles.infoCard}>
      <button
        type="button"
        className={styles.infoToggle}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={styles.infoToggleLeft}>
          <MIcon name="help_outline" size={16} />
          這是什麼？反向代理怎麼運作？
        </span>
        <span className={`${styles.infoChevron} ${open ? styles.open : ""}`}>
          <MIcon name="expand_more" size={18} />
        </span>
      </button>

      {open && (
        <div className={styles.infoBody}>
          <div className={styles.steps}>
            {STEPS.map((s) => (
              <div key={s.num} className={styles.step}>
                <div className={styles.stepNum}>{s.num}</div>
                <div className={styles.stepContent}>
                  <span className={styles.stepTitle}>{s.title}</span>
                  <span className={styles.stepDesc}>{s.desc}</span>
                </div>
              </div>
            ))}
          </div>

          <div className={styles.prereqBox}>
            <span className={styles.prereqTitle}>
              <MIcon name="checklist" size={15} />
              前置作業
            </span>
            <ul className={styles.prereqList}>
              {PREREQS.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 規則建立 / 編輯 Modal ──────────────────────────── */
function RuleModal({ rule, setupContext, isAdmin, loading, onClose, onSubmit }) {
  const toast = useToast();
  const zones = setupContext?.zones ?? [];
  const matchedZone = rule
    ? zones.find((z) => z.id === rule.zone_id) ?? findZoneByDomain(rule.domain, zones)
    : null;
  const matchedCommonPort = rule
    ? COMMON_PORTS.find((p) => p.value === String(rule.internal_port))
    : null;

  const [resources, setResources] = useState([]);
  const [loadingResources, setLoadingResources] = useState(true);
  const [form, setForm] = useState({
    vmid: rule ? String(rule.vmid) : "",
    zoneId: matchedZone?.id ?? zones[0]?.id ?? "",
    hostnamePrefix: rule
      ? matchedZone
        ? extractHostnamePrefix(rule.domain, matchedZone.name)
        : rule.domain
      : "",
    port: matchedCommonPort?.value ?? (rule ? "" : "80"),
    customPort: rule && !matchedCommonPort ? String(rule.internal_port) : "",
    useCustomPort: Boolean(rule && !matchedCommonPort),
    enableHttps: rule?.enable_https ?? true,
  });

  useEffect(() => {
    const fetcher = isAdmin ? ResourcesService.listAll() : ResourcesService.list();
    fetcher
      .then((res) => setResources(Array.isArray(res) ? res : res?.data ?? []))
      .catch(() => {})
      .finally(() => setLoadingResources(false));
  }, [isAdmin]);

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  const selectedZone = zones.find((z) => z.id === form.zoneId);
  const effectivePort = form.useCustomPort ? form.customPort : form.port;
  const prefix = form.hostnamePrefix.trim().toLowerCase().replace(/^\.+|\.+$/g, "");
  const previewDomain = selectedZone
    ? prefix
      ? `${prefix}.${selectedZone.name}`
      : selectedZone.name
    : "";

  function submit(e) {
    e.preventDefault();
    const parsedPort = Number(effectivePort);
    if (!form.vmid) {
      toast.error("請先選擇你要綁定的 VM");
      return;
    }
    if (!form.zoneId) {
      toast.error("請先選擇 Cloudflare Zone");
      return;
    }
    if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
      toast.error("Port 必須是 1 到 65535 之間的數字");
      return;
    }
    onSubmit({
      vmid: Number(form.vmid),
      zone_id: form.zoneId,
      hostname_prefix: prefix,
      internal_port: parsedPort,
      enable_https: form.enableHttps,
    });
  }

  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>{rule ? "編輯網域規則" : "新增網域"}</h2>
            <p>
              反向代理網址只能綁定到 Cloudflare 中已存在的 Zone。儲存後，系統會自動把 DNS
              record 指向預設目標並同步 Gateway 路由。
            </p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
            <MIcon name="close" size={18} />
          </button>
        </div>

        {setupContext?.default_dns_target_type && setupContext?.default_dns_target_value && (
          <div className={styles.noticeInfo}>
            <p>
              <strong>自動化目標：</strong>建立或更新網域時，Cloudflare 會自動指向{" "}
              {setupContext.default_dns_target_type} {setupContext.default_dns_target_value}
            </p>
          </div>
        )}

        <label className={styles.field}>
          <span>選擇你的 VM *</span>
          <select value={form.vmid} onChange={(e) => set("vmid", e.target.value)}>
            <option value="">{loadingResources ? "載入 VM 列表..." : "選擇一台 VM..."}</option>
            {resources.map((r) => (
              <option key={r.vmid} value={String(r.vmid)}>
                {r.name}（VM {r.vmid}）
              </option>
            ))}
          </select>
          {!loadingResources && resources.length === 0 && (
            <em className={styles.fieldHint}>你目前沒有任何 VM，請先建立一台 VM。</em>
          )}
        </label>

        <div className={styles.fieldRow}>
          <label className={styles.field}>
            <span>主機名前綴</span>
            <input
              value={form.hostnamePrefix}
              onChange={(e) => set("hostnamePrefix", e.target.value)}
              placeholder="例如 app，留空代表根網域"
            />
          </label>
          <label className={styles.field}>
            <span>Cloudflare Zone *</span>
            <select value={form.zoneId} onChange={(e) => set("zoneId", e.target.value)}>
              <option value="">選擇網域後綴</option>
              {zones.map((zone) => (
                <option key={zone.id} value={zone.id}>{zone.name}</option>
              ))}
            </select>
          </label>
        </div>

        <label className={styles.field}>
          <span>你的服務跑在哪個 Port？*</span>
          {!form.useCustomPort ? (
            <select value={form.port} onChange={(e) => set("port", e.target.value)}>
              {COMMON_PORTS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
          ) : (
            <input
              type="number"
              min={1}
              max={65535}
              value={form.customPort}
              onChange={(e) => set("customPort", e.target.value)}
              placeholder="輸入 Port 號碼（1-65535）"
            />
          )}
          <button
            type="button"
            className={styles.linkBtn}
            onClick={() => set("useCustomPort", !form.useCustomPort)}
          >
            {form.useCustomPort ? "← 選擇常見 Port" : "我的 Port 不在列表中"}
          </button>
        </label>

        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={form.enableHttps}
            onChange={(e) => set("enableHttps", e.target.checked)}
          />
          <span>啟用安全連線（HTTPS）— 自動申請與續期 SSL 憑證</span>
        </label>

        {previewDomain && form.vmid && (
          <div className={styles.noticeInfo}>
            <p>
              <strong>設定預覽：</strong>訪問 {form.enableHttps ? "https" : "http"}://{previewDomain}{" "}
              → 導向 VM {form.vmid} 的 Port {effectivePort}
            </p>
          </div>
        )}

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            取消
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? "儲存中..." : rule ? "儲存網域規則" : "建立網域規則"}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── Traefik Runtime（Admin） ───────────────────────── */
function TraefikPanel() {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || snapshot) return;
    setLoading(true);
    ReverseProxyService.runtime()
      .then(setSnapshot)
      .catch(() => setSnapshot({ runtime_error: "無法連線 Traefik API" }))
      .finally(() => setLoading(false));
  }, [open, snapshot]);

  const sections = snapshot
    ? [
        { label: "HTTP", data: snapshot.http },
        { label: "TCP", data: snapshot.tcp },
        { label: "UDP", data: snapshot.udp },
      ]
    : [];

  return (
    <div className={styles.adminCard}>
      <button
        type="button"
        className={styles.adminToggle}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={styles.adminToggleLeft}>
          <MIcon name="security" size={16} />
          管理員工具 — Traefik Runtime
          <span className={styles.adminBadge}>Admin</span>
        </span>
        <span className={`${styles.infoChevron} ${open ? styles.open : ""}`}>
          <MIcon name="expand_more" size={18} />
        </span>
      </button>

      {open && (
        <div className={styles.adminBody}>
          {loading ? (
            <div className={styles.loading}>載入 Traefik 狀態...</div>
          ) : snapshot?.runtime_error ? (
            <div className={styles.adminMeta}>
              <span className={`${styles.statusPill} ${styles.unknown}`}>
                {snapshot.runtime_error}
              </span>
            </div>
          ) : snapshot ? (
            <>
              <div className={styles.adminMeta}>
                <span className={`${styles.statusPill} ${styles.running}`}>
                  Traefik {snapshot.version?.Version ?? "running"}
                </span>
                <span className={styles.statusPill}>
                  {(snapshot.entrypoints ?? []).length} entrypoints
                </span>
              </div>

              <div className={styles.statsGrid}>
                {sections.map(({ label, data }) => (
                  <div key={label} className={styles.statCard}>
                    <span className={styles.statLabel}>{label}</span>
                    <dl className={styles.statList}>
                      <div>
                        <dt>Routers</dt>
                        <dd className={data?.routers?.length ? styles.numActive : styles.numZero}>
                          {data?.routers?.length ?? 0}
                        </dd>
                      </div>
                      <div>
                        <dt>Services</dt>
                        <dd className={data?.services?.length ? styles.numActive : styles.numZero}>
                          {data?.services?.length ?? 0}
                        </dd>
                      </div>
                      <div>
                        <dt>Middlewares</dt>
                        <dd className={data?.middlewares?.length ? styles.numActive : styles.numZero}>
                          {data?.middlewares?.length ?? 0}
                        </dd>
                      </div>
                    </dl>
                  </div>
                ))}
              </div>

              <div className={styles.entrySection}>
                <span className={styles.entrySectionLabel}>Entrypoints</span>
                <div className={styles.entryList}>
                  {(snapshot.entrypoints ?? []).map((ep) => (
                    <code key={ep.name ?? JSON.stringify(ep)} className={styles.entryChip}>
                      {ep.name} ({ep.address ?? ep.addr ?? "?"})
                    </code>
                  ))}
                </div>
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function ReverseProxyPage() {
  const { user } = useAuth();
  const toast = useToast();
  const isAdmin = isAdminUser(user);

  const [rules, setRules] = useState([]);
  const [setupContext, setSetupContext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [modal, setModal] = useState(null); // { kind: "rule", rule? } | { kind: "delete", rule }

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [rulesRes, ctxRes] = await Promise.all([
        ReverseProxyService.listRules(),
        ReverseProxyService.setupContext().catch(() => null),
      ]);
      setRules(rulesRes ?? []);
      if (ctxRes) setSetupContext(ctxRes);
    } catch (err) {
      toast.error(err?.message ?? "載入網域規則失敗");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const setupBlocked = setupContext?.enabled === false;

  async function handleSubmitRule(payload) {
    setSaving(true);
    try {
      if (modal?.rule) {
        await ReverseProxyService.updateRule(modal.rule.id, payload);
        toast.success("網域規則已更新，Cloudflare 與路由設定已同步");
      } else {
        await ReverseProxyService.createRule(payload);
        toast.success("網域規則建立成功，系統正在自動設定 Cloudflare 與路由");
      }
      setModal(null);
      fetchData();
    } catch (err) {
      toast.error(err?.message ?? "儲存網域規則失敗");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteRule() {
    if (!modal?.rule) return;
    setSaving(true);
    try {
      await ReverseProxyService.deleteRule(modal.rule.id);
      toast.success("網域規則已刪除");
      setModal(null);
      fetchData();
    } catch (err) {
      toast.error(err?.message ?? "刪除失敗");
    } finally {
      setSaving(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    try {
      const res = await ReverseProxyService.syncRules();
      toast.success(res?.message ?? "已重新同步路由");
    } catch (err) {
      toast.error(err?.message ?? "同步失敗");
    } finally {
      setSyncing(false);
    }
  }

  function openCreate() {
    if (setupBlocked) {
      toast.error(setupContext?.reasons?.[0] ?? "反向代理功能目前不可用");
      return;
    }
    setModal({ kind: "rule" });
  }

  return (
    <div className={styles.page}>
      {/* Header */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>反向代理</h1>
          <p className={styles.pageSubtitle}>
            讓別人透過一個好記的網址來訪問你 VM 裡的網站或服務
          </p>
        </div>
        <div className={styles.headerActions}>
          {isAdmin && (
            <button type="button" className={styles.btnSecondary} onClick={handleSync} disabled={syncing}>
              <MIcon name="sync" size={16} />
              {syncing ? "同步中..." : "重新同步"}
            </button>
          )}
          <button type="button" className={styles.btnPrimary} onClick={openCreate}>
            <MIcon name="add" size={16} />
            新增網域
          </button>
        </div>
      </div>

      {setupBlocked && (
        <div className={styles.noticeDanger}>
          <p><strong>反向代理功能暫時不可用</strong></p>
          <p>{(setupContext?.reasons ?? []).join("；") || "請先完成必要設定"}</p>
        </div>
      )}

      {/* How it works */}
      <HowItWorks />

      {/* Route list / empty */}
      <div className={styles.content}>
        {loading ? (
          <div className={styles.loading}>載入網域規則...</div>
        ) : rules.length === 0 ? (
          <div className={styles.empty}>
            <div className={styles.emptyIcon}>
              <MIcon name="swap_horiz" size={36} />
            </div>
            <h2 className={styles.emptyTitle}>尚無設定網域</h2>
            <p className={styles.emptyDesc}>
              網域設定可以讓別人透過一個好記的網址（例如 app.example.edu.tw）直接訪問你 VM
              裡跑的網站或服務，不需要記 IP 和 Port。
            </p>
          </div>
        ) : (
          <div className={styles.list}>
            {rules.map((rule) => (
              <div key={rule.id} className={styles.row}>
                <div className={styles.rowIcon}>
                  <MIcon name="swap_horiz" size={20} />
                </div>
                <div className={styles.rowMain}>
                  <span className={styles.rowName}>{rule.domain}</span>
                  <span className={styles.rowMeta}>
                    VM {rule.vmid}（{rule.vm_ip}）· Port {rule.internal_port}
                    {rule.enable_https && (
                      <span className={styles.badge}>
                        <MIcon name="lock" size={11} /> HTTPS
                      </span>
                    )}
                  </span>
                </div>
                <a
                  className={styles.rowStatus}
                  href={`${rule.enable_https ? "https" : "http"}://${rule.domain}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <MIcon name="open_in_new" size={14} />
                  開啟
                </a>
                <div className={styles.rowActions}>
                  <button
                    type="button"
                    className={styles.actionBtn}
                    title="編輯"
                    onClick={() => setModal({ kind: "rule", rule })}
                  >
                    <MIcon name="edit" size={16} />
                  </button>
                  <button
                    type="button"
                    className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                    title="刪除"
                    onClick={() => setModal({ kind: "delete", rule })}
                  >
                    <MIcon name="delete" size={16} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Admin: Traefik */}
      {isAdmin && <TraefikPanel />}

      {modal?.kind === "rule" && (
        <RuleModal
          rule={modal.rule}
          setupContext={setupContext}
          isAdmin={isAdmin}
          loading={saving}
          onClose={() => setModal(null)}
          onSubmit={handleSubmitRule}
        />
      )}
      {modal?.kind === "delete" && (
        <div className={styles.modalOverlay} onMouseDown={() => setModal(null)}>
          <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
            <div className={styles.confirmIcon}>
              <MIcon name="warning" size={24} />
            </div>
            <h2>刪除網域規則</h2>
            <p>
              確定要刪除 <strong>{modal.rule.domain}</strong> 嗎？Cloudflare DNS 與 Gateway
              路由會一併移除。
            </p>
            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => setModal(null)}>
                取消
              </button>
              <button type="button" className={styles.btnDanger} disabled={saving} onClick={handleDeleteRule}>
                {saving ? "刪除中..." : "刪除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
