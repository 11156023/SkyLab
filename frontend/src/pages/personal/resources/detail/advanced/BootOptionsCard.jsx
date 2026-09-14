/**
 * BootOptionsCard — 開機選項
 * 開機順序與 ISO 掛載（QEMU）。
 * 「主機開機時自動啟動」(onboot) 不開放設定：後端在每次開關機後自動對齊
 * （開機→啟用、關機→停用），卡片上只用一行說明告知。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "../ResourceDetailPage.module.scss";
import MIcon from "../../../../../components/MIcon";
import LoadingState from "../../../../../components/LoadingState/LoadingState";
import { useToast } from "../../../../../hooks/useToast";
import { ResourcesService } from "../../../../../services/resources";

const KIND_ICON = { disk: "hard_drive", cdrom: "album", network: "lan", other: "memory" };

function formatSize(bytes) {
  if (!bytes) return "";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}

export default function BootOptionsCard({ vmid, canManage }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const [options, setOptions] = useState(null);
  const [isoImages, setIsoImages] = useState([]);
  const [order, setOrder] = useState([]);
  const [selectedIso, setSelectedIso] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const opts = await ResourcesService.getBootOptions(vmid);
      setOptions(opts);
      setOrder(opts.boot_order ?? []);
      if (opts.supports_cdrom) {
        const images = await ResourcesService.listIsoImages(vmid).catch(() => []);
        setIsoImages(images ?? []);
        setSelectedIso(opts.cdrom_iso ?? "");
      }
    } catch (err) {
      toast.error(err?.message ?? t("BootOptionsCard.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [vmid, toast, t]);

  useEffect(() => {
    load();
  }, [load]);

  async function save(body, successKey) {
    setBusy(true);
    try {
      const updated = await ResourcesService.updateBootOptions(vmid, body);
      setOptions(updated);
      setOrder(updated.boot_order ?? []);
      setSelectedIso(updated.cdrom_iso ?? "");
      toast.success(t(successKey));
    } catch (err) {
      toast.error(err?.message ?? t("BootOptionsCard.saveFailed"));
    } finally {
      setBusy(false);
    }
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
          <p className={styles.cardDesc}>{t("BootOptionsCard.desc")}</p>
        </div>
      </div>
      <div className={styles.cardBody}>
        {loading || !options ? (
          <LoadingState text={t("BootOptionsCard.loading")} />
        ) : (
          <>
            {/* 主機開機時自動啟動：系統自動管理，僅說明 */}
            <p className={styles.hintLine}>
              <MIcon name="restart_alt" size={14} />
              {t("BootOptionsCard.onbootAutoNote")}
            </p>

            {/* 開機順序 */}
            {options.supports_boot_order && (
              <div className={styles.rowStack}>
                <span className={styles.factLabel}>{t("BootOptionsCard.bootOrderLabel")}</span>
                {order.length === 0 ? (
                  <p className={styles.mutedText}>{t("BootOptionsCard.bootOrderDefault")}</p>
                ) : (
                  <div className={styles.orderList}>
                    {order.map((key, index) => {
                      const dev = deviceMap[key];
                      return (
                        <div key={key} className={styles.orderItem}>
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
                  <div className={styles.fieldRow}>
                    <button
                      type="button"
                      className={styles.btnPrimary}
                      disabled={!orderChanged || busy}
                      onClick={() => save({ boot_order: order }, "BootOptionsCard.bootOrderSaved")}
                    >
                      {t("BootOptionsCard.saveBootOrder")}
                    </button>
                    <span className={styles.fieldHint}>{t("BootOptionsCard.bootOrderHint")}</span>
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
                        onClick={() => save({ eject_cdrom: true }, "BootOptionsCard.isoEjected")}
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

            {!options.supports_boot_order && (
              <p className={styles.mutedText}>{t("BootOptionsCard.lxcNote")}</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
