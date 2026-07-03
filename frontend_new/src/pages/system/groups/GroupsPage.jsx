import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./GroupsPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { apiGet } from "../../../services/api";
import { GroupsService } from "../../../services/groups";
import { BatchProvisionService } from "../../../services/batchProvision";

/* ── 共用小元件 ─────────────────────────────────────────── */

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function VmStatusBadge({ vmid, status }) {
  if (!vmid) {
    return <span className={`${styles.badge} ${styles.badge_muted}`}>未建立</span>;
  }
  if (status === "running") {
    return (
      <span className={`${styles.badge} ${styles.badge_success}`}>
        <MIcon name="power" size={13} />
        運行中
      </span>
    );
  }
  if (status === "stopped") {
    return (
      <span className={`${styles.badge} ${styles.badge_muted}`}>
        <MIcon name="power_off" size={13} />
        已關機
      </span>
    );
  }
  return (
    <span className={`${styles.badge} ${styles.badge_info}`}>
      <MIcon name="monitor" size={13} />
      {status ?? "未知"}
    </span>
  );
}

function UsagePill({ label, value }) {
  const pct = typeof value === "number" && !Number.isNaN(value)
    ? Math.max(0, Math.min(Math.round(value), 100))
    : null;
  return (
    <span className={styles.usagePill}>
      {label} {pct === null ? "--" : `${pct}%`}
    </span>
  );
}

function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="groups" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚無群組</h2>
      <p className={styles.emptyDesc}>點擊「建立群組」建立第一個課程或班級群組</p>
    </div>
  );
}

/* ── 建立群組 Modal ─────────────────────────────────────── */

function CreateGroupModal({ loading, onClose, onSubmit }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  function submit(e) {
    e.preventDefault();
    onSubmit({ name: name.trim(), description: description.trim() || undefined });
  }

  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>建立新群組</h2>
            <p>建立課程或班級群組，方便批量管理成員與資源。</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
            <MIcon name="close" size={18} />
          </button>
        </div>

        <label className={styles.field}>
          <span>群組名稱 *</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例：2024 Spring CS101"
            required
            maxLength={255}
          />
        </label>

        <label className={styles.field}>
          <span>說明</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="群組說明（選填）"
            rows={3}
          />
        </label>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            取消
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading || !name.trim()}>
            {loading ? "建立中..." : "建立"}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── 刪除群組確認 ───────────────────────────────────────── */

