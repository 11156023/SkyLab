import { useEffect, useState } from "react";
import styles from "./Teaching.module.scss";
import { useAuth } from "../../contexts/AuthContext";
import { GroupsService } from "../../services/groups";
import { PairSessionsService } from "../../services/pairSessions";
import { useToast } from "../../hooks/useToast";

/**
 * 邀請協作 dialog（Pair Mode）：
 * 列出我的群組成員（去重、排除自己），送出後回傳 session id。
 */
export default function PairInviteDialog({ vmid, onClose, onCreated }) {
  const toast = useToast();
  const { user } = useAuth();
  const [members, setMembers] = useState([]);
  const [inviteeId, setInviteeId] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const groups = (await GroupsService.list())?.data ?? [];
        const details = await Promise.all(groups.map((g) => GroupsService.detail(g.id)));
        const seen = new Map();
        for (const detail of details) {
          for (const m of detail.members ?? []) {
            seen.set(m.user_id, {
              id: m.user_id,
              email: m.email,
              full_name: m.full_name ?? null,
            });
          }
        }
        if (user?.id) seen.delete(user.id);
        if (!cancelled) setMembers([...seen.values()]);
      } catch {
        if (!cancelled) setMembers([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  const handleSubmit = async () => {
    setBusy(true);
    try {
      const created = await PairSessionsService.create(vmid, inviteeId);
      toast.success("協作邀請已送出，雙方可進入同一畫面");
      onCreated(created.id);
      onClose();
    } catch (e) {
      toast.error(e?.message ?? "建立協作失敗");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>邀請協作（Pair Mode）</span>
        <p className={styles.modalDesc}>邀請同群組成員與你共同操作這台 VM，雙方皆可輸入。</p>
        <select
          className={styles.select}
          value={inviteeId}
          onChange={(e) => setInviteeId(e.target.value)}
        >
          <option value="">選擇同群組成員</option>
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {m.full_name || m.email}
            </option>
          ))}
        </select>
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={!inviteeId || busy}
            onClick={handleSubmit}
          >
            {busy ? "送出中…" : "送出邀請"}
          </button>
        </div>
      </div>
    </div>
  );
}
