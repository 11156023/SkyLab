import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import sl from "./SpecificationsTab.module.scss";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import MIcon from "../../../../components/MIcon";
import { useConfirm } from "../../../../components/ConfirmDialog/ConfirmProvider";
import { useAuth } from "../../../../contexts/AuthContext";
import { ResourcesService } from "../../../../services/resources";
import {
  SpecChangeRequestsService,
  canApplySpecRequest,
  canCancelSpecRequest,
  isOpenSpecRequest,
  specRequestChangeLabel,
  specRequestDisplayStatus,
} from "../../../../services/specChangeRequests";
import { useToast } from "../../../../hooks/useToast";
import { focusInvalidField } from "../../../../utils/focusField";

/* 套用中（關機 → 改規格 → 開機）約 1～3 分鐘，期間每 5 秒跟一次進度 */
const APPLY_POLL_MS = 5000;

/* 拉桿範圍與後端 SpecChangeRequestCreate 的限制一致 */
const CORE_MIN = 1;
const CORE_MAX = 32;
const MEM_MIN = 512;
const MEM_MAX = 65536;
const MEM_STEP = 512;
const DISK_MAX = 1000;
const CORE_TICKS = [1, 4, 8, 16, 24, 32];
/* 記憶體刻度不放 8GB：與最左邊的 0.5GB 距離太近，標籤會疊在一起 */
const MEM_TICKS = [512, 16384, 32768, 49152, 65536];

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function formatGb(mb) {
  const gb = mb / 1024;
  return Number.isInteger(gb) ? String(gb) : gb.toFixed(1);
}

/** 帶正負號的差值；負號用 U+2212，比連字號好認 */
function signed(delta) {
  return `${delta > 0 ? "+" : "−"}${Math.abs(delta)}`;
}

function signedGb(deltaMb) {
  return `${deltaMb > 0 ? "+" : "−"}${formatGb(Math.abs(deltaMb))}`;
}

/** 磁碟拉桿的刻度：兩端固定，中間挑整數刻度、最多 4 個，且不擠在兩端 */
function diskTicks(min, max) {
  const span = max - min;
  if (span <= 0) return [min];
  const step = span >= 400 ? 100 : span >= 150 ? 50 : 10;
  const inner = [];
  for (let v = Math.ceil(min / step) * step; v < max; v += step) {
    if (v - min >= span * 0.12 && max - v >= span * 0.12) inner.push(v);
  }
  const keepEvery = Math.max(1, Math.ceil(inner.length / 4));
  return [min, ...inner.filter((_, i) => i % keepEvery === 0), max];
}

/** 這台機器目前還在流程中的申請（最多一張：後端擋重複送單） */
function findOpenRequest(list, vmid) {
  return (list ?? [])
    .filter((r) => r.vmid === vmid && isOpenSpecRequest(r))
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0] ?? null;
}

/** 最近一張已套用但自動開機失敗的申請：規格已改，但機器還關著要提醒 */
function findAppliedWithWarning(list, vmid) {
  return (list ?? []).find(
    (r) => r.vmid === vmid && r.status === "approved" && r.apply_status === "applied" && r.apply_error,
  ) ?? null;
}

