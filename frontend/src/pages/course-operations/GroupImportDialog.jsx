import { useState } from "react";
import { useTranslation } from "react-i18next";
import Modal from "../../components/Modal/Modal";
import { useQueryView } from "../../hooks/useQueryLoadState";
import { useUserGroupsQuery } from "../../queries/userGroups";
import shared from "./CourseOperations.module.scss";

/**
 * 從管理員建的群組（例如某一班）把學生匯入班級名單。
 * 群組只由管理員維護，這裡只能挑；匯入只複製當下的學生，之後群組異動不影響班級。
 *
 * @param {(group) => Promise<void>} onImport 父層呼叫 API、更新名單並決定是否關閉
 */
export default function GroupImportDialog({ closing, busy, onClose, onImport }) {
  const { t } = useTranslation("teaching");
  /* 不輪詢：打開對話框時快取舊了才重抓，管理員剛建好的群組也看得到 */
  const groupsQuery = useUserGroupsQuery();
  const { error: loadError } = useQueryView(groupsQuery);
  const groups = groupsQuery.data;
  const [pickedId, setPickedId] = useState("");
  /* 預設選第一個有學生的群組；自己選過就照選的（重抓後那組不見了才退回預設） */
  const fallbackId = ((groups ?? []).find((group) => group.student_count > 0) ?? groups?.[0])?.id ?? "";
  const groupId = groups?.some((group) => group.id === pickedId) ? pickedId : fallbackId;
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
      {loadError ? (
        <p role="alert">{loadError?.message ?? t("GroupImportDialog.loadFailed")}</p>
      ) : groups === undefined ? (
        <p>{t("GroupImportDialog.loading")}</p>
      ) : groups.length === 0 ? (
        <p>{t("GroupImportDialog.empty")}</p>
      ) : (
        <>
          <label className={shared.field}>
            <span>{t("GroupImportDialog.groupLabel")}</span>
            <select value={groupId} onChange={(event) => setPickedId(event.target.value)} autoFocus>
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
