/**
 * LoginPreflightPage — 每次登入後的服務檢查畫面。
 *
 * 登入成功時 AuthContext 立起 loginPreflightPending，App 在所有其他閘門之前只渲染這一頁。
 * 底層打 GET /users/me/preflight，真的檢查 DB、Redis、worker、PVE、Gateway、AI：
 * - 學生／老師：看到包裝過的冒險文案；有任何一項失敗就警告「部分功能可能無法正常使用」，
 *   可以重新檢查、登出，或仍要繼續進入（並請他們通知管理員）
 * - 管理員：看到真實服務名稱與錯誤細節，失敗時可以略過繼續，或直接前往資源監控
 * 全部通過就自動進入系統。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import { useAuth } from "../../contexts/AuthContext";
import { AccountService } from "../../services/account";
import shell from "../setup/SetupPage.module.scss";
import styles from "./LoginPreflightPage.module.scss";

const CHECK_KEYS = ["database", "redis", "worker", "pve", "gateway", "ai"];

/* 管理員看的真實名稱（產品名不翻譯）；細節列用後端回的元件 label */
const ADMIN_LABELS = {
  database: "PostgreSQL",
  redis: "Redis",
  worker: "Worker (arq)",
  pve: "Proxmox VE",
  gateway: "Gateway",
  ai: "AI Gateway (LiteLLM)",
};

/* 圈圈至少轉這麼久，太快跑完反而像沒檢查 */
const MIN_CHECK_MS = 900;
/* 結果回來後逐項打勾的間隔 */
const REVEAL_STEP_MS = 260;
/* 全部通過後停一下讓人看到「準備完成」再進入 */
const DONE_PAUSE_MS = 700;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function isAdminUser(user) {
  return Boolean(user?.is_superuser || user?.role === "admin");
}

/** 單項的狀態：checking（圈圈轉）／ok／fail／skipped */
function rowStatus(key, index, state) {
  if (state.phase === "error") return "fail";
  if (index >= state.revealed) return "checking";
  const check = state.result?.checks?.find((item) => item.key === key);
  return check?.status ?? "skipped";
}

function StatusRing({ status }) {
  return (
    <span className={`${styles.ring} ${styles[`ring_${status}`]}`} aria-hidden="true">
      {status === "ok" && <MIcon name="check" size={16} />}
      {status === "fail" && <MIcon name="close" size={16} />}
      {status === "skipped" && <MIcon name="remove" size={16} />}
    </span>
  );
}

function formatComponent(component, t) {
  const parts = [];
  if (component.detail) parts.push(component.detail);
  if (component.latency_ms != null && component.status !== "down") {
    parts.push(t("LoginPreflight.latency", { ms: Math.round(component.latency_ms) }));
  }
  return parts.join(" · ");
}

