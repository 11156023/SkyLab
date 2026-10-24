import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import sl from "./SpecificationsTab.module.scss";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import ErrorState from "../../../../components/ErrorState/ErrorState";
import NotFoundState from "../../../../components/ErrorState/NotFoundState";
import { isNotFound } from "../../../../services/api";
import MIcon from "../../../../components/MIcon";
import { useConfirm } from "../../../../components/ConfirmDialog/ConfirmProvider";
import { useAuth } from "../../../../contexts/AuthContext";
import { ResourcesService } from "../../../../services/resources";
import { growthRange, quotaRemaining, sliderTicks } from "../../../../utils/quotaLimits";
import {
  useLoadError,
  useRefreshResourceDetail,
  useResourceDetailCacheUpdater,
  useResourceQuery,
  useSpecsQuery,
} from "../../../../queries/resourceDetail";
import { useMyQuotaUsageQuery, useQuotaCacheUpdater } from "../../../../queries/quotas";
import { useMySpecChangeRequestsQuery, useSpecChangeRequestCacheUpdater } from "../../../../queries/specChangeRequests";
import NumberInput from "../../../../components/NumberInput/NumberInput";
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
import { isAdminUser } from "../../../../utils/roles";

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
    <div className={`${styles.noteBox} ${styles.noteCard}`}>
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
 * 數字框在離開欄位時才定稿（夾範圍、對齊步進）再呼叫 onInput，編輯中不干涉。
 * 目前值不是數字（例如 LXC 沒設核心上限）時 current 給 null，由 changed 決定要不要顯示變化標籤。
 */
export function SliderField({
  id, label, unit, wide, disabled,
  min, max, step, value, current, ticks, onChange,
  inputValue, inputMin, inputMax, inputStep, inputPlaceholder, onInput,
  currentText, deltaText, limitText, changed: changedProp,
}) {
  const { t } = useTranslation("personal");
  const pct = (v) => `${((v - min) / (max - min || 1)) * 100}%`;
  const changed = changedProp ?? (current != null && value !== current);
  const showMark = current != null && current > min && current < max;

  return (
    <div className={`${sl.field} ${disabled ? sl.fieldDisabled : ""} ${wide ? sl.gridWide : ""}`}>
      <div className={sl.labelRow}>
        <label htmlFor={id} className={sl.label}>{label}</label>
        <div className={sl.valueBox}>
          <NumberInput
            className={sl.numInput}
            min={inputMin}
            max={inputMax}
            step={inputStep}
            value={inputValue}
            placeholder={inputPlaceholder}
            disabled={disabled}
            aria-label={label}
            onCommit={onInput}
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
          <span className={`${sl.delta} ${current != null && value > current ? sl.deltaUp : sl.deltaDown}`}>{deltaText}</span>
        )}
      </div>
      {limitText && <p className={sl.limit}>{limitText}</p>}
    </div>
  );
}

