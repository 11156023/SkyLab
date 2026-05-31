import { useContext, useEffect, useRef, useState } from "react";
import rawData from "virtual:templates";
import styles from "./RequestFormPage.module.scss";
import { LayoutContext } from "../../../layout/DashboardLayout";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { VmRequestsService } from "../../../services/vmRequests";
import { GpuService } from "../../../services/gpu";
import { apiGet } from "../../../services/api";
import AiSidePanel from "./AiSidePanel";
import FastTemplatesPanel from "../../../components/FastTemplatesPanel/FastTemplatesPanel";
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
const QUICK_TEMPLATE_DURATION_HOURS = 3;
const QUICK_TEMPLATES = Object.entries(rawData)
  .filter(([key]) => !["metadata.json", "versions.json", "github-versions.json"].includes(key))
  .map(([, value]) => value)
  .filter(Boolean);
const getQuickTemplate = (slug) => QUICK_TEMPLATES.find((template) => template.slug === slug);
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

export default function RequestFormPage({ onBack, className, quickTemplateSlug }) {
  const { user }  = useAuth();
  const toast     = useToast();
  const isPrivileged = user?.is_superuser || user?.role === "admin" || user?.role === "teacher";
  const { setCompactFooter } = useContext(LayoutContext);
  useEffect(() => { setCompactFooter(true); return () => setCompactFooter(false); }, [setCompactFooter]);

  const [closing, setClosing]   = useState(false);
  const [aiOpen, setAiOpen]     = useState(false);
  const [rightTab, setRightTab] = useState("summary");

  /* Service template (LXC only) */
  const [serviceTemplateName, setServiceTemplateName] = useState("");
  const [serviceTemplateSlug, setServiceTemplateSlug] = useState("");
  const [showTemplatePanel, setShowTemplatePanel]     = useState(false);
  const [panelLeaving, setPanelLeaving]               = useState(false);
  const returnedFromTemplate = useRef(false);

  function openTemplatePanel() {
    returnedFromTemplate.current = false;
    setPanelLeaving(false);
    setShowTemplatePanel(true);
  }
  function closeTemplatePanel() {
    returnedFromTemplate.current = true;
    setPanelLeaving(true);
    setTimeout(() => { setShowTemplatePanel(false); setPanelLeaving(false); }, 180);
  }

  /* Form state */
  const [resourceType, setResourceType] = useState("lxc");
  const [mode, setMode]                 = useState(quickTemplateSlug ? "quick_template" : "scheduled");
  const [form, setForm] = useState({
    hostname:         "",
    ostemplate:       "",
    os_info:          "",
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
  const isQuickTemplate = mode === "quick_template";

  /* API data */
  const [lxcTemplates, setLxcTemplates] = useState([]);
  const [lxcLoading, setLxcLoading]     = useState(false);
  const [vmTemplates, setVmTemplates]   = useState([]);
  const [vmLoading, setVmLoading]       = useState(false);
  const [gpuOptions, setGpuOptions]     = useState([]);
  const [gpuLoading, setGpuLoading]     = useState(false);

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
    if (!quickTemplateSlug) return;
    const template = getQuickTemplate(quickTemplateSlug);
    if (!template) return;
    const res = template.install_methods?.[0]?.resources ?? {};
    setResourceType("lxc");
    setMode("quick_template");
    setServiceTemplateName(template.name || quickTemplateSlug);
    setServiceTemplateSlug(template.slug || quickTemplateSlug);
    setForm((prev) => ({
      ...prev,
      hostname: prev.hostname || normalizeHostname(`${template.slug || quickTemplateSlug}-${Date.now().toString().slice(-4)}`),
      cores: Math.min(Number(res.cpu || prev.cores || 2), 2),
      memory: Math.min(Number(res.ram || prev.memory || 2048), 4096),
      rootfs_size: Math.min(Math.max(Number(res.hdd || prev.rootfs_size || 8), 8), 32),
      os_info: prev.os_info || res.os || "",
      reason: prev.reason || `快速使用 ${template.name || quickTemplateSlug} 模板`,
      start_at: "",
      end_at: "",
      immediate_no_end: false,
    }));
  }, [quickTemplateSlug]);

  useEffect(() => {
    if (!isQuickTemplate || form.ostemplate || lxcTemplates.length === 0) return;
    set("ostemplate", lxcTemplates[0].volid);
  }, [isQuickTemplate, form.ostemplate, lxcTemplates]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (resourceType !== "vm" || vmTemplates.length > 0) return;
    setVmLoading(true);
    apiGet("/api/v1/vm/templates")
      .then(setVmTemplates)
      .catch(() => {})
      .finally(() => setVmLoading(false));
  }, [resourceType]); // eslint-disable-line react-hooks/exhaustive-deps

  const canLoadGpu = resourceType === "vm" &&
    (mode === "immediate" || (form.start_at && form.end_at));

  useEffect(() => {
    if (!canLoadGpu) {
      setGpuOptions([]);
      setForm((prev) => ({ ...prev, gpu_mapping_id: "" }));
      return;
    }
    setGpuLoading(true);
    const params = mode === "immediate"
      ? undefined
      : { startAt: form.start_at, endAt: form.end_at };
    GpuService.listOptions(params)
      .then(setGpuOptions)
      .catch(() => setGpuOptions([]))
      .finally(() => setGpuLoading(false));
  }, [canLoadGpu, form.start_at, form.end_at, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Helpers ── */
  function set(key, val) {
    setForm((prev) => ({ ...prev, [key]: val }));
    if (errors[key]) setErrors((prev) => ({ ...prev, [key]: "" }));
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

    if (resourceType === "lxc" && !form.ostemplate) errs.ostemplate = MSG.templateRequired;
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

      const body = {
        resource_type: resourceType,
        mode,
        hostname:  form.hostname,
        password:  form.password,
        cores:     form.cores,
        memory:    form.memory,
        os_info:   form.os_info || undefined,
        reason:    form.reason.trim(),
        storage:   "local-lvm",
        ...(resourceType === "lxc"
          ? {
              ostemplate: form.ostemplate,
              rootfs_size: form.rootfs_size,
              ...(serviceTemplateSlug ? {
                service_template_slug: serviceTemplateSlug,
                service_template_script_path: `ct/${serviceTemplateSlug}.sh`,
              } : {}),
            }
          : { template_id: Number(form.template_id), username: form.username, disk_size: form.disk_size }),
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

  /* ── Service template selection ── */
  function handleSelectTemplate(template) {
    setServiceTemplateName(template.name || "");
    setServiceTemplateSlug(template.slug || "");
    closeTemplatePanel();
    const res = template.install_methods?.[0]?.resources;
    if (res) {
      if (res.cpu) set("cores", res.cpu);
      if (res.ram) set("memory", res.ram);
      if (res.hdd) set("rootfs_size", Math.max(res.hdd, 8));
    }
    if (template.slug && !form.hostname.trim()) set("hostname", template.slug.slice(0, 63));
  }

  const animCls = closing ? styles.animSlideOutRight : (className ?? "");

  return (
    <div className={`${styles.formPage} ${isQuickTemplate ? styles.quickTemplatePage : ""} ${animCls}`}>
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
          {showTemplatePanel ? (
            <FastTemplatesPanel
              inline
              onClose={closeTemplatePanel}
              onSelect={handleSelectTemplate}
              className={panelLeaving ? styles.animSlideOutRight : styles.animSlideInRight}
            />
          ) : (
          <div className={`${styles.formInner} ${returnedFromTemplate.current ? styles.animSlideInLeft : ""}`}>
          <form id="request-form" onSubmit={handleSubmit} className={styles.form}>
            {isQuickTemplate && (
              <div className={styles.quickTemplateNotice}>
                <MIcon name="bolt" size={18} />
                <div>
                  <strong>快速模板</strong>
                  <span>送出後不需人工審核，系統會建立 {QUICK_TEMPLATE_DURATION_HOURS} 小時的 LXC 練習環境。</span>
                </div>
              </div>
            )}

            {/* ── 申請模式（管理員／老師） ── */}
            {!isQuickTemplate && isPrivileged && (
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
            {!isQuickTemplate && (
            <div className={styles.formSection}>
              <h2 className={styles.sectionTitle}>類型</h2>
              <div className={styles.typeToggle}>
                {[
                  { key: "lxc", label: "LXC 容器",   icon: "dashboard" },
                  { key: "vm",  label: "QEMU 虛擬機", icon: "computer"  },
                ].map((t) => (
                  <button
                    key={t.key}
                    type="button"
                    className={`${styles.typeBtn} ${resourceType === t.key ? styles.typeBtnActive : ""}`}
                    disabled={isQuickTemplate && t.key !== "lxc"}
                    onClick={() => setResourceType(t.key)}
                  >
                    <MIcon name={t.icon} size={16} />
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
            )}

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

                <FieldGroup label="服務模板（選填）">
                  {serviceTemplateName ? (
                    <div className={styles.templateSelected}>
                      <MIcon name="layers" size={16} />
                      <div className={styles.templateSelectedMeta}>
                        <span className={styles.templateSelectedName}>{serviceTemplateName}</span>
                        {serviceTemplateSlug && (
                          <span className={styles.templateSelectedSlug}>{serviceTemplateSlug}</span>
                        )}
                      </div>
                      <button
                        type="button"
                        className={styles.templateClearBtn}
                        disabled={isQuickTemplate}
                        onClick={() => { setServiceTemplateName(""); setServiceTemplateSlug(""); }}
                        title="清除"
                      >
                        <MIcon name="close" size={16} />
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className={styles.templateSelectBtn}
                      onClick={openTemplatePanel}
                    >
                      <MIcon name="layers" size={16} />
                      選擇模板
                    </button>
                  )}
                </FieldGroup>

                {(!isQuickTemplate || !form.ostemplate || errors.ostemplate) && (
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

                {!isQuickTemplate && (
                <FieldGroup label="作業系統資訊（選填）">
                  <input
                    className={styles.input}
                    placeholder="Ubuntu 22.04 LTS"
                    value={form.os_info}
                    onChange={(e) => set("os_info", e.target.value)}
                  />
                </FieldGroup>
                )}

                <FieldGroup label="Root 密碼" required error={errors.password}>
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
            {!isQuickTemplate && resourceType === "vm" && (
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

                <FieldGroup label="作業系統資訊（選填）">
                  <input
                    className={styles.input}
                    placeholder="Ubuntu 22.04 LTS"
                    value={form.os_info}
                    onChange={(e) => set("os_info", e.target.value)}
                  />
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
            {!isQuickTemplate && (
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
            )}

            {/* ── GPU（VM only）── */}
            {!isQuickTemplate && resourceType === "vm" && (
              <div className={styles.formSection}>
                <h2 className={styles.sectionTitle}>GPU 加速（選填）</h2>

                {!canLoadGpu && mode === "scheduled" && (
                  <p className={styles.fieldHint}>請先選擇租借時段，再載入該時段可用的 GPU。</p>
                )}
                {canLoadGpu && !gpuLoading && gpuOptions.length === 0 && (
                  <p className={styles.fieldHint}>此時段目前沒有可用 GPU，可改選其他時段或不使用 GPU。</p>
                )}

                <FieldGroup
                  label="選擇 GPU"
                  hint="GPU 會依所選時段重新計算可用性，送出前仍會再做一次即時檢查"
                >
                  <SelectField
                    value={form.gpu_mapping_id || "__none__"}
                    onChange={(v) => set("gpu_mapping_id", v === "__none__" ? "" : v)}
                    disabled={!canLoadGpu || gpuLoading || gpuOptions.length === 0}
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
            {!isQuickTemplate && (
            <div className={styles.formSection}>
              <div className={styles.sectionTitleRow}>
                <h2 className={styles.sectionTitle}>
                  {isQuickTemplate ? "快速模板使用期限" : mode === "immediate" ? "立即模式設定" : "租借時段"}
                </h2>
                {mode === "scheduled" && availabilityHint && (
                  <span className={styles.sectionHint}>{availabilityHint}</span>
                )}
              </div>

              {isQuickTemplate ? (
                <div className={styles.quickTemplateWindow}>
                  <MIcon name="schedule" size={18} />
                  <div>
                    <span>系統會立即送出並自動核准</span>
                    <strong>{QUICK_TEMPLATE_DURATION_HOURS} 小時後自動結束</strong>
                  </div>
                </div>
              ) : mode === "immediate" ? (
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
                      gpu_required: form.gpu_mapping_id ? 1 : 0,
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
            )}

            {/* ── 申請原因 ── */}
            {!isQuickTemplate && (
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
            )}

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
                : <><MIcon name={isQuickTemplate ? "bolt" : "send"} size={16} />{isQuickTemplate ? "快速使用" : "送出申請"}</>
              }
            </button>
          </div>
          </div>
          )}
        </div>

        {/* Mobile AI 側欄 */}
        {aiOpen && <AiSidePanel className={styles.aiPanelMobile} />}

        {/* Desktop 右側面板（摘要 + AI）*/}
        {!isQuickTemplate && (
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

          {rightTab === "summary" && (
            <div className={styles.summaryBody}>
              {/* Type / mode chips */}
              <div className={styles.summaryChips}>
                <span className={`${styles.summaryChip} ${resourceType === "lxc" ? styles.summaryChipLxc : styles.summaryChipVm}`}>
                  <MIcon name={resourceType === "lxc" ? "dashboard" : "computer"} size={12} />
                  {resourceType === "lxc" ? "LXC 容器" : "QEMU 虛擬機"}
                </span>
                {(isPrivileged || isQuickTemplate) && (
                  <span className={`${styles.summaryChip} ${mode === "scheduled" ? styles.summaryChipScheduled : styles.summaryChipImmediate}`}>
                    <MIcon name={mode === "scheduled" ? "calendar_month" : "bolt"} size={12} />
                    {isQuickTemplate ? "快速模板" : mode === "scheduled" ? "預約" : "立即"}
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
                <>
                  {serviceTemplateName && (
                    <div className={styles.summaryRow}>
                      <span className={styles.summaryLabel}>服務模板</span>
                      <span className={styles.summaryValue}>{serviceTemplateName}</span>
                    </div>
                  )}
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>映像檔</span>
                    <span className={`${styles.summaryValue} ${!form.ostemplate ? styles.summaryValueMuted : ""}`}>
                      {form.ostemplate ? formatOstemplate(form.ostemplate) : "未選擇"}
                    </span>
                  </div>
                  {form.os_info && (
                    <div className={styles.summaryRow}>
                      <span className={styles.summaryLabel}>系統資訊</span>
                      <span className={styles.summaryValue}>{form.os_info}</span>
                    </div>
                  )}
                </>
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
                  {form.os_info && (
                    <div className={styles.summaryRow}>
                      <span className={styles.summaryLabel}>系統資訊</span>
                      <span className={styles.summaryValue}>{form.os_info}</span>
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

              {isQuickTemplate ? (
                <div className={styles.summaryRow}>
                  <span className={styles.summaryLabel}>使用期限</span>
                  <span className={styles.summaryValue}>{QUICK_TEMPLATE_DURATION_HOURS} 小時</span>
                </div>
              ) : mode === "immediate" ? (
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
          )}

          {rightTab === "ai" && <AiSidePanel className={styles.aiPanelFill} />}
        </div>
        )}
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
