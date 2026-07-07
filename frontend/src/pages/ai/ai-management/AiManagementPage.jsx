import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import styles from "./AiManagementPage.module.scss";
import MIcon from "../../../components/MIcon";
import { AiApiService } from "../../../services/aiApi";
import { AiMonitoringService } from "../../../services/aiMonitoring";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString("zh-TW") : "—";
}

const CRED_STATUS_LABELS = {
  active:   "啟用中",
  inactive: "未啟用",
  disabled: "已停用",
  revoked:  "已撤銷",
  expired:  "已過期",
};

const TABS = [
  { key: "credentials", label: "API 憑證",  icon: "vpn_key" },
  { key: "users",       label: "使用者統計", icon: "groups" },
];

const CRED_COLS = ["名稱", "擁有者", "速率限制", "建立時間", "到期時間", "狀態", "動作"];
const USR_COLS  = ["使用者", "啟用憑證", "總呼叫", "總 Tokens", "最近憑證"];

function EmptyState({ icon, title, desc }) {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name={icon} size={40} />
      </div>
      <h2 className={styles.emptyTitle}>{title}</h2>
      <p className={styles.emptyDesc}>{desc}</p>
    </div>
  );
}

function Badge({ status, labels }) {
  const label = labels[status] ?? status ?? "—";
  return (
    <span className={`${styles.badge} ${styles[`badge_${status ?? "unknown"}`]}`}>
      <span className={styles.dot} />
      {label}
    </span>
  );
}

