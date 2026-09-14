/**
 * OverviewTab — 總覽
 * 身分卡（名稱、狀態、位置、標籤）＋ 四格資源指標（CPU／記憶體／磁碟／運行時間，
 * 執行中每 10 秒更新即時用量）＋ 環境資訊、連線與憑證、來源範本的使用手冊。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../../../contexts/AuthContext";
import styles from "./ResourceDetailPage.module.scss";
import ov from "./OverviewTab.module.scss";
import MIcon from "../../../../components/MIcon";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import useAutoRefresh from "../../../../hooks/useAutoRefresh";
import { ResourcesService } from "../../../../services/resources";
import { downloadBlob } from "../../../../services/api";
import { useToast } from "../../../../hooks/useToast";

const STATUS_META = {
  running: { labelKey: "OverviewTab.statusRunning", tone: "ok" },
  stopped: { labelKey: "OverviewTab.statusStopped", tone: "muted" },
  paused:  { labelKey: "OverviewTab.statusPaused",  tone: "muted" },
};

const TYPE_META = {
  qemu: { labelKey: "OverviewTab.typeQemu", icon: "computer" },
  lxc:  { labelKey: "OverviewTab.typeLxc",  icon: "terminal" },
};

const ROLE_KEYS = {
  owner: "OverviewTab.roleOwner",
  shared: "OverviewTab.roleShared",
  class_member: "OverviewTab.roleClassMember",
  admin: "OverviewTab.roleAdmin",
};

/* 與進階設定的 LifecycleCard 共用同一組原因文案 */
const AUTO_STOP_REASON_KEYS = {
  window_grace: "LifecycleCard.reasonWindowGrace",
  practice_quota: "LifecycleCard.reasonPracticeQuota",
  ttl_expired: "LifecycleCard.reasonTtlExpired",
  idle: "LifecycleCard.reasonIdle",
};

const LIVE_INTERVAL = 10_000;
const RESOURCE_INTERVAL = 30_000;
const GB = 1024 ** 3;
const MB = 1024 ** 2;
const MASK = "••••••••••••";

/* ── helpers ── */

/** 把 bytes 拆成「數字 + 單位」給指標格用；GB 以下改用 MB 顯示 */
function splitBytes(bytes) {
  if (!bytes) return { value: "—", unit: "" };
  if (bytes >= GB) {
    const gb = bytes / GB;
    return { value: String(gb >= 100 ? Math.round(gb) : Number(gb.toFixed(1))), unit: "GB" };
  }
  return { value: String(Math.round(bytes / MB)), unit: "MB" };
}

function formatBytes(bytes) {
  const { value, unit } = splitBytes(bytes);
  return unit ? `${value} ${unit}` : value;
}

function formatUptime(seconds, t) {
  if (!seconds || seconds <= 0) return null;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return t("OverviewTab.uptimeDays", { days, hours });
  if (hours > 0) return t("OverviewTab.uptimeHours", { hours, minutes });
  return t("OverviewTab.uptimeMinutes", { minutes });
}

/* expiry_date 是純日期字串（YYYY-MM-DD）；用本地時區拆解，避免 UTC 解析在時區邊界差一天 */
function parseDateOnly(value) {
  const [y, m, d] = String(value).slice(0, 10).split("-").map(Number);
  return y && m && d ? new Date(y, m - 1, d) : new Date(value);
}

function daysUntil(dateStr) {
  const target = parseDateOnly(dateStr);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86400000);
}

