import { useContext, useEffect, useMemo, useState } from "react";
import styles from "./RequestFormPage.module.scss";
import { LayoutContext } from "../../../layout/DashboardLayout";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { VmRequestsService } from "../../../services/vmRequests";
import { VmRequestAvailabilityService } from "../../../services/vmRequestAvailability";
import { GpuService } from "../../../services/gpu";
import { TemplatesService } from "../../../services/templates";
import { apiGet } from "../../../services/api";
import AiSidePanel from "./AiSidePanel";
import AvailabilityPanel from "../../../components/AvailabilityPanel/AvailabilityPanel";
import MIcon from "../../../components/MIcon";

/* Hostname normalization — preserves alphanumeric, replaces others with hyphen */
function normalizeHostname(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63);
}

/* ── Form field primitives ── */
function FieldGroup({ label, hint, required, error, children, labelRight }) {
  return (
    <div className={styles.formGroup}>
      <label className={styles.label}>
        <span>
          {label}
          {required && <span className={styles.required}> *</span>}
        </span>
        {labelRight && <span className={styles.labelValue}>{labelRight}</span>}
      </label>
      {children}
      {hint  && <p className={styles.fieldHint}>{hint}</p>}
      {error && <p className={styles.fieldError}>{error}</p>}
    </div>
  );
}

function SelectField({ value, onChange, disabled, children, placeholder }) {
  return (
    <select
      className={styles.select}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
    >
      {placeholder && <option value="" disabled>{placeholder}</option>}
      {children}
    </select>
  );
}

