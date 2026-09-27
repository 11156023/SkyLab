import { useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./IpManagementPage.module.scss";
import Modal from "../../../components/Modal/Modal";

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
    forward_port_start: String(config?.forward_port_start ?? 30000),
    forward_port_end:   String(config?.forward_port_end ?? 39999),
    forward_public_host: config?.forward_public_host ?? "",
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
      forward_port_start: Number(form.forward_port_start),
      forward_port_end:   Number(form.forward_port_end),
      forward_public_host: form.forward_public_host.trim() || null,
    });
  }

  /* 外框（遮罩、標題列、Esc、焦點、捲動鎖）交給共用 Modal；儲存或刪除中 Esc／點遮罩／× 都不關 */
  return (
    <Modal
      as="form"
      onSubmit={handleSubmit}
      closing={closing}
      onClose={onCancel}
      busy={busy}
      closeButton
      size="md"
      title={isEdit ? t("SubnetConfigForm.editTitle") : t("SubnetConfigForm.createTitle")}
      actions={
        <>
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
        </>
      }
    >

      <div className={styles.modalFormGrid}>
        <label className={styles.field}>
          <span>{t("SubnetConfigForm.cidr")}</span>
          <input
            value={form.cidr}
            onChange={(e) => set("cidr", e.target.value)}
            placeholder={t("SubnetConfigForm.cidrPlaceholder")}
            readOnly={cidrLocked}
            title={cidrLocked ? t("SubnetConfigForm.cidrLockedHint") : undefined}
            required
          />
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

      {/* 課程環境的 port_forward 是逐位學生配號的：模板不能寫死對外 port，
          開課時從這段池子挑；入口主機是學生看到的「host:port」裡的 host */}
      <div className={styles.modalFormGrid}>
        <label className={styles.field}>
          <span>{t("SubnetConfigForm.forwardPortStart")}</span>
          <input
            type="number" min="1024" max="65535"
            value={form.forward_port_start}
            onChange={(e) => set("forward_port_start", e.target.value)}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SubnetConfigForm.forwardPortEnd")}</span>
          <input
            type="number" min="1024" max="65535"
            value={form.forward_port_end}
            onChange={(e) => set("forward_port_end", e.target.value)}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SubnetConfigForm.forwardPublicHost")}</span>
          <input
            value={form.forward_public_host}
            onChange={(e) => set("forward_public_host", e.target.value)}
            placeholder={t("SubnetConfigForm.forwardPublicHostPlaceholder")}
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
      </label>
    </Modal>
  );
}