export default function SpecificationsTab({ vmid }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const { user } = useAuth();
  const isAdmin = isAdminUser(user);
  const detailCache = useResourceDetailCacheUpdater();
  const refreshDetail = useRefreshResourceDetail();
  const requestCache = useSpecChangeRequestCacheUpdater();
  const quotaCache = useQuotaCacheUpdater();

  /* /specs 回的是實際生效值（cpu_cores / memory_mb / disk_gb），不是 Proxmox 原始 config。
     表單用：不輪詢、切回視窗也不重抓，只在套用完成或管理員改完後重抓 */
  const specsQuery = useSpecsQuery(vmid);
  const config = specsQuery.data ?? null;
  /* 從沒載到就失敗才顯示錯誤；之後的重抓期間維持錯誤畫面、不閃轉圈圈（見 useLoadError） */
  const { error } = useLoadError(specsQuery);

  // 課堂與快速練習的機器照課程環境版本建立，規格不接受個別調整；
  // 資源的 can_request_spec_change 為 false 時鎖住整張調整表單（查不到時當成可以調整）。
  const resourceQuery = useResourceQuery(vmid, { refetchInterval: false });
  const specFixed = resourceQuery.data?.can_request_spec_change === false;

  /* 管理員直接套用，不走申請；只有一般使用者需要看自己的申請進度。
     有申請套用中時查詢自己會加快到每 5 秒一次；載入失敗不影響表單本身 */
  const requestsQuery = useMySpecChangeRequestsQuery({ enabled: !isAdmin });
  const myRequests = isAdmin ? undefined : requestsQuery.data;
  const openRequest = findOpenRequest(myRequests, vmid);
  const appliedWarning = findAppliedWithWarning(myRequests, vmid);

  // 表單值只記使用者動過的部分，null＝沿用目前規格。
  // 核心數的「目前」可能是 null（LXC 不設 cores 時可用整台主機的 CPU），使用者動過拉桿才會變成數字
  const [coresDraft, setCores] = useState(null);
  const [memoryDraft, setMemory] = useState(null);
  const [diskDraft, setDisk] = useState(null);
  /* 目前規格重抓到新值（套用完成、管理員直接改完）時，表單回到新的目前值 */
  useEffect(() => {
    setCores(null);
    setMemory(null);
    setDisk(null);
  }, [config]);
  const [reason, setReason] = useState("");
  const [reasonInvalid, setReasonInvalid] = useState(false);
  const reasonRef = useRef(null);
  const [busy, setBusy] = useState(false);

  /* 套用（關機 → 改規格 → 開機）約 1～3 分鐘；從「套用中」離開（完成、失敗或中斷）時重抓規格，
     讓「目前」數字更新，詳情與機器清單上的規格也跟著換 */
  const applying = openRequest?.apply_status === "applying";
  const wasApplyingRef = useRef(applying);
  useEffect(() => {
    if (wasApplyingRef.current && !applying) {
      detailCache.invalidateSpecs(vmid);
      refreshDetail(vmid);
    }
    wasApplyingRef.current = applying;
  }, [applying, vmid, detailCache, refreshDetail]);

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
      /* 就地把這張改成「套用中」：通知列馬上換狀態，清單查詢也隨之改成 5 秒追進度 */
      if (res?.request) await requestCache.update(res.request);
      else await requestCache.invalidate();
      toast.success(t("SpecificationsTab.applyStarted"));
    } catch (e) {
      toast.error(e?.message ?? t("SpecificationsTab.applyFailed"));
      await requestCache.invalidate();
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
      /* 回來的是撤銷後的申請（已結案）：就地換掉，通知列隨即消失 */
      const cancelled = await SpecChangeRequestsService.cancel(openRequest.id);
      if (cancelled?.id != null) await requestCache.update(cancelled);
      else await requestCache.invalidate();
      toast.success(t("SpecificationsTab.cancelSuccess"));
    } catch (e) {
      toast.error(e?.message ?? t("SpecificationsTab.cancelFailed"));
      await requestCache.invalidate();
    } finally {
      setBusy(false);
    }
  };

  /* 配額：後端送單、核准、套用都會對「調大的部分」檢查剩餘配額，
     所以拉桿上限＝目前規格＋剩餘配額。管理員直接改規格不走申請、不扣自己的配額。
     不輪詢；送出／撤銷／套用完成後剩餘量會變，申請狀態或目前規格一變就重抓
     （首次載入由查詢自己抓，兩邊都載好之後的變化才重抓） */
  const quotaQuery = useMyQuotaUsageQuery({ enabled: !isAdmin, refetchInterval: false });
  const quotaUsage = isAdmin ? null : quotaQuery.data ?? null;
  const requestsSettled = isAdmin || requestsQuery.data !== undefined || requestsQuery.isError;
  const quotaSignature = config && requestsSettled
    ? [openRequest?.id, openRequest?.apply_status, config.cpu_cores, config.memory_mb, config.disk_gb].join("|")
    : null;
  const quotaSignatureRef = useRef(null);
  useEffect(() => {
    if (quotaSignature === null) return;
    const previous = quotaSignatureRef.current;
    quotaSignatureRef.current = quotaSignature;
    if (!isAdmin && previous !== null && previous !== quotaSignature) quotaCache.invalidateMyUsage();
  }, [quotaSignature, isAdmin, quotaCache]);
  const quotaLeft = quotaRemaining(quotaUsage);
  const currentDisk = config?.disk_gb ?? 0;
  /* 不限 → 設上限時後端把目前值當 0 計算配額（spec_change_delta），拉桿上限跟著一致 */
  const coresRange = growthRange({
    min: CORE_MIN, max: CORE_MAX,
    current: config?.cpu_cores || 0, remaining: quotaLeft.cores,
  });
  const memoryRange = growthRange({
    min: MEM_MIN, max: MEM_MAX, step: MEM_STEP,
    current: config?.memory_mb || MEM_MIN, remaining: quotaLeft.memoryMb,
  });
  /* 磁碟只能放大，拉桿下限就是目前大小；讀不到目前大小時整條停用 */
  const diskMin = currentDisk || 1;
  const diskRange = growthRange({
    min: diskMin, max: Math.max(DISK_MAX, diskMin),
    current: diskMin, remaining: quotaLeft.diskGb,
  });
  /* 表單值：沒動過＝目前規格；動過的值在配額晚一步載入時壓回上限（目前規格本身一定在範圍內） */
  const cores = coresDraft == null ? (config?.cpu_cores || null) : Math.min(coresDraft, coresRange.max);
  const memory = Math.min(memoryDraft ?? (config?.memory_mb || 512), memoryRange.max);
  const disk = Math.min(diskDraft ?? (config?.disk_gb || 0), diskRange.max);
  const diskChanged = currentDisk > 0 && disk !== currentDisk;
  /* 沒設核心上限時顯示「不限」；只有使用者真的動過 CPU 拉桿／數字框才送核心數 */
  const coresUnlimited = !config?.cpu_cores;
  const coresChanged = config != null && cores != null && cores !== config.cpu_cores;
  const limitText = (range, capKey, value, exhaustedKey = "SpecificationsTab.quotaExhausted") => {
    if (isAdmin || specFixed || !range.quotaCapped) return null;
    return range.exhausted ? t(exhaustedKey) : t(capKey, { max: value });
  };

  const handleSubmit = async () => {
    const hasChanges = coresChanged || memory !== config.memory_mb || diskChanged;

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
          cores: coresChanged ? cores : undefined,
          memory: memory !== config.memory_mb ? memory : undefined,
          disk_size: diskChanged ? `+${disk - currentDisk}G` : undefined,
        });
        toast.success(t("SpecificationsTab.updateSuccess"));
        /* 規格與詳情一起重抓（總覽的核心數、記憶體與機器清單跟著換）；表單隨新規格回到目前值 */
        await Promise.all([detailCache.invalidateSpecs(vmid), refreshDetail(vmid)]);
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
      await SpecChangeRequestsService.create({
        vmid,
        change_type: "combined",
        reason,
        requested_cpu: coresChanged ? cores : undefined,
        requested_memory: memory !== config.memory_mb ? memory : undefined,
        requested_disk: diskChanged ? disk : undefined,
      });
      toast.success(t("SpecificationsTab.requestSubmitted"));
      setReason("");
      /* 新申請要出現在通知列並鎖住表單：重抓「我的申請」（期間 busy 擋住重複送出） */
      await requestCache.invalidate();
    } catch (e) {
      toast.error(e?.message ?? t("SpecificationsTab.submitFailed"));
    } finally {
      setBusy(false);
    }
  };

  if (error) return isNotFound(error) ? <NotFoundState /> : <ErrorState />;
  if (!config) return <LoadingState />;

  /* 一張處理中就不能再送（後端也擋），表單只留給管理員或沒有申請時 */
  const formLocked = !isAdmin && Boolean(openRequest);
  const inputsDisabled = specFixed || formLocked;

  let desc;
  if (specFixed) desc = t("SpecificationsTab.descFixed");
  else if (isAdmin) desc = t("SpecificationsTab.descAdmin");
  else if (formLocked) desc = t("SpecificationsTab.descLocked");
  else desc = t("SpecificationsTab.descUser");

  return (
    <div className={styles.tabStack}>
      {!isAdmin && appliedWarning && !openRequest && (
        <div className={`${styles.noteBox} ${styles.noteCard}`}>
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
        {/* 標題由外層分頁承擔，卡內只留說明 */}
        <div className={styles.cardHeader}>
          <p className={styles.cardDesc}>{desc}</p>
        </div>
        <div className={styles.cardBody}>
          <div className={sl.grid}>
            <SliderField
              id="spec-cores"
              label={t("SpecificationsTab.cpuCoresLabel")}
              unit={t("SpecificationsTab.coresUnit")}
              disabled={inputsDisabled}
              min={CORE_MIN}
              max={coresRange.max}
              step={1}
              /* 不限時拉桿停在最右端；一拉動就變成設定核心上限 */
              value={cores ?? coresRange.max}
              current={coresUnlimited ? null : config.cpu_cores}
              changed={coresChanged}
              ticks={sliderTicks(CORE_TICKS, coresRange).map(({ value, label }) => ({ value, label }))}
              onChange={setCores}
              inputValue={cores ?? ""}
              inputMin={CORE_MIN}
              inputMax={coresRange.max}
              inputStep={1}
              inputPlaceholder={coresUnlimited ? t("SpecificationsTab.coresUnlimited") : undefined}
              onInput={setCores}
              currentText={coresUnlimited
                ? t("SpecificationsTab.currentCoresUnlimited")
                : t("SpecificationsTab.currentLabel", { value: config.cpu_cores })}
              deltaText={coresUnlimited
                ? t("SpecificationsTab.newCoresCap", { value: cores })
                : t("SpecificationsTab.deltaCores", { delta: signed(cores - config.cpu_cores) })}
              limitText={limitText(coresRange, "SpecificationsTab.quotaCapCores", coresRange.max)}
            />
            <SliderField
              id="spec-memory"
              label={t("SpecificationsTab.memoryLabel")}
              unit="GB"
              disabled={inputsDisabled}
              min={MEM_MIN}
              max={memoryRange.max}
              step={MEM_STEP}
              value={memory}
              current={config.memory_mb}
              ticks={sliderTicks(MEM_TICKS, memoryRange, (v) => `${formatGb(v)}GB`).map(({ value, label }) => ({ value, label }))}
              onChange={setMemory}
              inputValue={memory / 1024}
              inputMin={MEM_MIN / 1024}
              inputMax={memoryRange.max / 1024}
              inputStep={0.5}
              /* 數字框以 GB 輸入（0.5 GB 一格），定稿後換回 MB */
              onInput={(gb) => setMemory(Math.round(gb * 1024))}
              currentText={t("SpecificationsTab.currentMemoryLabel", { value: formatGb(config.memory_mb) })}
              deltaText={t("SpecificationsTab.deltaGb", { delta: signedGb(memory - config.memory_mb) })}
              limitText={limitText(memoryRange, "SpecificationsTab.quotaCapGb", formatGb(memoryRange.max))}
            />
            <SliderField
              id="spec-disk"
              wide
              label={t("SpecificationsTab.diskLabel")}
              unit="GB"
              disabled={inputsDisabled || !currentDisk || diskRange.max <= diskRange.min}
              min={diskMin}
              max={diskRange.max}
              step={1}
              value={currentDisk ? disk : diskMin}
              current={currentDisk || null}
              ticks={diskTicks(diskMin, diskRange.max).map((v) => ({ value: v, label: String(v) }))}
              onChange={setDisk}
              inputValue={currentDisk ? disk : diskMin}
              inputMin={diskMin}
              inputMax={diskRange.max}
              inputStep={1}
              onInput={setDisk}
              currentText={currentDisk
                ? t("SpecificationsTab.currentDiskLabel", { value: currentDisk })
                : t("SpecificationsTab.diskUnknown")}
              deltaText={t("SpecificationsTab.deltaGb", { delta: signed(disk - currentDisk) })}
              limitText={limitText(diskRange, "SpecificationsTab.quotaCapGb", diskRange.max, "SpecificationsTab.quotaExhaustedDisk")}
            />
          </div>

          {!isAdmin && !specFixed && (
            <div className={`${styles.field} ${formLocked ? sl.fieldDisabled : ""} ${reasonInvalid ? styles.fieldInvalid : ""}`}>
              <label htmlFor="spec-reason">{t("SpecificationsTab.reasonLabel")}</label>
              <textarea
                id="spec-reason"
                ref={reasonRef}
                rows={4}
                placeholder={t("SpecificationsTab.reasonHint")}
                aria-invalid={reasonInvalid}
                value={reason}
                disabled={formLocked}
                onChange={(e) => { setReason(e.target.value); setReasonInvalid(false); }}
              />
            </div>
          )}

          {!specFixed && (
            <button
              type="button"
              className={`${styles.btnPrimary} ${sl.applyBtn}`}
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
