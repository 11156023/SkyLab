import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./RequestFormPage.module.scss";
import i18n from "../../../i18n";
import { LayoutContext } from "../../../layout/layoutContext";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { VmRequestsService } from "../../../services/vmRequests";
import { VmRequestAvailabilityService } from "../../../services/vmRequestAvailability";
import { GpuService } from "../../../services/gpu";
import { TemplatesService } from "../../../services/templates";
import { apiGet } from "../../../services/api";
import AvailabilityPanel from "../../../components/AvailabilityPanel/AvailabilityPanel";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { focusInvalidField } from "../../../utils/focusField";
import { formatShortDateTime } from "../../../utils/formatDate";

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
/* 密碼欄附顯示/隱藏切換（同登入頁的眼睛按鈕） */
function PasswordInput({ value, onChange, placeholder }) {
  const { t } = useTranslation("personal");
  const [show, setShow] = useState(false);
  return (
    <div className={styles.passwordWrap}>
      <input
        className={styles.input}
        type={show ? "text" : "password"}
        placeholder={placeholder}
        value={value}
        onChange={onChange}
      />
      <button
        type="button"
        className={styles.eyeBtn}
        onClick={() => setShow((v) => !v)}
        tabIndex={-1}
        aria-label={show ? t("RequestFormPage.hidePassword") : t("RequestFormPage.showPassword")}
      >
        <MIcon name={show ? "visibility_off" : "visibility"} size={18} />
      </button>
    </div>
  );
}

