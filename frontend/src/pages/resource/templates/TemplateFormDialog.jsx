import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./TemplatesPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useAuth } from "../../../contexts/AuthContext";
import { ResourcesService } from "../../../services/resources";
import { TemplatesService } from "../../../services/templates";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { focusInvalidField } from "../../../utils/focusField";

const CORE_MIN = 1;
const CORE_MAX = 8;
const MEMORY_MIN = 512;
const MEMORY_MAX = 32768;

// 與後端 template_files.py 的限制一致（前端先擋，後端仍會驗證）
const ATTACHMENT_MAX_BYTES = 50 * 1024 * 1024;
const ATTACHMENT_MAX_COUNT = 10;
const ATTACHMENT_EXTS = new Set([
  ".pdf", ".md", ".txt", ".doc", ".docx", ".ppt", ".pptx",
  ".xls", ".xlsx", ".odt", ".odp", ".zip",
  ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4",
]);

const fileExt = (name) => {
  const idx = String(name || "").lastIndexOf(".");
  return idx >= 0 ? String(name).slice(idx).toLowerCase() : "";
};

const formatBytes = (bytes) => {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
};

/**
 * 建立（從 VM 轉換）或編輯範本的 dialog。
 * template 有值 = 編輯模式。
 * 附件：編輯模式即時上傳；建立模式先暫存，create 成功後補上傳。
 */
