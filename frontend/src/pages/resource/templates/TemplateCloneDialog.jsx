import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./TemplatesPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import { TemplatesService } from "../../../services/templates";
import { GpuService } from "../../../services/gpu";
import { useToast } from "../../../hooks/useToast";

const CORE_MIN = 1;
const MEMORY_MIN = 512;
/** 一次最多克隆幾台；與 input 的 max 及後端上限一致 */
const CLONE_COUNT_MIN = 1;
const CLONE_COUNT_MAX = 50;

/** 把輸入框的字串夾回 1–50；空白或非數字一律當成 1 */
export function clampCloneCount(value) {
  return Math.min(CLONE_COUNT_MAX, Math.max(CLONE_COUNT_MIN, Number(value) || CLONE_COUNT_MIN));
}

const formatVram = (mb) =>
  mb >= 1024 ? `${Math.round(mb / 1024)}G` : `${mb}M`;

const gpuLabel = (gpu, t) => {
  const capacity = gpu.capacity_count || gpu.device_count;
  const parts = [];
  if (gpu.per_instance_vram_mb > 0) parts.push(t("TemplateCloneDialog.perUnit", { vram: formatVram(gpu.per_instance_vram_mb) }));
  else if (gpu.vram) parts.push(gpu.vram);
  const vram = parts.length ? `（${parts.join("，")}）` : "";
  return `${gpu.description || gpu.mapping_id}${vram} ${t("TemplateCloneDialog.availableCount", { available: gpu.available_count, capacity })}${gpu.available_count <= 0 ? t("TemplateCloneDialog.fullSuffix") : ""}`;
};

