/**
 * TemplateConvertDialog（共用元件）
 * 把一台調好的機器轉成範本。從資源列表每列的「更多」選單開啟（老師／管理員）。
 * 轉換會關機並把這台機器從資源列表移除，所以要輸入機器名稱確認。
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import styles from "./TemplateConvertDialog.module.scss";
import MIcon from "../MIcon";
import { useToast } from "../../hooks/useToast";
import { TemplatesService } from "../../services/templates";

export default function TemplateConvertDialog({ resource, closing = false, onClose, onDone }) {
  const { t } = useTranslation("components");
  const toast = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState(resource?.name ?? "");
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState("private");
  const [allowPasswordChange, setAllowPasswordChange] = useState(true);
  const [confirmName, setConfirmName] = useState("");
  const [busy, setBusy] = useState(false);
  const ready = name.trim().length > 0 && confirmName.trim() === resource?.name;

  async function submit(e) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    try {
      await TemplatesService.create({
        source_vmid: resource.vmid,
        name: name.trim(),
        description: description.trim() || null,
        visibility,
        allow_password_change: allowPasswordChange,
      });
      toast.success(t("TemplateConvertDialog.started"));
      onDone?.();
      onClose();
      navigate("/templates");
    } catch (err) {
      toast.error(err?.message ?? t("TemplateConvertDialog.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>{t("TemplateConvertDialog.title")}</h2>
            <p>{t("TemplateConvertDialog.desc")}</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label={t("TemplateConvertDialog.close")}>
            <MIcon name="close" size={18} />
          </button>
        </div>

        <ol className={styles.stepList}>
          <li>{t("TemplateConvertDialog.step1")}</li>
          <li>{t("TemplateConvertDialog.step2")}</li>
          <li>{t("TemplateConvertDialog.step3")}</li>
        </ol>

        <label className={styles.field}>
          <span>{t("TemplateConvertDialog.nameLabel")}</span>
          <input value={name} maxLength={255} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className={styles.field}>
          <span>{t("TemplateConvertDialog.descriptionLabel")}</span>
          <textarea rows={3} maxLength={1000} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t("TemplateConvertDialog.descriptionPlaceholder")} />
        </label>
        <label className={styles.field}>
          <span>{t("TemplateConvertDialog.visibilityLabel")}</span>
          <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
            <option value="private">{t("TemplateConvertDialog.visibilityPrivate")}</option>
            <option value="global">{t("TemplateConvertDialog.visibilityGlobal")}</option>
          </select>
        </label>
        <label className={styles.checkRow}>
          <input type="checkbox" checked={allowPasswordChange} onChange={(e) => setAllowPasswordChange(e.target.checked)} />
          <span>{t("TemplateConvertDialog.allowPasswordChange")}</span>
        </label>
        <label className={styles.field}>
          <span>{t("TemplateConvertDialog.confirmLabel", { name: resource?.name })}</span>
          <input value={confirmName} onChange={(e) => setConfirmName(e.target.value)} placeholder={resource?.name} />
        </label>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={busy}>
            {t("TemplateConvertDialog.cancel")}
          </button>
          <button type="submit" className={styles.btnDanger} disabled={busy || !ready}>
            <MIcon name="library_add" size={16} />
            {busy ? t("TemplateConvertDialog.converting") : t("TemplateConvertDialog.convert")}
          </button>
        </div>
      </form>
    </div>
  );
}