export default function TemplateFormDialog({ template, onClose, onSaved }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const confirm = useConfirm();
  const { user } = useAuth();
  const isEdit = Boolean(template);
  const isAdmin = user?.role === "admin" || user?.is_superuser === true;

  const [sourceVmid, setSourceVmid] = useState("");
  const [invalid, setInvalid] = useState("");
  const sourceRef = useRef(null);
  const nameRef = useRef(null);
  const [name, setName] = useState(template?.name ?? "");
  const [description, setDescription] = useState(template?.description ?? "");
  const [visibility, setVisibility] = useState(template?.visibility ?? "private");
  const [useCustomSpec, setUseCustomSpec] = useState(
    Boolean(template?.default_cores || template?.default_memory),
  );
  const [defaultCores, setDefaultCores] = useState(template?.default_cores || 2);
  const [defaultMemory, setDefaultMemory] = useState(template?.default_memory || 2048);
  const [allowPasswordChange, setAllowPasswordChange] = useState(
    template ? template.allow_password_change !== false : true,
  );
  const [requiresGpu, setRequiresGpu] = useState(Boolean(template?.requires_gpu));
  const [resources, setResources] = useState([]);
  const [resourcesLoading, setResourcesLoading] = useState(!isEdit);
  const [busy, setBusy] = useState(false);

  // 編輯模式：既有附件（即時操作）
  const [attachments, setAttachments] = useState([]);
  const [attachBusy, setAttachBusy] = useState(false);
  // 建立模式：暫存檔案，create 成功後補上傳
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const attachInputRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    if (!isEdit) {
      (isAdmin ? ResourcesService.listAll() : ResourcesService.list())
        .then((res) => !cancelled && setResources(res ?? []))
        .catch(() => {})
        .finally(() => !cancelled && setResourcesLoading(false));
    } else {
      TemplatesService.listAttachments(template.id)
        .then((res) => !cancelled && setAttachments(res?.data ?? []))
        .catch(() => {});
    }
    return () => {
      cancelled = true;
    };
  }, [isEdit, isAdmin, template?.id]);

  // 來源機類型決定可否設定 GPU（hostpci 僅 qemu 支援）
  const selectedResource = resources.find((r) => String(r.vmid) === sourceVmid);
  const resourceType = isEdit ? template.resource_type : selectedResource?.type;
  const gpuSelectable = resourceType !== "lxc";

  useEffect(() => {
    if (!gpuSelectable && requiresGpu) setRequiresGpu(false);
  }, [gpuSelectable, requiresGpu]);

  const validateAttachment = (file, currentCount) => {
    const ext = fileExt(file.name);
    if (!ATTACHMENT_EXTS.has(ext)) {
      toast.error(t("TemplateFormDialog.unsupportedFileType", { ext: ext || t("TemplateFormDialog.noExtension") }));
      return false;
    }
    if (file.size > ATTACHMENT_MAX_BYTES) {
      toast.error(t("TemplateFormDialog.fileTooLarge"));
      return false;
    }
    if (currentCount >= ATTACHMENT_MAX_COUNT) {
      toast.error(t("TemplateFormDialog.attachmentLimitReached", { max: ATTACHMENT_MAX_COUNT }));
      return false;
    }
    return true;
  };

  const handleAttachmentSelect = async (file) => {
    const currentCount = isEdit ? attachments.length : pendingAttachments.length;
    if (!file || !validateAttachment(file, currentCount)) {
      if (attachInputRef.current) attachInputRef.current.value = "";
      return;
    }
    if (!isEdit) {
      setPendingAttachments((prev) => [...prev, file]);
      if (attachInputRef.current) attachInputRef.current.value = "";
      return;
    }
    setAttachBusy(true);
    try {
      await TemplatesService.uploadAttachment(template.id, file);
      const res = await TemplatesService.listAttachments(template.id);
      setAttachments(res?.data ?? []);
      toast.success(t("TemplateFormDialog.attachmentUploaded"));
    } catch (e) {
      toast.error(e?.message ?? t("TemplateFormDialog.attachmentUploadFailed"));
    } finally {
      setAttachBusy(false);
      if (attachInputRef.current) attachInputRef.current.value = "";
    }
  };

  const handleAttachmentRemove = async (attachmentId) => {
    setAttachBusy(true);
    try {
      await TemplatesService.removeAttachment(template.id, attachmentId);
      setAttachments((prev) => prev.filter((a) => a.id !== attachmentId));
    } catch (e) {
      toast.error(e?.message ?? t("TemplateFormDialog.attachmentDeleteFailed"));
    } finally {
      setAttachBusy(false);
    }
  };

  /** create 成功後補上傳暫存檔（best-effort，失敗可稍後在編輯補） */
  const uploadPendingFiles = async (templateId) => {
    const failed = [];
    for (const file of pendingAttachments) {
      try {
        await TemplatesService.uploadAttachment(templateId, file);
      } catch {
        failed.push(file.name);
      }
    }
    if (failed.length > 0) {
      toast.error(
        t("TemplateFormDialog.pendingUploadPartialFail", { files: failed.join("、") }),
      );
    }
  };

  const handleSubmit = async () => {
    if (!isEdit && !sourceVmid) {
      setInvalid("source");
      focusInvalidField(sourceRef.current);
      return;
    }
    if (!name.trim()) {
      setInvalid("name");
      focusInvalidField(nameRef.current);
      return;
    }

    const common = {
      name: name.trim(),
      description: description.trim() || null,
      visibility,
      default_cores: useCustomSpec ? Number(defaultCores) : null,
      default_memory: useCustomSpec ? Number(defaultMemory) : null,
      allow_password_change: allowPasswordChange,
      requires_gpu: requiresGpu,
    };

    if (!isEdit) {
      const ok = await confirm({
        title: t("TemplateFormDialog.convertConfirmTitle"),
        message: t("TemplateFormDialog.convertConfirmMessage"),
        confirmText: t("TemplateFormDialog.convertConfirmButton"),
        danger: true,
      });
      if (!ok) return;
    }

    setBusy(true);
    try {
      if (isEdit) {
        await TemplatesService.update(template.id, common);
        toast.success(t("TemplateFormDialog.updatedToast"));
      } else {
        const res = await TemplatesService.create({
          ...common,
          source_vmid: Number(sourceVmid),
        });
        const newTemplateId = res?.template?.id;
        if (newTemplateId) {
          await uploadPendingFiles(newTemplateId);
        }
        toast.success(t("TemplateFormDialog.createdToast"));
      }
      onSaved();
      onClose();
    } catch (e) {
      toast.error(e?.message ?? (isEdit ? t("TemplateFormDialog.updateFailed") : t("TemplateFormDialog.createFailed")));
    } finally {
      setBusy(false);
    }
  };

  const shownAttachments = isEdit
    ? attachments
    : pendingAttachments.map((file, idx) => ({
        id: `pending-${idx}`,
        filename: file.name,
        size_bytes: file.size,
        pendingIndex: idx,
      }));

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={`${styles.modal} ${styles.modalWide}`} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>
          <MIcon name="library_books" size={20} />
          {isEdit ? t("TemplateFormDialog.editTitle") : t("TemplateFormDialog.createTitle")}
        </span>
        <p className={styles.modalDesc}>
          {isEdit
            ? t("TemplateFormDialog.editDescription")
            : t("TemplateFormDialog.createDescription")}
        </p>

        {!isEdit && (
          <div className={`${styles.field} ${invalid === "source" ? styles.fieldInvalid : ""}`}>
            <label htmlFor="tpl-source">{t("TemplateFormDialog.sourceLabel")}</label>
            <select
              id="tpl-source"
              ref={sourceRef}
              value={sourceVmid}
              aria-invalid={invalid === "source"}
              onChange={(e) => { setSourceVmid(e.target.value); setInvalid(""); }}
            >
              <option value="">{t("TemplateFormDialog.sourceDefaultOption")}</option>
              {resources
                .filter((r) => r.vmid != null && r.vmid > 0 && !r.is_placeholder)
                .map((r) => (
                  <option key={r.vmid} value={String(r.vmid)}>
                    {t("TemplateFormDialog.sourceOptionLabel", { name: r.name, vmid: r.vmid, type: r.type })}
                  </option>
                ))}
            </select>
            {!resourcesLoading && resources.length === 0 && (
              <span className={styles.fieldWarn}>{t("TemplateFormDialog.sourceNoneWarning")}</span>
            )}
          </div>
        )}

        <div className={`${styles.field} ${invalid === "name" ? styles.fieldInvalid : ""}`}>
          <label htmlFor="tpl-name">{t("TemplateFormDialog.nameLabel")}</label>
          <input
            id="tpl-name"
            ref={nameRef}
            type="text"
            maxLength={255}
            placeholder={t("TemplateFormDialog.namePlaceholder")}
            aria-invalid={invalid === "name"}
            value={name}
            onChange={(e) => { setName(e.target.value); setInvalid(""); }}
          />
        </div>

        <div className={styles.field}>
          <label htmlFor="tpl-desc">{t("TemplateFormDialog.descriptionLabel")}</label>
          <textarea
            id="tpl-desc"
            rows={3}
            maxLength={1000}
            placeholder={t("TemplateFormDialog.descriptionPlaceholder")}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className={styles.field}>
          <label>{t("TemplateFormDialog.visibilityLabel")}</label>
          <div className={styles.visibilityOptions}>
            <label className={`${styles.visibilityOption} ${visibility !== "global" ? styles.visibilityOptionActive : ""}`}>
              <input
                type="radio"
                name="template-visibility"
                value="private"
                checked={visibility !== "global"}
                onChange={() => setVisibility("private")}
              />
              <span>
                <strong>{t("TemplateFormDialog.visibilityPrivateTitle")}</strong>
                <small>{t("TemplateFormDialog.visibilityPrivateDesc")}</small>
              </span>
            </label>
            <label className={`${styles.visibilityOption} ${visibility === "global" ? styles.visibilityOptionActive : ""}`}>
              <input
                type="radio"
                name="template-visibility"
                value="global"
                checked={visibility === "global"}
                onChange={() => setVisibility("global")}
              />
              <span>
                <strong>{t("TemplateFormDialog.visibilityGlobalTitle")}</strong>
                <small>{t("TemplateFormDialog.visibilityGlobalDesc")}</small>
              </span>
            </label>
          </div>
        </div>

        <label className={styles.checkLine}>
          <input
            type="checkbox"
            checked={allowPasswordChange}
            onChange={(e) => setAllowPasswordChange(e.target.checked)}
          />
          <span className={styles.checkText}>
            <span>{t("TemplateFormDialog.allowPasswordChangeLabel")}</span>
            <small>{t("TemplateFormDialog.allowPasswordChangeHint")}</small>
          </span>
        </label>

        <label className={styles.checkLine} title={gpuSelectable ? undefined : t("TemplateFormDialog.gpuNotSupportedTitle")}>
          <input
            type="checkbox"
            checked={requiresGpu}
            disabled={!gpuSelectable}
            onChange={(e) => setRequiresGpu(e.target.checked)}
          />
          <span className={styles.checkText}>
            <span>{t("TemplateFormDialog.requiresGpuLabel")}</span>
            <small>{t("TemplateFormDialog.requiresGpuHint")}</small>
          </span>
        </label>

        <label className={styles.checkLine}>
          <input
            type="checkbox"
            checked={useCustomSpec}
            onChange={(e) => setUseCustomSpec(e.target.checked)}
          />
          <span className={styles.checkText}>
            <span>{t("TemplateFormDialog.customSpecLabel")}</span>
            <small>{t("TemplateFormDialog.customSpecHint")}</small>
          </span>
        </label>

        {useCustomSpec && (
          <>
            <div className={styles.field}>
              <div className={styles.sliderLabelRow}>
                <label htmlFor="tpl-cores">{t("TemplateFormDialog.defaultCoresLabel")}</label>
                <span className={styles.sliderValue}>{t("TemplateFormDialog.coresValue", { cores: defaultCores })}</span>
              </div>
              <input
                id="tpl-cores"
                type="range"
                min={CORE_MIN}
                max={CORE_MAX}
                step={1}
                className={styles.slider}
                value={defaultCores}
                onChange={(e) => setDefaultCores(Number(e.target.value))}
              />
              <div className={styles.sliderTicks}>
                {[1, 2, 4, 6, 8].map((v) => (
                  <span key={v} style={{ left: `${((v - CORE_MIN) / (CORE_MAX - CORE_MIN)) * 100}%` }}>
                    {v}
                  </span>
                ))}
              </div>
            </div>

            <div className={styles.field}>
              <div className={styles.sliderLabelRow}>
                <label htmlFor="tpl-memory">{t("TemplateFormDialog.defaultMemoryLabel")}</label>
                <span className={styles.sliderValue}>{(defaultMemory / 1024).toFixed(1)} GB</span>
              </div>
              <input
                id="tpl-memory"
                type="range"
                min={MEMORY_MIN}
                max={MEMORY_MAX}
                step={512}
                className={styles.slider}
                value={defaultMemory}
                onChange={(e) => setDefaultMemory(Number(e.target.value))}
              />
              <div className={styles.sliderTicks}>
                {[[1024, "1GB"], [8192, "8GB"], [16384, "16GB"], [24576, "24GB"], [32768, "32GB"]].map(([v, label]) => (
                  <span key={label} style={{ left: `${((v - MEMORY_MIN) / (MEMORY_MAX - MEMORY_MIN)) * 100}%` }}>
                    {label}
                  </span>
                ))}
              </div>
            </div>
          </>
        )}

        <div className={styles.field}>
          <label>{t("TemplateFormDialog.defaultDiskLabel")}</label>
          <div className={styles.diskFixed}>
            <MIcon name="lock" size={15} />
            {isEdit && template.default_disk
              ? t("TemplateFormDialog.defaultDiskWithSize", { size: template.default_disk })
              : t("TemplateFormDialog.defaultDiskAuto")}
          </div>
        </div>

        <div className={styles.field}>
          <label>{t("TemplateFormDialog.attachmentsLabel")}</label>
          {shownAttachments.length > 0 && (
            <div className={styles.attachList}>
              {shownAttachments.map((a) => (
                <div key={a.id} className={styles.attachItem}>
                  <MIcon name="description" size={15} />
                  <span className={styles.attachName}>{a.filename}</span>
                  <span className={styles.attachSize}>{formatBytes(a.size_bytes)}</span>
                  <button
                    type="button"
                    className={`${styles.attachBtn} ${styles.attachBtnDanger}`}
                    disabled={attachBusy}
                    onClick={() =>
                      isEdit
                        ? handleAttachmentRemove(a.id)
                        : setPendingAttachments((prev) =>
                            prev.filter((_, idx) => idx !== a.pendingIndex),
                          )
                    }
                    title={t("TemplateFormDialog.removeAttachmentTitle")}
                  >
                    <MIcon name="delete_outline" size={15} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <input
            ref={attachInputRef}
            type="file"
            style={{ display: "none" }}
            onChange={(e) => handleAttachmentSelect(e.target.files?.[0])}
          />
          <div>
            <button
              type="button"
              className={styles.btnSecondary}
              disabled={attachBusy || shownAttachments.length >= ATTACHMENT_MAX_COUNT}
              onClick={() => attachInputRef.current?.click()}
            >
              <MIcon name="upload_file" size={14} />
              {attachBusy ? t("TemplateFormDialog.processing") : t("TemplateFormDialog.uploadAttachment")}
            </button>
          </div>
          <span className={styles.fieldHint}>
            {t("TemplateFormDialog.attachmentHint")}
            {!isEdit && t("TemplateFormDialog.attachmentHintCreateSuffix")}
          </span>
        </div>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            {t("TemplateFormDialog.cancel")}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={busy}
            onClick={handleSubmit}
          >
            {busy ? t("TemplateFormDialog.processing") : isEdit ? t("TemplateFormDialog.saveChanges") : t("TemplateFormDialog.startConvert")}
          </button>
        </div>
      </div>
    </div>
  );
}