/* ── Helpers ── */
const DT_FMT = { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" };
const formatDT = (iso) => new Date(iso).toLocaleString("zh-TW", DT_FMT);
const formatOstemplate = (v) => v.split("/").pop()?.replace(".tar.zst", "") ?? v;
const GPU_OPTIONS_DEBOUNCE_MS = 300;
const ADVISE_DEBOUNCE_MS = 500;
const CONFIDENCE_LABEL = { high: "高信心", medium: "中信心", low: "低信心" };
const gpuLabel = (gpu) => {
  const vram = gpu.total_vram_mb > 0
    ? ` (${gpu.total_vram_mb >= 1024 ? `${(gpu.total_vram_mb / 1024).toFixed(0)} GB` : `${gpu.total_vram_mb} MB`})`
    : gpu.vram ? ` (${gpu.vram})` : "";
  return `${gpu.description || gpu.mapping_id}${vram} [${gpu.available_count}/${gpu.device_count} 可用]${gpu.available_count <= 0 ? " — 已滿" : ""}`;
};

/* ── Validation messages（對齊舊版 zh-TW locales）── */
const MSG = {
  hostnameRequired: "名稱為必填項",
  hostnameInvalid:  "僅允許小寫字母、數字和連字符，且不能以連字符開頭或結尾",
  passwordRequired: "密碼為必填項",
  passwordMinLen:   "密碼至少需要 8 個字符",
  reasonRequired:   "申請原因為必填項",
  reasonMinLen:     "申請原因至少需要 10 個字符",
  templateRequired: "範本為必填項",
  osRequired:       "作業系統為必填項",
  usernameRequired: "使用者名稱為必填項",
  startRequired:    "請選擇開始時間",
  endRequired:      "請選擇結束時間",
  endBeforeStart:   "結束時間必須晚於開始時間",
  endInPast:        "結束時間必須晚於現在",
};

export default function RequestFormPage({ onBack, className }) {
  const { user }  = useAuth();
  const toast     = useToast();
  const isPrivileged = user?.is_superuser || user?.role === "admin" || user?.role === "teacher";
  const { setCompactFooter } = useContext(LayoutContext);
  useEffect(() => { setCompactFooter(true); return () => setCompactFooter(false); }, [setCompactFooter]);

  const [closing, setClosing]   = useState(false);
  const [aiOpen, setAiOpen]     = useState(false);
  const [rightTab, setRightTab] = useState("ai");

  /* 範本系統 2.0：LXC 可選範本，選了走克隆路徑（免映像檔） */
  const [sysTemplates, setSysTemplates]   = useState([]);
  const [sysTplLoading, setSysTplLoading] = useState(false);
  const [selectedTplId, setSelectedTplId] = useState("");

  /* 類型選擇模式：manual = 手動選 LXC/VM；auto = 規則引擎自動判斷 */
  const [typeMode, setTypeMode]           = useState("manual");
  const [advice, setAdvice]               = useState(null);
  const [adviseLoading, setAdviseLoading] = useState(false);

  /* Form state */
  const [resourceType, setResourceType] = useState("lxc");
  const [mode, setMode]                 = useState("scheduled");
  const [form, setForm] = useState({
    hostname:         "",
    ostemplate:       "",
    password:         "",
    template_id:      "",
    username:         "",
    cores:            2,
    memory:           2048,
    rootfs_size:      8,
    disk_size:        20,
    gpu_mapping_id:   "",
    start_at:         "",
    end_at:           "",
    immediate_no_end: true,
    reason:           "",
  });
  const [errors, setErrors]           = useState({});
  const [submitting, setSubmitting]   = useState(false);
  const [availabilityHint, setAvailabilityHint] = useState(null);

  /* API data */
  const [lxcTemplates, setLxcTemplates] = useState([]);
  const [lxcLoading, setLxcLoading]     = useState(false);
  const [vmTemplates, setVmTemplates]   = useState([]);
  const [vmLoading, setVmLoading]       = useState(false);
  const [gpuOptions, setGpuOptions]     = useState([]);
  const [gpuLoading, setGpuLoading]     = useState(false);
  const [gpuOptionsKey, setGpuOptionsKey] = useState("");

  /* ── API fetches ── */
  useEffect(() => {
    if (resourceType !== "lxc" || lxcTemplates.length > 0) return;
    setLxcLoading(true);
    apiGet("/api/v1/lxc/templates")
      .then(setLxcTemplates)
      .catch(() => {})
      .finally(() => setLxcLoading(false));
  }, [resourceType]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (resourceType !== "vm" || vmTemplates.length > 0) return;
    setVmLoading(true);
    apiGet("/api/v1/vm/templates")
      .then(setVmTemplates)
      .catch(() => {})
      .finally(() => setVmLoading(false));
  }, [resourceType]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setSysTplLoading(true);
    TemplatesService.list()
      .then((res) => setSysTemplates(res?.data ?? []))
      .catch(() => {})
      .finally(() => setSysTplLoading(false));
  }, []);

  const lxcSysTemplates = useMemo(
    () => sysTemplates.filter(
      (t) => t.resource_type === "lxc" && t.status === "ready" && t.pve_exists !== false,
    ),
    [sysTemplates],
  );
  const selectedTpl = lxcSysTemplates.find((t) => t.id === selectedTplId) || null;

  const canLoadGpu = resourceType === "vm";
  const gpuWindowReady = Boolean(mode === "scheduled" && form.start_at && form.end_at);
  const gpuOptionsRequestKey = canLoadGpu
    ? `${mode}|${gpuWindowReady ? form.start_at : ""}|${gpuWindowReady ? form.end_at : ""}`
    : "";

  useEffect(() => {
    if (resourceType !== "vm" && form.gpu_mapping_id) {
      setForm((prev) => ({ ...prev, gpu_mapping_id: "" }));
    }
  }, [resourceType, form.gpu_mapping_id]);

  useEffect(() => {
    if (!canLoadGpu) {
      setGpuOptions([]);
      setGpuOptionsKey("");
      setGpuLoading(false);
      return;
    }
    let cancelled = false;
    const requestKey = gpuOptionsRequestKey;
    const params = gpuWindowReady
      ? { startAt: form.start_at, endAt: form.end_at }
      : undefined;
    const timeoutId = window.setTimeout(() => {
      if (cancelled) return;
      setGpuLoading(true);
      GpuService.listOptions(params)
        .then((options) => {
          if (cancelled) return;
          setGpuOptions(options);
          setGpuOptionsKey(requestKey);
        })
        .catch(() => {
          if (cancelled) return;
          setGpuOptions([]);
          setGpuOptionsKey(requestKey);
        })
        .finally(() => {
          if (!cancelled) setGpuLoading(false);
        });
    }, gpuWindowReady ? GPU_OPTIONS_DEBOUNCE_MS : 0);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [canLoadGpu, form.start_at, form.end_at, gpuOptionsRequestKey, gpuWindowReady, mode]);

  /* ── Helpers ── */
  useEffect(() => {
    if (resourceType !== "vm" || !form.gpu_mapping_id) return;
    if (!canLoadGpu) return;
    if (!gpuWindowReady && mode === "scheduled") return;
    if (gpuLoading) return;
    if (gpuOptionsKey !== gpuOptionsRequestKey) return;
    const selected = gpuOptions.find((gpu) => gpu.mapping_id === form.gpu_mapping_id);
    if (!selected || selected.available_count <= 0) {
      setForm((prev) => ({ ...prev, gpu_mapping_id: "" }));
    }
  }, [
    canLoadGpu,
    gpuWindowReady,
    mode,
    resourceType,
    form.gpu_mapping_id,
    gpuLoading,
    gpuOptions,
    gpuOptionsKey,
    gpuOptionsRequestKey,
  ]);

  function set(key, val) {
    setForm((prev) => ({ ...prev, [key]: val }));
    if (errors[key]) setErrors((prev) => ({ ...prev, [key]: "" }));
  }

  /* ── VM vs LXC 自動判斷（Auto 模式）── */
  const adviseRequest = useMemo(() => ({
    os_info: resourceType === "lxc"
      ? (selectedTpl?.name || (form.ostemplate ? formatOstemplate(form.ostemplate) : null))
      : (vmTemplates.find((t) => String(t.vmid) === String(form.template_id))?.name || null),
    reason: form.reason.trim() || null,
    cores: Number(form.cores) || null,
    memory: Number(form.memory) || null,
    gpu_mapping_id: form.gpu_mapping_id?.trim() || null,
  }), [
    resourceType,
    selectedTpl,
    vmTemplates,
    form.ostemplate,
    form.template_id,
    form.reason,
    form.cores,
    form.memory,
    form.gpu_mapping_id,
  ]);

  /* 500ms debounce，避免每個按鍵都打 advise API */
  useEffect(() => {
    if (typeMode !== "auto") return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setAdviseLoading(true);
      VmRequestsService.advise(adviseRequest)
        .then((res) => { if (!cancelled) setAdvice(res); })
        .catch(() => {
          /* advisor 被管理員停用（400）→ 退回手動模式 */
          if (cancelled) return;
          setTypeMode("manual");
          setAdvice(null);
          toast.error("自動判斷目前未啟用，已切換為手動選擇。");
        })
        .finally(() => { if (!cancelled) setAdviseLoading(false); });
    }, ADVISE_DEBOUNCE_MS);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [typeMode, adviseRequest]); // eslint-disable-line react-hooks/exhaustive-deps

  /* 建議結果 → 同步 resource_type（範本欄位區隨之切換） */
  useEffect(() => {
    if (typeMode !== "auto" || !advice) return;
    if (advice.resource_type !== resourceType) setResourceType(advice.resource_type);
  }, [typeMode, advice, resourceType]);

  const recommendationContext = useMemo(() => ({
    resource_type: resourceType,
    mode,
    start_at: form.start_at || null,
    end_at: form.end_at || null,
    selected_gpu_mapping_id: form.gpu_mapping_id || null,
    gpu_options: gpuOptions,
  }), [resourceType, mode, form.start_at, form.end_at, form.gpu_mapping_id, gpuOptions]);

  function applyAiPrefill(prefill) {
    if (!prefill) return;
    const nextResourceType = prefill.resource_type === "vm" ? "vm" : "lxc";
    setResourceType(nextResourceType);

    if (nextResourceType !== "lxc") setSelectedTplId("");

    setForm((prev) => {
      const disk = Number(prefill.disk_gb || 0);
      return {
        ...prev,
        hostname: prefill.hostname ? normalizeHostname(prefill.hostname) : prev.hostname,
        ostemplate: nextResourceType === "lxc"
          ? (prefill.lxc_os_image || prev.ostemplate)
          : prev.ostemplate,
        template_id: nextResourceType === "vm" && prefill.vm_template_id
          ? String(prefill.vm_template_id)
          : prev.template_id,
        username: nextResourceType === "vm" && prefill.username
          ? prefill.username
          : prev.username,
        cores: prefill.cores ? Number(prefill.cores) : prev.cores,
        memory: prefill.memory_mb ? Number(prefill.memory_mb) : prev.memory,
        rootfs_size: nextResourceType === "lxc" && disk
          ? Math.max(8, disk)
          : prev.rootfs_size,
        disk_size: nextResourceType === "vm" && disk
          ? Math.max(20, disk)
          : prev.disk_size,
        gpu_mapping_id: nextResourceType === "vm" && prefill.gpu_mapping_id
          ? prefill.gpu_mapping_id
          : prev.gpu_mapping_id,
        reason: prefill.reason || prev.reason,
      };
    });
    setErrors({});
    toast.success("已匯入 AI 推薦配置，請確認欄位後送出。");
  }

  function handleBack() {
    setClosing(true);
    setTimeout(onBack, 180);
  }

  /* ── Validation ── */
  function validate() {
    const errs = {};
    const hostnameRegex = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;

    if (!form.hostname.trim())          errs.hostname = MSG.hostnameRequired;
    else if (!hostnameRegex.test(form.hostname)) errs.hostname = MSG.hostnameInvalid;

    if (!form.password)                 errs.password = MSG.passwordRequired;
    else if (form.password.length < 8)  errs.password = MSG.passwordMinLen;

    if (!form.reason.trim())            errs.reason = MSG.reasonRequired;
    else if (form.reason.trim().length < 10) errs.reason = MSG.reasonMinLen;

    if (resourceType === "lxc" && !selectedTplId && !form.ostemplate)
      errs.ostemplate = MSG.templateRequired;
    if (resourceType === "vm") {
      if (!form.template_id)            errs.template_id = MSG.osRequired;
      if (!form.username.trim())        errs.username    = MSG.usernameRequired;
    }

    if (mode === "scheduled") {
      if (!form.start_at) errs.start_at = MSG.startRequired;
      if (!form.end_at)   errs.end_at   = MSG.endRequired;
      if (form.start_at && form.end_at && new Date(form.start_at) >= new Date(form.end_at))
        errs.end_at = MSG.endBeforeStart;
    }
    if (mode === "immediate" && !form.immediate_no_end && form.end_at) {
      if (new Date(form.end_at) <= new Date()) errs.end_at = MSG.endInPast;
    }
    return errs;
  }

  /* ── Submit ── */
  async function handleSubmit(e) {
    e.preventDefault();
    const errs = validate();
    if (Object.keys(errs).length > 0) { setErrors(errs); return; }

    setSubmitting(true);
    try {
      /* GPU re-availability check before submitting (mirrors old frontend logic) */
      const selectedGpuId = form.gpu_mapping_id?.trim();
      if (resourceType === "vm" && selectedGpuId) {
        const params = mode === "scheduled"
          ? { startAt: form.start_at || undefined, endAt: form.end_at || undefined }
          : undefined;
        const latestOptions = await GpuService.listOptions(params);
        const gpuStillAvailable = latestOptions.some(
          (g) => g.mapping_id === selectedGpuId && g.available_count > 0,
        );
        if (!gpuStillAvailable) {
          toast.error("目前所選時段的 GPU 已不可用，請重新選擇時段或 GPU。");
          setSubmitting(false);
          return;
        }
      }

      if (mode === "scheduled") {
        const windowAvailability = await VmRequestAvailabilityService.windowAvailability({
          resource_type: resourceType,
          cores: form.cores,
          memory: form.memory,
          ...(resourceType === "lxc"
            ? { rootfs_size: form.rootfs_size }
            : { disk_size: form.disk_size }),
          gpu_required: selectedGpuId ? 1 : 0,
          gpu_mapping_id: selectedGpuId || undefined,
          start_at: form.start_at,
          end_at: form.end_at,
          mode: "scheduled",
        });

        if (windowAvailability?.feasible === false) {
          toast.error(
            windowAvailability.reason ||
            windowAvailability.summary ||
            "所選時段目前沒有足夠資源，請重新選擇時段或調整規格。",
          );
          setSubmitting(false);
          return;
        }
      }

      const body = {
        resource_type: resourceType,
        mode,
        hostname:  form.hostname,
        password:  form.password,
        cores:     form.cores,
        memory:    form.memory,
        reason:    form.reason.trim(),
        storage:   "local-lvm",
        requested_mode: typeMode,
        ...(resourceType === "lxc"
          ? {
              rootfs_size: form.rootfs_size,
              ...(selectedTpl
                ? {
                    /* 範本系統克隆路徑：帶 PVE VMID，免映像檔 */
                    template_id: selectedTpl.pve_vmid,
                    os_info: selectedTpl.name,
                  }
                : {
                    ostemplate: form.ostemplate,
                    os_info: form.ostemplate ? formatOstemplate(form.ostemplate) : null,
                  }),
            }
          : {
              template_id: Number(form.template_id),
              username: form.username,
              disk_size: form.disk_size,
              os_info:
                vmTemplates.find((t) => String(t.vmid) === String(form.template_id))?.name ||
                null,
            }),
        ...(selectedGpuId ? { gpu_mapping_id: selectedGpuId } : {}),
        ...(mode === "scheduled"
          ? { start_at: form.start_at, end_at: form.end_at }
          : (!form.immediate_no_end && form.end_at ? { end_at: form.end_at } : {})),
      };

      await VmRequestsService.create(body);
      toast.success("申請已提交，等待管理員審核");
      handleBack();
    } catch (err) {
      toast.error(err?.message ?? "發生錯誤，請重試。");
    } finally {
      setSubmitting(false);
    }
  }

  /* ── 範本選擇（範本系統 2.0）── */
  function handleSelectSysTemplate(id) {
    setSelectedTplId(id);
    if (errors.ostemplate) setErrors((prev) => ({ ...prev, ostemplate: "" }));
    const tpl = lxcSysTemplates.find((t) => t.id === id);
    if (!tpl) return;
    if (tpl.default_cores)  set("cores", Math.min(8, Math.max(1, tpl.default_cores)));
    if (tpl.default_memory) set("memory", Math.min(32768, Math.max(512, tpl.default_memory)));
    if (tpl.default_disk)   set("rootfs_size", Math.min(500, Math.max(8, tpl.default_disk)));
    if (!form.hostname.trim()) set("hostname", normalizeHostname(tpl.name));
  }

  const animCls = closing ? styles.animSlideOutRight : (className ?? "");

  return (
    <div className={`${styles.formPage} ${animCls}`}>
      {/* ── 頁首 ── */}
      <div className={styles.formPageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>申請虛擬機 / 容器</h1>
          <p className={styles.pageSubtitle}>填寫申請表單後送出，待管理員審核通過後會自動建立資源</p>
        </div>
        <button type="button" className={styles.backBtn} onClick={handleBack}>
          <MIcon name="arrow_back" size={18} />
          返回
        </button>
      </div>

      {/* ── 主體：表單 + AI 側欄 ── */}
      <div className={styles.formPageBody}>
        <div className={styles.formScroll}>
          <div className={styles.formInner}>
          <form id="request-form" onSubmit={handleSubmit} className={styles.form}>
            {/* ── 申請模式（管理員／老師） ── */}
            {isPrivileged && (
              <div className={styles.formSection}>
                <h2 className={styles.sectionTitle}>申請模式</h2>
                <div className={styles.typeToggle}>
                  {[
                    { key: "scheduled", label: "預約模式", icon: "calendar_month" },
                    { key: "immediate", label: "立即模式", icon: "bolt" },
                  ].map((m) => (
                    <button
                      key={m.key}
                      type="button"
                      className={`${styles.typeBtn} ${mode === m.key ? styles.typeBtnActive : ""}`}
                      onClick={() => setMode(m.key)}
                    >
                      <MIcon name={m.icon} size={16} />
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* ── 資源類型 ── */}
            <div className={styles.formSection}>
              <h2 className={styles.sectionTitle}>類型</h2>
              <div className={styles.typeToggle}>
                {[
                  { key: "auto",   label: "自動判斷（推薦）", icon: "auto_fix_high" },
                  { key: "manual", label: "手動選擇",        icon: "tune" },
                ].map((m) => (
                  <button
                    key={m.key}
                    type="button"
                    className={`${styles.typeBtn} ${typeMode === m.key ? styles.typeBtnActive : ""}`}
                    onClick={() => setTypeMode(m.key)}
                  >
                    <MIcon name={m.icon} size={16} />
                    {m.label}
                  </button>
                ))}
              </div>

              {typeMode === "auto" && (
                <div className={styles.adviceCard}>
                  {adviseLoading && !advice ? (
                    <p className={styles.adviceEmpty}>
                      <MIcon name="progress_activity" size={14} className={styles.adviceSpin} />
                      分析工作負載中…
                    </p>
                  ) : advice ? (
                    <>
                      <div className={styles.adviceHeader}>
                        <MIcon name={advice.resource_type === "vm" ? "computer" : "dashboard"} size={16} />
                        <span className={styles.adviceTitle}>
                          建議使用 {advice.resource_type === "vm" ? "QEMU 虛擬機" : "LXC 容器"}
                        </span>
                        <span className={styles.adviceBadge}>
                          {CONFIDENCE_LABEL[advice.confidence] ?? advice.confidence}
                        </span>
                        {adviseLoading && (
                          <MIcon name="progress_activity" size={13} className={styles.adviceSpin} />
                        )}
                      </div>
                      <ul className={styles.adviceReasons}>
                        {advice.reasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                      </ul>
                      <p className={styles.adviceFootnote}>
                        建議會依「用途說明、範本、規格、GPU」即時更新；如需固定類型請改用手動選擇。
                      </p>
                    </>
                  ) : (
                    <p className={styles.adviceEmpty}>填寫用途說明與規格後即會顯示建議。</p>
                  )}
                </div>
              )}

              {typeMode === "manual" && (
                <div className={styles.typeToggle}>
                  {[
                    { key: "lxc", label: "LXC 容器",   icon: "dashboard" },
                    { key: "vm",  label: "QEMU 虛擬機", icon: "computer"  },
                  ].map((t) => (
                    <button
                      key={t.key}
                      type="button"
                      className={`${styles.typeBtn} ${resourceType === t.key ? styles.typeBtnActive : ""}`}
                      onClick={() => setResourceType(t.key)}
                    >
                      <MIcon name={t.icon} size={16} />
                      {t.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* ── LXC 設定 ── */}
            {resourceType === "lxc" && (
              <div className={styles.formSection}>
                <h2 className={styles.sectionTitle}>容器設定</h2>

                <FieldGroup label="容器名稱" required error={errors.hostname}>
                  <input
                    className={styles.input}
                    placeholder="project-alpha-web"
                    value={form.hostname}
                    onChange={(e) => set("hostname", e.target.value)}
                    onBlur={(e) => set("hostname", normalizeHostname(e.target.value))}
                  />
                </FieldGroup>

                <FieldGroup label="範本（選填）"
                  hint={selectedTpl
                    ? "將從範本克隆建立，免選映像檔；登入帳密沿用範本內建設定"
                    : "從範本系統選擇可用範本，或留空改用作業系統映像檔建立"}>
                  {selectedTpl ? (
                    <div className={styles.templateSelected}>
                      <MIcon name="layers" size={16} />
                      <div className={styles.templateSelectedMeta}>
                        <span className={styles.templateSelectedName}>{selectedTpl.name}</span>
                        <span className={styles.templateSelectedSlug}>
                          v{selectedTpl.version}
                          {selectedTpl.description ? ` · ${selectedTpl.description}` : ""}
                        </span>
                      </div>
                      <button
                        type="button"
                        className={styles.templateClearBtn}
                        onClick={() => setSelectedTplId("")}
                        title="清除"
                      >
                        <MIcon name="close" size={16} />
                      </button>
                    </div>
                  ) : (
                    <SelectField
                      value={selectedTplId}
                      onChange={handleSelectSysTemplate}
                      disabled={sysTplLoading}
                    >
                      <option value="">
                        {sysTplLoading ? "載入中…" : "不使用範本（從映像檔建立）"}
                      </option>
                      {lxcSysTemplates.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}（v{t.version}）
                        </option>
                      ))}
                      {!sysTplLoading && lxcSysTemplates.length === 0 && (
                        <option value="__empty__" disabled>目前沒有可用的 LXC 範本</option>
                      )}
                    </SelectField>
                  )}
                </FieldGroup>

                {!selectedTpl && (
                  <FieldGroup label="作業系統映像檔" required error={errors.ostemplate}
                    hint="請從已上傳到節點的映像檔中選擇">
                    <SelectField
                      value={form.ostemplate}
                      onChange={(v) => set("ostemplate", v)}
                      disabled={lxcLoading}
                      placeholder={lxcLoading ? "載入中…" : "選擇映像檔"}
                    >
                      {lxcTemplates.map((t) => (
                        <option key={t.volid} value={t.volid}>{formatOstemplate(t.volid)}</option>
                      ))}
                      {!lxcLoading && lxcTemplates.length === 0 && (
                        <option value="" disabled>目前沒有可用映像檔</option>
                      )}
                    </SelectField>
                  </FieldGroup>
                )}

                <FieldGroup label="Root 密碼" required error={errors.password}
                  hint={selectedTpl
                    ? "克隆建立的容器沿用範本內建帳密，此密碼僅作平台紀錄"
                    : undefined}>
                  <input
                    className={styles.input}
                    type="password"
                    placeholder="至少 8 個字元"
                    value={form.password}
                    onChange={(e) => set("password", e.target.value)}
                  />
                </FieldGroup>
              </div>
            )}

            {/* ── VM 設定 ── */}
            {resourceType === "vm" && (
              <div className={styles.formSection}>
                <h2 className={styles.sectionTitle}>虛擬機設定</h2>

                <FieldGroup label="虛擬機名稱" required error={errors.hostname}>
                  <input
                    className={styles.input}
                    placeholder="web-server-01"
                    value={form.hostname}
                    onChange={(e) => set("hostname", e.target.value)}
                    onBlur={(e) => set("hostname", normalizeHostname(e.target.value))}
                  />
                </FieldGroup>

                <FieldGroup label="作業系統" required error={errors.template_id}>
                  <SelectField
                    value={form.template_id}
                    onChange={(v) => set("template_id", v)}
                    disabled={vmLoading}
                    placeholder={vmLoading ? "載入中…" : "選擇作業系統"}
                  >
                    {vmTemplates.map((t) => (
                      <option key={t.vmid} value={t.vmid}>{t.name}</option>
                    ))}
                    {!vmLoading && vmTemplates.length === 0 && (
                      <option value="" disabled>目前沒有可用範本</option>
                    )}
                  </SelectField>
                </FieldGroup>

                <div className={styles.formGrid}>
                  <FieldGroup label="使用者名稱" required error={errors.username}>
                    <input
                      className={styles.input}
                      placeholder="admin"
                      value={form.username}
                      onChange={(e) => set("username", e.target.value)}
                    />
                  </FieldGroup>

                  <FieldGroup label="密碼" required error={errors.password}>
                    <input
                      className={styles.input}
                      type="password"
                      placeholder="至少 8 個字元"
                      value={form.password}
                      onChange={(e) => set("password", e.target.value)}
                    />
                  </FieldGroup>
                </div>
              </div>
            )}

            {/* ── 硬體資源配置 ── */}
            <div className={styles.formSection}>
              <h2 className={styles.sectionTitle}>硬體資源配置</h2>

              <FieldGroup label="CPU 核心數" labelRight={`${form.cores} 核心`}>
                <input
                  type="range" min={1} max={8} step={1}
                  className={styles.slider}
                  value={form.cores}
                  onChange={(e) => set("cores", Number(e.target.value))}
                />
                <div className={styles.sliderTicks}>
                  {[1, 2, 4, 6, 8].map((v) => (
                    <span key={v} style={{ left: `${(v - 1) / (8 - 1) * 100}%` }}>{v}</span>
                  ))}
                </div>
              </FieldGroup>

              <FieldGroup label="記憶體 (RAM)" labelRight={`${(form.memory / 1024).toFixed(1)} GB`}>
                <input
                  type="range" min={512} max={32768} step={512}
                  className={styles.slider}
                  value={form.memory}
                  onChange={(e) => set("memory", Number(e.target.value))}
                />
                <div className={styles.sliderTicks}>
                  {[[1024,"1GB"],[8192,"8GB"],[16384,"16GB"],[24576,"24GB"],[32768,"32GB"]].map(([v, label]) => (
                    <span key={label} style={{ left: `${(v - 512) / (32768 - 512) * 100}%` }}>{label}</span>
                  ))}
                </div>
              </FieldGroup>

              {(() => {
                const isLxc   = resourceType === "lxc";
                const diskKey = isLxc ? "rootfs_size" : "disk_size";
                const diskMin = isLxc ? 8 : 20;
                return (
                  <FieldGroup label="硬碟空間 (Disk)" labelRight={
                    <div className={styles.diskInput}>
                      <input
                        type="number" min={diskMin} max={500}
                        className={`${styles.input} ${styles.inputNumber}`}
                        value={form[diskKey]}
                        onChange={(e) => set(diskKey, Math.min(500, Math.max(diskMin, Number(e.target.value) || diskMin)))}
                      />
                      <span className={styles.diskUnit}>GB</span>
                    </div>
                  }>
                    <input
                      type="range" min={diskMin} max={500} step={1}
                      className={styles.slider}
                      value={form[diskKey]}
                      onChange={(e) => set(diskKey, Number(e.target.value))}
                    />
                  </FieldGroup>
                );
              })()}
            </div>

            {/* ── GPU（VM only）── */}
            {resourceType === "vm" && (
              <div className={styles.formSection}>
                <h2 className={styles.sectionTitle}>GPU 加速（選填）</h2>

                {!canLoadGpu && mode === "scheduled" && (
                  <p className={styles.fieldHint}>請先選擇租借時段，再載入該時段可用的 GPU。</p>
                )}
                {!gpuLoading && gpuOptions.length === 0 && (
                  <p className={styles.fieldHint}>此時段目前沒有可用 GPU，可改選其他時段或不使用 GPU。</p>
                )}

                <FieldGroup
                  label="選擇 GPU"
                  hint="GPU 會依所選時段重新計算可用性，送出前仍會再做一次即時檢查"
                >
                  <SelectField
                    value={form.gpu_mapping_id || "__none__"}
                    onChange={(v) => set("gpu_mapping_id", v === "__none__" ? "" : v)}
                    disabled={gpuLoading || gpuOptions.length === 0}
                    placeholder={!canLoadGpu ? "請先選擇時段" : undefined}
                  >
                    <option value="__none__">不需要 GPU</option>
                    {gpuOptions.map((gpu) => (
                      <option key={gpu.mapping_id} value={gpu.mapping_id} disabled={gpu.available_count <= 0}>
                        {gpuLabel(gpu)}
                      </option>
                    ))}
                  </SelectField>
                </FieldGroup>
              </div>
            )}

            {/* ── 租借時段 ── */}
            <div className={styles.formSection}>
              <div className={styles.sectionTitleRow}>
                <h2 className={styles.sectionTitle}>
                  {mode === "immediate" ? "立即模式設定" : "租借時段"}
                </h2>
                {mode === "scheduled" && availabilityHint && (
                  <span className={styles.sectionHint}>{availabilityHint}</span>
                )}
              </div>

              {mode === "immediate" ? (
                <>
                  <p className={styles.fieldHint}>
                    立即模式會在送出申請後馬上開始部署，不需要選擇開始時間。
                  </p>
                  <label className={styles.checkboxLabel}>
                    <input
                      type="checkbox"
                      className={styles.checkbox}
                      checked={form.immediate_no_end}
                      onChange={(e) => set("immediate_no_end", e.target.checked)}
                    />
                    無限期 (No end date)
                  </label>
                  {!form.immediate_no_end && (
                    <FieldGroup label="結束時間" error={errors.end_at}>
                      <input
                        type="datetime-local"
                        className={styles.input}
                        value={form.end_at}
                        onChange={(e) => set("end_at", e.target.value)}
                      />
                    </FieldGroup>
                  )}
                </>
              ) : (
                <>
                  <AvailabilityPanel
                    draft={{
                      resource_type: resourceType,
                      cores:         form.cores,
                      memory:        form.memory,
                      ...(resourceType === "lxc"
                        ? { rootfs_size: form.rootfs_size }
                        : { disk_size:   form.disk_size }),
                      gpu_required: resourceType === "vm" && form.gpu_mapping_id ? 1 : 0,
                      gpu_mapping_id: resourceType === "vm" && form.gpu_mapping_id
                        ? form.gpu_mapping_id
                        : undefined,
                    }}
                    onChange={({ start_at, end_at }) => {
                      setForm((prev) => ({ ...prev, start_at: start_at ?? "", end_at: end_at ?? "" }));
                      setErrors((prev) => ({ ...prev, start_at: "", end_at: "" }));
                    }}
                    onHintChange={setAvailabilityHint}
                  />
                  {(errors.start_at || errors.end_at) && (
                    <p className={styles.fieldError}>{errors.start_at || errors.end_at}</p>
                  )}
                </>
              )}
            </div>

            {/* ── 申請原因 ── */}
            <div className={styles.formSection}>
              <h2 className={styles.sectionTitle}>申請原因<span className={styles.required}> *</span></h2>
              <FieldGroup error={errors.reason}>
                <textarea
                  className={styles.textarea}
                  placeholder="請描述您的申請用途..."
                  value={form.reason}
                  onChange={(e) => set("reason", e.target.value)}
                />
                <div className={styles.charCount}>{form.reason.length} 字</div>
              </FieldGroup>
            </div>

          </form>

          <div className={styles.formActions}>
            <button type="button" className={styles.btnSecondary} onClick={handleBack}>
              取消
            </button>
            <button
              type="submit"
              form="request-form"
              className={styles.btnPrimary}
              disabled={submitting}
            >
              {submitting
                ? <><MIcon name="hourglass_empty" size={16} />送出中…</>
                : <><MIcon name="send" size={16} />送出申請</>
              }
            </button>
          </div>
          </div>
        </div>

        {/* Mobile AI 側欄 */}
        {aiOpen && (
          <AiSidePanel
            className={styles.aiPanelMobile}
            recommendationContext={recommendationContext}
            onImportPlan={applyAiPrefill}
          />
        )}

        {/* Desktop 右側面板（摘要 + AI）*/}
        <div className={styles.rightPanel}>
          <div className={styles.rightPanelTabs}>
            {[
              { key: "summary", label: "摘要",   icon: "receipt_long" },
              { key: "ai",      label: "AI 助手", icon: "smart_toy"    },
            ].map((t) => (
              <button
                key={t.key}
                type="button"
                className={`${styles.rightPanelTab} ${rightTab === t.key ? styles.rightPanelTabActive : ""}`}
                onClick={() => setRightTab(t.key)}
              >
                <MIcon name={t.icon} size={14} />
                {t.label}
              </button>
            ))}
          </div>

          <div className={`${styles.summaryBody} ${rightTab !== "summary" ? styles.rightPanelPaneHidden : ""}`}>
              {/* Type / mode chips */}
              <div className={styles.summaryChips}>
                <span className={`${styles.summaryChip} ${resourceType === "lxc" ? styles.summaryChipLxc : styles.summaryChipVm}`}>
                  <MIcon name={resourceType === "lxc" ? "dashboard" : "computer"} size={12} />
                  {resourceType === "lxc" ? "LXC 容器" : "QEMU 虛擬機"}
                </span>
                {typeMode === "auto" && (
                  <span className={`${styles.summaryChip} ${styles.summaryChipAuto}`}>
                    <MIcon name="auto_fix_high" size={12} />
                    自動判斷
                  </span>
                )}
                {isPrivileged && (
                  <span className={`${styles.summaryChip} ${mode === "scheduled" ? styles.summaryChipScheduled : styles.summaryChipImmediate}`}>
                    <MIcon name={mode === "scheduled" ? "calendar_month" : "bolt"} size={12} />
                    {mode === "scheduled" ? "預約" : "立即"}
                  </span>
                )}
              </div>

              <div className={styles.summaryDivider} />

              <div className={styles.summaryRow}>
                <span className={styles.summaryLabel}>名稱</span>
                <span className={`${styles.summaryValue} ${!form.hostname ? styles.summaryValueMuted : ""}`}>
                  {form.hostname || "未填寫"}
                </span>
              </div>

              {resourceType === "lxc" && (
                selectedTpl ? (
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>範本</span>
                    <span className={styles.summaryValue}>{selectedTpl.name}</span>
                  </div>
                ) : (
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>映像檔</span>
                    <span className={`${styles.summaryValue} ${!form.ostemplate ? styles.summaryValueMuted : ""}`}>
                      {form.ostemplate ? formatOstemplate(form.ostemplate) : "未選擇"}
                    </span>
                  </div>
                )
              )}

              {resourceType === "vm" && (
                <>
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>作業系統</span>
                    <span className={`${styles.summaryValue} ${!form.template_id ? styles.summaryValueMuted : ""}`}>
                      {form.template_id
                        ? (vmTemplates.find((t) => String(t.vmid) === String(form.template_id))?.name ?? form.template_id)
                        : "未選擇"}
                    </span>
                  </div>
                  {form.username && (
                    <div className={styles.summaryRow}>
                      <span className={styles.summaryLabel}>使用者</span>
                      <span className={styles.summaryValue}>{form.username}</span>
                    </div>
                  )}
                </>
              )}

              <div className={styles.summaryDivider} />

              <div className={styles.summaryRow}>
                <span className={styles.summaryLabel}>CPU</span>
                <span className={styles.summaryValue}>{form.cores} 核心</span>
              </div>
              <div className={styles.summaryRow}>
                <span className={styles.summaryLabel}>記憶體</span>
                <span className={styles.summaryValue}>{(form.memory / 1024).toFixed(1)} GB</span>
              </div>
              <div className={styles.summaryRow}>
                <span className={styles.summaryLabel}>硬碟</span>
                <span className={styles.summaryValue}>
                  {resourceType === "lxc" ? form.rootfs_size : form.disk_size} GB
                </span>
              </div>
              {resourceType === "vm" && form.gpu_mapping_id && (
                <div className={styles.summaryRow}>
                  <span className={styles.summaryLabel}>GPU</span>
                  <span className={styles.summaryValue}>
                    {gpuOptions.find((g) => g.mapping_id === form.gpu_mapping_id)?.description || form.gpu_mapping_id}
                  </span>
                </div>
              )}

              <div className={styles.summaryDivider} />

              {mode === "immediate" ? (
                <div className={styles.summaryRow}>
                  <span className={styles.summaryLabel}>時段</span>
                  <span className={styles.summaryValue}>
                    {form.immediate_no_end
                      ? "立即 / 無限期"
                      : form.end_at ? `至 ${formatDT(form.end_at)}` : "立即開始"}
                  </span>
                </div>
              ) : form.start_at && form.end_at ? (
                <>
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>開始</span>
                    <span className={styles.summaryTimeValue}>{formatDT(form.start_at)}</span>
                  </div>
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>結束</span>
                    <span className={styles.summaryTimeValue}>{formatDT(form.end_at)}</span>
                  </div>
                </>
              ) : (
                <div className={styles.summaryRow}>
                  <span className={styles.summaryLabel}>時段</span>
                  <span className={`${styles.summaryValue} ${styles.summaryValueMuted}`}>未選擇</span>
                </div>
              )}
          </div>

          <AiSidePanel
            className={`${styles.aiPanelFill} ${rightTab !== "ai" ? styles.rightPanelPaneHidden : ""}`}
            recommendationContext={recommendationContext}
            onImportPlan={applyAiPrefill}
          />
        </div>
      </div>

      {/* 浮動 AI Tab（僅手機）*/}
      <button
        type="button"
        className={`${styles.aiFloatingTab} ${styles.aiFloatingTabMobileOnly} ${aiOpen ? styles.aiFloatingTabOpen : ""}`}
        onClick={() => setAiOpen((v) => !v)}
      >
        <MIcon name="smart_toy" size={16} />
        <span>{aiOpen ? "關閉 AI" : "AI 助手"}</span>
        <MIcon name={aiOpen ? "keyboard_arrow_down" : "keyboard_arrow_up"} size={16} />
      </button>

    </div>
  );
}
