import { useCallback, useEffect, useState } from "react";
import styles from "./Teaching.module.scss";
import MIcon from "../MIcon";
import { useAuth } from "../../contexts/AuthContext";
import { PairSessionsService } from "../../services/pairSessions";
import { useToast } from "../../hooks/useToast";

/** 協作邀請卡（掛在「我的資源」頁；有邀請才顯示，15 秒輪詢） */
export default function PairInvitesCard({ onJoin }) {
  const toast = useToast();
  const { user } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [ending, setEnding] = useState(null);

  const load = useCallback(async () => {
    try {
      setSessions((await PairSessionsService.listMine()) ?? []);
    } catch {
      /* 下一輪再試 */
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 15_000);
    return () => clearInterval(timer);
  }, [load]);

  const handleEnd = async (sessionId) => {
    setEnding(sessionId);
    try {
      await PairSessionsService.end(sessionId);
      await load();
    } catch (e) {
      toast.error(e?.message ?? "結束協作失敗");
    } finally {
      setEnding(null);
    }
  };

  if (sessions.length === 0) return null;

  return (
    <div className={`${styles.card} ${styles.cardAccent}`}>
      <h2 className={styles.cardTitle}>
        <MIcon name="group" size={18} />
        協作邀請
      </h2>
      <div className={styles.inviteList}>
        {sessions.map((s) => {
          const isOwner = s.owner_id === user?.id;
          return (
            <div key={s.id} className={styles.inviteRow}>
              <span>
                {isOwner
                  ? `你邀請 ${s.invitee_name ?? "成員"} 協作 VM ${s.vmid}`
                  : `${s.owner_name ?? "成員"} 邀請你協作 VM ${s.vmid}`}
              </span>
              <div className={styles.inviteActions}>
                <button
                  type="button"
                  className={styles.btnPrimary}
                  onClick={() => onJoin(s.id, s.vmid)}
                >
                  加入
                </button>
                {isOwner && (
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    disabled={ending === s.id}
                    onClick={() => handleEnd(s.id)}
                  >
                    {ending === s.id ? "結束中…" : "結束"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
