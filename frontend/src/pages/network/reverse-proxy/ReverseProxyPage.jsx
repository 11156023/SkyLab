import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { Trans, useTranslation } from "react-i18next";
import styles from "./ReverseProxyPage.module.scss";
import useDialogPresence from "../../../hooks/useDialogPresence";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { ReverseProxyService } from "../../../services/reverseProxy";
import ReverseProxyRuleModal from "../../../components/ReverseProxyRuleModal/ReverseProxyRuleModal";

function isAdminUser(user) {
  return user?.role === "admin" || user?.is_superuser === true;
}

/* ── Traefik Runtime（Admin） ───────────────────────── */
function TraefikPanel() {
  const { t } = useTranslation("network");
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || snapshot) return;
    setLoading(true);
    ReverseProxyService.runtime()
      .then(setSnapshot)
      .catch(() => setSnapshot({ runtime_error: t("ReverseProxyPage.traefik.connectFailed") }))
      .finally(() => setLoading(false));
  }, [open, snapshot, t]);

  const sections = snapshot
    ? [
        { label: "HTTP", data: snapshot.http },
        { label: "TCP", data: snapshot.tcp },
        { label: "UDP", data: snapshot.udp },
      ]
    : [];

  return (
    <div className={styles.adminCard}>
      <button
        type="button"
        className={styles.adminToggle}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={styles.adminToggleLeft}>
          <MIcon name="security" size={16} />
          {t("ReverseProxyPage.traefik.toggle")}
          <span className={styles.adminBadge}>Admin</span>
        </span>
        <span className={`${styles.infoChevron} ${open ? styles.open : ""}`}>
          <MIcon name="expand_more" size={18} />
        </span>
      </button>

      {open && (
        <div className={styles.adminBody}>
          {loading ? (
            <LoadingState text={t("ReverseProxyPage.traefik.loading")} />
          ) : snapshot?.runtime_error ? (
            <div className={styles.adminMeta}>
              <span className={`${styles.statusPill} ${styles.unknown}`}>
                {snapshot.runtime_error}
              </span>
            </div>
          ) : snapshot ? (
            <>
              <div className={styles.adminMeta}>
                <span className={`${styles.statusPill} ${styles.running}`}>
                  Traefik {snapshot.version?.Version ?? "running"}
                </span>
                <span className={styles.statusPill}>
                  {(snapshot.entrypoints ?? []).length} entrypoints
                </span>
              </div>

              <div className={styles.statsGrid}>
                {sections.map(({ label, data }) => (
                  <div key={label} className={styles.statCard}>
                    <span className={styles.statLabel}>{label}</span>
                    <dl className={styles.statList}>
                      <div>
                        <dt>Routers</dt>
                        <dd className={data?.routers?.length ? styles.numActive : styles.numZero}>
                          {data?.routers?.length ?? 0}
                        </dd>
                      </div>
                      <div>
                        <dt>Services</dt>
                        <dd className={data?.services?.length ? styles.numActive : styles.numZero}>
                          {data?.services?.length ?? 0}
                        </dd>
                      </div>
                      <div>
                        <dt>Middlewares</dt>
                        <dd className={data?.middlewares?.length ? styles.numActive : styles.numZero}>
                          {data?.middlewares?.length ?? 0}
                        </dd>
                      </div>
                    </dl>
                  </div>
                ))}
              </div>

              <div className={styles.entrySection}>
                <span className={styles.entrySectionLabel}>Entrypoints</span>
                <div className={styles.entryList}>
                  {(snapshot.entrypoints ?? []).map((ep) => (
                    <code key={ep.name ?? JSON.stringify(ep)} className={styles.entryChip}>
                      {ep.name} ({ep.address ?? ep.addr ?? "?"})
                    </code>
                  ))}
                </div>
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

/* ── Panel（嵌在網域管理頁的「對外網址」分頁；管理員總覽所有 VM 的對外網址） ── */
export function ReverseProxyPanel() {
  const { t } = useTranslation("network");
  const { user } = useAuth();
  const toast = useToast();
  const isAdmin = isAdminUser(user);

  const [rules, setRules] = useState([]);
  const [setupContext, setSetupContext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [modal, setModal] = useState(null); // { kind: "rule", rule? } | { kind: "delete", rule }
  const modalPresence = useDialogPresence(modal);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [rulesRes, ctxRes] = await Promise.all([
        ReverseProxyService.listRules(),
        ReverseProxyService.setupContext().catch(() => null),
      ]);
      setRules(rulesRes ?? []);
      if (ctxRes) setSetupContext(ctxRes);
    } catch (err) {
      toast.error(err?.message ?? t("ReverseProxyPage.loadListFailed"));
    } finally {
      setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const setupBlocked = setupContext?.enabled === false;
  async function handleSubmitRule(payload) {
    setSaving(true);
    try {
      if (modal?.rule) {
        await ReverseProxyService.updateRule(modal.rule.id, payload);
        toast.success(t("ReverseProxyPage.updateSuccess"));
      } else {
        await ReverseProxyService.createRule(payload);
        toast.success(t("ReverseProxyPage.createSuccess"));
      }
      setModal(null);
      fetchData();
    } catch (err) {
      toast.error(err?.message ?? t("ReverseProxyPage.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteRule() {
    if (!modal?.rule) return;
    setSaving(true);
    try {
      await ReverseProxyService.deleteRule(modal.rule.id);
      toast.success(t("ReverseProxyPage.deleteSuccess"));
      setModal(null);
      fetchData();
    } catch (err) {
      toast.error(err?.message ?? t("ReverseProxyPage.deleteFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    try {
      const res = await ReverseProxyService.syncRules();
      toast.success(res?.message ?? t("ReverseProxyPage.syncSuccess"));
    } catch (err) {
      toast.error(err?.message ?? t("ReverseProxyPage.syncFailed"));
    } finally {
      setSyncing(false);
    }
  }

  function openCreate() {
    if (setupBlocked) {
      toast.error(setupContext?.reasons?.[0] ?? t("ReverseProxyPage.featureDisabled"));
      return;
    }
    setModal({ kind: "rule" });
  }

  return (
    <div className={styles.panel}>
      {setupBlocked && (
        <div className={styles.noticeDanger}>
          <p><strong>{t("ReverseProxyPage.featureDisabled")}</strong></p>
          <p>{(setupContext?.reasons ?? []).join("；") || t("ReverseProxyPage.setupIncomplete")}</p>
        </div>
      )}

      {/* 清單卡片：頁首由網域管理頁提供，這裡只放「標題＋筆數」與動作列，下面接網址列表 */}
      <section className={styles.listCard}>
        <div className={styles.listToolbar}>
          <div className={styles.listHeading}>
            <h2 className={styles.listTitle}>{t("ReverseProxyPage.listTitle")}</h2>
            {!loading && (
              <span className={styles.listCount}>
                {t("ReverseProxyPage.listCount", { count: rules.length })}
              </span>
            )}
          </div>
          <div className={styles.headerActions}>
            {isAdmin && (
              <button type="button" className={styles.btnSecondary} onClick={handleSync} disabled={syncing}>
                <MIcon name="sync" size={16} />
                {syncing ? t("ReverseProxyPage.syncing") : t("ReverseProxyPage.resync")}
              </button>
            )}
            <button type="button" className={styles.btnPrimary} onClick={openCreate} data-guide="proxy-create">
              <MIcon name="add" size={16} />
              {t("ReverseProxyPage.addDomain")}
            </button>
          </div>
        </div>

        <p className={styles.listDesc}>{t("ReverseProxyPage.listDesc")}</p>

        <div className={styles.listBody} data-guide="proxy-list">
          {loading ? (
            <LoadingState text={t("ReverseProxyPage.loadingList")} />
          ) : rules.length === 0 ? (
            <EmptyState icon="swap_horiz" title={t("ReverseProxyPage.emptyTitle")} />
          ) : (
            <div className={styles.list}>
              {rules.map((rule) => (
                <div key={rule.id} className={styles.row}>
                  <div className={styles.rowIcon}>
                    <MIcon name="swap_horiz" size={20} />
                  </div>
                  <div className={styles.rowMain}>
                    <span className={styles.rowName}>{rule.domain}</span>
                    <span className={styles.rowMeta}>
                      {t("ReverseProxyPage.rowMeta", { vmid: rule.vmid, ip: rule.vm_ip, port: rule.internal_port })}
                      {rule.enable_https && (
                        <span className={styles.badge}>
                          <MIcon name="lock" size={11} /> HTTPS
                        </span>
                      )}
                    </span>
                  </div>
                  <a
                    className={styles.rowStatus}
                    href={`${rule.enable_https ? "https" : "http"}://${rule.domain}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <MIcon name="open_in_new" size={14} />
                    {t("ReverseProxyPage.open")}
                  </a>
                  <div className={styles.rowActions}>
                    <button
                      type="button"
                      className={styles.actionBtn}
                      title={t("ReverseProxyPage.edit")}
                      onClick={() => setModal({ kind: "rule", rule })}
                    >
                      <MIcon name="edit" size={16} />
                    </button>
                    <button
                      type="button"
                      className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                      title={t("ReverseProxyPage.delete")}
                      onClick={() => setModal({ kind: "delete", rule })}
                    >
                      <MIcon name="delete" size={16} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* Admin: Traefik */}
      {isAdmin && <TraefikPanel />}

      {modalPresence.item?.kind === "rule" && (
        <ReverseProxyRuleModal
          rule={modalPresence.item.rule}
          setupContext={setupContext}
          isAdmin={isAdmin}
          loading={saving}
          onClose={() => setModal(null)}
          onSubmit={handleSubmitRule}
          closing={modalPresence.closing}
        />
      )}
      {modalPresence.item?.kind === "delete" && (
        <div
          className={`${styles.modalOverlay} ${modalPresence.closing ? styles.modalOverlayOut : ""}`}
          onMouseDown={() => setModal(null)}
        >
          <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
            <div className={styles.confirmIcon}>
              <MIcon name="warning" size={24} />
            </div>
            <h2>{t("ReverseProxyPage.deleteDomainTitle")}</h2>
            <p>
              <Trans
                i18nKey="ReverseProxyPage.deleteDomainConfirm"
                ns="network"
                values={{ domain: modalPresence.item.rule.domain }}
                components={{ strong: <strong /> }}
              />
            </p>
            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => setModal(null)}>
                {t("ReverseProxyPage.cancel")}
              </button>
              <button type="button" className={styles.btnDanger} disabled={saving} onClick={handleDeleteRule}>
                {saving ? t("ReverseProxyPage.deleting") : t("ReverseProxyPage.delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 舊網址：獨立頁已併入網域管理（管理員），直接導過去 ── */
export default function ReverseProxyPage() {
  return <Navigate to="/domain?tab=reverse-proxy" replace />;
}