function ConfirmDeleteGroup({ group, loading, onClose, onConfirm }) {
  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.confirmIcon}>
          <MIcon name="warning" size={24} />
        </div>
        <h2>刪除群組</h2>
        <p>
          確定要刪除 <strong>{group.name}</strong> 嗎？成員帳號不會被刪除，但群組與成員關聯將移除。
        </p>
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            取消
          </button>
          <button type="button" className={styles.btnDanger} disabled={loading} onClick={onConfirm}>
            {loading ? "刪除中..." : "刪除"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── 加入成員 Modal ─────────────────────────────────────── */

function AddMembersModal({ loading, onClose, onSubmit }) {
  const [emailsText, setEmailsText] = useState("");

  function submit(e) {
    e.preventDefault();
    const emails = emailsText
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (emails.length === 0) return;
    onSubmit(emails);
  }

  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>加入成員</h2>
            <p>輸入 Email 列表（每行一個或逗號分隔），帳號需已存在。</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
            <MIcon name="close" size={18} />
          </button>
        </div>

        <label className={styles.field}>
          <span>Email 列表</span>
          <textarea
            value={emailsText}
            onChange={(e) => setEmailsText(e.target.value)}
            placeholder={"student1@example.com\nstudent2@example.com"}
            rows={6}
            required
          />
        </label>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            取消
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? "加入中..." : "加入"}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── CSV 匯入 Modal ─────────────────────────────────────── */

function ImportCsvModal({ groupId, onClose, onImported }) {
  const toast = useToast();
  const fileRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  async function handleImport() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setLoading(true);
    setResult(null);
    try {
      const data = await GroupsService.importCsv(groupId, file);
      setResult(data);
      toast.success(`匯入完成：新建 ${data.created.length} 人，加入群組 ${data.added_to_group} 人`);
      onImported();
    } catch (err) {
      toast.error(err?.message ?? "匯入失敗");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <div className={styles.modal} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>從 CSV 大量匯入學生</h2>
            <p>
              CSV 格式：學號, 姓名, 班級（支援 Big5 / UTF-8）。帳號不存在時自動建立，
              email 為 學號@ntub.edu.tw，系統將寄送通知信。
            </p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
            <MIcon name="close" size={18} />
          </button>
        </div>

        <label className={styles.field}>
          <span>選擇 CSV 檔案</span>
          <input type="file" accept=".csv" ref={fileRef} />
        </label>

        {result && (
          <div className={styles.importResult}>
            <p>新建帳號：<strong>{result.created.length}</strong> 人</p>
            <p>帳號已存在：<strong>{result.already_existed.length}</strong> 人</p>
            <p>加入群組：<strong>{result.added_to_group}</strong> 人</p>
            {result.errors.length > 0 && (
              <div className={styles.importErrors}>
                <p>錯誤（{result.errors.length}）：</p>
                <ul>
                  {result.errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            關閉
          </button>
          <button type="button" className={styles.btnPrimary} onClick={handleImport} disabled={loading}>
            {loading ? "匯入中..." : "匯入"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── 批量建立資源 Modal ─────────────────────────────────── */

const BATCH_STATUS_LABEL = {
  pending_review: "等待管理員審核",
  approved: "已通過，準備建立",
  rejected: "已被退回",
  cancelled: "已取消",
  pending: "排隊中",
  running: "建立中",
  completed: "已完成",
  failed: "失敗",
};

const BATCH_ACTIVE_STATUSES = new Set(["pending_review", "approved", "pending", "running"]);

function batchBadgeClass(status) {
  if (status === "completed") return styles.badge_success;
  if (status === "failed" || status === "rejected") return styles.badge_danger;
  if (status === "cancelled") return styles.badge_muted;
  return styles.badge_info;
}

function BatchProvisionModal({ groupId, memberCount, onClose }) {
  const toast = useToast();
  const [resourceType, setResourceType] = useState("lxc");
  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const [form, setForm] = useState({
    hostnamePrefix: "",
    password: "",
    cores: 2,
    memory: 2048,
    rootfsSize: 8,
    diskSize: 20,
    ostemplate: "",
    templateId: "",
    username: "",
    expiryDate: "",
  });

  const [lxcTemplates, setLxcTemplates] = useState([]);
  const [vmTemplates, setVmTemplates] = useState([]);

  useEffect(() => {
    if (resourceType === "lxc" && lxcTemplates.length === 0) {
      apiGet("/api/v1/lxc/templates").then(setLxcTemplates).catch(() => {});
    }
    if (resourceType === "qemu" && vmTemplates.length === 0) {
      apiGet("/api/v1/vm/templates").then(setVmTemplates).catch(() => {});
    }
  }, [resourceType, lxcTemplates.length, vmTemplates.length]);

  /* 送出後輪詢進度；審核中每 5 秒、建立中每 2 秒，終態停止 */
  useEffect(() => {
    if (!jobId) return undefined;
    let cancelled = false;
    let timer = null;

    async function poll() {
      try {
        const data = await BatchProvisionService.getStatus(jobId);
        if (cancelled) return;
        setJobStatus(data);
        if (BATCH_ACTIVE_STATUSES.has(data.status)) {
          timer = setTimeout(poll, data.status === "pending_review" ? 5000 : 2000);
        }
      } catch {
        if (!cancelled) timer = setTimeout(poll, 5000);
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  const canSubmit =
    form.hostnamePrefix.trim() &&
    form.password &&
    (resourceType === "lxc"
      ? Boolean(form.ostemplate)
      : Boolean(form.templateId) && form.username.trim());

  async function handleSubmit() {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const body = {
        resource_type: resourceType,
        hostname_prefix: form.hostnamePrefix.trim(),
        password: form.password,
        cores: form.cores,
        memory: form.memory,
        environment_type: "批量建立",
      };
      if (form.expiryDate) body.expiry_date = form.expiryDate;
      if (resourceType === "lxc") {
        body.ostemplate = form.ostemplate;
        body.rootfs_size = form.rootfsSize;
      } else {
        body.template_id = Number(form.templateId);
        body.username = form.username.trim();
        body.disk_size = form.diskSize;
      }
      const job = await BatchProvisionService.submit(groupId, body);
      setJobId(job.id);
    } catch (err) {
      toast.error(err?.message ?? "批量建立失敗");
    } finally {
      setSubmitting(false);
    }
  }

  const isRunning = jobStatus && BATCH_ACTIVE_STATUSES.has(jobStatus.status);
  const progressPct = jobStatus?.total
    ? Math.round(((jobStatus.done ?? 0) / jobStatus.total) * 100)
    : 0;

  return (
    <div className={styles.modalOverlay} onMouseDown={isRunning ? undefined : onClose}>
      <div className={`${styles.modal} ${styles.modalWide}`} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>批量建立資源</h2>
            <p>
              為群組 {memberCount} 位成員各建立一台資源並自動分配。Hostname 會自動加上流水號。
            </p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
            <MIcon name="close" size={18} />
          </button>
        </div>

        {!jobId ? (
          <>
            <div className={styles.noticeInfo}>
              <p><strong>送出後需經管理員審核</strong></p>
              <p>
                申請會先進入「待審核」，通過後才會開始建立。送出後可關閉視窗，進度可在群組頁或批量審核頁查看。
              </p>
            </div>

            <div className={styles.typeTabs}>
              <button
                type="button"
                className={resourceType === "lxc" ? styles.typeTabActive : styles.typeTab}
                onClick={() => setResourceType("lxc")}
              >
                LXC 容器
              </button>
              <button
                type="button"
                className={resourceType === "qemu" ? styles.typeTabActive : styles.typeTab}
                onClick={() => setResourceType("qemu")}
              >
                VM 虛擬機
              </button>
            </div>

            <div className={styles.formGrid}>
              <label className={styles.field}>
                <span>Hostname 前綴 *</span>
                <input
                  value={form.hostnamePrefix}
                  onChange={(e) => set("hostnamePrefix", e.target.value)}
                  placeholder={resourceType === "lxc" ? "webdev" : "lab-vm"}
                />
              </label>

              {resourceType === "lxc" ? (
                <label className={styles.field}>
                  <span>OS Template *</span>
                  <select value={form.ostemplate} onChange={(e) => set("ostemplate", e.target.value)}>
                    <option value="">選擇 OS 模板</option>
                    {lxcTemplates.map((t) => (
                      <option key={t.volid} value={t.volid}>
                        {t.volid.split("/").pop()?.replace(".tar.zst", "")}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <label className={styles.field}>
                  <span>OS Template *</span>
                  <select value={form.templateId} onChange={(e) => set("templateId", e.target.value)}>
                    <option value="">選擇 VM 模板</option>
                    {vmTemplates.map((t) => (
                      <option key={t.vmid} value={String(t.vmid)}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {resourceType === "qemu" && (
                <label className={styles.field}>
                  <span>用戶名 *</span>
                  <input
                    value={form.username}
                    onChange={(e) => set("username", e.target.value)}
                    placeholder="student"
                  />
                </label>
              )}

              <label className={styles.field}>
                <span>{resourceType === "lxc" ? "Root 密碼 *" : "密碼 *"}</span>
                <input
                  type="password"
                  value={form.password}
                  onChange={(e) => set("password", e.target.value)}
                  placeholder="統一密碼"
                />
              </label>

              <label className={styles.field}>
                <span>到期日</span>
                <input
                  type="date"
                  value={form.expiryDate}
                  onChange={(e) => set("expiryDate", e.target.value)}
                />
              </label>
            </div>

            <div className={styles.specCard}>
              <p className={styles.specTitle}>硬體規格</p>
              <label className={styles.sliderRow}>
                <span>CPU</span>
                <input
                  type="range"
                  min={1}
                  max={8}
                  step={1}
                  value={form.cores}
                  onChange={(e) => set("cores", Number(e.target.value))}
                />
                <strong>{form.cores} Cores</strong>
              </label>
              <label className={styles.sliderRow}>
                <span>記憶體</span>
                <input
                  type="range"
                  min={512}
                  max={32768}
                  step={512}
                  value={form.memory}
                  onChange={(e) => set("memory", Number(e.target.value))}
                />
                <strong>{(form.memory / 1024).toFixed(1)} GB</strong>
              </label>
              {resourceType === "lxc" ? (
                <label className={styles.sliderRow}>
                  <span>磁碟</span>
                  <input
                    type="range"
                    min={8}
                    max={500}
                    step={1}
                    value={form.rootfsSize}
                    onChange={(e) => set("rootfsSize", Number(e.target.value))}
                  />
                  <strong>{form.rootfsSize} GB</strong>
                </label>
              ) : (
                <label className={styles.sliderRow}>
                  <span>磁碟</span>
                  <input
                    type="range"
                    min={20}
                    max={500}
                    step={1}
                    value={form.diskSize}
                    onChange={(e) => set("diskSize", Number(e.target.value))}
                  />
                  <strong>{form.diskSize} GB</strong>
                </label>
              )}
            </div>

            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={submitting}>
                取消
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                onClick={handleSubmit}
                disabled={!canSubmit || submitting}
              >
                {submitting ? "送出中..." : `送出審核（${memberCount} 台）`}
              </button>
            </div>
          </>
        ) : (
          <>
            {jobStatus?.status === "pending_review" && (
              <div className={styles.noticeInfo}>
                <p><strong>已送出，等待管理員審核</strong></p>
                <p>資源尚未建立。可關閉此視窗，待管理員通過後系統會自動建立。</p>
              </div>
            )}
            {jobStatus?.status === "rejected" && (
              <div className={styles.noticeDanger}>
                <p><strong>申請已被退回</strong></p>
                {jobStatus.review_comment && <p>管理員留言：{jobStatus.review_comment}</p>}
              </div>
            )}

            <div className={styles.progressHead}>
              <span>
                {jobStatus?.done ?? 0} / {jobStatus?.total ?? memberCount} 台完成
              </span>
              <span className={`${styles.badge} ${batchBadgeClass(jobStatus?.status)}`}>
                {BATCH_STATUS_LABEL[jobStatus?.status] ?? "處理中"}
              </span>
            </div>
            <div className={styles.progressBar}>
              <div className={styles.progressFill} style={{ width: `${progressPct}%` }} />
            </div>

            <div className={styles.taskList}>
              {(jobStatus?.tasks ?? []).map((task) => (
                <div key={task.id} className={styles.taskRow}>
                  <span className={styles.taskIndex}>#{task.member_index}</span>
                  <div className={styles.taskMain}>
                    <span className={styles.rowName}>{task.user_name ?? "-"}</span>
                    <span className={styles.rowMeta}>{task.user_email}</span>
                  </div>
                  <span className={styles.rowMeta}>{task.vmid ?? "-"}</span>
                  <span
                    className={`${styles.badge} ${
                      task.status === "completed"
                        ? styles.badge_success
                        : task.status === "failed"
                          ? styles.badge_danger
                          : task.status === "running"
                            ? styles.badge_info
                            : styles.badge_muted
                    }`}
                    title={task.error ?? undefined}
                  >
                    {task.status === "completed" && "完成"}
                    {task.status === "failed" && "失敗"}
                    {task.status === "running" && "建立中"}
                    {task.status === "pending" && "等待"}
                  </span>
                </div>
              ))}
            </div>

            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={onClose}>
                {isRunning ? "關閉並於稍後查看" : "關閉"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ── 群組詳情 ───────────────────────────────────────────── */

function GroupDetail({ groupId, onBack }) {
  const toast = useToast();
  const [group, setGroup] = useState(null);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(null); // "add" | "csv" | "batch"
  const [savingMembers, setSavingMembers] = useState(false);
  const [removingId, setRemovingId] = useState(null);

  const fetchDetail = useCallback(async () => {
    try {
      const data = await GroupsService.detail(groupId);
      setGroup(data);
    } catch (err) {
      toast.error(err?.message ?? "載入群組詳情失敗");
    } finally {
      setLoading(false);
    }
  }, [groupId, toast]);

  useEffect(() => {
    fetchDetail();
  }, [fetchDetail]);

  const members = group?.members ?? [];

  const vmStats = useMemo(() => {
    const lxc = members.filter((m) => m.vm_type === "lxc" && m.vmid);
    const vm = members.filter((m) => m.vm_type === "qemu" && m.vmid);
    return {
      lxcRunning: lxc.filter((m) => m.vm_status === "running").length,
      lxcTotal: lxc.length,
      vmRunning: vm.filter((m) => m.vm_status === "running").length,
      vmTotal: vm.length,
    };
  }, [members]);

  async function handleAddMembers(emails) {
    setSavingMembers(true);
    try {
      const res = await GroupsService.addMembers(groupId, emails);
      const message = res?.message ?? "成員已加入";
      if (message.includes("Not found:")) {
        toast.error(message);
      } else {
        toast.success(message);
        setModal(null);
      }
      fetchDetail();
    } catch (err) {
      toast.error(err?.message ?? "加入成員失敗");
    } finally {
      setSavingMembers(false);
    }
  }

  async function handleRemoveMember(member) {
    setRemovingId(member.user_id);
    try {
      await GroupsService.removeMember(groupId, member.user_id);
      toast.success("成員已移除");
      setGroup((prev) => ({
        ...prev,
        members: (prev?.members ?? []).filter((m) => m.user_id !== member.user_id),
      }));
    } catch (err) {
      toast.error(err?.message ?? "移除失敗");
    } finally {
      setRemovingId(null);
    }
  }

  if (loading) {
    return <div className={styles.loading}>載入群組詳情...</div>;
  }
  if (!group) {
    return (
      <div className={styles.empty}>
        <p className={styles.emptyDesc}>找不到群組，可能已被刪除。</p>
        <button type="button" className={styles.btnSecondary} onClick={onBack}>
          返回列表
        </button>
      </div>
    );
  }

  return (
    <>
      <div className={styles.pageHeader}>
        <div className={styles.detailHeading}>
          <button type="button" className={styles.iconBtn} onClick={onBack} aria-label="返回">
            <MIcon name="arrow_back" size={20} />
          </button>
          <div className={styles.pageHeading}>
            <h1 className={styles.pageTitle}>{group.name}</h1>
            {group.description && <p className={styles.pageSubtitle}>{group.description}</p>}
          </div>
        </div>
        <div className={styles.headerActions}>
          <button type="button" className={styles.btnSecondary} onClick={() => setModal("csv")}>
            <MIcon name="upload" size={16} />
            匯入 CSV
          </button>
          <button type="button" className={styles.btnSecondary} onClick={() => setModal("add")}>
            <MIcon name="person_add" size={16} />
            加入成員
          </button>
          {members.length > 0 && (
            <button type="button" className={styles.btnPrimary} onClick={() => setModal("batch")}>
              <MIcon name="dns" size={16} />
              批量建立資源
            </button>
          )}
        </div>
      </div>

      <div className={styles.memberCard}>
        <div className={styles.memberCardHead}>
          <h2 className={styles.sectionTitle}>成員列表（{members.length} 人）</h2>
          <div className={styles.vmStats}>
            <span>LXC <strong>{vmStats.lxcRunning}/{vmStats.lxcTotal}</strong></span>
            <span>VM <strong>{vmStats.vmRunning}/{vmStats.vmTotal}</strong></span>
          </div>
        </div>

        {members.length === 0 ? (
          <p className={styles.emptyDesc}>尚無成員，點擊「加入成員」開始新增</p>
        ) : (
          <div className={styles.list}>
            {members.map((member) => (
              <div key={member.user_id} className={styles.memberRow}>
                <div className={styles.rowMain}>
                  <span className={styles.rowName}>{member.full_name ?? "-"}</span>
                  <span className={styles.rowMeta}>{member.email}</span>
                </div>
                <span className={styles.rowMeta}>{member.vmid ?? "—"}</span>
                <div className={styles.memberStatus}>
                  <VmStatusBadge vmid={member.vmid} status={member.vm_status} />
                  {member.vmid && member.vm_status === "running" && (
                    <span className={styles.usageGroup}>
                      <UsagePill label="CPU" value={member.vm_cpu_usage_pct} />
                      <UsagePill label="RAM" value={member.vm_ram_usage_pct} />
                      <UsagePill label="碟" value={member.vm_disk_usage_pct} />
                    </span>
                  )}
                </div>
                <span className={styles.createdAt}>{formatDate(member.added_at)}</span>
                <div className={styles.rowActions}>
                  <button
                    type="button"
                    className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                    title="移除成員"
                    disabled={removingId === member.user_id}
                    onClick={() => handleRemoveMember(member)}
                  >
                    <MIcon name="person_remove" size={16} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {modal === "add" && (
        <AddMembersModal
          loading={savingMembers}
          onClose={() => setModal(null)}
          onSubmit={handleAddMembers}
        />
      )}
      {modal === "csv" && (
        <ImportCsvModal
          groupId={groupId}
          onClose={() => setModal(null)}
          onImported={fetchDetail}
        />
      )}
      {modal === "batch" && (
        <BatchProvisionModal
          groupId={groupId}
          memberCount={members.length}
          onClose={() => {
            setModal(null);
            fetchDetail();
          }}
        />
      )}
    </>
  );
}

/* ── 主頁 ───────────────────────────────────────────────── */

export default function GroupsPage() {
  const toast = useToast();
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [selectedGroupId, setSelectedGroupId] = useState(null);

  const fetchGroups = useCallback(async () => {
    setLoading(true);
    try {
      const res = await GroupsService.list();
      setGroups(res?.data ?? []);
    } catch (err) {
      toast.error(err?.message ?? "載入群組失敗");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchGroups();
  }, [fetchGroups]);

  async function handleCreate(payload) {
    setCreating(true);
    try {
      const created = await GroupsService.create(payload);
      setGroups((prev) => [created, ...prev]);
      toast.success("群組已建立");
      setShowCreate(false);
    } catch (err) {
      toast.error(err?.message ?? "建立群組失敗");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await GroupsService.remove(deleteTarget.id);
      setGroups((prev) => prev.filter((g) => g.id !== deleteTarget.id));
      toast.success("群組已刪除");
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err?.message ?? "刪除失敗");
    } finally {
      setDeleting(false);
    }
  }

  if (selectedGroupId) {
    return (
      <div className={styles.page}>
        <GroupDetail groupId={selectedGroupId} onBack={() => {
          setSelectedGroupId(null);
          fetchGroups();
        }} />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>群組管理</h1>
          <p className={styles.pageSubtitle}>
            管理課程/班級群組，<span className={styles.accent}>批量分配虛擬機</span>
          </p>
        </div>
        <button type="button" className={styles.btnPrimary} onClick={() => setShowCreate(true)}>
          <MIcon name="add" size={16} />
          建立群組
        </button>
      </div>

      <div className={styles.content}>
        {loading ? (
          <div className={styles.loading}>載入群組...</div>
        ) : groups.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.list}>
            {groups.map((g) => (
              <div key={g.id} className={styles.row}>
                <div className={styles.rowIcon}>
                  <MIcon name="groups" size={20} />
                </div>
                <button type="button" className={styles.rowLink} onClick={() => setSelectedGroupId(g.id)}>
                  <span className={styles.rowName}>{g.name}</span>
                  <span className={styles.rowMeta}>
                    {g.member_count ?? 0} 位成員 · 建立於 {formatDate(g.created_at)}
                    {g.description ? ` · ${g.description}` : ""}
                  </span>
                </button>
                <div className={styles.rowActions}>
                  <button
                    type="button"
                    className={styles.actionBtn}
                    title="管理"
                    onClick={() => setSelectedGroupId(g.id)}
                  >
                    <MIcon name="settings" size={16} />
                  </button>
                  <button
                    type="button"
                    className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                    title="刪除"
                    onClick={() => setDeleteTarget(g)}
                  >
                    <MIcon name="delete" size={16} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateGroupModal
          loading={creating}
          onClose={() => setShowCreate(false)}
          onSubmit={handleCreate}
        />
      )}
      {deleteTarget && (
        <ConfirmDeleteGroup
          group={deleteTarget}
          loading={deleting}
          onClose={() => setDeleteTarget(null)}
          onConfirm={handleDelete}
        />
      )}
    </div>
  );
}