function OpenRequestNotice({ request, busy, onApply, onCancel }) {
  const { t } = useTranslation("personal");
  const display = specRequestDisplayStatus(request);
  const statusLabel = display.labelKey ? t(display.labelKey) : display.key;
  const showApply = canApplySpecRequest(request);
  const showCancel = canCancelSpecRequest(request);

  let line;
  switch (display.key) {
    case "pending":
      line = t("SpecificationsTab.noticePending");
      break;
    case "ready":
      line = t("SpecificationsTab.noticeReady");
      break;
    case "applying":
      line = t("SpecificationsTab.noticeApplying");
      break;
    case "apply_failed":
      line = t("SpecificationsTab.noticeApplyFailed", {
        error: request.apply_error ?? t("SpecificationsTab.unknownError"),
      });
      break;
    case "apply_interrupted":
      line = t("SpecificationsTab.noticeInterrupted");
      break;
    default:
      line = statusLabel;
  }

  return (
    <div className={styles.noteBox}>
      <span className={styles.noteBoxTitle}>
        <MIcon name={display.key === "applying" ? "hourglass_top" : "tune"} size={14} />
        {t("SpecificationsTab.noticeTitle", { status: statusLabel })}
      </span>
      <span className={styles.noteBoxLine}>{specRequestChangeLabel(request, t)}</span>
      <span className={styles.noteBoxLine}>{line}</span>
      {(showApply || showCancel) && (
        <div className={styles.noteActions}>
          {showApply && (
            <button type="button" className={styles.btnPrimary} disabled={busy} onClick={onApply}>
              <MIcon name="play_arrow" size={16} />
              {display.key === "ready" ? t("SpecificationsTab.applyNewSpec") : t("SpecificationsTab.reapply")}
            </button>
          )}
          {showCancel && (
            <button type="button" className={styles.btnDangerOutline} disabled={busy} onClick={onCancel}>
              {t("SpecificationsTab.cancelRequest")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 規格拉桿：標題列右側是可直接鍵入的數字框，下面是拉桿、刻度與「目前值」標記，
 * 最底下顯示目前值與相對於目前的變化量。
 * value／min／max／step 是拉桿的原始單位；數字框可用另一個單位（記憶體用 GB）。
 */
function SliderField({
  id, label, unit, wide, disabled,
  min, max, step, value, current, ticks, onChange,
  inputValue, inputMin, inputMax, inputStep, onInput,
  currentText, deltaText,
}) {
  const { t } = useTranslation("personal");
  const pct = (v) => `${((v - min) / (max - min || 1)) * 100}%`;
  const changed = current != null && value !== current;
  const showMark = current != null && current > min && current < max;

  return (
    <div className={`${sl.field} ${disabled ? sl.fieldDisabled : ""} ${wide ? sl.gridWide : ""}`}>
      <div className={sl.labelRow}>
        <label htmlFor={id} className={sl.label}>{label}</label>
        <div className={sl.valueBox}>
          <input
            type="number"
            className={sl.numInput}
            min={inputMin}
            max={inputMax}
            step={inputStep}
            value={inputValue}
            disabled={disabled}
            aria-label={label}
            onChange={(e) => onInput(e.target.value)}
          />
          <span className={sl.unit}>{unit}</span>
        </div>
      </div>
      <div className={sl.track}>
        {showMark && (
          <span
            className={sl.currentMark}
            style={{ left: pct(current) }}
            title={t("SpecificationsTab.currentMarker")}
            aria-hidden="true"
          />
        )}
        <input
          id={id}
          type="range"
          className={sl.slider}
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </div>
      <div className={sl.ticks}>
        {ticks.map((tk) => (
          <span key={tk.value} style={{ left: pct(tk.value) }}>{tk.label}</span>
        ))}
      </div>
      <div className={sl.meta}>
        <span>{currentText}</span>
        {changed && (
          <span className={`${sl.delta} ${value > current ? sl.deltaUp : sl.deltaDown}`}>{deltaText}</span>
        )}
      </div>
    </div>
  );
}

export default function SpecificationsTab({ vmid }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin" || user?.is_superuser || false;

  const [config, setConfig] = useState(null);
  // 課堂與快速練習的機器照課程環境版本建立，規格不接受個別調整；
  // 後端一直有算 can_request_spec_change，只是沒有人讀。
  const [specFixed, setSpecFixed] = useState(false);
  const [cores, setCores] = useState(1);
  const [memory, setMemory] = useState(512);
  const [disk, setDisk] = useState(0);
  const [reason, setReason] = useState("");
  const [reasonInvalid, setReasonInvalid] = useState(false);
  const reasonRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [openRequest, setOpenRequest] = useState(null);
  const [appliedWarning, setAppliedWarning] = useState(null);

  const loadConfig = useCallback(async () => {
    try {
      /* /specs 回的是實際生效值（cpu_cores / memory_mb / disk_gb），不是 Proxmox 原始 config */
      const c = await ResourcesService.getSpecs(vmid);
      setConfig(c);
      setCores(c.cpu_cores || 1);
      setMemory(c.memory_mb || 512);
      setDisk(c.disk_gb || 0);
    } catch {
      setError(true);
      return;
    }
    try {
      const resource = await ResourcesService.get(vmid);
      setSpecFixed(resource?.can_request_spec_change === false);
    } catch {
      setSpecFixed(false);
    }
  }, [vmid]);

  /* 管理員直接套用，不走申請；只有一般使用者需要看自己的申請進度 */
  const loadRequests = useCallback(async () => {
    if (isAdmin) return;
    try {
      const res = await SpecChangeRequestsService.listMy();
      setOpenRequest(findOpenRequest(res.data, vmid));
      setAppliedWarning(findAppliedWithWarning(res.data, vmid));
    } catch {
      /* 申請進度載入失敗不影響表單本身 */
    }
  }, [isAdmin, vmid]);

  useEffect(() => {
    loadConfig();
    loadRequests();
  }, [loadConfig, loadRequests]);

  const applying = openRequest?.apply_status === "applying";
  useEffect(() => {
    if (!applying) return undefined;
    const timer = setInterval(async () => {
      const before = openRequest?.id;
      await loadRequests();
      /* 套用完成後 openRequest 會消失；重新載入規格讓「目前」數字更新 */
      if (before) loadConfig();
    }, APPLY_POLL_MS);
    return () => clearInterval(timer);
  }, [applying, openRequest?.id, loadRequests, loadConfig]);

  const handleApply = async () => {
    if (!openRequest) return;
    const ok = await confirm({
      title: t("SpecificationsTab.confirmApplyTitle"),
      message: t("SpecificationsTab.confirmApplyMessage"),
      confirmText: t("SpecificationsTab.confirmApplyLabel"),
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await SpecChangeRequestsService.apply(openRequest.id);
      setOpenRequest(res.request);
      toast.success(t("SpecificationsTab.applyStarted"));
    } catch (e) {
      toast.error(e?.message ?? t("SpecificationsTab.applyFailed"));
      await loadRequests();
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!openRequest) return;
    const ok = await confirm({
      title: t("SpecificationsTab.confirmCancelTitle"),
      message: t("SpecificationsTab.confirmCancelMessage"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await SpecChangeRequestsService.cancel(openRequest.id);
      setOpenRequest(null);
      toast.success(t("SpecificationsTab.cancelSuccess"));
    } catch (e) {
      toast.error(e?.message ?? t("SpecificationsTab.cancelFailed"));
      await loadRequests();
    } finally {
      setBusy(false);
    }
  };

  const currentDisk = config?.disk_gb ?? 0;
  const diskChanged = currentDisk > 0 && disk !== currentDisk;

  const handleSubmit = async () => {
    const hasChanges = cores !== config.cpu_cores || memory !== config.memory_mb || diskChanged;

    /* 磁碟只能放大：Proxmox resize 只接受增量，縮小會弄壞檔案系統 */
    if (diskChanged && disk < currentDisk) {
      toast.error(t("SpecificationsTab.diskShrinkNotAllowed", { current: currentDisk }));
      return;
    }

    if (isAdmin) {
      if (!hasChanges) {
        toast.error(t("SpecificationsTab.noChanges"));
        return;
      }
      setBusy(true);
      try {
        await ResourcesService.updateSpecDirect(vmid, {
          cores: cores !== config.cpu_cores ? cores : undefined,
          memory: memory !== config.memory_mb ? memory : undefined,
          disk_size: diskChanged ? `+${disk - currentDisk}G` : undefined,
        });
        toast.success(t("SpecificationsTab.updateSuccess"));
        await loadConfig();
      } catch (e) {
        toast.error(e?.message ?? t("SpecificationsTab.updateFailed"));
      } finally {
        setBusy(false);
      }
      return;
    }

    if (reason.trim().length < 10) {
      setReasonInvalid(true);
      focusInvalidField(reasonRef.current);
      return;
    }
    if (!hasChanges) {
      toast.error(t("SpecificationsTab.noChanges"));
      return;
    }

    setBusy(true);
    try {
      const created = await SpecChangeRequestsService.create({
        vmid,
        change_type: "combined",
        reason,
        requested_cpu: cores !== config.cpu_cores ? cores : undefined,
        requested_memory: memory !== config.memory_mb ? memory : undefined,
        requested_disk: diskChanged ? disk : undefined,
      });
      setOpenRequest(created);
      toast.success(t("SpecificationsTab.requestSubmitted"));
      setReason("");
    } catch (e) {
      toast.error(e?.message ?? t("SpecificationsTab.submitFailed"));
    } finally {
      setBusy(false);
    }
  };

  if (error) return <p className={styles.stateText}>{t("SpecificationsTab.loadFailed")}</p>;
  if (!config) return <LoadingState />;

  /* 一張處理中就不能再送（後端也擋），表單只留給管理員或沒有申請時 */
  const formLocked = !isAdmin && Boolean(openRequest);
  const inputsDisabled = specFixed || formLocked;

  let desc;
  if (specFixed) desc = t("SpecificationsTab.descFixed");
  else if (isAdmin) desc = t("SpecificationsTab.descAdmin");
  else if (formLocked) desc = t("SpecificationsTab.descLocked");
  else desc = t("SpecificationsTab.descUser");

  /* 磁碟只能放大，拉桿下限就是目前大小；讀不到目前大小時整條停用 */
  const diskMin = currentDisk || 1;
  const diskMax = Math.max(DISK_MAX, diskMin);

  return (
    <div className={styles.tabStack}>
      {!isAdmin && appliedWarning && !openRequest && (
        <div className={styles.noteBox}>
          <span className={styles.noteBoxTitle}>
            <MIcon name="warning" size={14} />
            {t("SpecificationsTab.appliedWarningTitle")}
          </span>
          <span className={styles.noteBoxLine}>{appliedWarning.apply_error}</span>
        </div>
      )}

      {openRequest && (
        <OpenRequestNotice
          request={openRequest}
          busy={busy}
          onApply={handleApply}
          onCancel={handleCancel}
        />
      )}

      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>{t("SpecificationsTab.title")}</h2>
            <p className={styles.cardDesc}>{desc}</p>
          </div>
        </div>
        <div className={styles.cardBody}>
          <div className={sl.grid}>
            <SliderField
              id="spec-cores"
              label={t("SpecificationsTab.cpuCoresLabel")}
              unit={t("SpecificationsTab.coresUnit")}
              disabled={inputsDisabled}
              min={CORE_MIN}
              max={CORE_MAX}
              step={1}
              value={cores}
              current={config.cpu_cores}
              ticks={CORE_TICKS.map((v) => ({ value: v, label: String(v) }))}
              onChange={setCores}
              inputValue={cores}
              inputMin={CORE_MIN}
              inputMax={CORE_MAX}
              inputStep={1}
              onInput={(raw) => {
                const n = Number.parseInt(raw, 10);
                if (Number.isFinite(n)) setCores(clamp(n, CORE_MIN, CORE_MAX));
              }}
              currentText={t("SpecificationsTab.currentLabel", { value: config.cpu_cores })}
              deltaText={t("SpecificationsTab.deltaCores", { delta: signed(cores - config.cpu_cores) })}
            />
            <SliderField
              id="spec-memory"
              label={t("SpecificationsTab.memoryLabel")}
              unit="GB"
              disabled={inputsDisabled}
              min={MEM_MIN}
              max={MEM_MAX}
              step={MEM_STEP}
              value={memory}
              current={config.memory_mb}
              ticks={MEM_TICKS.map((v) => ({ value: v, label: `${formatGb(v)}GB` }))}
              onChange={setMemory}
              inputValue={memory / 1024}
              inputMin={MEM_MIN / 1024}
              inputMax={MEM_MAX / 1024}
              inputStep={0.5}
              onInput={(raw) => {
                const gb = Number.parseFloat(raw);
                /* 數字框以 GB 輸入，換回 MB 後對齊 512 MB 一格 */
                if (Number.isFinite(gb)) setMemory(clamp(Math.round(gb * 2) * MEM_STEP, MEM_MIN, MEM_MAX));
              }}
              currentText={t("SpecificationsTab.currentMemoryLabel", { value: formatGb(config.memory_mb) })}
              deltaText={t("SpecificationsTab.deltaGb", { delta: signedGb(memory - config.memory_mb) })}
            />
            <SliderField
              id="spec-disk"
              wide
              label={t("SpecificationsTab.diskLabel")}
              unit="GB"
              disabled={inputsDisabled || !currentDisk}
              min={diskMin}
              max={diskMax}
              step={1}
              value={currentDisk ? disk : diskMin}
              current={currentDisk || null}
              ticks={diskTicks(diskMin, diskMax).map((v) => ({ value: v, label: String(v) }))}
              onChange={setDisk}
              inputValue={currentDisk ? disk : diskMin}
              inputMin={diskMin}
              inputMax={diskMax}
              inputStep={1}
              onInput={(raw) => {
                const n = Number.parseInt(raw, 10);
                if (Number.isFinite(n)) setDisk(clamp(n, diskMin, diskMax));
              }}
              currentText={currentDisk
                ? t("SpecificationsTab.currentDiskLabel", { value: currentDisk })
                : t("SpecificationsTab.diskUnknown")}
              deltaText={t("SpecificationsTab.deltaGb", { delta: signed(disk - currentDisk) })}
            />
          </div>

          {!isAdmin && !specFixed && (
            <div className={`${styles.field} ${reasonInvalid ? styles.fieldInvalid : ""}`}>
              <label htmlFor="spec-reason">{t("SpecificationsTab.reasonLabel")}</label>
              <textarea
                id="spec-reason"
                ref={reasonRef}
                rows={4}
                placeholder={t("SpecificationsTab.reasonPlaceholder")}
                aria-invalid={reasonInvalid}
                value={reason}
                disabled={formLocked}
                onChange={(e) => { setReason(e.target.value); setReasonInvalid(false); }}
              />
              <span className={styles.fieldHint}>{t("SpecificationsTab.reasonHint")}</span>
            </div>
          )}

          {!specFixed && (
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={busy || formLocked}
              onClick={handleSubmit}
            >
              {busy ? t("SpecificationsTab.processing") : isAdmin ? t("SpecificationsTab.applyChanges") : t("SpecificationsTab.submitRequest")}
            </button>
          )}
        </div>
      </div>

      {!isAdmin && !specFixed && (
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
      )}
    </div>
  );
}