function FieldGroup({ label, hint, required, error, children, labelRight, name }) {
  return (
    <div className={`${styles.formGroup} ${error ? styles.formGroupInvalid : ""}`} data-field={name}>
      <label className={styles.label}>
        <span>
          {label}
          {required && <span className={styles.required}> *</span>}
        </span>
        {labelRight && <span className={styles.labelValue}>{labelRight}</span>}
      </label>
      {/* 提示放在控制項前面：擺在欄位下方會被使用者直接忽略 */}
      {hint  && <p className={styles.fieldHint}>{hint}</p>}
      {children}
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
const formatDT = (iso) => formatShortDateTime(iso);
const OS_DISPLAY_NAMES = {
  ubuntu: "Ubuntu",
  debian: "Debian",
  alpine: "Alpine",
  centos: "CentOS",
  rockylinux: "Rocky Linux",
  almalinux: "AlmaLinux",
  fedora: "Fedora",
  archlinux: "Arch Linux",
  opensuse: "openSUSE",
  gentoo: "Gentoo",
  devuan: "Devuan",
  nixos: "NixOS",
};
/* 範本名稱結尾加上 -GPU 代表此作業系統需要 GPU */
const GPU_MARKER_RE = /-gpu$/i;
const osNameNeedsGpu = (name) => GPU_MARKER_RE.test(String(name ?? "").trim());
const stripGpuMarker = (name) => String(name ?? "").trim().replace(GPU_MARKER_RE, "");
const withGpuTag = (label, needsGpu) => (needsGpu ? `${label}${i18n.t("RequestFormPage.gpuTagSuffix", { ns: "personal" })}` : label);
const parseLxcImage = (v) => {
  const file = v.split("/").pop() ?? v;
  const base = file.replace(/\.tar\.(zst|gz|xz|bz2)$/, "");
  // 檔名慣例：<distro>-<version>-<variant>_<build>_<arch>，如 ubuntu-22.04-standard_22.04-1_amd64
  // -GPU 可加在副檔名前或版本段前：..._amd64-GPU.tar.zst / ubuntu-22.04-standard-GPU_...
  const pkgRaw = base.split("_")[0];
  const needsGpu = osNameNeedsGpu(base) || osNameNeedsGpu(pkgRaw);
  const pkg = stripGpuMarker(pkgRaw).replace(/-(standard|default|base|minimal)$/, "");
  const [distro, ...rest] = pkg.split("-");
  const displayName = OS_DISPLAY_NAMES[distro.toLowerCase()];
  const label = displayName
    ? (rest.length ? `${displayName} ${rest.join(" ")}` : displayName)
    : stripGpuMarker(base);
  return { label, needsGpu };
};
const formatOstemplate = (v) => parseLxcImage(v).label;
const toDateTimeLocalValue = (value) => {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};
const fromDateTimeLocalValue = (value) => value ? new Date(value).toISOString() : "";
const toDateInputValue = (value) => toDateTimeLocalValue(value).slice(0, 10);
const fromDateInputValue = (value, endOfDay = false) => {
  if (!value) return "";
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(
    year,
    month - 1,
    day,
    endOfDay ? 23 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 59 : 0,
    0,
  );
  return date.toISOString();
};
const GPU_OPTIONS_DEBOUNCE_MS = 300;
const ADVISE_DEBOUNCE_MS = 500;
const formatVramMb = (mb) => (mb >= 1024 ? `${Number.isInteger(mb / 1024) ? mb / 1024 : (mb / 1024).toFixed(1)} GB` : `${mb} MB`);
const gpuLabel = (gpu) => {
  const t = (key, opts) => i18n.t(key, { ns: "personal", ...opts });
  /* SR-IOV vGPU 以 framebuffer 可切數為上限（capacity_count），非 VF 插槽數 */
  const capacity = gpu.capacity_count || gpu.device_count;
  const parts = [];
  if (gpu.per_instance_vram_mb > 0) parts.push(t("RequestFormPage.gpuVramPerUnit", { vram: formatVramMb(gpu.per_instance_vram_mb) }));
  if (gpu.total_vram_mb > 0) parts.push(t("RequestFormPage.gpuVramTotal", { vram: formatVramMb(gpu.total_vram_mb) }));
  else if (gpu.vram) parts.push(gpu.vram);
  const vram = parts.length ? ` (${parts.join(", ")})` : "";
  return `${gpu.description || gpu.mapping_id}${vram} ${t("RequestFormPage.gpuAvailability", { available: gpu.available_count, capacity })}${gpu.available_count <= 0 ? t("RequestFormPage.gpuFullSuffix") : ""}`;
};


function buildAiScheduleOptions(availability) {
  const days = (availability?.days || [])
    .filter((day) => (day.slots || []).some(
      (slot) => slot.status === "available" || slot.status === "limited",
    ))
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const runs = [];
  for (const day of days) {
    const previousRun = runs[runs.length - 1];
    const previousDay = previousRun?.[previousRun.length - 1];
    const expectedDate = previousDay
      ? new Date(`${previousDay.date}T00:00:00`)
      : null;
    expectedDate?.setDate(expectedDate.getDate() + 1);
    if (previousDay && toDateInputValue(expectedDate) === day.date) {
      previousRun.push(day);
    } else {
      runs.push([day]);
    }
  }

  const options = [];
  for (const run of runs) {
    for (const dayCount of [1, 3, 7, 14, 30]) {
      if (run.length < dayCount) continue;
      const selected = run.slice(0, dayCount);
      const selectedSlots = selected.flatMap((day) => day.slots || []);
      options.push({
        start_at: fromDateInputValue(selected[0].date),
        end_at: fromDateInputValue(selected[selected.length - 1].date, true),
        status: selectedSlots.some((slot) => slot.status === "limited") ? "limited" : "available",
        summary: i18n.t("RequestFormPage.availableDaysSummary", { ns: "personal", count: dayCount }),
        recommended_nodes: selectedSlots.find((slot) => slot.recommended_nodes?.length)?.recommended_nodes || [],
      });
    }
  }
  return options.slice(0, 12);
}

/* 依畫面順序排列，送出時定位到第一個有問題的欄位 */
const FIELD_ORDER = [
  "hostname", "ostemplate", "template_id", "username", "password",
  "gpu_mapping_id", "start_at", "end_at", "reason",
];

function focusFirstError(formEl, errs) {
  const key = FIELD_ORDER.find((field) => errs[field]);
  if (!key || !formEl) return;
  const group = formEl.querySelector(`[data-field~="${key}"]`);
  focusInvalidField(group?.querySelector("input, select, textarea"));
}

/* ── Validation messages（對齊舊版 zh-TW locales）── */
const MSG = {
  hostnameRequired: "RequestFormPage.msgHostnameRequired",
  hostnameInvalid:  "RequestFormPage.msgHostnameInvalid",
  passwordRequired: "RequestFormPage.msgPasswordRequired",
  passwordMinLen:   "RequestFormPage.msgPasswordMinLen",
  reasonRequired:   "RequestFormPage.msgReasonRequired",
  reasonMinLen:     "RequestFormPage.msgReasonMinLen",
  osRequired:       "RequestFormPage.msgOsRequired",
  gpuRequired:      "RequestFormPage.msgGpuRequired",
  usernameRequired: "RequestFormPage.msgUsernameRequired",
  startRequired:    "RequestFormPage.msgStartRequired",
  endRequired:      "RequestFormPage.msgEndRequired",
  endBeforeStart:   "RequestFormPage.msgEndBeforeStart",
  endInPast:        "RequestFormPage.msgEndInPast",
  scheduleOutOfRange: "RequestFormPage.msgScheduleOutOfRange",
};

export default function RequestFormPage({ onBack, className, initialPrefill = null }) {
  const { t } = useTranslation("personal");
  const { user }  = useAuth();
  const toast     = useToast();
  const isPrivileged = user?.is_superuser || user?.role === "admin" || user?.role === "teacher";
  const { setCompactFooter, registerRequestForm, registerSurface, reportRequestSubmission } =
    useContext(LayoutContext);
  useEffect(() => { setCompactFooter(true); return () => setCompactFooter(false); }, [setCompactFooter]);

  const [closing, setClosing]   = useState(false);

  /* 範本系統 2.0：LXC 可選範本，選了走克隆路徑（免映像檔） */
  const [sysTemplates, setSysTemplates]   = useState([]);
  /* 學生看到的是「全部可見」的應用範本目錄，不是完整母範本清單 */
  const [catalog, setCatalog]             = useState([]);
  const [sysTplLoading, setSysTplLoading] = useState(false);
  const [selectedTplId, setSelectedTplId] = useState("");

  /* Form state */
  const [resourceType, setResourceType] = useState("lxc");
  const [advice, setAdvice]                   = useState(null);
  const [adviceLoading, setAdviceLoading]     = useState(false);
  const [advisorDisabled, setAdvisorDisabled] = useState(false);
  /* 自動模式的統一作業系統選擇；選了 OS 即決定型別（advise 退為提示） */
  const [autoOsChoice, setAutoOsChoice]       = useState("");
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
    gpu_mdev_profile: "",
    start_at:         "",
    end_at:           "",
    immediate_no_end: true,
    reason:           "",
  });
  const [errors, setErrors]           = useState({});
  const [submitting, setSubmitting]   = useState(false);
  const [availabilityData, setAvailabilityData] = useState(null);
  const scheduleBounds = useMemo(() => {
    const minimum = new Date();
    minimum.setSeconds(0, 0);
    const maximum = new Date(minimum);
    maximum.setDate(maximum.getDate() + 90);
    return {
      min: toDateTimeLocalValue(minimum),
      max: toDateTimeLocalValue(maximum),
    };
  }, []);

  /* API data */
  const [lxcTemplates, setLxcTemplates] = useState([]);
  const [lxcLoading, setLxcLoading]     = useState(false);
  const [vmTemplates, setVmTemplates]   = useState([]);
  const [vmLoading, setVmLoading]       = useState(false);
  const [gpuOptions, setGpuOptions]     = useState([]);
  /* AI 建議的 GPU：等可用清單就緒才敢寫進表單（見下方 effect） */
  const [pendingGpu, setPendingGpu]     = useState(null);
  const [gpuLoading, setGpuLoading]     = useState(false);
  const [gpuOptionsKey, setGpuOptionsKey] = useState("");

  /* ── API fetches ── */
  useEffect(() => {
    if (lxcTemplates.length > 0) return;
    setLxcLoading(true);
    apiGet("/api/v1/lxc/templates")
      .then(setLxcTemplates)
      .catch(() => {})
      .finally(() => setLxcLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (vmTemplates.length > 0) return;
    setVmLoading(true);
    apiGet("/api/v1/vm/templates")
      .then(setVmTemplates)
      .catch(() => {})
      .finally(() => setVmLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // 單機母範本只供教師／管理員組裝環境或建立管理用資源。
    // 學生的一般申請仍可選擇平台提供的 VM/LXC 基礎映像，但不讀取
    // VMTemplate 目錄，也不會取得直接克隆入口。
    if (!isPrivileged) return;
    setSysTplLoading(true);
    TemplatesService.list()
      .then((res) => setSysTemplates(res?.data ?? []))
      .catch(() => {})
      .finally(() => setSysTplLoading(false));
  }, [isPrivileged]);

  useEffect(() => {
    // 學生／一般使用者：只拿教師設為「全部可見」且已就緒的範本。
    // 規格由範本決定，送出後仍走一般審核流程。
    if (isPrivileged) return;
    setSysTplLoading(true);
    TemplatesService.catalog()
      .then((res) => {
        const rows = res?.data ?? [];
        setCatalog(rows);
        setSysTemplates(
          rows
            .filter((item) => item.resource_type === "lxc")
            .map((item) => ({
              id: item.id,
              pve_vmid: item.pve_vmid,
              name: item.name,
              description: item.description,
              resource_type: "lxc",
              status: "ready",
              version: item.version,
              default_cores: item.cores,
              default_memory: item.memory_mb,
              default_disk: item.disk_gb,
            })),
        );
      })
      .catch(() => {})
      .finally(() => setSysTplLoading(false));
  }, [isPrivileged]);

  /* 申請一律是單台；範本只是「來源」，VM 或 LXC 由範本本身決定 */
  const catalogChoices = useMemo(
    () => catalog.map((item) => ({
      ...item,
      choice: item.resource_type === "lxc"
        ? `tpl:${item.id}`
        : `vm:${item.pve_vmid}`,
    })),
    [catalog],
  );

  /* VM 選項 = 應用範本目錄 + 平台基礎映像（後端已依角色過濾清單） */
  const catalogVmChoices = useMemo(
    () => catalog
      .filter((item) => item.resource_type !== "lxc")
      .map((item) => ({
        vmid: item.pve_vmid,
        name: item.name,
        node: item.node,
        is_windows: item.is_windows,
        cores: item.cores,
        memory_mb: item.memory_mb,
        disk_gb: item.disk_gb,
        catalog: true,
      })),
    [catalog],
  );
  const vmChoices = useMemo(
    () => [...catalogVmChoices, ...vmTemplates],
    [catalogVmChoices, vmTemplates],
  );

  const lxcSysTemplates = useMemo(
    () => sysTemplates.filter(
      (t) => t.resource_type === "lxc" && t.status === "ready" && t.pve_exists !== false,
    ),
    [sysTemplates],
  );
  const selectedTpl = lxcSysTemplates.find((t) => t.id === selectedTplId) || null;

  /* Windows 範本帳號由 cloudbase-init 固定（PVE 的 ciuser 對 Windows 無效），不開放自訂 */
  const selectedVmTemplate =
    vmChoices.find((t) => String(t.vmid) === String(form.template_id)) || null;
  const isWindowsVm = resourceType === "vm" && Boolean(selectedVmTemplate?.is_windows);

  /* 目前選到的應用範本：帶入建議規格並顯示說明；規格仍可調整，
     但磁碟不得小於範本本身（克隆只能放大，後端會再守一次） */
  const selectedCatalogItem = useMemo(() => catalogChoices.find((item) => (
    item.resource_type === "lxc"
      ? item.id === selectedTplId
      : String(item.pve_vmid) === String(form.template_id)
  )) ?? null, [catalogChoices, selectedTplId, form.template_id]);

  /* 範本政策 requires_gpu：學生看目錄項目、老師看完整範本清單（以 PVE VMID 對應） */
  const selectedTemplateRequiresGpu = useMemo(() => {
    if (resourceType !== "vm" || !form.template_id) return false;
    if (selectedCatalogItem?.requires_gpu) return true;
    const tpl = sysTemplates.find((t) => String(t.pve_vmid) === String(form.template_id));
    return Boolean(tpl?.requires_gpu);
  }, [resourceType, form.template_id, selectedCatalogItem, sysTemplates]);

  /* 是否已完成作業系統選擇（型別確定後，帳密欄位才顯示） */
  const osChosen = resourceType === "vm"
    ? Boolean(form.template_id)
    : Boolean(selectedTplId || form.ostemplate);

  /* 所選作業系統是否標記需要 GPU（範本名稱 / 映像檔名結尾 -GPU） */
  const selectedOsNeedsGpu = useMemo(() => {
    if (resourceType === "vm") {
      const tpl = vmChoices.find((t) => String(t.vmid) === String(form.template_id));
      return tpl ? osNameNeedsGpu(tpl.name) : false;
    }
    if (selectedTplId) {
      const tpl = lxcSysTemplates.find((t) => t.id === selectedTplId);
      return tpl ? osNameNeedsGpu(tpl.name) : false;
    }
    return form.ostemplate ? parseLxcImage(form.ostemplate).needsGpu : false;
  }, [resourceType, vmChoices, form.template_id, selectedTplId, lxcSysTemplates, form.ostemplate]);
  /* GPU 區塊只在需要時出現：作業系統名稱標 -GPU，或範本政策要求 GPU */
  const gpuNeeded = selectedOsNeedsGpu || selectedTemplateRequiresGpu;
  const canLoadGpu = resourceType === "vm" && gpuNeeded;
  /* 範本所在節點：GPU 不可跨 PVE 連線（叢集），只顯示與範本同叢集的 GPU */
  const selectedTemplateNode = selectedVmTemplate?.node || "";
  const gpuWindowReady = Boolean(mode === "scheduled" && form.start_at && form.end_at);
  const selectedGpuProfiles = useMemo(() => {
    if (!form.gpu_mapping_id) return [];
    const gpu = gpuOptions.find((g) => g.mapping_id === form.gpu_mapping_id);
    return gpu?.profiles ?? [];
  }, [gpuOptions, form.gpu_mapping_id]);

  const smallestCreatableProfile = useMemo(() => {
    const creatable = selectedGpuProfiles.filter((p) => p.creatable && p.vram_mb > 0);
    if (creatable.length === 0) return null;
    return creatable.reduce((min, p) => (p.vram_mb < min.vram_mb ? p : min));
  }, [selectedGpuProfiles]);

  const gpuOptionsRequestKey = canLoadGpu
    ? `${mode}|${gpuWindowReady ? form.start_at : ""}|${gpuWindowReady ? form.end_at : ""}|${selectedTemplateNode}`
    : "";

  useEffect(() => {
    if ((resourceType !== "vm" || !gpuNeeded) && form.gpu_mapping_id) {
      setForm((prev) => ({ ...prev, gpu_mapping_id: "", gpu_mdev_profile: "" }));
    }
  }, [resourceType, gpuNeeded, form.gpu_mapping_id]);

  useEffect(() => {
    if (!canLoadGpu) {
      setGpuOptions([]);
      setGpuOptionsKey("");
      setGpuLoading(false);
      return;
    }
    let cancelled = false;
    const requestKey = gpuOptionsRequestKey;
    const params = {
      ...(gpuWindowReady ? { startAt: form.start_at, endAt: form.end_at } : {}),
      ...(selectedTemplateNode ? { node: selectedTemplateNode } : {}),
    };
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
  }, [canLoadGpu, form.start_at, form.end_at, gpuOptionsRequestKey, gpuWindowReady, mode, selectedTemplateNode]);

  /* ── Helpers ── */
  useEffect(() => {
    if (resourceType !== "vm" || !form.gpu_mapping_id) return;
    if (!canLoadGpu) return;
    if (!gpuWindowReady && mode === "scheduled") return;
    if (gpuLoading) return;
    if (gpuOptionsKey !== gpuOptionsRequestKey) return;
    const selected = gpuOptions.find((gpu) => gpu.mapping_id === form.gpu_mapping_id);
    if (!selected || selected.available_count <= 0) {
      setForm((prev) => ({ ...prev, gpu_mapping_id: "", gpu_mdev_profile: "" }));
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

  /* ── 自動判斷：依申請原因/規格即時呼叫 advise，自動切換建議型別 ── */
  useEffect(() => {
    if (advisorDisabled) return undefined;
    const reasonText = form.reason.trim();
    if (!reasonText && !form.gpu_mapping_id) {
      setAdvice(null);
      return undefined;
    }
    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      setAdviceLoading(true);
      VmRequestsService.advise({
        reason: reasonText || null,
        cores: Number(form.cores) || null,
        memory: Number(form.memory) || null,
        gpu_mapping_id: form.gpu_mapping_id || null,
      })
        .then((res) => {
          if (cancelled) return;
          setAdvice(res);
          /* 已選作業系統時型別由 OS 決定，advise 僅作提示 */
          if (!autoOsChoice) setResourceType(res.resource_type);
        })
        .catch((err) => {
          if (cancelled) return;
          /* 管理員停用 advisor 時後端回 400：隱藏自動選項並退回手動 */
          if (err?.status === 400) {
            setAdvisorDisabled(true);
            setAdvice(null);
          }
        })
        .finally(() => {
          if (!cancelled) setAdviceLoading(false);
        });
    }, ADVISE_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [advisorDisabled, autoOsChoice, form.reason, form.cores, form.memory, form.gpu_mapping_id]);

  function set(key, val) {
    setForm((prev) => ({ ...prev, [key]: val }));
    if (errors[key]) setErrors((prev) => ({ ...prev, [key]: "" }));
  }


  const recommendationContext = useMemo(() => ({
    resource_type: resourceType,
    mode,
    hostname: form.hostname || null,
    reason: form.reason || null,
    lxc_os_image: resourceType === "lxc" ? form.ostemplate || null : null,
    vm_template_id: resourceType === "vm" && form.template_id ? Number(form.template_id) : null,
    cores: Number(form.cores) || null,
    memory_mb: Number(form.memory) || null,
    disk_gb: Number(resourceType === "vm" ? form.disk_size : form.rootfs_size) || null,
    storage: "local-lvm",
    start_at: form.start_at || null,
    end_at: form.end_at || null,
    immediate_no_end: Boolean(form.immediate_no_end),
    selected_gpu_mapping_id: form.gpu_mapping_id || null,
    gpu_options: gpuOptions,
    schedule_options: buildAiScheduleOptions(availabilityData),
    lxc_os_options: lxcTemplates.map((template) => ({
      value: template.volid,
      label: formatOstemplate(template.volid),
    })),
    vm_os_options: vmChoices.map((template) => ({
      template_id: Number(template.vmid),
      label: template.name || String(template.vmid),
      node: template.node || "",
    })),
    /* 應用範本的候選由後端提供，前端不送，避免候選清單可被偽造 */
    resource_options_from_client: true,
  }), [resourceType, mode, form, gpuOptions, availabilityData, lxcTemplates, vmChoices]);

  function applyAiPrefill(prefill) {
    if (!prefill) return;
    const nextResourceType = prefill.resource_type === "vm" ? "vm" : "lxc";
    setResourceType(nextResourceType);
    setPendingGpu(
      nextResourceType === "vm" && prefill.gpu_mapping_id
        ? String(prefill.gpu_mapping_id)
        : null,
    );
    /* AI 選了容器應用範本時給的是 PVE VMID，要換回目錄項目的 id */
    const lxcCatalogPick = nextResourceType === "lxc" && prefill.lxc_template_id
      ? catalogChoices.find((item) => (
        item.resource_type === "lxc"
          && String(item.pve_vmid) === String(prefill.lxc_template_id)
      ))
      : null;
    if (nextResourceType === "vm") {
      setAutoOsChoice(prefill.vm_template_id ? `vm:${prefill.vm_template_id}` : "");
    } else if (lxcCatalogPick) {
      setAutoOsChoice(`tpl:${lxcCatalogPick.id}`);
    } else {
      setAutoOsChoice(prefill.lxc_os_image ? `img:${prefill.lxc_os_image}` : "");
    }
    if (isPrivileged && (prefill.mode === "scheduled" || prefill.mode === "immediate")) {
      setMode(prefill.mode);
    } else if (!isPrivileged) {
      setMode("scheduled");
    }

    if (nextResourceType !== "lxc") setSelectedTplId("");
    else setSelectedTplId(lxcCatalogPick ? lxcCatalogPick.id : "");

    setForm((prev) => {
      const disk = Number(prefill.disk_gb || 0);
      return {
        ...prev,
        hostname: prefill.hostname ? normalizeHostname(prefill.hostname) : prev.hostname,
        ostemplate: nextResourceType === "lxc"
          ? (lxcCatalogPick ? "" : (prefill.lxc_os_image || prev.ostemplate))
          : prev.ostemplate,
        template_id: nextResourceType === "vm" && prefill.vm_template_id
          ? String(prefill.vm_template_id)
          : prev.template_id,
        cores: prefill.cores ? Number(prefill.cores) : prev.cores,
        memory: prefill.memory_mb ? Number(prefill.memory_mb) : prev.memory,
        rootfs_size: nextResourceType === "lxc" && disk
          ? Math.max(8, disk)
          : prev.rootfs_size,
        disk_size: nextResourceType === "vm" && disk
          ? Math.max(20, disk)
          : prev.disk_size,
        /* GPU 由 pendingGpu 那支 effect 在可用清單就緒後才寫入；這裡先留空，
           免得寫進去又被清單檢查清掉，使用者以為填好了其實沒有。 */
        gpu_mapping_id: "",
        start_at: prefill.start_at
          ? fromDateInputValue(toDateInputValue(prefill.start_at))
          : prev.start_at,
        end_at: prefill.end_at
          ? fromDateInputValue(toDateInputValue(prefill.end_at), true)
          : prev.end_at,
        immediate_no_end: typeof prefill.immediate_no_end === "boolean"
          ? prefill.immediate_no_end
          : prev.immediate_no_end,
        reason: prefill.reason || prev.reason,
      };
    });
    setErrors({});
    toast.success(
      nextResourceType === "vm"
        ? t("RequestFormPage.aiPrefillAppliedVm")
        : t("RequestFormPage.aiPrefillAppliedLxc"),
    );
  }

  /* GPU 不能跟其他欄位一起寫進去：可用清單要等「作業系統確定是 GPU 版」加上
     「時段選好」之後才非同步載入，先寫會被清單載入後的檢查清掉。所以先記著，
     等清單就緒再套；真的套不上就講出來，不要安靜地把它丟掉。 */
  useEffect(() => {
    if (!pendingGpu) return;
    if (resourceType !== "vm") { setPendingGpu(null); return; }
    if (!selectedOsNeedsGpu) {
      setPendingGpu(null);
      toast.error(t("RequestFormPage.aiGpuOsMismatch"));
      return;
    }
    if (mode === "scheduled" && !gpuWindowReady) return;   // 等使用者把時段選完
    if (gpuLoading) return;
    if (gpuOptionsKey !== gpuOptionsRequestKey) return;    // 等這個時段的清單回來

    const match = gpuOptions.find((gpu) => gpu.mapping_id === pendingGpu);
    if (match && match.available_count > 0) {
      setForm((prev) => ({ ...prev, gpu_mapping_id: pendingGpu }));
    } else {
      toast.error(t("RequestFormPage.aiGpuUnavailable"));
    }
    setPendingGpu(null);
  }, [
    pendingGpu, resourceType, selectedOsNeedsGpu, mode, gpuWindowReady,
    gpuLoading, gpuOptions, gpuOptionsKey, gpuOptionsRequestKey, toast,
  ]);

  /* AI 助手在別的頁面談完需求後，會帶著推薦配置導到這裡。等候選清單載入完再套用，
     否則 LXC 應用範本會對不到目錄項目而退回成映像檔。 */
  const [aiPrefilled, setAiPrefilled] = useState(false);
  const appliedPrefillRef = useRef(null);
  useEffect(() => {
    if (!initialPrefill || sysTplLoading) return;
    // 比對物件本身而不是布林旗標：助手再規劃一次帶新的配置過來時要能重新套用
    if (appliedPrefillRef.current === initialPrefill) return;
    appliedPrefillRef.current = initialPrefill;
    applyAiPrefill(initialPrefill);
    setAiPrefilled(true);
  }, [initialPrefill, sysTplLoading]); // eslint-disable-line react-hooks/exhaustive-deps

  /* 把自己交給 AI 助手：它就地把欄位填好，並且拿得到這張表單當下的真實候選
     （這個時段的 GPU、可用時段、作業系統清單）。用 ref 轉一手，
     註冊一次就好，不必每次 render 重註冊，也不會抓到過期的閉包。 */
  const applyPrefillRef = useRef(applyAiPrefill);
  const contextRef = useRef(recommendationContext);
  applyPrefillRef.current = applyAiPrefill;
  contextRef.current = recommendationContext;
  useEffect(() => {
    if (!registerRequestForm) return undefined;
    registerRequestForm({
      applyPrefill: (prefill) => applyPrefillRef.current(prefill),
      getContext: () => contextRef.current,
    });
    return () => registerRequestForm(null);
  }, [registerRequestForm]);

  /* 畫面說明用的動態狀態：欄位當下的值與驗證錯誤。欄位的「意義」不在這裡——
     label、說明與限制一律由後端的 surface 定義提供，這裡只回答「填了什麼」。
     contextVersion 每次狀態變動就換一個值，助手用它丟棄過期的回答。 */
  const surfaceState = () => ({
    "request.hostname": { value: form.hostname, error: errors.hostname ?? null },
    "request.os": {
      value: resourceType === "vm" ? form.template_id : (selectedTplId || form.ostemplate),
      error: errors.template_id ?? errors.ostemplate ?? null,
    },
    "request.username": { value: form.username, error: errors.username ?? null },
    "request.password": { value: form.password, error: errors.password ?? null },
    "request.cores": { value: String(form.cores ?? "") },
    "request.memory": { value: String(form.memory ?? "") },
    "request.disk": {
      value: String(
        (resourceType === "lxc" ? form.rootfs_size : form.disk_size) ?? "",
      ),
    },
    "request.gpu": {
      value: form.gpu_mapping_id,
      error: errors.gpu_mapping_id ?? null,
    },
    "request.vgpu": { value: form.gpu_mdev_profile },
    "request.mode": { value: mode },
    "request.start_at": { value: form.start_at, error: errors.start_at ?? null },
    "request.end_at": { value: form.end_at, error: errors.end_at ?? null },
    "request.reason": { value: form.reason, error: errors.reason ?? null },
    /* 送出鈕沒有被驗證停用——這張表單是按下去才驗證的。只有送出中才是真的停用，
       據實回報，否則助手會解釋一個不存在的停用原因。 */
    "request.submit": { disabled: submitting },
  });
  const surfaceStateRef = useRef(surfaceState);
  surfaceStateRef.current = surfaceState;
  /* render 期間不做副作用：版本號在 render 之後才遞增，語意一樣是「畫面又變了」。 */
  const contextVersion = useRef(0);
  useEffect(() => {
    contextVersion.current += 1;
  });

  useEffect(() => {
    if (!registerSurface) return undefined;
    registerSurface("request-form", {
      getState: () => surfaceStateRef.current(),
      getVersion: () => contextVersion.current,
    });
    return () => registerSurface("request-form", null);
  }, [registerSurface]);

  function handleBack() {
    setClosing(true);
    setTimeout(onBack, 180);
  }

  /* ── Validation ── */
  function validate() {
    const errs = {};
    const hostnameRegex = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;

    if (!form.hostname.trim())          errs.hostname = t(MSG.hostnameRequired);
    else if (!hostnameRegex.test(form.hostname)) errs.hostname = t(MSG.hostnameInvalid);

    if (!form.password)                 errs.password = t(MSG.passwordRequired);
    else if (form.password.length < 8)  errs.password = t(MSG.passwordMinLen);

    if (!form.reason.trim())            errs.reason = t(MSG.reasonRequired);
    else if (form.reason.trim().length < 10) errs.reason = t(MSG.reasonMinLen);

    if (resourceType === "lxc" && !selectedTplId && !form.ostemplate)
      errs.ostemplate = t(MSG.osRequired);
    if (resourceType === "vm") {
      if (!form.template_id)            errs.template_id = t(MSG.osRequired);
      if (!isWindowsVm && !form.username.trim()) errs.username = t(MSG.usernameRequired);
      if (gpuNeeded && !form.gpu_mapping_id) errs.gpu_mapping_id = t(MSG.gpuRequired);
    }

    if (mode === "scheduled") {
      if (!form.start_at) errs.start_at = t(MSG.startRequired);
      if (!form.end_at)   errs.end_at   = t(MSG.endRequired);
      if (form.start_at && form.end_at && new Date(form.start_at) >= new Date(form.end_at))
        errs.end_at = t(MSG.endBeforeStart);
      const maximum = new Date(scheduleBounds.max);
      if (form.start_at && new Date(form.start_at) > maximum)
        errs.start_at = t(MSG.scheduleOutOfRange);
      if (form.end_at && new Date(form.end_at) > maximum)
        errs.end_at = t(MSG.scheduleOutOfRange);
    }
    if (mode === "immediate" && !form.immediate_no_end && form.end_at) {
      if (new Date(form.end_at) <= new Date()) errs.end_at = t(MSG.endInPast);
    }
    return errs;
  }

  /* ── Submit ── */
  async function handleSubmit(e) {
    e.preventDefault();
    const formEl = e.currentTarget;
    const errs = validate();
    if (Object.keys(errs).length > 0) { setErrors(errs); focusFirstError(formEl, errs); return; }

    setSubmitting(true);
    try {
      /* GPU re-availability check before submitting (mirrors old frontend logic) */
      const selectedGpuId = form.gpu_mapping_id?.trim();
      if (resourceType === "vm" && selectedGpuId) {
        const params = {
          ...(mode === "scheduled"
            ? { startAt: form.start_at || undefined, endAt: form.end_at || undefined }
            : {}),
          ...(selectedTemplateNode ? { node: selectedTemplateNode } : {}),
        };
        const latestOptions = await GpuService.listOptions(params);
        const gpuStillAvailable = latestOptions.some(
          (g) => g.mapping_id === selectedGpuId && g.available_count > 0,
        );
        if (!gpuStillAvailable) {
          toast.error(t("RequestFormPage.gpuNoLongerAvailable"));
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
            ? {
                rootfs_size: form.rootfs_size,
                ostemplate: !selectedTplId && form.ostemplate ? form.ostemplate : undefined,
              }
            : {
                disk_size: form.disk_size,
                template_id: form.template_id ? Number(form.template_id) : undefined,
              }),
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
            t("RequestFormPage.windowInsufficientResources"),
          );
          setSubmitting(false);
          return;
        }
      }

      const body = {
        resource_type: resourceType,
        requested_mode: advisorDisabled ? "manual" : "auto",
        mode,
        hostname:  form.hostname,
        password:  form.password,
        cores:     form.cores,
        memory:    form.memory,
        reason:    form.reason.trim(),
        storage:   "local-lvm",
        ...(resourceType === "lxc"
          ? {
              rootfs_size: form.rootfs_size,
              ...(selectedTpl
                ? {
                    /* 範本系統克隆路徑：帶 PVE VMID，免映像檔 */
                    template_id: selectedTpl.pve_vmid,
                    os_info: stripGpuMarker(selectedTpl.name),
                  }
                : {
                    ostemplate: form.ostemplate,
                    os_info: form.ostemplate ? formatOstemplate(form.ostemplate) : null,
                  }),
            }
          : {
              template_id: Number(form.template_id),
              ...(isWindowsVm ? {} : { username: form.username }),
              disk_size: form.disk_size,
              os_info:
                stripGpuMarker(
                  vmChoices.find((t) => String(t.vmid) === String(form.template_id))?.name ?? "",
                ) || null,
            }),
        ...(selectedGpuId ? { gpu_mapping_id: selectedGpuId } : {}),
        ...(selectedGpuId && form.gpu_mdev_profile
          ? { gpu_mdev_profile: form.gpu_mdev_profile }
          : {}),
        ...(mode === "scheduled"
          ? { start_at: form.start_at, end_at: form.end_at }
          : (!form.immediate_no_end && form.end_at ? { end_at: form.end_at } : {})),
      };

      const created = await VmRequestsService.create(body);
      reportRequestSubmission?.({ id: created.id });
      toast.success(t("RequestFormPage.submitSuccess"));
      handleBack();
    } catch (err) {
      toast.error(err?.message ?? t("RequestFormPage.submitFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  /* ── VM 範本選擇：以範本自身規格帶入預設值（磁碟為克隆下限，不可低於範本） ── */
  function handleSelectVmTemplate(v) {
    set("template_id", v);
    if (errors.template_id) setErrors((prev) => ({ ...prev, template_id: "" }));
    const tpl = vmChoices.find((t) => String(t.vmid) === String(v));
    if (!tpl) return;
    if (tpl.cores)     set("cores", Math.min(8, Math.max(1, tpl.cores)));
    if (tpl.memory_mb) set("memory", Math.min(32768, Math.max(512, tpl.memory_mb)));
    // 不以 500 截斷：範本大於上限時，克隆機天生就是範本大小
    if (tpl.disk_gb)   set("disk_size", tpl.disk_gb);
  }

  /* ── 範本選擇（範本系統 2.0）── */
  function handleSelectSysTemplate(id) {
    setSelectedTplId(id);
    if (errors.ostemplate) setErrors((prev) => ({ ...prev, ostemplate: "" }));
    const tpl = lxcSysTemplates.find((t) => t.id === id);
    if (!tpl) return;
    if (tpl.default_cores)  set("cores", Math.min(8, Math.max(1, tpl.default_cores)));
    if (tpl.default_memory) set("memory", Math.min(32768, Math.max(512, tpl.default_memory)));
    if (tpl.default_disk)   set("rootfs_size", Math.max(8, tpl.default_disk));
    if (!form.hostname.trim()) set("hostname", normalizeHostname(tpl.name));
  }

  /* ── 自動模式：統一作業系統選擇 → 直接決定型別與對應欄位 ── */
  function handleAutoOsSelect(value) {
    setAutoOsChoice(value);
    if (errors.template_id || errors.ostemplate) {
      setErrors((prev) => ({ ...prev, template_id: "", ostemplate: "" }));
    }
    if (!value) return;
    const [kind, ...rest] = value.split(":");
    const id = rest.join(":"); // volid 本身含冒號（storage:vztmpl/...）
    if (kind === "vm") {
      setResourceType("vm");
      setSelectedTplId("");
      set("ostemplate", "");
      handleSelectVmTemplate(id);
    } else if (kind === "tpl") {
      setResourceType("lxc");
      set("template_id", "");
      set("ostemplate", "");
      handleSelectSysTemplate(id);
    } else if (kind === "img") {
      setResourceType("lxc");
      setSelectedTplId("");
      set("template_id", "");
      set("ostemplate", id);
    }
  }

  const animCls = closing ? styles.animSlideOutRight : (className ?? "");

  return (
    <div className={`${styles.formPage} ${animCls}`}>
      {/* ── 頁首 ── */}
      <PageHeader title={t("RequestFormPage.title")} subtitle={t("RequestFormPage.subtitle")}>
        <button type="button" className={styles.backBtn} onClick={handleBack}>
          <MIcon name="arrow_back" size={18} />
          {t("RequestFormPage.back")}
        </button>
      </PageHeader>

      {/* ── 主體：表單 + AI 側欄 ── */}
      <div className={styles.formPageBody}>
        <div className={styles.formScroll}>
          <div className={styles.formInner}>
          {/* AI 代填一定要說出來：使用者要知道哪些值不是自己填的 */}
          {aiPrefilled && (
            <p className={styles.adviceBox}>
              <MIcon name="auto_awesome" size={15} />
              {" "}{t("RequestFormPage.aiPrefillNotice")}
            </p>
          )}
          <form id="request-form" onSubmit={handleSubmit} className={styles.form}>
            {/* ── 申請模式（管理員／老師） ── */}
            {isPrivileged && (
              <div className={styles.formSection}>
                <h2 className={styles.sectionTitle}>{t("RequestFormPage.requestModeTitle")}</h2>
                <div className={styles.typeToggle}>
                  {[
                    { key: "scheduled", labelKey: "RequestFormPage.modeScheduled", icon: "calendar_month" },
                    { key: "immediate", labelKey: "RequestFormPage.modeImmediate", icon: "bolt" },
                  ].map((m) => (
                    <button
                      key={m.key}
                      type="button"
                      className={`${styles.typeBtn} ${mode === m.key ? styles.typeBtnActive : ""}`}
                      onClick={() => setMode(m.key)}
                    >
                      <MIcon name={m.icon} size={16} />
                      {t(m.labelKey)}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* ── 資源設定（型別由作業系統選擇 + 規則引擎自動決定，學生免選 QEMU/LXC） ── */}
            <div className={styles.formSection}>
              <h2 className={styles.sectionTitle}>{t("RequestFormPage.resourceSettingsTitle")}</h2>

              <p className={styles.adviceBox}>
                {t("RequestFormPage.resourceSettingsHint")}
              </p>
              {!advisorDisabled && (adviceLoading || advice) && (
                <p className={styles.adviceBox}>
                  {adviceLoading
                    ? t("RequestFormPage.adviceLoading")
                    : (() => {
                        const typeLabel = (rt) => (rt === "vm" ? t("RequestFormPage.typeVm") : t("RequestFormPage.typeLxcContainer"));
                        const text = t("RequestFormPage.adviceSuggested", { type: typeLabel(advice.resource_type), reasons: advice.reasons.join("；") });
                        return osChosen && advice.resource_type !== resourceType
                          ? t("RequestFormPage.adviceOverriddenByOs", { text, currentType: typeLabel(resourceType) })
                          : text;
                      })()}
                </p>
              )}

              <FieldGroup label={t("RequestFormPage.resourceNameLabel")} required error={errors.hostname} name="hostname">
                <input
                  className={styles.input}
                  placeholder="project-alpha-web"
                  value={form.hostname}
                  onChange={(e) => set("hostname", e.target.value)}
                  onBlur={(e) => set("hostname", normalizeHostname(e.target.value))}
                />
              </FieldGroup>

              <FieldGroup label={t("RequestFormPage.osLabel")} required name="ostemplate template_id"
                error={errors.template_id || errors.ostemplate}
                hint={osChosen
                  ? t("RequestFormPage.osHintChosen", { type: resourceType === "vm" ? t("RequestFormPage.typeVm") : t("RequestFormPage.typeLxcContainer") })
                  : t("RequestFormPage.osHintNotChosen")}>
                <SelectField
                  value={autoOsChoice}
                  onChange={handleAutoOsSelect}
                  disabled={vmLoading || lxcLoading || sysTplLoading}
                  placeholder={(vmLoading || lxcLoading || sysTplLoading) ? t("RequestFormPage.loading") : t("RequestFormPage.selectOs")}
                >
                  {catalogChoices.length > 0 && (
                    <optgroup label={t("RequestFormPage.optgroupCatalog")}>
                      {catalogChoices.map((tpl) => (
                        <option key={`cat-${tpl.id}`} value={tpl.choice}>
                          {withGpuTag(stripGpuMarker(tpl.name), osNameNeedsGpu(tpl.name) || Boolean(tpl.requires_gpu))}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {vmTemplates.length > 0 && (
                    <optgroup label={isPrivileged ? t("RequestFormPage.optgroupVmTemplate") : t("RequestFormPage.optgroupVmImage")}>
                      {vmTemplates.map((tpl) => (
                        <option key={`vm-${tpl.vmid}`} value={`vm:${tpl.vmid}`}>
                          {withGpuTag(stripGpuMarker(tpl.name), osNameNeedsGpu(tpl.name))}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {isPrivileged && lxcSysTemplates.length > 0 && (
                    <optgroup label={t("RequestFormPage.optgroupContainerTemplate")}>
                      {lxcSysTemplates.map((tpl) => (
                        <option key={`tpl-${tpl.id}`} value={`tpl:${tpl.id}`}>
                          {withGpuTag(t("RequestFormPage.templateVersionLabel", { name: stripGpuMarker(tpl.name), version: tpl.version }), osNameNeedsGpu(tpl.name))}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {lxcTemplates.length > 0 && (
                    <optgroup label={t("RequestFormPage.optgroupContainerImage")}>
                      {lxcTemplates.map((tpl) => {
                        const img = parseLxcImage(tpl.volid);
                        return (
                          <option key={`img-${tpl.volid}`} value={`img:${tpl.volid}`}>
                            {withGpuTag(img.label, img.needsGpu)}
                          </option>
                        );
                      })}
                    </optgroup>
                  )}
                </SelectField>
                {selectedCatalogItem && (
                  <p className={styles.fieldHint}>
                    {selectedCatalogItem.description
                      || t("RequestFormPage.catalogDefaultDesc")}
                    {" "}{t("RequestFormPage.templateRecommendedSpec", {
                      cores: selectedCatalogItem.cores ?? "—",
                      memory: selectedCatalogItem.memory_mb
                        ? `${(selectedCatalogItem.memory_mb / 1024).toFixed(1)} GB RAM`
                        : t("RequestFormPage.ramDash"),
                    })}
                    {selectedCatalogItem.disk_gb ? ` · ${selectedCatalogItem.disk_gb} GB` : ""}
                  </p>
                )}
              </FieldGroup>

              {/* 帳密欄位：選定作業系統（型別確定）後才顯示 */}
              {osChosen && resourceType === "vm" && (
                <div className={styles.formGrid}>
                  {!isWindowsVm && (
                    <FieldGroup label={t("RequestFormPage.usernameLabel")} required error={errors.username} name="username">
                      <input
                        className={styles.input}
                        placeholder="admin"
                        value={form.username}
                        onChange={(e) => set("username", e.target.value)}
                      />
                    </FieldGroup>
                  )}
                  <FieldGroup
                    label={t("RequestFormPage.passwordLabel")}
                    required
                    error={errors.password}
                    name="password"
                    hint={isWindowsVm
                      ? t("RequestFormPage.windowsPasswordHint")
                      : undefined}
                  >
                    <PasswordInput
                      placeholder={t("RequestFormPage.passwordPlaceholder")}
                      value={form.password}
                      onChange={(e) => set("password", e.target.value)}
                    />
                  </FieldGroup>
                </div>
              )}
              {osChosen && resourceType === "lxc" && (
                <FieldGroup label={t("RequestFormPage.passwordLabel")} required error={errors.password} name="password"
                  hint={selectedTpl
                    ? t("RequestFormPage.clonedPasswordHint")
                    : t("RequestFormPage.lxcPasswordHint")}>
                  <PasswordInput
                    placeholder={t("RequestFormPage.passwordPlaceholder")}
                    value={form.password}
                    onChange={(e) => set("password", e.target.value)}
                  />
                </FieldGroup>
              )}
            </div>

            {/* ── 硬體資源配置 ── */}
            <div className={styles.formSection}>
              <h2 className={styles.sectionTitle}>{t("RequestFormPage.hardwareConfigTitle")}</h2>

              {selectedCatalogItem && (
                <p className={styles.fieldHint}>
                  {t("RequestFormPage.hardwareConfigHint")}
                </p>
              )}

              <FieldGroup label={t("RequestFormPage.cpuCoresLabel")} labelRight={t("RequestFormPage.coresValue", { count: form.cores })}>
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

              <FieldGroup label={t("RequestFormPage.memoryLabel")} labelRight={`${(form.memory / 1024).toFixed(1)} GB`}>
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
                // 選了範本時磁碟下限為範本自身大小（克隆後只能放大，不能縮小）；
                // 範本若大於 500 上限，上限跟著放寬——克隆機天生就是範本大小
                const diskMin = isLxc
                  ? (selectedTpl?.default_disk || 8)
                  : (selectedVmTemplate?.disk_gb || 20);
                const diskMax = Math.max(500, diskMin);
                return (
                  <FieldGroup label={t("RequestFormPage.diskSpaceLabel")} labelRight={
                    <div className={styles.diskInput}>
                      <input
                        type="number" min={diskMin} max={diskMax}
                        className={`${styles.input} ${styles.inputNumber}`}
                        value={form[diskKey]}
                        onChange={(e) => set(diskKey, Math.min(diskMax, Math.max(diskMin, Number(e.target.value) || diskMin)))}
                      />
                      <span className={styles.diskUnit}>GB</span>
                    </div>
                  }>
                    <input
                      type="range" min={diskMin} max={diskMax} step={1}
                      className={styles.slider}
                      value={form[diskKey]}
                      onChange={(e) => set(diskKey, Number(e.target.value))}
                    />
                  </FieldGroup>
                );
              })()}
            </div>

            {/* ── GPU（作業系統標記 -GPU、或範本政策要求 GPU 時才顯示）── */}
            {resourceType === "vm" && gpuNeeded && (
              <div className={styles.formSection}>
                <h2 className={styles.sectionTitle}>{t("RequestFormPage.gpuAccelTitle")}</h2>
                {selectedTemplateRequiresGpu && (
                  <p className={styles.fieldHint}>{t("RequestFormPage.templateRequiresGpuHint")}</p>
                )}

                {!canLoadGpu && mode === "scheduled" && (
                  <p className={styles.fieldHint}>{t("RequestFormPage.selectScheduleFirstHint")}</p>
                )}
                {!gpuLoading && gpuOptions.length === 0 && (
                  <p className={styles.fieldHint}>{t("RequestFormPage.noGpuAvailableHint")}</p>
                )}

                <FieldGroup
                  label={t("RequestFormPage.selectGpuLabel")}
                  required
                  error={errors.gpu_mapping_id}
                  name="gpu_mapping_id"
                  hint={t("RequestFormPage.gpuAvailabilityHint")}
                >
                  <SelectField
                    value={form.gpu_mapping_id || "__none__"}
                    onChange={(v) => {
                      setForm((prev) => ({
                        ...prev,
                        gpu_mapping_id: v === "__none__" ? "" : v,
                        gpu_mdev_profile: "",
                      }));
                      if (errors.gpu_mapping_id) setErrors((prev) => ({ ...prev, gpu_mapping_id: "" }));
                    }}
                    disabled={gpuLoading || gpuOptions.length === 0}
                    placeholder={!canLoadGpu ? t("RequestFormPage.selectScheduleFirstPlaceholder") : undefined}
                  >
                    <option value="__none__">{t("RequestFormPage.selectGpuPlaceholder")}</option>
                    {gpuOptions.map((gpu) => (
                      <option key={gpu.mapping_id} value={gpu.mapping_id} disabled={gpu.available_count <= 0}>
                        {gpuLabel(gpu)}
                      </option>
                    ))}
                  </SelectField>
                </FieldGroup>

                {selectedGpuProfiles.length > 0 && (
                  <FieldGroup
                    label={t("RequestFormPage.vgpuSpecLabel")}
                    hint={t("RequestFormPage.vgpuSpecHint")}
                  >
                    <SelectField
                      value={form.gpu_mdev_profile || smallestCreatableProfile?.mdev_type || ""}
                      onChange={(v) => set("gpu_mdev_profile", v)}
                      placeholder={smallestCreatableProfile ? undefined : t("RequestFormPage.noCreatableProfile")}
                    >
                      {selectedGpuProfiles.map((p) => (
                        <option key={p.mdev_type} value={p.mdev_type} disabled={!p.creatable}>
                          {`${p.name || p.mdev_type} — ${formatVramMb(p.vram_mb)}`}
                          {p.creatable ? "" : t("RequestFormPage.insufficientMemorySuffix")}
                        </option>
                      ))}
                    </SelectField>
                  </FieldGroup>
                )}
              </div>
            )}

            {/* ── 租借時段 ── */}
            <div className={styles.formSection}>
              <div className={styles.sectionTitleRow}>
                <h2 className={styles.sectionTitle}>
                  {mode === "immediate" ? t("RequestFormPage.immediateModeSettingsTitle") : t("RequestFormPage.scheduleTitle")}
                </h2>
              </div>

              {mode === "immediate" ? (
                <>
                  <p className={styles.fieldHint}>
                    {t("RequestFormPage.immediateModeHint")}
                  </p>
                  <label className={styles.checkboxLabel}>
                    <input
                      type="checkbox"
                      className={styles.checkbox}
                      checked={form.immediate_no_end}
                      onChange={(e) => set("immediate_no_end", e.target.checked)}
                    />
                    {t("RequestFormPage.noEndDate")}
                  </label>
                  {!form.immediate_no_end && (
                    <FieldGroup label={t("RequestFormPage.endTimeLabel")} error={errors.end_at} name="end_at">
                      <input
                      type="datetime-local"
                      className={styles.input}
                      min={scheduleBounds.min}
                      max={scheduleBounds.max}
                      value={toDateTimeLocalValue(form.end_at)}
                      onChange={(e) => set("end_at", fromDateTimeLocalValue(e.target.value))}
                      />
                    </FieldGroup>
                  )}
                </>
              ) : (
                <>
                  <div className={styles.scheduleInputGrid}>
                    <FieldGroup label={t("RequestFormPage.startDateLabel")} required error={errors.start_at} name="start_at">
                      <input
                        type="datetime-local"
                        className={styles.input}
                        min={scheduleBounds.min}
                        max={scheduleBounds.max}
                        value={toDateTimeLocalValue(form.start_at)}
                        onChange={(e) => set("start_at", fromDateTimeLocalValue(e.target.value))}
                      />
                    </FieldGroup>
                    <FieldGroup label={t("RequestFormPage.endDateLabel")} required error={errors.end_at} name="end_at">
                      <input
                        type="datetime-local"
                        className={styles.input}
                        min={toDateTimeLocalValue(form.start_at) || scheduleBounds.min}
                        max={scheduleBounds.max}
                        value={toDateTimeLocalValue(form.end_at)}
                        onChange={(e) => set("end_at", fromDateTimeLocalValue(e.target.value))}
                      />
                    </FieldGroup>
                  </div>
                  <div className={styles.scheduleDivider}><span>{t("RequestFormPage.orUseAvailabilityCalendar")}</span></div>
                  <AvailabilityPanel
                    startAt={form.start_at}
                    endAt={form.end_at}
                    draft={{
                      resource_type: resourceType,
                      cores:         form.cores,
                      memory:        form.memory,
                      ...(resourceType === "lxc"
                        ? {
                            rootfs_size: form.rootfs_size,
                            /* 模板節點約束：行事曆只推薦拿得到模板的節點（克隆路徑節點由範本釘死，不帶） */
                            ostemplate: !selectedTplId && form.ostemplate ? form.ostemplate : undefined,
                          }
                        : {
                            disk_size: form.disk_size,
                            template_id: form.template_id ? Number(form.template_id) : undefined,
                          }),
                      gpu_required: resourceType === "vm" && form.gpu_mapping_id ? 1 : 0,
                      gpu_mapping_id: resourceType === "vm" && form.gpu_mapping_id
                        ? form.gpu_mapping_id
                        : undefined,
                    }}
                    onChange={({ start_at, end_at }) => {
                      setForm((prev) => ({ ...prev, start_at: start_at ?? "", end_at: end_at ?? "" }));
                      setErrors((prev) => ({ ...prev, start_at: "", end_at: "" }));
                    }}
                    onDataChange={setAvailabilityData}
                  />
                </>
              )}
            </div>

            {/* ── 申請原因 ── */}
            <div className={styles.formSection}>
              <h2 className={styles.sectionTitle}>{t("RequestFormPage.reasonTitle")}<span className={styles.required}> *</span></h2>
              <FieldGroup error={errors.reason} name="reason">
                <textarea
                  className={styles.textarea}
                  placeholder={t("RequestFormPage.reasonPlaceholder")}
                  value={form.reason}
                  onChange={(e) => set("reason", e.target.value)}
                />
                <div className={styles.charCount}>{t("RequestFormPage.charCount", { count: form.reason.length })}</div>
              </FieldGroup>
            </div>

          </form>

          <div className={styles.formActions}>
            <button type="button" className={styles.btnSecondary} onClick={handleBack}>
              {t("RequestFormPage.cancel")}
            </button>
            <button
              type="submit"
              form="request-form"
              className={styles.btnPrimary}
              disabled={submitting}
            >
              {submitting
                ? <><span className={styles.spin}><MIcon name="hourglass_empty" size={16} /></span>{t("RequestFormPage.submitting")}</>
                : <><MIcon name="send" size={16} />{t("RequestFormPage.submitRequest")}</>
              }
            </button>
          </div>
          </div>
        </div>

        {/* Desktop 右側面板（摘要 + AI）*/}
        <div className={styles.rightPanel}>
          <div className={styles.summaryBody}>
              {/* Type / mode chips */}
              <div className={styles.summaryChips}>
                <span className={`${styles.summaryChip} ${resourceType === "lxc" ? styles.summaryChipLxc : styles.summaryChipVm}`}>
                  <MIcon name={resourceType === "lxc" ? "dashboard" : "computer"} size={12} />
                  {resourceType === "lxc" ? t("RequestFormPage.typeLxcContainer") : t("RequestFormPage.typeVm")}
                </span>
                {isPrivileged && (
                  <span className={`${styles.summaryChip} ${mode === "scheduled" ? styles.summaryChipScheduled : styles.summaryChipImmediate}`}>
                    <MIcon name={mode === "scheduled" ? "calendar_month" : "bolt"} size={12} />
                    {mode === "scheduled" ? t("RequestFormPage.modeScheduledShort") : t("RequestFormPage.modeImmediateShort")}
                  </span>
                )}
              </div>

              <div className={styles.summaryDivider} />

              <div className={styles.summaryRow}>
                <span className={styles.summaryLabel}>{t("RequestFormPage.summaryName")}</span>
                <span className={`${styles.summaryValue} ${!form.hostname ? styles.summaryValueMuted : ""}`}>
                  {form.hostname || t("RequestFormPage.notFilled")}
                </span>
              </div>

              {resourceType === "lxc" && (
                selectedTpl ? (
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>{t("RequestFormPage.summaryTemplate")}</span>
                    <span className={styles.summaryValue}>{selectedTpl.name}</span>
                  </div>
                ) : (
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>{t("RequestFormPage.summaryImage")}</span>
                    <span className={`${styles.summaryValue} ${!form.ostemplate ? styles.summaryValueMuted : ""}`}>
                      {form.ostemplate ? formatOstemplate(form.ostemplate) : t("RequestFormPage.notSelected")}
                    </span>
                  </div>
                )
              )}

              {resourceType === "vm" && (
                <>
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>{t("RequestFormPage.summaryOs")}</span>
                    <span className={`${styles.summaryValue} ${!form.template_id ? styles.summaryValueMuted : ""}`}>
                      {form.template_id
                        ? (vmChoices.find((tpl) => String(tpl.vmid) === String(form.template_id))?.name ?? form.template_id)
                        : t("RequestFormPage.notSelected")}
                    </span>
                  </div>
                  {(isWindowsVm || form.username) && (
                    <div className={styles.summaryRow}>
                      <span className={styles.summaryLabel}>{t("RequestFormPage.summaryUsername")}</span>
                      <span className={styles.summaryValue}>
                        {isWindowsVm ? t("RequestFormPage.windowsFixedAdmin") : form.username}
                      </span>
                    </div>
                  )}
                </>
              )}

              <div className={styles.summaryDivider} />

              <div className={styles.summaryRow}>
                <span className={styles.summaryLabel}>CPU</span>
                <span className={styles.summaryValue}>{t("RequestFormPage.coresValue", { count: form.cores })}</span>
              </div>
              <div className={styles.summaryRow}>
                <span className={styles.summaryLabel}>{t("RequestFormPage.summaryMemory")}</span>
                <span className={styles.summaryValue}>{(form.memory / 1024).toFixed(1)} GB</span>
              </div>
              <div className={styles.summaryRow}>
                <span className={styles.summaryLabel}>{t("RequestFormPage.summaryDisk")}</span>
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
                  <span className={styles.summaryLabel}>{t("RequestFormPage.summaryPeriod")}</span>
                  <span className={styles.summaryValue}>
                    {form.immediate_no_end
                      ? t("RequestFormPage.immediateUnlimited")
                      : form.end_at ? t("RequestFormPage.untilDate", { date: formatDT(form.end_at) }) : t("RequestFormPage.startsImmediately")}
                  </span>
                </div>
              ) : form.start_at && form.end_at ? (
                <>
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>{t("RequestFormPage.summaryStart")}</span>
                    <span className={styles.summaryTimeValue}>{formatDT(form.start_at)}</span>
                  </div>
                  <div className={styles.summaryRow}>
                    <span className={styles.summaryLabel}>{t("RequestFormPage.summaryEnd")}</span>
                    <span className={styles.summaryTimeValue}>{formatDT(form.end_at)}</span>
                  </div>
                </>
              ) : (
                <div className={styles.summaryRow}>
                  <span className={styles.summaryLabel}>{t("RequestFormPage.summaryPeriod")}</span>
                  <span className={`${styles.summaryValue} ${styles.summaryValueMuted}`}>{t("RequestFormPage.notSelected")}</span>
                </div>
              )}
          </div>
        </div>
      </div>


    </div>
  );
}