export default function LoginPreflightPage() {
  const { t } = useTranslation("login");
  const navigate = useNavigate();
  const { user, logout, finishLoginPreflight } = useAuth();
  const admin = isAdminUser(user);
  const [state, setState] = useState({ phase: "checking", result: null, revealed: 0, error: null });
  const runRef = useRef(0);

  const run = useCallback(
    async ({ refresh = false } = {}) => {
      const id = ++runRef.current;
      const alive = () => runRef.current === id;
      setState({ phase: "checking", result: null, revealed: 0, error: null });

      let result;
      try {
        [result] = await Promise.all([AccountService.preflight({ refresh }), wait(MIN_CHECK_MS)]);
      } catch (error) {
        if (!alive()) return;
        // 舊版後端沒有這個端點：不能因此擋住所有人
        if (error?.status === 404) {
          finishLoginPreflight();
          return;
        }
        setState({ phase: "error", result: null, revealed: CHECK_KEYS.length, error });
        return;
      }

      for (let revealed = 1; revealed <= CHECK_KEYS.length; revealed += 1) {
        if (!alive()) return;
        setState({ phase: "checking", result, revealed, error: null });
        await wait(REVEAL_STEP_MS);
      }
      if (!alive()) return;

      if (result?.ok) {
        setState({ phase: "passed", result, revealed: CHECK_KEYS.length, error: null });
        await wait(DONE_PAUSE_MS);
        if (alive()) finishLoginPreflight();
      } else {
        setState({ phase: "failed", result, revealed: CHECK_KEYS.length, error: null });
      }
    },
    [finishLoginPreflight],
  );

  useEffect(() => {
    void run();
    return () => {
      runRef.current += 1;
    };
  }, [run]);

  const checking = state.phase === "checking";
  const failed = state.phase === "failed" || state.phase === "error";
  const role = admin ? "admin" : "user";

  let title = t(`LoginPreflight.${role}.title`);
  if (state.phase === "passed") title = t(`LoginPreflight.${role}.passedTitle`);
  if (state.phase === "failed") title = t(`LoginPreflight.${role}.failedTitle`);
  if (state.phase === "error") {
    title = admin ? t("LoginPreflight.admin.errorTitle") : t("LoginPreflight.user.failedTitle");
  }

  const skip = () => finishLoginPreflight();
  const openMonitoring = () => {
    finishLoginPreflight();
    navigate("/monitoring");
  };

  return (
    <main className={shell.page}>
      <div className={shell.glow} aria-hidden="true">
        <span />
        <span />
        <span />
      </div>

      <section
        className={`${shell.card} ${styles.card} ${admin ? styles.card_admin : ""}`}
        aria-busy={checking}
      >
        <header className={shell.header}>
          <span
            className={`${styles.hero} ${failed ? styles.hero_fail : ""} ${
              state.phase === "passed" ? styles.hero_ok : ""
            }`}
            aria-hidden="true"
          >
            <MIcon
              name={failed ? "error" : admin ? "monitor_heart" : "rocket_launch"}
              size={32}
            />
          </span>
          <h1 className={shell.title}>{title}</h1>
          {!failed && (
            <p className={styles.subtitle}>
              {state.phase === "passed"
                ? t(`LoginPreflight.${role}.passedDesc`)
                : t(`LoginPreflight.${role}.desc`)}
            </p>
          )}
        </header>

        <ul className={styles.checklist} aria-label={t("LoginPreflight.checklistLabel")}>
          {CHECK_KEYS.map((key, index) => {
            const status = rowStatus(key, index, state);
            // 學生／老師看不到「沒設定」這件事：沒啟用的功能對他們來說就是沒問題
            const shown = !admin && status === "skipped" ? "ok" : status;
            const check = state.result?.checks?.find((item) => item.key === key);
            const components = admin && index < state.revealed ? check?.components ?? [] : [];
            return (
              <li key={key} className={`${styles.row} ${styles[`row_${shown}`]}`}>
                <StatusRing status={shown} />
                <div className={styles.rowBody}>
                  <div className={styles.rowHead}>
                    <span className={styles.rowLabel}>
                      {admin ? ADMIN_LABELS[key] : t(`LoginPreflight.steps.${key}`)}
                    </span>
                    {admin && (
                      <span className={`${styles.rowStatus} ${styles[`rowStatus_${shown}`]}`}>
                        {t(`LoginPreflight.status.${shown}`)}
                      </span>
                    )}
                  </div>
                  {components.length > 0 && (
                    <ul className={styles.components}>
                      {components.map((component, i) => {
                        const text = formatComponent(component, t);
                        // 只有一個元件時名稱就是這一列本身，不重複顯示；多個（多條 PVE 連線）才逐一標名
                        const showLabel = components.length > 1;
                        if (!showLabel && !text) return null;
                        const broken = component.status === "down" || component.status === "unknown";
                        return (
                          <li key={`${component.label}-${i}`}>
                            {showLabel && (
                              <span className={styles.componentLabel}>{component.label}</span>
                            )}
                            {text && (
                              <span
                                className={`${styles.componentDetail} ${
                                  broken ? styles.componentDetail_fail : ""
                                }`}
                              >
                                {text}
                              </span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              </li>
            );
          })}
        </ul>

        {failed && (
          <div className={`${shell.notice} ${styles.notice_fail}`} role="alert">
            <MIcon name={admin ? "info" : "warning"} size={20} />
            <span>
              {admin
                ? state.phase === "error"
                  ? t("LoginPreflight.admin.errorNotice", {
                      message: state.error?.message || String(state.error?.status ?? ""),
                    })
                  : t("LoginPreflight.admin.failedNotice")
                : t("LoginPreflight.user.failedNotice")}
            </span>
          </div>
        )}

        {failed && (
          <div className={shell.actions}>
            <button type="button" className={shell.btnGhost} onClick={logout}>
              <MIcon name="logout" size={18} />
              {t("LoginPreflight.logout")}
            </button>
            <div className={shell.actionGroup}>
              <button
                type="button"
                className={shell.btnSecondary}
                onClick={() => run({ refresh: admin })}
              >
                <MIcon name="refresh" size={18} />
                {t("LoginPreflight.retry")}
              </button>
              {admin && (
                <button type="button" className={shell.btnSecondary} onClick={openMonitoring}>
                  <MIcon name="monitor_heart" size={18} />
                  {t("LoginPreflight.admin.openMonitoring")}
                </button>
              )}
              {/* 學生／老師也能帶著警告繼續；壞掉的功能在各頁面自己會報錯 */}
              <button type="button" className={shell.btnPrimary} onClick={skip}>
                {t(`LoginPreflight.${role}.skip`)}
                <MIcon name="arrow_forward" size={18} />
              </button>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
