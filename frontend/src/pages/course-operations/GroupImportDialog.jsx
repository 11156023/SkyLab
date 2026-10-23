import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Modal from "../../components/Modal/Modal";
import { UserGroupsService } from "../../services/userGroups";
import shared from "./CourseOperations.module.scss";

/**
 * 從管理員建的群組（例如某一班）把學生匯入班級名單。
 * 群組只由管理員維護，這裡只能挑；匯入只複製當下的學生，之後群組異動不影響班級。
 *
 * @param {(group) => Promise<void>} onImport 父層呼叫 API、更新名單並決定是否關閉
 */
export default function GroupImportDialog({ closing, busy, onClose, onImport }) {
  const { t } = useTranslation("teaching");
  const [groups, setGroups] = useState(null); // null＝載入中
  const [loadError, setLoadError] = useState("");
  const [groupId, setGroupId] = useState("");

  useEffect(() => {
    let active = true;
    UserGroupsService.list()
      .then((res) => {
        if (!active) return;
        const list = res?.data ?? [];
        setGroups(list);
        setGroupId((list.find((group) => group.student_count > 0) ?? list[0])?.id ?? "");
      })
      .catch((reason) => {
        if (!active) return;
        setGroups([]);
        setLoadError(reason?.message ?? t("GroupImportDialog.loadFailed"));
      });
    return () => { active = false; };
  }, [t]);

  const selected = groups?.find((group) => group.id === groupId);
  const canSubmit = Boolean(selected) && selected.student_count > 0 && !busy;

  function submit(event) {
    event.preventDefault();
    if (canSubmit) onImport(selected);
  }

  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={busy}
      size="sm"
      title={t("GroupImportDialog.title")}
      actions={(
        <>
          <button type="button" className={shared.btnSecondary} onClick={onClose}>{t("GroupImportDialog.cancel")}</button>
          <button type="submit" className={shared.btnPrimary} disabled={!canSubmit}>
            {busy ? t("GroupImportDialog.importing") : t("GroupImportDialog.submit")}
          </button>
        </>
      )}
    >
      {groups === null ? (
        <p>{t("GroupImportDialog.loading")}</p>
      ) : loadError ? (
        <p role="alert">{loadError}</p>
      ) : groups.length === 0 ? (
        <p>{t("GroupImportDialog.empty")}</p>
      ) : (
        <>
          <label className={shared.field}>
            <span>{t("GroupImportDialog.groupLabel")}</span>
            <select value={groupId} onChange={(event) => setGroupId(event.target.value)} autoFocus>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>
                  {t("GroupImportDialog.groupOption", { name: group.name, count: group.student_count })}
                </option>
              ))}
            </select>
          </label>
          <p>{selected?.student_count === 0 ? t("GroupImportDialog.noStudents") : t("GroupImportDialog.snapshotNote")}</p>
        </>
      )}
    </Modal>
  );
}
