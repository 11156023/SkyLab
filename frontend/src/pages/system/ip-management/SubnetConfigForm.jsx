import { useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./IpManagementPage.module.scss";

const IPV4_PATTERN = "^(\\d{1,3}\\.){3}\\d{1,3}$";

/** textarea 內容切成 CIDR / IP 陣列（換行或逗號皆可） */
function parseBlockedList(text) {
  return (text ?? "")
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function buildInitialForm(config) {
  return {
    cidr:          config?.cidr ?? "",
    gateway:       config?.gateway ?? "",
    bridge_name:   config?.bridge_name ?? "vmbr1",
    gateway_vm_ip: config?.gateway_vm_ip ?? "",
    dns_servers:   config?.dns_servers ?? "",
    extra_blocked_subnets: (config?.extra_blocked_subnets ?? []).join("\n"),
  };
}

/**
 * 子網設定表單 — 純受控元件，不直接呼叫 API。
 * 送出時把整理好的 payload 交給 onSubmit，由頁面負責打 service。
 */
export default function SubnetConfigForm({
  config,
  cidrLocked,
  saving,
  deleting,
  closing = false,
  onSubmit,
  onCancel,
  onDelete,
}) {
  const { t } = useTranslation("system");
  const [form, setForm] = useState(() => buildInitialForm(config));
  const set = (name, value) => setForm((prev) => ({ ...prev, [name]: value }));
  const isEdit = Boolean(config);
  const busy = saving || deleting;

  function handleSubmit(e) {
    e.preventDefault();
    onSubmit({
      cidr:          form.cidr.trim(),
      gateway:       form.gateway.trim(),
      bridge_name:   form.bridge_name.trim(),
      gateway_vm_ip: form.gateway_vm_ip.trim(),
      dns_servers:   form.dns_servers.trim() || null,
      extra_blocked_subnets: parseBlockedList(form.extra_blocked_subnets),
    });
  }

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onCancel}
    >
    <form className={styles.modal} onSubmit={handleSubmit} onMouseDown={(e) => e.stopPropagation()}>
      <span className={styles.modalTitle}>
        {isEdit ? t("SubnetConfigForm.editTitle") : t("SubnetConfigForm.createTitle")}
      </span>
      <p className={styles.cardDesc}>
        {t("SubnetConfigForm.cardDesc")}
      </p>

      <div className={styles.modalFormGrid}>
        <label className={styles.field}>
          <span>{t("SubnetConfigForm.cidr")}</span>
          <input
            value={form.cidr}
            onChange={(e) => set("cidr", e.target.value)}
            placeholder={t("SubnetConfigForm.cidrPlaceholder")}
            readOnly={cidrLocked}
            required
          />
          {cidrLocked && (
            <span className={styles.fieldHint}>
              {t("SubnetConfigForm.cidrLockedHint")}
            </span>
          )}
        </label>

        <label className={styles.field}>
          <span>{t("SubnetConfigForm.gateway")}</span>
          <input
            value={form.gateway}
            onChange={(e) => set("gateway", e.target.value)}
            placeholder={t("SubnetConfigForm.gatewayPlaceholder")}
            pattern={IPV4_PATTERN}
            required
          />
        </label>

        <label className={styles.field}>
          <span>{t("SubnetConfigForm.bridgeName")}</span>
          <input
            value={form.bridge_name}
            onChange={(e) => set("bridge_name", e.target.value)}
            placeholder={t("SubnetConfigForm.bridgeNamePlaceholder")}
            required
          />
        </label>

        <label className={styles.field}>
          <span>{t("SubnetConfigForm.gatewayVmIp")}</span>
          <input
            value={form.gateway_vm_ip}
            onChange={(e) => set("gateway_vm_ip", e.target.value)}
            placeholder={t("SubnetConfigForm.gatewayVmIpPlaceholder")}
            pattern={IPV4_PATTERN}
            required
          />
          <span className={styles.fieldHint}>{t("SubnetConfigForm.gatewayVmIpHint")}</span>
        </label>

        <label className={styles.field}>
          <span>DNS Servers</span>
          <input
            value={form.dns_servers}
            onChange={(e) => set("dns_servers", e.target.value)}
            placeholder={t("SubnetConfigForm.dnsServersPlaceholder")}
          />
        </label>
      </div>

      <label className={styles.field}>
        <span>{t("SubnetConfigForm.extraBlockedSubnets")}</span>
        <textarea
          rows={4}
          value={form.extra_blocked_subnets}
          onChange={(e) => set("extra_blocked_subnets", e.target.value)}
          placeholder={t("SubnetConfigForm.extraBlockedSubnetsPlaceholder")}
          spellCheck={false}
        />
        <span className={styles.fieldHint}>
          {t("SubnetConfigForm.extraBlockedSubnetsHint")}
        </span>
      </label>

      <div className={styles.modalActions}>
        {isEdit && (
          <button
            type="button"
            className={styles.btnDanger}
            onClick={onDelete}
            disabled={busy}
          >
            {deleting ? t("SubnetConfigForm.deleting") : t("SubnetConfigForm.deleteConfig")}
          </button>
        )}
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={onCancel}
          disabled={busy}
        >
          {t("SubnetConfigForm.cancel")}
        </button>
        <button type="submit" className={styles.btnPrimary} disabled={busy}>
          {saving ? t("SubnetConfigForm.saving") : isEdit ? t("SubnetConfigForm.updateConfig") : t("SubnetConfigForm.createConfig")}
        </button>
      </div>
    </form>
    </div>
  );
}