function formatDate(value, lang) {
  if (!value) return null;
  return parseDateOnly(value).toLocaleDateString(lang, { year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatDateTime(value, lang) {
  if (!value) return null;
  return new Date(value).toLocaleString(lang, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/* ── sub-components ── */

function Kpi({ icon, label, value, unit, caption, pct, text = false }) {
  const showBar = typeof pct === "number" && Number.isFinite(pct);
  return (
    <div className={ov.kpi}>
      <div className={ov.kpiHead}>
        <span className={ov.kpiLabel}>{label}</span>
        <span className={ov.kpiIcon}>
          <MIcon name={icon} size={18} />
        </span>
      </div>
      <div className={`${ov.kpiValue} ${text ? ov.kpiValue_text : ""}`}>
        {value}
        {unit && <span className={ov.kpiUnit}>{unit}</span>}
      </div>
      {caption && <span className={ov.kpiCaption}>{caption}</span>}
      {showBar && (
        <div className={ov.bar} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div
            className={`${ov.barFill} ${pct >= 90 ? ov.barFill_danger : ""}`}
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, note, children }) {
  return (
    <div className={ov.row}>
      <span className={ov.rowLabel}>{label}</span>
      <div className={ov.rowValue}>
        <span className={ov.rowMain}>{children}</span>
        {note && <span className={ov.rowNote}>{note}</span>}
      </div>
    </div>
  );
}

/**
 * 憑證列：secret=true 時預設遮罩、點「顯示」才展開；公鑰不遮罩，只是「展開」把整段秀出來。
 * 展開後完整內容放在下方的 pre，方便整段選取。
 */
function SecretRow({ label, value, secret = false, note, copyId, copied, onCopy, downloadName, t }) {
  const [open, setOpen] = useState(false);
  const toggleLabel = secret
    ? (open ? t("OverviewTab.hide") : t("OverviewTab.show"))
    : (open ? t("OverviewTab.collapse") : t("OverviewTab.expand"));
  const toggleIcon = secret
    ? (open ? "visibility_off" : "visibility")
    : (open ? "unfold_less" : "unfold_more");

  return (
    <div className={ov.secret}>
      <div className={ov.secretHead}>
        <span className={ov.secretLabel}>{label}</span>
        {!open && (
          <span className={`${ov.secretValue} ${secret ? ov.secretMasked : ""}`} title={secret ? undefined : value}>
            {secret ? MASK : value}
          </span>
        )}
        <div className={ov.secretActions}>
          <button type="button" className={styles.ghostBtn} onClick={() => setOpen((v) => !v)}>
            <MIcon name={toggleIcon} size={14} />
            {toggleLabel}
          </button>
          <button type="button" className={styles.ghostBtn} onClick={() => onCopy(value, copyId)}>
            <MIcon name={copied === copyId ? "check" : "content_copy"} size={14} />
            {copied === copyId ? t("OverviewTab.copied") : t("OverviewTab.copy")}
          </button>
          {downloadName && (
            <button
              type="button"
              className={styles.ghostBtn}
              onClick={() => downloadBlob(new Blob([value], { type: "text/plain" }), downloadName)}
            >
              <MIcon name="download" size={14} />
              {t("OverviewTab.download")}
            </button>
          )}
        </div>
      </div>
      {open && <pre className={ov.secretPre}>{value}</pre>}
      {note && <span className={`${ov.rowNote} ${ov.secretNote}`}>{note}</span>}
    </div>
  );
}

/* ── main ── */

export default function OverviewTab({ vmid }) {
  const { t, i18n } = useTranslation("personal");
  const lang = i18n.language || "zh-TW";
  const toast = useToast();
  const { user } = useAuth();
  /* VMID 是系統內部編號，僅管理員／老師看得到 */
  const showVmid = user?.is_superuser || user?.role === "admin" || user?.role === "teacher";

  const [resource, setResource] = useState(null);
  const [live, setLive] = useState(null);
  const [sshKey, setSshKey] = useState(null);
  const [manual, setManual] = useState(null);
  const [copied, setCopied] = useState("");
  const [downloadingId, setDownloadingId] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setResource(null);
    setLive(null);
    setSshKey(null);
    setError(false);
    ResourcesService.get(vmid)
      .then((r) => {
        if (cancelled) return;
        setResource(r);
        if (r.ssh_public_key || r.has_login_password) {
          ResourcesService.getSshKey(vmid)
            .then((k) => !cancelled && setSshKey(k))
            .catch(() => {});
        }
      })
      .catch(() => !cancelled && setError(true));
    // 來源範本手冊（非克隆機或無附件時 count=0，不顯示區塊）
    ResourcesService.getTemplateManual(vmid)
      .then((m) => !cancelled && setManual(m))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [vmid]);

  /* 即時用量：一進來先抓一次（拿磁碟容量），之後只在執行中時每 10 秒更新 */
  const loadLive = useCallback(async () => {
    try {
      setLive(await ResourcesService.getCurrentStats(vmid));
    } catch {
      /* 下一輪再試 */
    }
  }, [vmid]);

  useEffect(() => {
    loadLive();
  }, [loadLive]);

  const isRunning = resource?.status === "running";
  useAutoRefresh(() => {
    if (isRunning) loadLive();
  }, LIVE_INTERVAL);

  /* 狀態、到期、自動關機等會在別處改變，靜默重抓讓卡片跟得上 */
  useAutoRefresh(async () => {
    try {
      setResource(await ResourcesService.get(vmid));
    } catch {
      /* 保留上一筆 */
    }
  }, RESOURCE_INTERVAL);

  const downloadManual = async (attachment) => {
    setDownloadingId(attachment.id);
    try {
      const blob = await ResourcesService.downloadTemplateManual(vmid, attachment.id);
      downloadBlob(blob, attachment.filename);
    } catch (e) {
      toast.error(e?.message ?? t("OverviewTab.downloadFailed"));
    } finally {
      setDownloadingId(null);
    }
  };

  const copy = async (text, id) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(id);
      setTimeout(() => setCopied(""), 2000);
    } catch {
      toast.error(t("OverviewTab.copyFailed"));
    }
  };

  if (error) return <p className={styles.stateText}>{t("OverviewTab.loadFailed")}</p>;
  if (!resource) return <LoadingState />;

  const statusMeta = STATUS_META[resource.status] ?? { label: String(resource.status), tone: "info" };
  const typeMeta = TYPE_META[String(resource.type).toLowerCase()]
    ?? { label: String(resource.type).toUpperCase(), icon: "dns" };

  /* 用量只在執行中才有意義；關機的機器只秀配置量 */
  const cpuRatio = live?.cpu ?? resource.cpu;
  const cpuPct = isRunning && cpuRatio != null ? Math.round(cpuRatio * 100) : null;
  const memMax = live?.maxmem ?? resource.maxmem ?? null;
  const memUsed = isRunning ? (live?.mem ?? resource.mem ?? null) : null;
  const memPct = memUsed != null && memMax ? Math.round((memUsed / memMax) * 100) : null;
  const diskMax = live?.maxdisk ?? null;
  const diskUsed = isRunning && live?.disk ? live.disk : null;   // VM 沒裝 guest agent 時 disk 為 0
  const diskPct = diskUsed != null && diskMax ? Math.round((diskUsed / diskMax) * 100) : null;
  const uptimeSec = isRunning ? (live?.uptime ?? resource.uptime ?? null) : null;
  const uptimeText = formatUptime(uptimeSec, t);
  const bootedAt = uptimeSec ? formatDateTime(new Date(Date.now() - uptimeSec * 1000), lang) : null;
  const mem = splitBytes(memMax);
  const disk = splitBytes(diskMax);

  const daysLeft = resource.expiry_date ? daysUntil(resource.expiry_date) : null;
  const expiryDanger = daysLeft != null && daysLeft <= 7;
  const expiryText = (() => {
    if (daysLeft == null) return t("OverviewTab.expiryUnlimited");
    if (daysLeft === 0) return t("OverviewTab.expiryToday");
    if (daysLeft < 0) return t("OverviewTab.expiryExpired", { count: -daysLeft });
    return t("OverviewTab.expiryDaysLeft", { count: daysLeft });
  })();

  const reasonKey = resource.auto_stop_reason ? AUTO_STOP_REASON_KEYS[resource.auto_stop_reason] : null;
  const roleKey = ROLE_KEYS[resource.access_role] ?? ROLE_KEYS.owner;
  const hasCredentials = Boolean(sshKey?.login_password || resource.ssh_public_key);

  return (
    <div className={styles.tabStack}>
      {/* 身分卡 */}
      <section className={`${styles.card} ${ov.hero}`}>
        <div className={ov.heroTop}>
          <div className={ov.identity}>
            <span className={ov.typeIcon}>
              <MIcon name={typeMeta.icon} size={28} />
            </span>
            <div className={ov.nameBlock}>
              <h2 className={ov.name} title={resource.name}>{resource.name}</h2>
              <div className={ov.subline}>
                <span>{typeMeta.labelKey ? t(typeMeta.labelKey) : typeMeta.label}</span>
                <span className={ov.sep} aria-hidden="true" />
                <span>
                  <MIcon name="dns" size={14} />
                  {resource.node}
                </span>
                {showVmid && (
                  <>
                    <span className={ov.sep} aria-hidden="true" />
                    <span className={ov.mono}>VMID {resource.vmid}</span>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className={ov.heroSide}>
            <span className={`${ov.status} ${ov[`status_${statusMeta.tone}`]}`}>
              <span className={`${ov.statusDot} ${isRunning ? ov.statusDot_live : ""}`} aria-hidden="true" />
              {statusMeta.labelKey ? t(statusMeta.labelKey) : statusMeta.label}
            </span>
            <span className={`${ov.expiry} ${expiryDanger ? ov.expiry_danger : ""}`}>
              <MIcon name="event" size={14} />
              {resource.expiry_date
                ? `${formatDate(resource.expiry_date, lang)} · ${expiryText}`
                : expiryText}
            </span>
          </div>
        </div>

        <div className={ov.chips}>
          {resource.ip_address ? (
            <button
              type="button"
              className={`${ov.chip} ${ov.chipBtn} ${ov.chipMono}`}
              title={t("OverviewTab.copyIp")}
              onClick={() => copy(resource.ip_address, "ip")}
            >
              <MIcon name={copied === "ip" ? "check" : "content_copy"} size={14} />
              {resource.ip_address}
            </button>
          ) : (
            <span className={ov.chip}>
              <MIcon name="wifi_off" size={14} />
              {t("OverviewTab.noIp")}
            </span>
          )}
          {resource.os_info && (
            <span className={ov.chip}>
              <MIcon name="album" size={14} />
              {resource.os_info}
            </span>
          )}
          {resource.environment_type && (
            <span className={ov.chip}>
              <MIcon name="category" size={14} />
              {resource.environment_type}
            </span>
          )}
          {(resource.tags ?? []).map((tag) => (
            <span key={tag} className={`${ov.chip} ${ov.chipTag}`}>
              <MIcon name="label" size={14} />
              {tag}
            </span>
          ))}
        </div>
      </section>

      {/* 資源指標 */}
      <div className={ov.kpiGrid}>
        <Kpi
          icon="memory"
          label="CPU"
          value={resource.maxcpu ?? "—"}
          unit={t("OverviewTab.coresUnit")}
          caption={cpuPct != null ? t("OverviewTab.liveUsage", { pct: cpuPct }) : t("OverviewTab.allocated")}
          pct={cpuPct}
        />
        <Kpi
          icon="sd_card"
          label={t("MonitoringTab.memory")}
          value={mem.value}
          unit={mem.unit}
          caption={memPct != null ? t("OverviewTab.liveUsage", { pct: memPct }) : t("OverviewTab.allocated")}
          pct={memPct}
        />
        <Kpi
          icon="storage"
          label={t("MonitoringTab.disk")}
          value={disk.value}
          unit={disk.unit}
          caption={
            diskPct != null
              ? t("OverviewTab.diskUsage", { used: formatBytes(diskUsed), pct: diskPct })
              : (diskMax ? t("OverviewTab.allocated") : t("OverviewTab.noDiskData"))
          }
          pct={diskPct}
        />
        <Kpi
          icon="schedule"
          label={t("OverviewTab.uptimeLabel")}
          value={uptimeText ?? "—"}
          text
          caption={uptimeText ? t("OverviewTab.uptimeSince", { time: bootedAt }) : t("OverviewTab.notRunning")}
        />
      </div>

      {/* 使用手冊（克隆機來源範本附件） */}
      {(manual?.count ?? 0) > 0 && (
        <section className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <h2 className={styles.cardTitle}>
                <MIcon name="menu_book" size={18} />
                {t("OverviewTab.manualTitle")}
              </h2>
              <p className={styles.cardDesc}>{t("OverviewTab.manualDesc", { name: manual.template_name })}</p>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={ov.manualRow}>
              {manual.data.map((a) => (
                <div key={a.id} className={ov.manualChip}>
                  <span className={ov.manualIcon}>
                    <MIcon name="description" size={18} />
                  </span>
                  <span className={ov.manualName} title={a.filename}>{a.filename}</span>
                  <button
                    type="button"
                    className={ov.manualDl}
                    disabled={downloadingId === a.id}
                    onClick={() => downloadManual(a)}
                  >
                    <MIcon name="download" size={16} />
                    {downloadingId === a.id ? t("OverviewTab.downloading") : t("OverviewTab.download")}
                  </button>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      <div className={ov.grid2}>
        {/* 環境資訊 */}
        <section className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <h2 className={styles.cardTitle}>
                <MIcon name="info" size={18} />
                {t("OverviewTab.envInfoTitle")}
              </h2>
              <p className={styles.cardDesc}>{t("OverviewTab.envInfoDesc")}</p>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={ov.list}>
              {showVmid && (
                <InfoRow label={t("OverviewTab.idLabel")}>
                  <span className={ov.mono}>{resource.vmid}</span>
                </InfoRow>
              )}
              <InfoRow label={t("OverviewTab.nodeLabel")}>{resource.node}</InfoRow>
              <InfoRow label={t("OverviewTab.envTypeLabel")}>
                {resource.environment_type ?? <span className={ov.muted}>{t("OverviewTab.notSet")}</span>}
              </InfoRow>
              <InfoRow label={t("OverviewTab.osLabel")}>
                {resource.os_info ?? <span className={ov.muted}>{t("OverviewTab.notSet")}</span>}
              </InfoRow>
              <InfoRow label={t("OverviewTab.expiryLabel")}>
                {resource.expiry_date ? (
                  <>
                    {formatDate(resource.expiry_date, lang)}
                    <span className={`${ov.pill} ${expiryDanger ? ov.pill_danger : ""}`}>{expiryText}</span>
                  </>
                ) : (
                  <span className={ov.muted}>{expiryText}</span>
                )}
              </InfoRow>
              {resource.auto_stop_at && (
                <InfoRow label={t("OverviewTab.autoStopLabel")} note={reasonKey ? t(reasonKey) : null}>
                  {formatDateTime(resource.auto_stop_at, lang)}
                </InfoRow>
              )}
              {resource.idle_since && (
                <InfoRow label={t("OverviewTab.idleSinceLabel")}>
                  {formatDateTime(resource.idle_since, lang)}
                </InfoRow>
              )}
              {resource.scheduled_deletion_at && (
                <InfoRow label={t("OverviewTab.scheduledDeletionLabel")}>
                  <span className={ov.dangerText}>{formatDateTime(resource.scheduled_deletion_at, lang)}</span>
                </InfoRow>
              )}
              <InfoRow
                label={t("OverviewTab.accessRoleLabel")}
                note={resource.access_role === "shared" && resource.owner_email
                  ? t("OverviewTab.sharedBy", { email: resource.owner_email })
                  : null}
              >
                {t(roleKey)}
              </InfoRow>
            </div>
          </div>
        </section>

        {/* 連線與憑證 */}
        <section className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <h2 className={styles.cardTitle}>
                <MIcon name="vpn_key" size={18} />
                {t("OverviewTab.accessTitle")}
              </h2>
              <p className={styles.cardDesc}>{t("OverviewTab.accessDesc")}</p>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={ov.list}>
              <InfoRow label={t("OverviewTab.ipLabel")}>
                {resource.ip_address ? (
                  <>
                    <span className={ov.mono}>{resource.ip_address}</span>
                    <button type="button" className={styles.ghostBtn} onClick={() => copy(resource.ip_address, "ip-row")}>
                      <MIcon name={copied === "ip-row" ? "check" : "content_copy"} size={14} />
                      {copied === "ip-row" ? t("OverviewTab.copied") : t("OverviewTab.copy")}
                    </button>
                  </>
                ) : (
                  <span className={ov.muted}>{t("OverviewTab.noIp")}</span>
                )}
              </InfoRow>
              {sshKey?.login_password && (
                <SecretRow
                  label={t("OverviewTab.passwordLabel")}
                  value={sshKey.login_password}
                  secret
                  note={t("OverviewTab.loginPasswordDesc")}
                  copyId="password"
                  copied={copied}
                  onCopy={copy}
                  t={t}
                />
              )}
              {resource.ssh_public_key && (
                <SecretRow
                  label={t("OverviewTab.publicKeyLabel")}
                  value={resource.ssh_public_key}
                  note={t("OverviewTab.sshKeyDesc")}
                  copyId="public"
                  copied={copied}
                  onCopy={copy}
                  t={t}
                />
              )}
              {sshKey?.ssh_private_key && (
                <SecretRow
                  label={t("OverviewTab.privateKeyLabel")}
                  value={sshKey.ssh_private_key}
                  secret
                  copyId="private"
                  copied={copied}
                  onCopy={copy}
                  downloadName={`id_ed25519_vm${vmid}`}
                  t={t}
                />
              )}
              {!hasCredentials && <p className={ov.emptyNote}>{t("OverviewTab.noCredentials")}</p>}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
