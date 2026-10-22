/**
 * BootOptionsCard — 開機選項
 * 開機順序與 ISO 掛載（QEMU）。
 * 「主機開機時自動啟動」(onboot) 不開放設定：後端在每次開關機後自動對齊
 * （開機→啟用、關機→停用），卡片上只用一行說明告知。
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "../ResourceDetailPage.module.scss";
import MIcon from "../../../../../components/MIcon";
import LoadingState from "../../../../../components/LoadingState/LoadingState";
import ErrorState from "../../../../../components/ErrorState/ErrorState";
import NotFoundState from "../../../../../components/ErrorState/NotFoundState";
import { useToast } from "../../../../../hooks/useToast";
import useDragReorder from "../../../../../hooks/useDragReorder";
import { useConfirm } from "../../../../../components/ConfirmDialog/ConfirmProvider";
import { ResourcesService } from "../../../../../services/resources";
import { isNotFound } from "../../../../../services/api";
import {
  useBootOptionsQuery,
  useIsoImagesQuery,
  useLoadError,
  useQueryErrorEffect,
  useResourceDetailCacheUpdater,
} from "../../../../../queries/resourceDetail";

const KIND_ICON = { disk: "storage", cdrom: "album", network: "lan", other: "memory" };

function formatSize(bytes) {
  if (!bytes) return "";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}

export default function BootOptionsCard({ vmid, canManage }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const detailCache = useResourceDetailCacheUpdater();
  const [busy, setBusy] = useState(false);

  /* 卡片是編輯器：查詢不輪詢、切回視窗也不重抓，不會蓋掉排到一半的順序。
     每次載入失敗都提示；第一次就失敗時換成錯誤狀態（可重試），不能讓轉圈圈一直轉 */
  const optionsQuery = useBootOptionsQuery(vmid);
  useQueryErrorEffect(optionsQuery, (err) => {
    toast.error(err?.message ?? t("Error.generic", { ns: "common" }));
  });
  const options = optionsQuery.data ?? null;
  /* 錯誤狀態在之後的重抓期間維持、不閃轉圈圈，按重試才回到載入中（見 useLoadError） */
  const { error: loadError, retry: retryOptions } = useLoadError(optionsQuery);
  /* ISO 清單只有支援 CD-ROM 的機器才查；查不到當成沒有 */
  const isoQuery = useIsoImagesQuery(vmid, { enabled: options?.supports_cdrom === true });
  const isoImages = isoQuery.data ?? [];
  const loading = (!options && (optionsQuery.isPending || optionsQuery.isFetching))
    || (options?.supports_cdrom === true && isoQuery.data === undefined);

  /* 編輯中的順序與 ISO 選擇；null＝沿用伺服器上的設定。存檔或重抓到新設定時清掉 */
  const [orderDraft, setOrderDraft] = useState(null);
  const [isoDraft, setSelectedIso] = useState(null);
  useEffect(() => {
    setOrderDraft(null);
    setSelectedIso(null);
  }, [options]);
  const order = orderDraft ?? options?.boot_order ?? [];
  const selectedIso = isoDraft ?? options?.cdrom_iso ?? "";
  const setOrder = (update) => setOrderDraft((prev) => update(prev ?? options?.boot_order ?? []));

  async function save(body, successKey) {
    setBusy(true);
    try {
      const updated = await ResourcesService.updateBootOptions(vmid, body);
      /* 存回來的設定就地寫進快取；編輯中的順序與 ISO 選擇隨之回到新設定 */
      detailCache.setBootOptions(vmid, updated);
      toast.success(t(successKey));
    } catch (err) {
      toast.error(err?.message ?? t("BootOptionsCard.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  /* 退出 CD-ROM 會直接動到執行中的機器（安裝到一半就斷了）：先確認 */
  async function ejectCdrom() {
    const ok = await confirm({
      title: t("BootOptionsCard.ejectConfirmTitle"),
      message: t("BootOptionsCard.ejectConfirmMessage", {
        name: options?.cdrom_iso?.split("/").pop() ?? "",
      }),
      confirmText: t("BootOptionsCard.eject"),
      danger: true,
    });
    if (!ok) return;
    await save({ eject_cdrom: true }, "BootOptionsCard.isoEjected");
  }

  function move(index, delta) {
    setOrder((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function addDevice(key) {
    setOrder((prev) => (prev.includes(key) ? prev : [...prev, key]));
  }

  function removeDevice(key) {
    setOrder((prev) => prev.filter((k) => k !== key));
  }

  /* 按住整列拖到想要的位置（觸控按住左側把手）；上下移按鈕留給鍵盤 */
  const drag = useDragReorder({
    disabled: busy || !canManage,
    onMove: (from, to) => setOrder((prev) => {
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    }),
  });

  const orderChanged = options && JSON.stringify(order) !== JSON.stringify(options.boot_order ?? []);
  const deviceMap = Object.fromEntries((options?.boot_devices ?? []).map((d) => [d.key, d]));
  const unusedDevices = (options?.boot_devices ?? []).filter((d) => !order.includes(d.key));

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="power_settings_new" size={18} />
            {t("BootOptionsCard.title")}
          </h2>
          {/* 主機開機時自動啟動：系統自動管理，說明放在大標下方 */}
          <p className={styles.cardDesc}>{t("BootOptionsCard.onbootAutoNote")}</p>
        </div>
      </div>
      <div className={styles.cardBody}>
        {loadError && !options ? (
          isNotFound(loadError) ? <NotFoundState /> : <ErrorState onRetry={() => retryOptions()} />
        ) : loading || !options ? (
          <LoadingState text={t("BootOptionsCard.loading")} />
        ) : (
          <>
            {/* 開機順序 */}
            {options.supports_boot_order && (
              <div className={styles.rowStack}>
                <span className={styles.factLabel}>{t("BootOptionsCard.bootOrderLabel")}</span>
                {order.length === 0 ? (
                  <p className={styles.mutedText}>{t("BootOptionsCard.bootOrderDefault")}</p>
                ) : (
                  <div className={styles.orderList} ref={drag.listRef}>
                    {order.map((key, index) => {
                      const dev = deviceMap[key];
                      return (
                        <div
                          key={key}
                          {...drag.getItemProps(key)}
                          className={`${styles.orderItem} ${canManage && !busy ? styles.orderItemDraggable : ""} ${drag.draggingKey === key ? styles.orderItemDragging : ""}`}
                        >
                          {canManage && (
                            <span
                              {...drag.getHandleProps()}
                              className={`${styles.dragHandle} ${busy ? styles.dragHandleDisabled : ""}`}
                              title={t("BootOptionsCard.dragToReorder")}
                            >
                              <MIcon name="drag_indicator" size={18} />
                            </span>
                          )}
                          <span className={styles.orderIndex}>{index + 1}</span>
                          <span className={styles.orderMain}>
                            <MIcon name={KIND_ICON[dev?.kind ?? "other"]} size={16} />
                            <code>{key}</code>
                            {dev?.description && <span className={styles.mutedText}>{dev.description}</span>}
                          </span>
                          {canManage && (
                            <span className={styles.orderBtns}>
                              <button type="button" className={styles.rpIconBtn} disabled={index === 0 || busy} onClick={() => move(index, -1)} title={t("BootOptionsCard.moveUp")}>
                                <MIcon name="arrow_upward" size={16} />
                              </button>
                              <button type="button" className={styles.rpIconBtn} disabled={index === order.length - 1 || busy} onClick={() => move(index, 1)} title={t("BootOptionsCard.moveDown")}>
                                <MIcon name="arrow_downward" size={16} />
                              </button>
                              <button type="button" className={`${styles.rpIconBtn} ${styles.rpIconBtnDanger}`} disabled={busy} onClick={() => removeDevice(key)} title={t("BootOptionsCard.removeFromOrder")}>
                                <MIcon name="close" size={16} />
                              </button>
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
                {canManage && unusedDevices.length > 0 && (
                  <div className={styles.chipList}>
                    {unusedDevices.map((dev) => (
                      <button key={dev.key} type="button" className={styles.chip} onClick={() => addDevice(dev.key)} disabled={busy}>
                        <MIcon name="add" size={12} /> {dev.key}
                        {dev.description ? ` · ${dev.description}` : ""}
                      </button>
                    ))}
                  </div>
                )}
                {canManage && (
                  /* 儲存鈕靠右、對齊上方清單的右緣，說明放在按鈕左邊 */
                  <div className={`${styles.fieldRow} ${styles.fieldRowEnd}`}>
                    <span className={styles.fieldHint}>{t("BootOptionsCard.bootOrderHint")}</span>
                    <button
                      type="button"
                      className={styles.btnPrimary}
                      disabled={!orderChanged || busy}
                      onClick={() => save({ boot_order: order }, "BootOptionsCard.bootOrderSaved")}
                    >
                      {t("BootOptionsCard.saveBootOrder")}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* ISO 掛載 */}
            {options.supports_cdrom && (
              <div className={styles.rowStack}>
                <span className={styles.factLabel}>{t("BootOptionsCard.isoLabel")}</span>
                <p className={styles.mutedText}>
                  {options.cdrom_iso
                    ? t("BootOptionsCard.isoMounted", { name: options.cdrom_iso.split("/").pop(), slot: options.cdrom_slot })
                    : t("BootOptionsCard.isoNone")}
                </p>
                {canManage && (
                  <div className={styles.fieldRow}>
                    <div className={styles.field} style={{ flex: 1, minWidth: 240 }}>
                      <select value={selectedIso} onChange={(e) => setSelectedIso(e.target.value)} disabled={busy}>
                        <option value="">{t("BootOptionsCard.selectIso")}</option>
                        {isoImages.map((img) => (
                          <option key={img.volid} value={img.volid}>
                            {img.name}{img.size ? ` (${formatSize(img.size)})` : ""}
                          </option>
                        ))}
                      </select>
                    </div>
                    <button
                      type="button"
                      className={styles.btnSecondary}
                      disabled={!selectedIso || selectedIso === options.cdrom_iso || busy}
                      onClick={() => save({ cdrom_iso: selectedIso }, "BootOptionsCard.isoMountedToast")}
                    >
                      <MIcon name="album" size={16} />
                      {t("BootOptionsCard.mount")}
                    </button>
                    {options.cdrom_iso && (
                      <button
                        type="button"
                        className={styles.btnDangerOutline}
                        disabled={busy}
                        onClick={ejectCdrom}
                      >
                        <MIcon name="eject" size={16} />
                        {t("BootOptionsCard.eject")}
                      </button>
                    )}
                  </div>
                )}
                <span className={styles.fieldHint}>
                  {isoImages.length === 0 ? t("BootOptionsCard.noIsoImages", { storage: options.iso_storage ?? "-" }) : t("BootOptionsCard.isoHint")}
                </span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
