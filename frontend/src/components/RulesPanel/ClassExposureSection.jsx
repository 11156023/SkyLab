/**
 * ClassExposureSection
 * 老師在自己的機器上「開放給班級」：選班級、填允許的埠。
 * 這是學生從自己的機器連到老師機器的授權依據；關閉或縮小埠清單時，
 * 後端會一併拆掉學生已連上的規則，所以移除前要確認。
 * 只在規則面板裡、且這台機器可管理時顯示。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createClassExposure,
  deleteClassExposure,
  listClassExposures,
  updateClassExposure,
} from "../../services/firewall";
import { TeachingClassesService } from "../../services/teachingClasses";
import { formatPortList, parsePortList } from "./classExposurePorts";
import styles from "./RulesPanel.module.scss";
import MIcon from "../MIcon";
import { useToast } from "../../hooks/useToast";
import { useConfirm } from "../ConfirmDialog/ConfirmProvider";

export default function ClassExposureSection({ vmid }) {
  const { t } = useTranslation("components");
  const toast = useToast();
  const confirm = useConfirm();
  const [exposures, setExposures] = useState([]);
  const [classes, setClasses] = useState(null); // null = 還沒抓
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [classId, setClassId] = useState("");
  const [portsText, setPortsText] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");
  const [formError, setFormError] = useState("");

  const load = useCallback(async () => {
    try {
      setExposures((await listClassExposures(vmid)) ?? []);
    } catch (err) {
      toast.error(err?.message ?? t("ClassExposure.loadFailed"));
    } finally {
      setLoaded(true);
    }
  }, [vmid, toast, t]);

  useEffect(() => {
    setLoaded(false);
    setAdding(false);
    setEditingId(null);
    load();
  }, [load]);

  /* 班級清單只在要新增時才抓；已開放的班級不再列 */
  const openAdd = async () => {
    setFormError("");
    setAdding(true);
    if (classes !== null) return;
    try {
      const list = await TeachingClassesService.list();
      setClasses(Array.isArray(list) ? list : []);
    } catch {
      setClasses([]);
    }
  };
  const exposedClassIds = new Set(exposures.map((e) => String(e.class_id)));
  const candidates = (classes ?? []).filter((c) => !exposedClassIds.has(String(c.id)));
  useEffect(() => {
    if (adding && !classId && candidates[0]) setClassId(String(candidates[0].id));
  }, [adding, classId, candidates]);

  const describeError = (parsed) =>
    parsed.error === "ClassExposure.portInvalid"
      ? t(parsed.error, { token: parsed.token })
      : t(parsed.error);

  async function handleCreate(e) {
    e.preventDefault();
    const parsed = parsePortList(portsText);
    if (parsed.error) { setFormError(describeError(parsed)); return; }
    if (!classId) { setFormError(t("ClassExposure.classRequired")); return; }
    setBusy(true);
    try {
      await createClassExposure(vmid, { class_id: classId, ports: parsed.ports });
      toast.success(t("ClassExposure.created"));
      setAdding(false);
      setPortsText("");
      setClassId("");
      await load();
    } catch (err) {
      setFormError(err?.message ?? t("ClassExposure.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  function startEdit(exposure) {
    setFormError("");
    setEditingId(exposure.id);
    setEditText(formatPortList(exposure.ports));
  }

  async function handleSaveEdit(exposure) {
    const parsed = parsePortList(editText);
    if (parsed.error) { setFormError(describeError(parsed)); return; }
    setBusy(true);
    try {
      await updateClassExposure(vmid, exposure.id, { ports: parsed.ports });
      toast.success(t("ClassExposure.updated"));
      setEditingId(null);
      await load();
    } catch (err) {
      setFormError(err?.message ?? t("ClassExposure.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(exposure) {
    const ok = await confirm({
      title: t("ClassExposure.removeTitle"),
      message: t("ClassExposure.removeMessage", { cls: exposure.class_name ?? "" }),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await deleteClassExposure(vmid, exposure.id);
      toast.success(res?.message ?? t("ClassExposure.removed"));
      await load();
    } catch (err) {
      toast.error(err?.message ?? t("ClassExposure.removeFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <h3 className={styles.sectionTitle}>{t("ClassExposure.title")}</h3>
        {!adding && (
          <button type="button" className={styles.linkBtn} onClick={openAdd} disabled={busy}>
            <MIcon name="add" size={14} />
            {t("ClassExposure.add")}
          </button>
        )}
      </div>
      <p className={styles.hint}>{t("ClassExposure.desc")}</p>

      {loaded && exposures.length === 0 && !adding && (
        <p className={styles.hint}>{t("ClassExposure.empty")}</p>
      )}

      {exposures.map((exposure) => (
        <div key={exposure.id} className={styles.exposureRow}>
          <div className={styles.exposureInfo}>
            <span className={styles.exposureClass}>
              <MIcon name="school" size={14} />
              {exposure.class_name ?? t("ClassExposure.unknownClass")}
            </span>
            {editingId === exposure.id ? (
              <input
                className={styles.exposureInput}
                value={editText}
                onChange={(e) => { setFormError(""); setEditText(e.target.value); }}
                placeholder={t("ClassExposure.portsPlaceholder")}
                aria-label={t("ClassExposure.portsLabel")}
                autoFocus
              />
            ) : (
              <span className={styles.exposurePorts}>{formatPortList(exposure.ports)}</span>
            )}
          </div>
          <div className={styles.ruleActions}>
            {editingId === exposure.id ? (
              <>
                <button type="button" className={styles.ruleBtn} disabled={busy} title={t("ClassExposure.save")} onClick={() => handleSaveEdit(exposure)}>
                  <MIcon name="check" size={16} />
                </button>
                <button type="button" className={styles.ruleBtn} disabled={busy} title={t("ClassExposure.cancel")} onClick={() => { setEditingId(null); setFormError(""); }}>
                  <MIcon name="close" size={16} />
                </button>
              </>
            ) : (
              <>
                <button type="button" className={styles.ruleBtn} disabled={busy} title={t("ClassExposure.editPorts")} onClick={() => startEdit(exposure)}>
                  <MIcon name="edit" size={14} />
                </button>
                <button type="button" className={`${styles.ruleBtn} ${styles.ruleBtnDanger}`} disabled={busy} title={t("ClassExposure.remove")} onClick={() => handleRemove(exposure)}>
                  <MIcon name="delete" size={14} />
                </button>
              </>
            )}
          </div>
        </div>
      ))}

      {adding && (
        <form className={styles.exposureForm} onSubmit={handleCreate}>
          <label className={styles.exposureLabel} htmlFor="exposure-class">{t("ClassExposure.classLabel")}</label>
          {classes === null ? (
            <span className={styles.hint}>{t("ClassExposure.loadingClasses")}</span>
          ) : candidates.length === 0 ? (
            <span className={styles.hint}>{t("ClassExposure.noClasses")}</span>
          ) : (
            <select
              id="exposure-class"
              className={styles.exposureInput}
              value={classId}
              onChange={(e) => setClassId(e.target.value)}
            >
              {candidates.map((c) => <option key={c.id} value={String(c.id)}>{c.name}</option>)}
            </select>
          )}
          <label className={styles.exposureLabel} htmlFor="exposure-ports">{t("ClassExposure.portsLabel")}</label>
          <input
            id="exposure-ports"
            className={styles.exposureInput}
            value={portsText}
            onChange={(e) => { setFormError(""); setPortsText(e.target.value); }}
            placeholder={t("ClassExposure.portsPlaceholder")}
          />
          <span className={styles.hint}>{t("ClassExposure.portsHint")}</span>
          <div className={styles.exposureActions}>
            <button type="button" className={styles.linkBtn} onClick={() => { setAdding(false); setFormError(""); }} disabled={busy}>
              {t("ClassExposure.cancel")}
            </button>
            <button type="submit" className={styles.addBtn} disabled={busy || candidates.length === 0}>
              <MIcon name="lock_open" size={14} />
              {t("ClassExposure.confirm")}
            </button>
          </div>
        </form>
      )}

      {formError && <p className={styles.errorMsg}>{formError}</p>}
    </div>
  );
}
