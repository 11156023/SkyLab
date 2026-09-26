/**
 * SharingCard — 共享與轉移
 * 授權同學開關機／開主控台（共享），或把整台機器交給別人（轉移）。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import styles from "../ResourceDetailPage.module.scss";
import MIcon from "../../../../../components/MIcon";
import Modal from "../../../../../components/Modal/Modal";
import LoadingState from "../../../../../components/LoadingState/LoadingState";
import useDialogPresence from "../../../../../hooks/useDialogPresence";
import { useToast } from "../../../../../hooks/useToast";
import { useConfirm } from "../../../../../components/ConfirmDialog/ConfirmProvider";
import { ResourcesService } from "../../../../../services/resources";

function TransferModal({ resource, closing, loading, onClose, onSubmit }) {
  const { t } = useTranslation("personal");
  const [email, setEmail] = useState("");
  const [keepAccess, setKeepAccess] = useState(true);
  const [confirmName, setConfirmName] = useState("");
  const ready = email.trim().length > 3 && confirmName.trim() === resource?.name;

  function submit(e) {
    e.preventDefault();
    if (!ready) return;
    onSubmit({ email: email.trim(), keepAccess });
  }

  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={loading}
      title={t("SharingCard.transferTitle")}
      description={t("SharingCard.transferDesc")}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            {t("SharingCard.cancel")}
          </button>
          <button type="submit" className={styles.btnDanger} disabled={loading || !ready}>
            {loading ? t("SharingCard.processing") : t("SharingCard.transfer")}
          </button>
        </>
      }
    >
      <div className={styles.field}>
        <label htmlFor="xfer-email">{t("SharingCard.transferEmailLabel")}</label>
        <input id="xfer-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t("SharingCard.emailPlaceholder")} required />
      </div>
      <label className={styles.checkRow}>
        <input type="checkbox" checked={keepAccess} onChange={(e) => setKeepAccess(e.target.checked)} />
        <span>{t("SharingCard.keepAccess")}</span>
      </label>
      <div className={styles.field}>
        <label htmlFor="xfer-confirm">{t("SharingCard.transferConfirmLabel", { name: resource?.name })}</label>
        <input id="xfer-confirm" value={confirmName} onChange={(e) => setConfirmName(e.target.value)} placeholder={resource?.name} />
      </div>
    </Modal>
  );
}

export default function SharingCard({ vmid, resource, canManage, backTo }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [shares, setShares] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [showTransfer, setShowTransfer] = useState(false);
  const transferPresence = useDialogPresence(showTransfer);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setShares((await ResourcesService.listShares(vmid)) ?? []);
    } catch (err) {
      toast.error(err?.message ?? t("Error.generic", { ns: "common" }));
    } finally {
      setLoading(false);
    }
  }, [vmid, toast, t]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAdd(e) {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    try {
      await ResourcesService.addShare(vmid, email.trim());
      toast.success(t("SharingCard.shared", { email: email.trim() }));
      setEmail("");
      await load();
    } catch (err) {
      toast.error(err?.message ?? t("SharingCard.shareFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(share) {
    const ok = await confirm({
      title: t("SharingCard.revokeTitle"),
      message: t("SharingCard.revokeMessage", { email: share.user_email }),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await ResourcesService.removeShare(vmid, share.id);
      toast.success(t("SharingCard.revoked"));
      await load();
    } catch (err) {
      toast.error(err?.message ?? t("SharingCard.revokeFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleTransfer({ email: target, keepAccess }) {
    setBusy(true);
    try {
      const res = await ResourcesService.transferOwnership(vmid, target, keepAccess);
      toast.success(res.message);
      setShowTransfer(false);
      navigate(backTo ?? "/my-resources");
    } catch (err) {
      toast.error(err?.message ?? t("SharingCard.transferFailed"));
    } finally {
      setBusy(false);
    }
  }

  const classGoverned = resource?.allocation_scope === "teaching_class";

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="group" size={18} />
            {t("SharingCard.title")}
          </h2>
          {/* 卡片層級的說明放在大標下方（cardDesc），不在內文自成一行 */}
          {!classGoverned && canManage && <p className={styles.cardDesc}>{t("SharingCard.scopeNote")}</p>}
        </div>
      </div>
      <div className={styles.cardBody}>
        {classGoverned ? (
          <p className={styles.mutedText}>{t("SharingCard.classGoverned")}</p>
        ) : loading ? (
          <LoadingState text={t("SharingCard.loading")} />
        ) : (
          <>
            {canManage && (
              <div className={styles.shareForm}>
                <form className={styles.inlineForm} onSubmit={handleAdd}>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder={t("SharingCard.emailPlaceholder")}
                    aria-label={t("SharingCard.emailPlaceholder")}
                    disabled={busy}
                  />
                  <button type="submit" className={styles.btnSecondary} disabled={busy || !email.trim()}>
                    <MIcon name="person_add" size={16} />
                    {t("SharingCard.share")}
                  </button>
                </form>
              </div>
            )}

            {/* 還沒共享時，上方的輸入框本身就說明了狀態，不另寫「還沒有共享給任何人」 */}
            {shares.length === 0 ? (
              !canManage && <p className={styles.mutedText}>{t("SharingCard.noShares")}</p>
            ) : (
              <div className={styles.rowStack}>
                <span className={styles.factLabel}>{t("SharingCard.sharedWith", { count: shares.length })}</span>
              <div className={styles.keyList}>
                {shares.map((share) => (
                  <div key={share.id} className={styles.keyItem}>
                    <MIcon name="person" size={16} />
                    <span className={styles.rpMain}>
                      <span className={styles.rpDomain}>{share.user_full_name || share.user_email}</span>
                      <span className={styles.rpMeta}>
                        {share.user_email}
                        <span className={`${styles.badge} ${styles.badge_info}`}>{t("SharingCard.permissionControl")}</span>
                      </span>
                    </span>
                    {canManage && (
                      <button
                        type="button"
                        className={`${styles.rpIconBtn} ${styles.rpIconBtnDanger}`}
                        disabled={busy}
                        title={t("SharingCard.revoke")}
                        onClick={() => handleRemove(share)}
                      >
                        <MIcon name="person_remove" size={16} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
              </div>
            )}

            {/* 轉移是不可逆的危險操作：不放標題列跟一般操作搶位置，收在卡片底部的白底列 */}
            {canManage && (
              <div className={styles.transferRow}>
                <div className={styles.transferRowText}>
                  <span className={styles.transferRowTitle}>{t("SharingCard.transferTitle")}</span>
                  <span className={styles.mutedText}>{t("SharingCard.transferZoneDesc")}</span>
                </div>
                <button type="button" className={styles.btnDangerOutline} disabled={busy} onClick={() => setShowTransfer(true)}>
                  <MIcon name="swap_horiz" size={16} />
                  {t("SharingCard.transferButton")}
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {transferPresence.open && (
        <TransferModal
          resource={resource}
          closing={transferPresence.closing}
          loading={busy}
          onClose={() => setShowTransfer(false)}
          onSubmit={handleTransfer}
        />
      )}
    </div>
  );
}
