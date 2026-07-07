import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./ClassroomPage.module.scss";
import MIcon from "../../components/MIcon";
import ClassroomWatchDialog from "../../components/Classroom/ClassroomWatchDialog";
import { ClassroomService } from "../../services/classroom";
import { GroupsService } from "../../services/groups";
import { useToast } from "../../hooks/useToast";

function StudentCard({ student, watching, onWatch }) {
  const qemuVms = (student.vms ?? []).filter((v) => v.vm_type !== "lxc");
  const primaryVm = qemuVms.find((v) => v.status === "running") ?? qemuVms[0];

  return (
    <div className={styles.studentCard}>
      <div className={styles.studentHead}>
        <div className={styles.studentInfo}>
          <span className={styles.studentName}>{student.full_name || student.email}</span>
          <span className={styles.studentEmail}>{student.email}</span>
        </div>
        <span
          className={`${styles.badge} ${student.online ? styles.badge_ok : styles.badge_muted}`}
        >
          <MIcon name={student.online ? "wifi" : "wifi_off"} size={12} />
          {student.online ? "在線" : "離線"}
        </span>
      </div>

      <div className={styles.studentFoot}>
        {primaryVm ? (
          <span className={styles.vmLine}>
            <span
              className={`${styles.vmDot} ${primaryVm.status === "running" ? styles.vmDot_on : ""}`}
            />
            {primaryVm.name || `VM ${primaryVm.vmid}`}
          </span>
        ) : (
          <span className={styles.vmLineMuted}>無 VM</span>
        )}

        <button
          type="button"
          className={styles.btnSecondary}
          disabled={primaryVm?.status !== "running" || watching}
          onClick={() => primaryVm && onWatch(primaryVm.vmid)}
        >
          <MIcon name="monitor" size={14} />
          觀看
        </button>
      </div>
    </div>
  );
}

export default function ClassroomPage() {
  const toast = useToast();
  const [groups, setGroups] = useState([]);
  const [groupId, setGroupId] = useState(null);
  const [students, setStudents] = useState(null);
  const [watch, setWatch] = useState(null); // { sessionId, title }
  const [watching, setWatching] = useState(false);
  const [broadcasting, setBroadcasting] = useState(false);

  const effectiveGroupId = groupId ?? groups[0]?.id ?? null;

  useEffect(() => {
    let cancelled = false;
    GroupsService.list()
      .then((res) => !cancelled && setGroups(res?.data ?? []))
      .catch((err) => toast.error(err?.message ?? "載入群組失敗"));
    return () => {
      cancelled = true;
    };
  }, [toast]);

  /* 學生清單：10 秒輪詢 */
  const loadStudents = useCallback(async () => {
    if (!effectiveGroupId) return;
    try {
      setStudents(await ClassroomService.listStudents(effectiveGroupId));
    } catch {
      setStudents((prev) => prev ?? []);
    }
  }, [effectiveGroupId]);

  useEffect(() => {
    setStudents(null);
    if (!effectiveGroupId) return undefined;
    loadStudents();
    const timer = setInterval(loadStudents, 10_000);
    return () => clearInterval(timer);
  }, [effectiveGroupId, loadStudents]);

  // 可作為直播源的 running VM（含老師自己在群組中的 VM）
  const broadcastCandidates = useMemo(
    () =>
      (students ?? []).flatMap((s) => (s.vms ?? []).filter((v) => v.status === "running")),
    [students],
  );

  const handleWatch = async (vmid) => {
    setWatching(true);
    try {
      const session = await ClassroomService.createSession({ vmid, mode: "monitor" });
      setWatch({ sessionId: session.id, title: `觀看 VM ${vmid}` });
    } catch (e) {
      toast.error(e?.message ?? "開啟觀看失敗");
    } finally {
      setWatching(false);
    }
  };

  const handleBroadcast = async (vmid) => {
    setBroadcasting(true);
    try {
      await ClassroomService.createSession({
        vmid,
        mode: "broadcast",
        group_id: effectiveGroupId,
      });
      toast.success("已開始直播");
    } catch (e) {
      toast.error(e?.message ?? "開始直播失敗");
    } finally {
      setBroadcasting(false);
    }
  };

  const closeWatch = () => {
    if (watch) {
      ClassroomService.stopSession(watch.sessionId).catch(() => {});
      setWatch(null);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>虛擬教室</h1>
          <p className={styles.pageSubtitle}>即時觀看學生畫面、直播示範給全班</p>
        </div>
        <select
          className={styles.groupSelect}
          value={effectiveGroupId ?? ""}
          onChange={(e) => setGroupId(e.target.value)}
        >
          {groups.length === 0 && <option value="">選擇群組</option>}
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </div>

      {broadcastCandidates.length > 0 && (
        <div className={styles.broadcastBar}>
          <MIcon name="sensors" size={16} />
          <span className={styles.broadcastLabel}>直播示範</span>
          <select
            className={styles.broadcastSelect}
            value=""
            disabled={broadcasting}
            onChange={(e) => e.target.value && handleBroadcast(Number(e.target.value))}
          >
            <option value="">選擇要直播的 VM</option>
            {broadcastCandidates.map((v) => (
              <option key={v.vmid} value={String(v.vmid)}>
                {v.name || `VM ${v.vmid}`}（{v.vmid}）
              </option>
            ))}
          </select>
          <span className={styles.broadcastHint}>直播為唯讀，全班可觀看你的畫面</span>
        </div>
      )}

      {students === null ? (
        <p className={styles.stateText}>載入中…</p>
      ) : students.length === 0 ? (
        <div className={styles.emptyHint}>
          <MIcon name="groups" size={32} />
          <p>此群組沒有學生</p>
        </div>
      ) : (
        <div className={styles.studentGrid}>
          {students.map((student) => (
            <StudentCard
              key={student.user_id}
              student={student}
              watching={watching}
              onWatch={handleWatch}
            />
          ))}
        </div>
      )}

      {watch && (
        <ClassroomWatchDialog
          sessionId={watch.sessionId}
          title={watch.title}
          canControl
          onClose={closeWatch}
        />
      )}
    </div>
  );
}
