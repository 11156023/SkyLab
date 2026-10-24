/**
 * ResourceDetailGuideDemo — 資源詳情的導覽示範頁（/my-resources/demo）
 * 沒有真的機器可看時，導覽改用這頁講解六個分頁。版面一律照正式分頁畫：
 * 共用正式分頁的子元件（KpiCard、StatCard、RrdChart、SliderField、InfoRow、SecretRow）與同一套 class，
 * 正式頁改版時示範頁跟著變。所有按鈕都不會呼叫後端，只有拉桿、分頁切換、複製這類純前端互動照常動作。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./ResourceDetailPage.module.scss";
import ov from "./OverviewTab.module.scss";
import sl from "./SpecificationsTab.module.scss";
import MIcon from "../../../../components/MIcon";
import DataTable, { DataTableCells } from "../../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../../components/DataTable/features";
import MachineKindBadge from "../../../../components/MachineKindBadge/MachineKindBadge";
import RrdChart from "../../../../components/RrdChart/RrdChart";
import SegmentedControl from "../../../../components/SegmentedControl/SegmentedControl";
import Switch from "../../../../components/Switch/Switch";
import KpiCard from "./KpiCard";
import { InfoRow, MASK, SecretRow } from "./OverviewTab";
import { StatCard } from "./MonitoringTab";
import { SliderField } from "./SpecificationsTab";
import { coreSegments, gbSegments } from "./kpiBar";
import { formatDate, formatDateTime as formatLocalDateTime } from "./lifecycleFormat";
import { actionBadgeClass, actionLabel } from "./auditActions";
import { formatDateTime, formatTime } from "../../../../utils/formatDate";
import useDragReorder from "../../../../hooks/useDragReorder";

const GB = 1024 ** 3;
const DEMO_NAME = "demo-web-01";
const DEMO_NODE = "pve";
const DEMO_OS = "Ubuntu 24.04 LTS";
const DEMO_IP = "10.20.0.24";
const DEMO_URL = "https://demo-web-01.example.edu";
const DEMO_USER = "student";
/* 到期日跟著今天往後推，「剩幾天」永遠是同一個數字，示範頁不會哪天變成已到期 */
const DEMO_DAYS_LEFT = 82;
const DEMO_UPTIME = { days: 3, hours: 4 };

const TIMEFRAMES = [
  { value: "hour",  labelKey: "MonitoringTab.timeframeHour" },
  { value: "day",   labelKey: "MonitoringTab.timeframeDay" },
  { value: "week",  labelKey: "MonitoringTab.timeframeWeek" },
  { value: "month", labelKey: "MonitoringTab.timeframeMonth" },
  { value: "year",  labelKey: "MonitoringTab.timeframeYear" },
];

const CHART_TABS = [
  { key: "cpu",     label: "CPU" },
  { key: "memory",  labelKey: "MonitoringTab.memory" },
  { key: "network", labelKey: "MonitoringTab.network" },
];

/* 一筆受保護的初始快照（只能還原）、一筆一般快照（可還原、刪除），對應正式分頁的兩種列 */
const DEMO_SNAPSHOTS = [
  { name: "skylab-init", descKey: "ResourceDetailPage.guideDemoSnapshotInitDesc", time: new Date(2026, 8, 1, 9, 0), protected: true },
  { name: "before-upgrade", descKey: "ResourceDetailPage.guideDemoSnapshotDesc", time: new Date(2026, 8, 10, 14, 30), protected: false },
];

/* 時間跟快照分頁對得上：建立機器時留下初始快照、升級前建了 before-upgrade */
const DEMO_AUDIT = [
  { id: 4, time: new Date(2026, 8, 12, 9, 5), action: "resource_start", details: DEMO_NAME },
  { id: 3, time: new Date(2026, 8, 11, 16, 20), action: "firewall_rule_create", details: "TCP 80, 443" },
  { id: 2, time: new Date(2026, 8, 10, 14, 30), action: "snapshot_create", details: "before-upgrade" },
  { id: 1, time: new Date(2026, 8, 1, 9, 0), action: "vm_create", details: DEMO_NAME },
];

/* 進站 SSH、網頁兩條自訂規則，加一條連線產生的鎖定規則（正式頁備註欄會標「由連線管理」） */
const DEMO_RULES = [
  { pos: 0, type: "in", proto: "TCP", port: "22", peer: null, comment: "SSH" },
  { pos: 1, type: "in", proto: "TCP", port: "80, 443", peer: null, comment: "HTTP / HTTPS" },
  { pos: 2, type: "out", proto: "TCP", port: "3306", peer: "10.20.0.31", managed: true },
];

const KIND_ICON = { disk: "storage", cdrom: "album", network: "lan" };
const DEMO_ISO = "ubuntu-24.04-live-server-amd64.iso";
const DEMO_BOOT = [
  { key: "scsi0", kind: "disk", desc: "local-lvm · 40 GB" },
  { key: "net0", kind: "network", desc: "virtio" },
  { key: "ide2", kind: "cdrom", desc: DEMO_ISO },
];