function formatTokens(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export default function AiManagementPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [tab, setTab] = useState("credentials");
  const [query, setQuery] = useState("");
  const [credentials, setCredentials] = useState([]);
  const [requests, setRequests] = useState([]);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [credRes, reqRes, usersRes] = await Promise.all([
        AiApiService.listAllCredentials(),
        AiApiService.listAllRequests(),
        AiMonitoringService.listUsersUsage({ limit: 200 }).catch(() => ({ data: [] })),
      ]);
      setCredentials(credRes?.data ?? []);
      setRequests(reqRes?.data ?? []);
      setUsers(usersRes?.data ?? []);
    } catch (e) {
      if (!silent) toast.error(e?.message ?? "載入 AI 管理資料失敗");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  const revokeCred = async (id) => {
    if (!window.confirm("確定撤銷此憑證?")) return;
    try {
      await AiApiService.revokeCredential(id);
      toast.success("已撤銷");
      load();
    } catch (e) {
      toast.error(e?.message ?? "撤銷失敗");
    }
  };

  const stats = useMemo(
    () => ({
      activeCreds: credentials.filter((c) => c.status === "active").length,
      pendingReqs: requests.filter((r) => r.status === "pending").length,
      revokedCreds: credentials.filter(
        (c) => c.status === "inactive" || c.inactive_reason,
      ).length,
    }),
    [credentials, requests],
  );

  const visibleCreds = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return credentials;
    return credentials.filter(
      (c) =>
        (c.api_key_name ?? "").toLowerCase().includes(q) ||
        (c.api_key_prefix ?? "").toLowerCase().includes(q) ||
        (c.user_email ?? "").toLowerCase().includes(q) ||
        (c.user_full_name ?? "").toLowerCase().includes(q),
    );
  }, [credentials, query]);

  const visibleUsers = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        (u.user_email ?? "").toLowerCase().includes(q) ||
        (u.user_full_name ?? "").toLowerCase().includes(q),
    );
  }, [users, query]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>AI 管理</h1>
          <p className={styles.pageSubtitle}>管理 AI API 憑證與檢視使用者統計；金鑰申請請至「申請審核」處理</p>
        </div>
      </div>

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="key" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>啟用中憑證</span>
            <span className={styles.statValue}>{stats.activeCreds}</span>
          </div>
        </div>
        <div
          className={styles.statCard}
          role="link"
          tabIndex={0}
          style={{ cursor: "pointer" }}
          title="前往申請審核"
          onClick={() => navigate("/request-review")}
          onKeyDown={(e) => { if (e.key === "Enter") navigate("/request-review"); }}
        >
          <div className={`${styles.statIcon} ${styles.statIconWarn}`}>
            <MIcon name="pending_actions" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>待審核申請（前往審核）</span>
            <span className={styles.statValue}>{stats.pendingReqs}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconErr}`}>
            <MIcon name="block" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>已停用 / 撤銷</span>
            <span className={styles.statValue}>{stats.revokedCreds}</span>
          </div>
        </div>
      </div>

      <div className={styles.tabs}>
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`${styles.tab} ${tab === t.key ? styles.tabActive : ""}`}
            onClick={() => setTab(t.key)}
          >
            <MIcon name={t.icon} size={16} />
            {t.label}
          </button>
        ))}
      </div>

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <MIcon name="search" size={16} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder={tab === "credentials" ? "搜尋憑證名稱或擁有者" : "搜尋使用者"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      <div className={styles.content}>
        {tab === "credentials" &&
          (visibleCreds.length === 0 ? (
            <EmptyState
              icon="vpn_key"
              title="尚未建立任何憑證"
              desc="建立 API 憑證後即可分配給使用者呼叫 AI 服務"
            />
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    {CRED_COLS.map((c) => (
                      <th key={c} className={styles.th}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleCreds.map((c) => (
                    <tr key={c.id} className={styles.tr}>
                      <td className={styles.td}>
                        <div className={styles.nameCell}>
                          <div className={styles.nameIcon}>
                            <MIcon name="vpn_key" size={18} />
                          </div>
                          <div>
                            <div className={styles.namePrimary}>{c.api_key_name}</div>
                            <div className={styles.nameSub}>{c.api_key_prefix}</div>
                          </div>
                        </div>
                      </td>
                      <td className={styles.td}>{c.user_email ?? c.user_full_name ?? "—"}</td>
                      <td className={styles.td}>{c.rate_limit != null ? `${c.rate_limit}/min` : "—"}</td>
                      <td className={styles.td}>{fmtDate(c.created_at)}</td>
                      <td className={styles.td}>{c.expires_at ? fmtDate(c.expires_at) : "永久"}</td>
                      <td className={styles.td}>
                        <Badge
                          status={c.inactive_reason ?? c.status}
                          labels={CRED_STATUS_LABELS}
                        />
                      </td>
                      <td className={styles.td}>
                        <div className={styles.actions}>
                          <button
                            type="button"
                            className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                            title="撤銷"
                            disabled={c.status !== "active"}
                            onClick={() => revokeCred(c.id)}
                          >
                            <MIcon name="delete" size={16} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

        {tab === "users" &&
          (visibleUsers.length === 0 ? (
            <EmptyState
              icon="groups"
              title="尚無使用者統計"
              desc="使用者開始呼叫 AI 服務後,統計資料會出現在這裡"
            />
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    {USR_COLS.map((c) => (
                      <th key={c} className={styles.th}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleUsers.map((u) => {
                    const activeCreds = credentials.filter(
                      (c) => c.user_id === u.user_id && c.status === "active",
                    ).length;
                    const totalCalls = (u.proxy_calls ?? 0) + (u.template_calls ?? 0);
                    const totalTokens =
                      (u.proxy_input_tokens ?? 0) +
                      (u.proxy_output_tokens ?? 0) +
                      (u.template_input_tokens ?? 0) +
                      (u.template_output_tokens ?? 0);
                    const latestCredential = credentials
                      .filter((c) => c.user_id === u.user_id)
                      .map((c) => c.created_at)
                      .filter(Boolean)
                      .sort()
                      .at(-1);

                    return (
                      <tr key={u.user_id} className={styles.tr}>
                        <td className={styles.td}>
                          <div className={styles.nameCell}>
                            <div className={styles.nameIcon}>
                              <MIcon name="person" size={18} />
                            </div>
                            <div>
                              <div className={styles.namePrimary}>
                                {u.user_full_name || u.user_email || u.user_id}
                              </div>
                              <div className={styles.nameSub}>{u.user_email ?? u.user_id}</div>
                            </div>
                          </div>
                        </td>
                        <td className={styles.td}>{activeCreds}</td>
                        <td className={styles.td}>{totalCalls}</td>
                        <td className={styles.td}>{formatTokens(totalTokens)}</td>
                        <td className={styles.td}>{fmtDate(latestCredential)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
      </div>
    </div>
  );
}