/** 從範本克隆開通（teacher/admin 可批量，student 固定單台） */
export default function TemplateCloneDialog({ template, canBatch, closing = false, onClose, onCloned }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const [hostname, setHostname] = useState("");
  const [count, setCount] = useState("1");
  const [cores, setCores] = useState(template?.default_cores || 2);
  const [memory, setMemory] = useState(template?.default_memory || 2048);
  const [password, setPassword] = useState("");
  const [start, setStart] = useState(true);
  const [busy, setBusy] = useState(false);

  const needsGpu = Boolean(template?.requires_gpu) && template?.resource_type === "qemu";
  const [gpuOptions, setGpuOptions] = useState([]);
  const [gpuLoading, setGpuLoading] = useState(needsGpu);
  const [gpuMappingId, setGpuMappingId] = useState("");
  const [gpuProfile, setGpuProfile] = useState("");

  const allowPassword = template?.allow_password_change !== false;
  const coresMax = Math.max(8, template?.default_cores || 0);
  const memoryMax = Math.max(32768, template?.default_memory || 0);
  const coreTicks = [...new Set([1, 2, 4, 6, 8, coresMax])].sort((a, b) => a - b);
  const memoryTicks = [
    ...new Set([1024, 8192, 16384, 24576, 32768, memoryMax]),
  ].sort((a, b) => a - b);

  useEffect(() => {
    if (!needsGpu) return undefined;
    let cancelled = false;
    /* GPU 不可跨 PVE 連線：只列出與範本同叢集的 GPU */
    GpuService.listOptions(template?.node ? { node: template.node } : undefined)
      .then((res) => !cancelled && setGpuOptions(res ?? []))
      .catch(() => !cancelled && toast.error(t("Error.generic", { ns: "common" })))
      .finally(() => !cancelled && setGpuLoading(false));
    return () => {
      cancelled = true;
    };
  }, [needsGpu, toast, template?.node]);

  const selectedGpu = gpuOptions.find((g) => g.mapping_id === gpuMappingId);
  const gpuProfiles = selectedGpu?.profiles ?? [];
  const smallestCreatableProfile = gpuProfiles
    .filter((p) => p.creatable && p.vram_mb > 0)
    .reduce((min, p) => (min && min.vram_mb <= p.vram_mb ? min : p), null);

  /* 下拉預設顯示最小可建規格，但 state 是空字串，送出時會變成 null，
     結果跟畫面上看到的不一樣。把預設值收斂成同一個來源。 */
  const effectiveGpuProfile = gpuProfile || smallestCreatableProfile?.mdev_type || "";

  /* 選項載入（或換 GPU）後把預設規格寫回 state，讓畫面與送出值一致 */
  useEffect(() => {
    if (!gpuProfile && smallestCreatableProfile?.mdev_type) {
      setGpuProfile(smallestCreatableProfile.mdev_type);
    }
  }, [gpuProfile, smallestCreatableProfile?.mdev_type]);

  /* 數量欄位可以手打或貼上，超出 1–50 時夾回範圍並在欄位下方說明 */
  const clampedCount = clampCloneCount(count);
  const countOutOfRange = count !== "" && String(clampedCount) !== String(count).trim();

  const handleSubmit = async () => {
    if (allowPassword && password && password.length < 8) {
      toast.error(t("TemplateCloneDialog.passwordTooShort"));
      return;
    }
    if (needsGpu && !gpuMappingId) {
      toast.error(t("TemplateCloneDialog.gpuRequired"));
      return;
    }
    setBusy(true);
    try {
      const res = await TemplatesService.clone(template.id, {
        hostname: hostname.trim() || null,
        count: canBatch ? clampedCount : 1,
        cores: Number(cores),
        memory: Number(memory),
        login_password: allowPassword && password ? password : null,
        gpu_mapping_id: needsGpu ? gpuMappingId : null,
        gpu_mdev_profile: needsGpu && effectiveGpuProfile ? effectiveGpuProfile : null,
        start,
      });
      toast.success(
        (res?.tasks?.length ?? 0) > 1
          ? t("TemplateCloneDialog.cloneQueuedMultiple", { count: res.tasks.length })
          : t("TemplateCloneDialog.cloneQueuedSingle"),
      );
      onCloned?.();
      onClose();
    } catch (e) {
      toast.error(e?.message ?? t("TemplateCloneDialog.cloneFailed"));
    } finally {
      setBusy(false);
    }
  };

  /* 外框（遮罩、標題列、Esc、焦點、捲動鎖）交給共用 Modal；欄位多，內容區自己捲、按鈕列固定在底部 */
  return (
    <Modal
      closing={closing}
      onClose={onClose}
      closeButton
      size="md"
      icon={<MIcon name="content_copy" size={20} />}
      title={t("TemplateCloneDialog.title", { name: template.name })}
      description={t("TemplateCloneDialog.description")}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            {t("TemplateCloneDialog.cancel")}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={busy || (needsGpu && !gpuMappingId)}
            onClick={handleSubmit}
          >
            <MIcon name="content_copy" size={14} />
            {busy ? t("TemplateCloneDialog.submitting") : t("TemplateCloneDialog.submit")}
          </button>
        </>
      }
    >
      <div className={styles.cloneGrid}>
        <div className={styles.field}>
          <label htmlFor="clone-hostname">{t("TemplateCloneDialog.hostnameLabel")}</label>
          <input
            id="clone-hostname"
            type="text"
            maxLength={63}
            placeholder={t("TemplateCloneDialog.hostnamePlaceholder")}
            value={hostname}
            onChange={(e) => setHostname(e.target.value)}
          />
        </div>
        {canBatch && (
          <div className={styles.field}>
            <label htmlFor="clone-count">{t("TemplateCloneDialog.countLabel")}</label>
            <input
              id="clone-count"
              type="number"
              min={CLONE_COUNT_MIN}
              max={CLONE_COUNT_MAX}
              value={count}
              onChange={(e) => setCount(e.target.value)}
              onBlur={() => setCount(String(clampCloneCount(count)))}
            />
            {countOutOfRange && (
              <span className={styles.fieldWarn}>
                {t("TemplateCloneDialog.countClamped", {
                  min: CLONE_COUNT_MIN,
                  max: CLONE_COUNT_MAX,
                  count: clampedCount,
                })}
              </span>
            )}
          </div>
        )}
      </div>

      <div className={styles.field}>
        <div className={styles.sliderLabelRow}>
          <label htmlFor="clone-cores">{t("TemplateCloneDialog.coresLabel")}</label>
          <span className={styles.sliderValue}>{t("TemplateCloneDialog.coresValue", { cores })}</span>
        </div>
        <input
          id="clone-cores"
          type="range"
          min={CORE_MIN}
          max={coresMax}
          step={1}
          className={styles.slider}
          value={cores}
          onChange={(e) => setCores(Number(e.target.value))}
        />
        <div className={styles.sliderTicks}>
          {coreTicks.map((v) => (
            <span key={v} style={{ left: `${((v - CORE_MIN) / (coresMax - CORE_MIN)) * 100}%` }}>
              {v}
            </span>
          ))}
        </div>
      </div>

      <div className={styles.field}>
        <div className={styles.sliderLabelRow}>
          <label htmlFor="clone-memory">{t("TemplateCloneDialog.memoryLabel")}</label>
          <span className={styles.sliderValue}>{(memory / 1024).toFixed(1)} GB</span>
        </div>
        <input
          id="clone-memory"
          type="range"
          min={MEMORY_MIN}
          max={memoryMax}
          step={512}
          className={styles.slider}
          value={memory}
          onChange={(e) => setMemory(Number(e.target.value))}
        />
        <div className={styles.sliderTicks}>
          {memoryTicks.map((v) => (
            <span
              key={v}
              style={{ left: `${((v - MEMORY_MIN) / (memoryMax - MEMORY_MIN)) * 100}%` }}
            >
              {v >= 1024 ? `${Math.round(v / 1024)}GB` : `${v}MB`}
            </span>
          ))}
        </div>
      </div>

      <div className={styles.field}>
        <label>{t("TemplateCloneDialog.diskLabel")}</label>
        <div className={styles.diskFixed}>
          <MIcon name="lock" size={15} />
          {template.default_disk
            ? t("TemplateCloneDialog.diskFixedWithSize", { size: template.default_disk })
            : t("TemplateCloneDialog.diskFixedDefault")}
        </div>
      </div>

      {allowPassword ? (
        <div className={styles.field}>
          <label htmlFor="clone-password">{t("TemplateCloneDialog.passwordLabel")}</label>
          <input
            id="clone-password"
            type="password"
            maxLength={64}
            placeholder={t("TemplateCloneDialog.passwordPlaceholder")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
      ) : (
        <div className={styles.policyNote}>
          <MIcon name="lock" size={15} />
          {t("TemplateCloneDialog.passwordLockedNote")}
        </div>
      )}

      {needsGpu && (
        <>
          <div className={styles.field}>
            <label htmlFor="clone-gpu">{t("TemplateCloneDialog.gpuLabel")}</label>
            <select
              id="clone-gpu"
              value={gpuMappingId}
              disabled={gpuLoading}
              onChange={(e) => {
                setGpuMappingId(e.target.value);
                setGpuProfile("");
              }}
            >
              <option value="">
                {gpuLoading ? t("TemplateCloneDialog.gpuLoadingOption") : t("TemplateCloneDialog.gpuSelectOption")}
              </option>
              {gpuOptions.map((gpu) => (
                <option
                  key={gpu.mapping_id}
                  value={gpu.mapping_id}
                  disabled={gpu.available_count <= 0}
                >
                  {gpuLabel(gpu, t)}
                </option>
              ))}
            </select>
            {!gpuLoading && gpuOptions.length === 0 && (
              <span className={styles.fieldWarn}>
                {t("TemplateCloneDialog.gpuNoneWarning")}
              </span>
            )}
          </div>
          {gpuProfiles.length > 0 && (
            <div className={styles.field}>
              <label htmlFor="clone-gpu-profile">{t("TemplateCloneDialog.gpuProfileLabel")}</label>
              <select
                id="clone-gpu-profile"
                value={effectiveGpuProfile}
                onChange={(e) => setGpuProfile(e.target.value)}
              >
                {!smallestCreatableProfile && (
                  <option value="" disabled>{t("TemplateCloneDialog.gpuProfileNoneOption")}</option>
                )}
                {gpuProfiles.map((p) => (
                  <option key={p.mdev_type} value={p.mdev_type} disabled={!p.creatable}>
                    {`${p.name || p.mdev_type} — ${formatVram(p.vram_mb)}`}
                    {p.creatable ? "" : t("TemplateCloneDialog.gpuProfileInsufficientSuffix")}
                  </option>
                ))}
              </select>
            </div>
          )}
        </>
      )}

      <label className={styles.checkLine}>
        <input
          type="checkbox"
          checked={start}
          onChange={(e) => setStart(e.target.checked)}
        />
        {t("TemplateCloneDialog.autoStartLabel")}
      </label>
    </Modal>
  );
}