function demoExpiryDate() {
  const date = new Date(Date.now() + DEMO_DAYS_LEFT * 86_400_000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/** 歷史圖表的示範資料：最近一小時每分鐘一點，起伏固定（每次打開長一樣） */
function demoSeries() {
  const now = Date.now();
  return Array.from({ length: 60 }, (_, i) => {
    const wave = Math.sin(i / 6);
    return {
      time: formatTime(now - (59 - i) * 60_000),
      cpu: Number((34 + wave * 8 + (i % 5)).toFixed(2)),
      memory: Number((56 + Math.cos(i / 9) * 4).toFixed(2)),
      netin: Number((120 + wave * 60 + (i % 7) * 8).toFixed(2)),
      netout: Number((40 + Math.cos(i / 5) * 15).toFixed(2)),
    };
  });
}

function formatGb(mb) {
  const gb = mb / 1024;
  return Number.isInteger(gb) ? String(gb) : gb.toFixed(1);
}

/** 帶正負號的差值，寫法同規格分頁（負號用 U+2212） */
function signed(delta) {
  return `${delta > 0 ? "+" : "−"}${Math.abs(delta)}`;
}

/** 複製示範值：只有剪貼簿，不打後端；兩秒後把「已複製」換回來 */
function useDemoCopy() {
  const [copied, setCopied] = useState("");
  const timerRef = useRef(null);
  useEffect(() => () => clearTimeout(timerRef.current), []);
  const copy = async (text, id) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    setCopied(id);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(""), 2000);
  };
  return [copied, copy];
}

function CopyButton({ id, text, copied, onCopy, t }) {
  return (
    <button type="button" className={styles.btnSecondary} onClick={() => onCopy(text, id)}>
      <MIcon name={copied === id ? "check" : "content_copy"} size={14} />
      {copied === id ? t("OverviewTab.copied") : t("OverviewTab.copy")}
    </button>
  );
}

