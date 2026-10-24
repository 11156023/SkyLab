import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AdminPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { UserGroupsService } from "../../../services/userGroups";
import { joinClauses } from "../../../utils/joinList";

const GROUP_NAME_MAX = 120;

function GroupRow({ group, busy, onRename, onImport, onDelete }) {
  const { t } = useTranslation("system");
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(group.name);

  async function submit(event) {
    event.preventDefault();
    const value = name.trim();
    if (!value || value === group.name) { setEditing(false); setName(group.name); return; }
    if (await onRename(group, value)) setEditing(false);
  }

  return (
    <li className={styles.groupRow}>
      {editing ? (
        <form className={styles.groupRename} onSubmit={submit}>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={GROUP_NAME_MAX}
            aria-label={t("UserGroups.renameAria", { name: group.name })}
            autoFocus
            /* Esc 只取消改名，不關整個對話框（Modal 會略過 defaultPrevented 的 Esc） */
            onKeyDown={(event) => {
              if (event.key === "Escape") { event.preventDefault(); setEditing(false); setName(group.name); }
            }}
          />
          <button type="submit" className={styles.actionBtn} disabled={busy} aria-label={t("UserGroups.saveName")}>
            <MIcon name="check" size={17} />
          </button>
        </form>
      ) : (
        <div className={styles.groupRowMain}>
          <strong>{group.name}</strong>
          <small>{t("UserGroups.memberSummary", { count: group.member_count, students: group.student_count })}</small>
        </div>
      )}
      <div className={styles.rowActions}>
        <button type="button" className={styles.actionBtn} disabled={busy} title={t("UserGroups.importCsv")} aria-label={t("UserGroups.importCsvAria", { name: group.name })} onClick={() => onImport(group)}>
          <MIcon name="upload" size={17} />
        </button>
        <button type="button" className={styles.actionBtn} disabled={busy || editing} title={t("UserGroups.rename")} aria-label={t("UserGroups.renameAria", { name: group.name })} onClick={() => setEditing(true)}>
          <MIcon name="edit" size={17} />
        </button>
        <button type="button" className={styles.actionBtnDanger} disabled={busy} title={t("UserGroups.delete")} aria-label={t("UserGroups.deleteAria", { name: group.name })} onClick={() => onDelete(group)}>
          <MIcon name="delete" size={17} />
        </button>
      </div>
    </li>
  );
}

/**
 * 群組管理：建立、改名、刪除群組，用 CSV（email 或學號）匯入成員。
 * 勾選使用者加入／移出群組走清單的批量列（GroupPickDialog）。
 * 每次改動後呼叫 onChanged 讓頁面重抓群組清單（人數、篩選選單）。
 */
export function GroupManagerDialog({ groups, closing, onClose, onChanged }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);
  const importTargetRef = useRef(null);

  /* 成功回傳 { value }（API 的回應），失敗已跳過 toast、回傳 null */
  async function run(action, fallbackMessage) {
    setBusy(true);
    try {
      const value = await action();
      await onChanged();
      return { value };
    } catch (error) {
      toast.error(error?.message ?? fallbackMessage);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function create(event) {
    event.preventDefault();
    const name = newName.trim();
    if (!name) return;
    const ok = await run(() => UserGroupsService.create(name), t("UserGroups.saveFailed"));
    if (ok) {
      setNewName("");
      toast.success(t("UserGroups.created", { name }));
    }
  }

  async function rename(group, name) {
    const ok = await run(() => UserGroupsService.rename(group.id, name), t("UserGroups.saveFailed"));
    if (ok) toast.success(t("UserGroups.renamed", { name }));
    return Boolean(ok);
  }

  async function remove(group) {
    const confirmed = await confirm({
      title: t("UserGroups.deleteTitle"),
      message: t("UserGroups.deleteConfirm", { name: group.name }),
      confirmText: t("UserGroups.delete"),
      danger: true,
    });
    if (!confirmed) return;
    const ok = await run(() => UserGroupsService.delete(group.id), t("UserGroups.deleteFailed"));
    if (ok) toast.success(t("UserGroups.deleted", { name: group.name }));
  }

  function pickCsv(group) {
    importTargetRef.current = group;
    fileRef.current?.click();
  }

  async function importCsv() {
    const file = fileRef.current?.files?.[0];
    const group = importTargetRef.current;
    if (!file || !group) return;
    const done = await run(() => UserGroupsService.importMembers(group.id, file), t("UserGroups.importFailed"));
    if (fileRef.current) fileRef.current.value = "";
    if (!done) return;
    const result = done.value;
    const message = t("UserGroups.imported", { name: group.name, count: result.added });
    if (result.not_found?.length) {
      toast.warning(joinClauses([message, t("UserGroups.notFoundCount", { count: result.not_found.length })]));
    } else {
      toast.success(message);
    }
  }

  return (
    <Modal closing={closing} onClose={onClose} busy={busy} closeButton size="md" title={t("UserGroups.managerTitle")}>
      <form className={styles.groupCreate} onSubmit={create}>
        <label className={styles.field}>
          <span>{t("UserGroups.newGroupLabel")}</span>
          <input
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            maxLength={GROUP_NAME_MAX}
            placeholder={t("UserGroups.newGroupPlaceholder")}
            autoFocus
          />
        </label>
        <button type="submit" className={styles.btnPrimary} disabled={busy || !newName.trim()}>
          <MIcon name="add" size={16} />
          {t("UserGroups.create")}
        </button>
      </form>

      <input ref={fileRef} className={styles.hiddenFileInput} type="file" accept=".csv,text/csv" onChange={importCsv} tabIndex={-1} aria-hidden="true" />
      {groups.length === 0 ? (
        <p className={styles.registrationEmpty}>{t("UserGroups.empty")}</p>
      ) : (
        <ul className={styles.groupList}>
          {groups.map((group) => (
            <GroupRow key={group.id} group={group} busy={busy} onRename={rename} onImport={pickCsv} onDelete={remove} />
          ))}
        </ul>
      )}
      <em className={styles.fieldHint}>{t("UserGroups.csvHint")}</em>
    </Modal>
  );
}

/** 批量列的「加入群組／移出群組」：挑一個群組，一次送出所有勾選的帳號 */
export function GroupPickDialog({ mode, groups, defaultGroupId, count, busy, closing, onClose, onSubmit }) {
  const { t } = useTranslation("system");
  const [groupId, setGroupId] = useState(
    () => (groups.some((group) => group.id === defaultGroupId) ? defaultGroupId : groups[0]?.id ?? ""),
  );
  const isAdd = mode === "add";

  function submit(event) {
    event.preventDefault();
    const group = groups.find((item) => item.id === groupId);
    if (group && !busy) onSubmit(group);
  }

  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={busy}
      title={isAdd ? t("UserGroups.addTitle", { count }) : t("UserGroups.removeTitle", { count })}
      actions={(
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>{t("AdminPage.cancel")}</button>
          <button type="submit" className={styles.btnPrimary} disabled={busy || !groupId}>
            {isAdd ? t("UserGroups.addSubmit") : t("UserGroups.removeSubmit")}
          </button>
        </>
      )}
    >
      {groups.length === 0 ? (
        <p>{t("UserGroups.emptyCreateFirst")}</p>
      ) : (
        <label className={styles.field}>
          <span>{t("UserGroups.groupLabel")}</span>
          <select value={groupId} onChange={(event) => setGroupId(event.target.value)} autoFocus>
            {groups.map((group) => (
              <option key={group.id} value={group.id}>{group.name}</option>
            ))}
          </select>
        </label>
      )}
    </Modal>
  );
}