/* ── 總覽：身分卡 → 四張指標卡 → 環境資訊／連線與憑證 ── */
function DemoOverview({ t, lang }) {
  const [copied, copy] = useDemoCopy();
  const expiry = useMemo(demoExpiryDate, []);
  const bootedAt = useMemo(
    () => formatLocalDateTime(new Date(Date.now() - (DEMO_UPTIME.days * 24 + DEMO_UPTIME.hours) * 3_600_000), lang),
    [lang],
  );
  const expiryText = t("OverviewTab.expiryDaysLeft", { count: DEMO_DAYS_LEFT });

  return (
    <div className={styles.tabStack}>
      <section className={`${styles.card} ${ov.hero}`}>
        <div className={ov.heroTop}>
          <div className={ov.identity}>
            <span className={ov.typeIcon}>
              <MIcon name="computer" size={28} />
            </span>
            <div className={ov.nameBlock}>
              <h2 className={ov.name}>{DEMO_NAME}</h2>
              <div className={ov.subline}>
                <span>{t("OverviewTab.typeQemu")}</span>
                <span className={ov.sep} aria-hidden="true" />
                <span>
                  <MIcon name="dns" size={14} />
                  {DEMO_NODE}
                </span>
                <span className={ov.sep} aria-hidden="true" />
                <MachineKindBadge plain kind="personal" />
              </div>
            </div>
          </div>
          <div className={ov.heroSide}>
            <span className={`${ov.status} ${ov.status_success}`}>
              <span className={ov.statusDot} aria-hidden="true" />
              {t("OverviewTab.statusRunning")}
            </span>
            <span className={ov.expiry}>
              <MIcon name="event" size={14} />
              {`${formatDate(expiry, lang)} · ${expiryText}`}
            </span>
          </div>
        </div>
        <div className={ov.chips}>
          <button
            type="button"
            className={`${ov.chip} ${ov.chipBtn} ${ov.chipMono}`}
            title={t("OverviewTab.copyIp")}
            onClick={() => copy(DEMO_IP, "ip")}
          >
            <MIcon name={copied === "ip" ? "check" : "content_copy"} size={14} />
            {DEMO_IP}
          </button>
          {/* 示範網址不是真的站，畫成晶片但不做成連結 */}
          <span className={`${ov.chip} ${ov.chipLink}`}>
            <MIcon name="open_in_new" size={14} />
            {DEMO_URL.replace(/^https?:\/\//, "")}
          </span>
          <span className={ov.chip}>
            <MIcon name="album" size={14} />
            {DEMO_OS}
          </span>
        </div>
      </section>

      <div className={ov.kpiGrid}>
        <KpiCard
          icon="memory"
          label="CPU"
          value={2}
          unit={t("OverviewTab.coresUnit")}
          caption={t("OverviewTab.liveUsage", { pct: 36 })}
          pct={36}
          segments={coreSegments(2)}
          live
        />
        <KpiCard
          icon="sd_card"
          label={t("MonitoringTab.memory")}
          value={4}
          unit="GB"
          caption={t("OverviewTab.liveUsage", { pct: 58 })}
          pct={58}
          segments={gbSegments(4 * GB)}
          live
        />
        <KpiCard
          icon="storage"
          label={t("MonitoringTab.disk")}
          value={40}
          unit="GB"
          caption={t("OverviewTab.diskUsage", { used: "16.8 GB", pct: 42 })}
          pct={42}
          segments={gbSegments(40 * GB)}
          live
        />
        <KpiCard
          icon="schedule"
          label={t("OverviewTab.uptimeLabel")}
          value={t("OverviewTab.uptimeDays", DEMO_UPTIME)}
          text
          caption={t("OverviewTab.uptimeSince", { time: bootedAt })}
          live
        />
      </div>

      <div className={ov.grid2}>
        <section className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <h2 className={styles.cardTitle}>
                <MIcon name="info" size={18} />
                {t("OverviewTab.envInfoTitle")}
              </h2>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={ov.list}>
              <InfoRow label={t("OverviewTab.nodeLabel")}>{DEMO_NODE}</InfoRow>
              <InfoRow label={t("OverviewTab.osLabel")}>{DEMO_OS}</InfoRow>
              <InfoRow label={t("OverviewTab.expiryLabel")}>
                {formatDate(expiry, lang)}
                <span className={ov.pill}>{expiryText}</span>
              </InfoRow>
              <InfoRow label={t("OverviewTab.accessRoleLabel")}>{t("OverviewTab.roleOwner")}</InfoRow>
            </div>
          </div>
        </section>

        <section className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <h2 className={styles.cardTitle}>
                <MIcon name="vpn_key" size={18} />
                {t("OverviewTab.accessTitle")}
              </h2>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={ov.list}>
              <InfoRow label={t("OverviewTab.ipLabel")}>
                <span className={ov.mono}>{DEMO_IP}</span>
                <CopyButton id="ip-row" text={DEMO_IP} copied={copied} onCopy={copy} t={t} />
              </InfoRow>
              <InfoRow label={t("OverviewTab.publicUrlsLabel")}>
                <span className={ov.urlItem}>
                  <span className={ov.urlLink}>
                    <MIcon name="open_in_new" size={14} />
                    {DEMO_URL}
                  </span>
                  <CopyButton id="url" text={DEMO_URL} copied={copied} onCopy={copy} t={t} />
                </span>
              </InfoRow>
              <InfoRow label={t("OverviewTab.usernameLabel")}>
                <span className={ov.mono}>{DEMO_USER}</span>
                <CopyButton id="username" text={DEMO_USER} copied={copied} onCopy={copy} t={t} />
              </InfoRow>
              {/* 示範頁不放密碼字串，顯示與複製都只會拿到遮蔽字元 */}
              <SecretRow
                label={t("OverviewTab.passwordLabel")}
                value={MASK}
                secret
                note={t("OverviewTab.loginPasswordDesc")}
                copyId="password"
                copied={copied}
                onCopy={copy}
                t={t}
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

/* ── 監控：分頁列右側切時間範圍 → 四張即時卡 → 歷史圖表 ── */
function DemoMonitoring({ t, toolbar }) {
  const [timeframe, setTimeframe] = useState("hour");
  const [chartTab, setChartTab] = useState("cpu");
  const data = useMemo(demoSeries, []);

  return (
    <div className={styles.tabStack}>
      {toolbar && createPortal(
        <SegmentedControl
          className={styles.segmentScroll}
          options={TIMEFRAMES.map((tf) => ({ value: tf.value, label: t(tf.labelKey) }))}
          value={timeframe}
          onChange={setTimeframe}
          ariaLabel={t("MonitoringTab.timeframeAria")}
        />,
        toolbar,
      )}

      <div className={styles.statGrid}>
        <StatCard title={t("MonitoringTab.cpuUsage")} pct="36.00" detail={t("MonitoringTab.coresDetail", { count: 2 })} icon="memory" />
        <StatCard title={t("MonitoringTab.memory")} pct="58.00" detail="2.32 GB / 4.00 GB" icon="sd_card" />
        <StatCard title={t("MonitoringTab.disk")} pct="42.00" detail="16.80 GB / 40.00 GB" icon="storage" />
        <div className={styles.statCard}>
          <div className={styles.overviewTop}>
            <div className={styles.overviewInfo}>
              <span className={styles.factLabel}>{t("MonitoringTab.network")}</span>
              <span className={styles.netLine}>↓ 1.24 GB</span>
              <span className={styles.netLine}>↑ 312.50 MB</span>
            </div>
            <span className={styles.specIcon}>
              <MIcon name="swap_vert" size={18} />
            </span>
          </div>
        </div>
      </div>

      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>{t("MonitoringTab.historyTitle")}</h2>
            <p className={styles.cardDesc}>{t("MonitoringTab.dataPointsCount", { count: data.length })}</p>
          </div>
          <SegmentedControl
            options={CHART_TABS.map((ct) => ({ value: ct.key, label: ct.labelKey ? t(ct.labelKey) : ct.label }))}
            value={chartTab}
            onChange={setChartTab}
            ariaLabel={t("MonitoringTab.chartTabsAria")}
          />
        </div>
        <div className={styles.cardBody}>
          {chartTab === "cpu" && (
            <RrdChart data={data} series={[{ key: "cpu", label: t("MonitoringTab.seriesCpu"), color: "--color-info" }]} unit="%" height={260} />
          )}
          {chartTab === "memory" && (
            <RrdChart data={data} series={[{ key: "memory", label: t("MonitoringTab.seriesMemory"), color: "--color-success" }]} unit="%" height={260} />
          )}
          {chartTab === "network" && (
            <RrdChart
              data={data}
              series={[
                { key: "netin",  label: t("MonitoringTab.seriesDownload"), color: "--color-info" },
                { key: "netout", label: t("MonitoringTab.seriesUpload"), color: "--color-danger" },
              ]}
              unit="KB"
              height={260}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/* ── 規格：說明＋三支拉桿＋申請理由＋送出，下方是審核流程 ── */
function DemoSpecifications({ t }) {
  const [cores, setCores] = useState(2);
  const [memory, setMemory] = useState(4096);
  const [disk, setDisk] = useState(40);
  const [reason, setReason] = useState("");

  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <p className={styles.cardDesc}>{t("SpecificationsTab.descUser")}</p>
        </div>
        <div className={styles.cardBody}>
          <div className={sl.grid}>
            <SliderField
              id="demo-spec-cores"
              label={t("SpecificationsTab.cpuCoresLabel")}
              unit={t("SpecificationsTab.coresUnit")}
              min={1}
              max={8}
              step={1}
              value={cores}
              current={2}
              ticks={[1, 2, 4, 6, 8].map((v) => ({ value: v, label: String(v) }))}
              onChange={setCores}
              inputValue={cores}
              inputMin={1}
              inputMax={8}
              inputStep={1}
              onInput={setCores}
              currentText={t("SpecificationsTab.currentLabel", { value: 2 })}
              deltaText={t("SpecificationsTab.deltaCores", { delta: signed(cores - 2) })}
            />
            <SliderField
              id="demo-spec-memory"
              label={t("SpecificationsTab.memoryLabel")}
              unit="GB"
              min={512}
              max={16384}
              step={512}
              value={memory}
              current={4096}
              ticks={[512, 4096, 8192, 12288, 16384].map((v) => ({ value: v, label: `${formatGb(v)}GB` }))}
              onChange={setMemory}
              inputValue={memory / 1024}
              inputMin={0.5}
              inputMax={16}
              inputStep={0.5}
              onInput={(gb) => setMemory(Math.round(gb * 1024))}
              currentText={t("SpecificationsTab.currentMemoryLabel", { value: 4 })}
              deltaText={t("SpecificationsTab.deltaGb", { delta: `${memory > 4096 ? "+" : "−"}${formatGb(Math.abs(memory - 4096))}` })}
            />
            <SliderField
              id="demo-spec-disk"
              wide
              label={t("SpecificationsTab.diskLabel")}
              unit="GB"
              min={40}
              max={200}
              step={1}
              value={disk}
              current={40}
              ticks={[40, 80, 120, 160, 200].map((v) => ({ value: v, label: String(v) }))}
              onChange={setDisk}
              inputValue={disk}
              inputMin={40}
              inputMax={200}
              inputStep={1}
              onInput={setDisk}
              currentText={t("SpecificationsTab.currentDiskLabel", { value: 40 })}
              deltaText={t("SpecificationsTab.deltaGb", { delta: signed(disk - 40) })}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="demo-spec-reason">{t("SpecificationsTab.reasonLabel")}</label>
            <textarea
              id="demo-spec-reason"
              rows={4}
              placeholder={t("SpecificationsTab.reasonHint")}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          <button type="button" className={`${styles.btnPrimary} ${sl.applyBtn}`}>
            {t("SpecificationsTab.submitRequest")}
          </button>
        </div>
      </div>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <h2 className={styles.cardTitle}>{t("SpecificationsTab.reviewProcessTitle")}</h2>
        </div>
        <div className={styles.cardBody}>
          <ol className={styles.stepList}>
            <li>{t("SpecificationsTab.step1")}</li>
            <li>{t("SpecificationsTab.step2")}</li>
            <li>{t("SpecificationsTab.step3")}</li>
            <li>{t("SpecificationsTab.step4")}</li>
          </ol>
        </div>
      </div>
    </div>
  );
}

/* ── 示範表格的欄位（模組層級：cell／header 會被當成元件掛載，render 裡新建會整格重掛）。
   示範資料的欄位跟正式分頁不同（time、descKey、managed…），所以各自一份定義，class 與正式分頁相同 ── */
function DemoHeader({ column }) {
  const { t } = useTranslation("personal");
  return t(column.columnDef.meta.labelKey);
}

function DemoSnapshotNameCell({ row }) {
  const { t } = useTranslation("personal");
  const snap = row.original;
  return (
    <span className={styles.snapName}>
      {snap.name}
      {snap.protected && (
        <span className={`${styles.badge} ${styles.badge_info}`}>
          <MIcon name="verified_user" size={12} />
          {t("SnapshotsTab.protected")}
        </span>
      )}
    </span>
  );
}

function DemoSnapshotDescCell({ row }) {
  const { t } = useTranslation("personal");
  return t(row.original.descKey);
}

function DemoSnapshotActionsCell({ row }) {
  const { t } = useTranslation("personal");
  return (
    <>
      <button type="button" className={styles.btnSecondary}><MIcon name="history" size={14} />{t("SnapshotsTab.restore")}</button>
      {!row.original.protected && (
        <button type="button" className={styles.btnDangerOutline}><MIcon name="delete_outline" size={14} />{t("SnapshotsTab.delete")}</button>
      )}
    </>
  );
}

const columnHelper = createColumnHelper();
const DEMO_SNAPSHOT_COLUMNS = columnHelper.columns([
  columnHelper.accessor("name", {
    header: DemoHeader,
    cell: DemoSnapshotNameCell,
    meta: { labelKey: "SnapshotsTab.colName", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "desc",
    header: DemoHeader,
    cell: DemoSnapshotDescCell,
    meta: { labelKey: "SnapshotsTab.colDesc", thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.accessor("time", {
    header: DemoHeader,
    cell: (info) => formatDateTime(info.getValue()),
    meta: {
      labelKey: "SnapshotsTab.colCreatedAt",
      thClassName: styles.th,
      tdClassName: `${styles.td} ${styles.mutedCell} ${styles.nowrapCell}`,
    },
  }),
  columnHelper.display({
    id: "actions",
    header: DemoHeader,
    cell: DemoSnapshotActionsCell,
    meta: { labelKey: "SnapshotsTab.colActions", thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdActions}` },
  }),
]);

function DemoAuditActionCell({ row }) {
  const { t } = useTranslation("personal");
  const { action } = row.original;
  return (
    <span className={`${styles.badge} ${styles[actionBadgeClass(action)]}`}>
      {actionLabel(action, t)}
    </span>
  );
}

const DEMO_AUDIT_COLUMNS = columnHelper.columns([
  columnHelper.accessor("time", {
    header: DemoHeader,
    cell: (info) => formatDateTime(info.getValue()),
    meta: { labelKey: "AuditLogsTab.colTime", thClassName: styles.th, tdClassName: `${styles.td} ${styles.nowrapCell}` },
  }),
  columnHelper.display({
    id: "operator",
    header: DemoHeader,
    cell: () => (
      <div className={styles.userCell}>
        <span className={styles.userName}>{DEMO_USER}</span>
      </div>
    ),
    meta: { labelKey: "AuditLogsTab.colOperator", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "action",
    header: DemoHeader,
    cell: DemoAuditActionCell,
    meta: { labelKey: "AuditLogsTab.colAction", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("details", {
    header: DemoHeader,
    cell: (info) => info.getValue(),
    meta: { labelKey: "AuditLogsTab.colDetails", thClassName: styles.th, tdClassName: `${styles.td} ${styles.detailCell}` },
  }),
]);

function DemoRuleDirectionCell({ row }) {
  const { t } = useTranslation("personal");
  const inbound = row.original.type === "in";
  return (
    <span className={`${styles.badge} ${inbound ? styles.badge_info : styles.badge_muted}`}>
      {inbound ? t("FirewallCard.directionIn") : t("FirewallCard.directionOut")}
    </span>
  );
}

function DemoRulePeerCell({ row }) {
  const { t } = useTranslation("personal");
  return row.original.peer ?? t("FirewallCard.any");
}

/* 規則的開關狀態是示範頁自己的 state，經 DemoRuleRow 的 rowState 傳進來 */
function DemoRuleActionCell({ rowState }) {
  const { t } = useTranslation("personal");
  return rowState.off ? (
    <span className={`${styles.badge} ${styles.badge_danger}`}>{t("FirewallCard.ruleDisabled")}</span>
  ) : (
    <span className={`${styles.badge} ${styles.badge_success}`}>{t("FirewallCard.policyAccept")}</span>
  );
}

function DemoRuleNoteCell({ row }) {
  const { t } = useTranslation("personal");
  const rule = row.original;
  return rule.managed ? (
    <span className={styles.hintLine}>
      <MIcon name="lock" size={12} />
      {t("FirewallCard.managedByService")}
    </span>
  ) : rule.comment;
}

function DemoRuleActionsCell({ row, rowState }) {
  const { t } = useTranslation("personal");
  if (row.original.managed) return null;
  return (
    <>
      <Switch
        checked={!rowState.off}
        onChange={rowState.onToggle}
        ariaLabel={t("FirewallCard.enableSwitch")}
        title={t("FirewallCard.enableSwitch")}
      />
      <button type="button" className={`${styles.rpIconBtn} ${styles.rpIconBtnDanger}`} title={t("FirewallCard.deleteRule")}>
        <MIcon name="delete" size={16} />
      </button>
    </>
  );
}

const DEMO_RULE_COLUMNS = columnHelper.columns([
  columnHelper.accessor("pos", {
    header: "#",
    cell: (info) => info.getValue() + 1,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.display({
    id: "direction",
    header: DemoHeader,
    cell: DemoRuleDirectionCell,
    meta: { labelKey: "FirewallCard.direction", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("proto", {
    header: DemoHeader,
    cell: (info) => info.getValue(),
    meta: { labelKey: "FirewallCard.protocol", thClassName: styles.th, tdClassName: `${styles.td} ${styles.nowrapCell}` },
  }),
  columnHelper.accessor("port", {
    header: DemoHeader,
    cell: (info) => info.getValue(),
    meta: { labelKey: "FirewallCard.port", thClassName: styles.th, tdClassName: `${styles.td} ${styles.nowrapCell}` },
  }),
  columnHelper.display({
    id: "peer",
    header: DemoHeader,
    cell: DemoRulePeerCell,
    meta: { labelKey: "FirewallCard.sourceCol", thClassName: styles.th, tdClassName: `${styles.td} ${styles.monoText}` },
  }),
  columnHelper.display({
    id: "action",
    header: DemoHeader,
    cell: DemoRuleActionCell,
    meta: { labelKey: "FirewallCard.action", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "note",
    header: DemoHeader,
    cell: DemoRuleNoteCell,
    meta: { labelKey: "FirewallCard.noteCol", thClassName: styles.th, tdClassName: `${styles.td} ${styles.detailCell}` },
  }),
  columnHelper.display({
    id: "actions",
    header: DemoHeader,
    cell: DemoRuleActionsCell,
    meta: { labelKey: "FirewallCard.actionsCol", thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdActions}` },
  }),
]);

const getNameId = (item) => item.name;
const getDemoAuditId = (log) => String(log.id);
const getDemoRuleId = (rule) => String(rule.pos);
const demoRowClassName = () => styles.tr;

/* 示範資料是常數、列物件不會變：規則開關的狀態要經列元件的 props 進 rowState，儲存格才會跟著重繪 */
function DemoRuleRow({ row, rule, off, onToggle }) {
  return (
    <tr className={`${styles.tr} ${rule.managed ? styles.lockedRow : ""} ${off ? styles.ruleOffRow : ""}`}>
      <DataTableCells row={row} rowState={{ off, onToggle: () => onToggle(rule.pos) }} />
    </tr>
  );
}

/* ── 快照：表格＋分頁列右側的一鍵重置／建立快照 ── */
function DemoSnapshots({ t, toolbar }) {
  const snapshotTable = useTable({
    features: baseFeatures,
    columns: DEMO_SNAPSHOT_COLUMNS,
    data: DEMO_SNAPSHOTS,
    getRowId: getNameId,
  });
  return (
    <div className={styles.tabStack}>
      {toolbar && createPortal(
        <>
          <button type="button" className={styles.btnSecondary}><MIcon name="restart_alt" size={14} />{t("SnapshotsTab.oneClickReset")}</button>
          <button type="button" className={styles.btnPrimary}><MIcon name="add" size={14} />{t("SnapshotsTab.createSnapshot")}</button>
        </>,
        toolbar,
      )}
      <div className={styles.card}>
        <div className={styles.tableScroll}>
          <DataTable table={snapshotTable} className={styles.table} rowClassName={demoRowClassName} />
        </div>
      </div>
    </div>
  );
}

/* ── 操作紀錄：四欄表格，動作用徽章 ── */
function DemoAuditLogs() {
  const auditTable = useTable({
    features: baseFeatures,
    columns: DEMO_AUDIT_COLUMNS,
    data: DEMO_AUDIT,
    getRowId: getDemoAuditId,
  });
  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        <div className={styles.tableScroll}>
          <DataTable table={auditTable} className={styles.table} rowClassName={demoRowClassName} />
        </div>
      </div>
    </div>
  );
}

function CardTitle({ icon, children }) {
  return (
    <h2 className={styles.cardTitle}>
      <MIcon name={icon} size={18} />
      {children}
    </h2>
  );
}

/* ── 進階設定：生命週期、防火牆、開機選項、登入憑證、共享與轉移，卡片與正式頁同序同名 ── */
function DemoAdvanced({ t, lang, onShowOverview }) {
  const expiry = useMemo(demoExpiryDate, []);
  const [shareEmail, setShareEmail] = useState("");
  /* 規則開關只切示範頁自己的狀態，不送出 */
  const [rulesOff, setRulesOff] = useState(() => new Set());
  /* 開機順序：拖移、上下移、移除、儲存都只動示範頁自己的狀態；儲存＝把目前順序當成已存的版本 */
  const [bootOrder, setBootOrder] = useState(DEMO_BOOT);
  const [savedBootOrder, setSavedBootOrder] = useState(DEMO_BOOT);
  const moveBoot = (from, to) => setBootOrder((prev) => {
    if (to < 0 || to >= prev.length) return prev;
    const next = [...prev];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    return next;
  });
  const drag = useDragReorder({ onMove: moveBoot });
  const bootChanged = bootOrder.map((dev) => dev.key).join() !== savedBootOrder.map((dev) => dev.key).join();
  const toggleRule = (pos) => setRulesOff((prev) => {
    const next = new Set(prev);
    if (next.has(pos)) next.delete(pos); else next.add(pos);
    return next;
  });
  const policy = (key) => t(`FirewallCard.${key}`);
  const ruleTable = useTable({
    features: baseFeatures,
    columns: DEMO_RULE_COLUMNS,
    data: DEMO_RULES,
    getRowId: getDemoRuleId,
  });

  return (
    <div className={styles.tabStack}>
      <div data-guide="resource-setting-lifecycle">
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div><CardTitle icon="schedule">{t("LifecycleCard.title")}</CardTitle></div>
            <div className={styles.headerActions}>
              <button type="button" className={styles.btnPrimary}>
                <MIcon name="more_time" size={16} />
                {t("LifecycleCard.requestExtension")}
              </button>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={styles.factGrid}>
              <div className={styles.fact}>
                <span className={styles.factLabel}>{t("LifecycleCard.expiryLabel")}</span>
                <span className={styles.factValue}>{formatDate(expiry, lang)}</span>
                <span className={styles.mutedText}>{t("LifecycleCard.expiryHint")}</span>
              </div>
              <div className={styles.fact}>
                <span className={styles.factLabel}>{t("LifecycleCard.autoStopLabel")}</span>
                <span className={styles.factValue}>{t("LifecycleCard.none")}</span>
              </div>
              <div className={styles.fact}>
                <span className={styles.factLabel}>{t("LifecycleCard.deletionLabel")}</span>
                <span className={styles.factValue}>{t("LifecycleCard.none")}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 正式頁還有連線拓撲小圖，示範頁只畫狀態、新增規則與規則表 */}
      <div data-guide="resource-setting-firewall">
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div><CardTitle icon="security">{t("FirewallCard.title")}</CardTitle></div>
            <div className={styles.headerActions}>
              <span className={`${styles.badge} ${styles.badge_success}`}>{t("FirewallCard.enabled")}</span>
              <span className={`${styles.badge} ${styles.badge_muted}`}>
                {t("FirewallCard.policySummary", { in: policy("policyDrop"), out: policy("policyAccept") })}
              </span>
              <button type="button" className={styles.btnSecondary}>
                <MIcon name="add" size={16} />
                {t("FirewallCard.addRule")}
              </button>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={styles.tableScroll}>
              <DataTable
                table={ruleTable}
                className={styles.table}
                renderRow={(row) => (
                  <DemoRuleRow
                    key={row.id}
                    row={row}
                    rule={row.original}
                    off={rulesOff.has(row.original.pos)}
                    onToggle={toggleRule}
                  />
                )}
              />
            </div>
          </div>
        </div>
      </div>

      <div data-guide="resource-setting-boot">
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <CardTitle icon="power_settings_new">{t("BootOptionsCard.title")}</CardTitle>
              <p className={styles.cardDesc}>{t("BootOptionsCard.onbootAutoNote")}</p>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={styles.rowStack}>
              <span className={styles.factLabel}>{t("BootOptionsCard.bootOrderLabel")}</span>
              {bootOrder.length === 0 ? (
                <p className={styles.mutedText}>{t("BootOptionsCard.bootOrderDefault")}</p>
              ) : (
              <div className={styles.orderList} ref={drag.listRef}>
                {bootOrder.map((dev, index) => (
                  <div
                    key={dev.key}
                    {...drag.getItemProps(dev.key)}
                    className={`${styles.orderItem} ${styles.orderItemDraggable} ${drag.draggingKey === dev.key ? styles.orderItemDragging : ""}`}
                  >
                    <span {...drag.getHandleProps()} className={styles.dragHandle} title={t("BootOptionsCard.dragToReorder")}>
                      <MIcon name="drag_indicator" size={18} />
                    </span>
                    <span className={styles.orderIndex}>{index + 1}</span>
                    <span className={styles.orderMain}>
                      <MIcon name={KIND_ICON[dev.kind]} size={16} />
                      <code>{dev.key}</code>
                      <span className={styles.mutedText}>{dev.desc}</span>
                    </span>
                    <span className={styles.orderBtns}>
                      <button type="button" className={styles.rpIconBtn} disabled={index === 0} onClick={() => moveBoot(index, index - 1)} title={t("BootOptionsCard.moveUp")}>
                        <MIcon name="arrow_upward" size={16} />
                      </button>
                      <button type="button" className={styles.rpIconBtn} disabled={index === bootOrder.length - 1} onClick={() => moveBoot(index, index + 1)} title={t("BootOptionsCard.moveDown")}>
                        <MIcon name="arrow_downward" size={16} />
                      </button>
                      <button
                        type="button"
                        className={`${styles.rpIconBtn} ${styles.rpIconBtnDanger}`}
                        onClick={() => setBootOrder((prev) => prev.filter((item) => item.key !== dev.key))}
                        title={t("BootOptionsCard.removeFromOrder")}
                      >
                        <MIcon name="close" size={16} />
                      </button>
                    </span>
                  </div>
                ))}
              </div>
              )}
              {/* 移除的裝置跟正式頁一樣出現在下方，點一下加回順序尾端 */}
              {bootOrder.length < DEMO_BOOT.length && (
                <div className={styles.chipList}>
                  {DEMO_BOOT.filter((dev) => !bootOrder.some((item) => item.key === dev.key)).map((dev) => (
                    <button key={dev.key} type="button" className={styles.chip} onClick={() => setBootOrder((prev) => [...prev, dev])}>
                      <MIcon name="add" size={12} /> {dev.key} · {dev.desc}
                    </button>
                  ))}
                </div>
              )}
              <div className={`${styles.fieldRow} ${styles.fieldRowEnd}`}>
                <span className={styles.fieldHint}>{t("BootOptionsCard.bootOrderHint")}</span>
                <button type="button" className={styles.btnPrimary} disabled={!bootChanged} onClick={() => setSavedBootOrder(bootOrder)}>
                  {t("BootOptionsCard.saveBootOrder")}
                </button>
              </div>
            </div>
            <div className={styles.rowStack}>
              <span className={styles.factLabel}>{t("BootOptionsCard.isoLabel")}</span>
              <p className={styles.mutedText}>{t("BootOptionsCard.isoMounted", { name: DEMO_ISO, slot: "ide2" })}</p>
              <div className={styles.fieldRow}>
                <div className={styles.field} style={{ flex: 1, minWidth: 240 }}>
                  <select defaultValue="" aria-label={t("BootOptionsCard.selectIso")}>
                    <option value="">{t("BootOptionsCard.selectIso")}</option>
                    <option value={DEMO_ISO}>{DEMO_ISO}</option>
                  </select>
                </div>
                <button type="button" className={styles.btnSecondary} disabled>
                  <MIcon name="album" size={16} />
                  {t("BootOptionsCard.mount")}
                </button>
                <button type="button" className={styles.btnDangerOutline}>
                  <MIcon name="eject" size={16} />
                  {t("BootOptionsCard.eject")}
                </button>
              </div>
              <span className={styles.fieldHint}>{t("BootOptionsCard.isoHint")}</span>
            </div>
          </div>
        </div>
      </div>

      <div data-guide="resource-setting-credentials">
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <CardTitle icon="key">{t("CredentialsCard.title")}</CardTitle>
              <p className={styles.cardDesc}>{t("CredentialsCard.cloudInitNote")}</p>
            </div>
            <div className={styles.headerActions}>
              <button type="button" className={styles.btnSecondary}>
                <MIcon name="password" size={16} />
                {t("CredentialsCard.resetPassword")}
              </button>
              <button type="button" className={styles.btnSecondary}>
                <MIcon name="autorenew" size={16} />
                {t("CredentialsCard.regenerateKey")}
              </button>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={styles.factGrid}>
              <div className={styles.fact}>
                <span className={styles.factLabel}>{t("CredentialsCard.usernameLabel")}</span>
                <span className={`${styles.factValue} ${styles.credentialValue} ${styles.monoText}`}>{DEMO_USER}</span>
              </div>
              <div className={styles.fact}>
                <span className={styles.factLabel}>{t("CredentialsCard.passwordLabel")}</span>
                <span className={`${styles.factValue} ${styles.credentialValue}`}>{t("CredentialsCard.passwordStored")}</span>
                <button type="button" className={styles.textLink} onClick={onShowOverview}>
                  {t("CredentialsCard.passwordWhere")}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div data-guide="resource-setting-sharing">
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <CardTitle icon="group">{t("SharingCard.title")}</CardTitle>
              <p className={styles.cardDesc}>{t("SharingCard.scopeNote")}</p>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={styles.shareForm}>
              <form className={styles.inlineForm} onSubmit={(e) => e.preventDefault()}>
                <input
                  type="email"
                  value={shareEmail}
                  onChange={(e) => setShareEmail(e.target.value)}
                  placeholder={t("SharingCard.emailPlaceholder")}
                  aria-label={t("SharingCard.emailPlaceholder")}
                />
                <button type="submit" className={styles.btnSecondary} disabled={!shareEmail.trim()}>
                  <MIcon name="person_add" size={16} />
                  {t("SharingCard.share")}
                </button>
              </form>
            </div>
            <div className={styles.rowStack}>
              <span className={styles.factLabel}>{t("SharingCard.sharedWith", { count: 1 })}</span>
              <div className={styles.keyList}>
                <div className={styles.keyItem}>
                  <MIcon name="person" size={16} />
                  <span className={styles.rpMain}>
                    <span className={styles.rpDomain}>teammate</span>
                    <span className={styles.rpMeta}>
                      teammate@example.edu
                      <span className={`${styles.badge} ${styles.badge_info}`}>{t("SharingCard.permissionControl")}</span>
                    </span>
                  </span>
                  <button type="button" className={`${styles.rpIconBtn} ${styles.rpIconBtnDanger}`} title={t("SharingCard.revoke")}>
                    <MIcon name="person_remove" size={16} />
                  </button>
                </div>
              </div>
            </div>
            <div className={styles.transferRow}>
              <div className={styles.transferRowText}>
                <span className={styles.transferRowTitle}>{t("SharingCard.transferTitle")}</span>
                <span className={styles.mutedText}>{t("SharingCard.transferZoneDesc")}</span>
              </div>
              <button type="button" className={styles.btnDangerOutline}>
                <MIcon name="swap_horiz" size={16} />
                {t("SharingCard.transferButton")}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ResourceDetailGuideDemo({ tab, toolbar, onShowOverview }) {
  const { t, i18n } = useTranslation("personal");
  const lang = i18n.language || "zh-TW";

  if (tab === "overview") return <DemoOverview t={t} lang={lang} />;
  if (tab === "monitoring") return <DemoMonitoring t={t} toolbar={toolbar} />;
  if (tab === "specifications") return <DemoSpecifications t={t} />;
  if (tab === "snapshots") return <DemoSnapshots t={t} toolbar={toolbar} />;
  if (tab === "auditLogs") return <DemoAuditLogs />;
  return <DemoAdvanced t={t} lang={lang} onShowOverview={onShowOverview} />;
}
