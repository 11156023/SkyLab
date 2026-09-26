import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./ReverseProxyRuleModal.module.scss";
import MIcon from "../MIcon";
import Modal from "../Modal/Modal";
import { useToast } from "../../hooks/useToast";
import { ResourcesService } from "../../services/resources";

/* label 是模組層級常數，無法呼叫 hook，改存 labelKey，實際 render 處再 t() */
export const COMMON_PORTS = [
  { value: "80", labelKey: "ReverseProxyRuleModal.port80" },
  { value: "443", labelKey: "ReverseProxyRuleModal.port443" },
  { value: "3000", labelKey: "ReverseProxyRuleModal.port3000" },
  { value: "5000", labelKey: "ReverseProxyRuleModal.port5000" },
  { value: "8000", labelKey: "ReverseProxyRuleModal.port8000" },
  { value: "8080", labelKey: "ReverseProxyRuleModal.port8080" },
  { value: "8888", labelKey: "ReverseProxyRuleModal.port8888" },
];

export function findZoneByDomain(domain, zones = []) {
  return [...zones]
    .sort((a, b) => b.name.length - a.name.length)
    .find((zone) => domain === zone.name || domain.endsWith(`.${zone.name}`));
}

export function extractHostnamePrefix(domain, zoneName) {
  if (domain === zoneName) return "";
  const suffix = `.${zoneName}`;
  return domain.endsWith(suffix) ? domain.slice(0, -suffix.length) : domain;
}

/**
 * 反向代理規則建立／編輯 Modal（共用元件）。
 * - 全域反向代理頁：不帶 fixedResource，顯示 VM 下拉選單。
 * - 資源詳情頁：帶 fixedResource（{ vmid, name }），鎖定綁定的 VM。
 */
export default function ReverseProxyRuleModal({
  rule,
  setupContext,
  isAdmin = false,
  fixedResource = null,
  loading,
  onClose,
  onSubmit,
  closing = false,
}) {
  const { t } = useTranslation("components");
  const toast = useToast();
  const zones = setupContext?.zones ?? [];
  const matchedZone = rule
    ? zones.find((z) => z.id === rule.zone_id) ?? findZoneByDomain(rule.domain, zones)
    : null;
  const matchedCommonPort = rule
    ? COMMON_PORTS.find((p) => p.value === String(rule.internal_port))
    : null;

  const [resources, setResources] = useState([]);
  const [loadingResources, setLoadingResources] = useState(!fixedResource);
  const [form, setForm] = useState({
    vmid: rule
      ? String(rule.vmid)
      : fixedResource
        ? String(fixedResource.vmid)
        : "",
    zoneId: matchedZone?.id ?? zones[0]?.id ?? "",
    hostnamePrefix: rule
      ? matchedZone
        ? extractHostnamePrefix(rule.domain, matchedZone.name)
        : rule.domain
      : "",
    port: matchedCommonPort?.value ?? (rule ? "" : "80"),
    customPort: rule && !matchedCommonPort ? String(rule.internal_port) : "",
    useCustomPort: Boolean(rule && !matchedCommonPort),
    enableHttps: rule?.enable_https ?? true,
  });

  useEffect(() => {
    if (fixedResource) return;
    const fetcher = isAdmin ? ResourcesService.listAll() : ResourcesService.list();
    fetcher
      .then((res) => setResources(Array.isArray(res) ? res : res?.data ?? []))
      .catch(() => {})
      .finally(() => setLoadingResources(false));
  }, [isAdmin, fixedResource]);

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  const selectedZone = zones.find((z) => z.id === form.zoneId);
  const effectivePort = form.useCustomPort ? form.customPort : form.port;
  const prefix = form.hostnamePrefix.trim().toLowerCase().replace(/^\.+|\.+$/g, "");
  const previewDomain = selectedZone
    ? prefix
      ? `${prefix}.${selectedZone.name}`
      : selectedZone.name
    : "";

  function submit(e) {
    e.preventDefault();
    const parsedPort = Number(effectivePort);
    if (!form.vmid) {
      toast.error(t("ReverseProxyRuleModal.selectVmFirst"));
      return;
    }
    if (!form.zoneId) {
      toast.error(t("ReverseProxyRuleModal.selectDomainSuffixFirst"));
      return;
    }
    if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
      toast.error(t("ReverseProxyRuleModal.portRangeError"));
      return;
    }
    onSubmit({
      vmid: Number(form.vmid),
      zone_id: form.zoneId,
      hostname_prefix: prefix,
      internal_port: parsedPort,
      enable_https: form.enableHttps,
    });
  }

  /* 外框（遮罩、標題列、Esc、焦點、捲動鎖）交給共用 Modal；送出中 Esc／點遮罩／× 都不關 */
  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={loading}
      closeButton
      size="md"
      title={rule ? t("ReverseProxyRuleModal.editTitle") : t("ReverseProxyRuleModal.createTitle")}
      description={t("ReverseProxyRuleModal.headerDescription")}
      data-guide="proxy-rule-form"
      closeProps={{ "data-guide": "proxy-rule-close" }}
      actionsProps={{ "data-guide": "proxy-rule-actions" }}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            {t("ReverseProxyRuleModal.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? t("ReverseProxyRuleModal.saving") : rule ? t("ReverseProxyRuleModal.saveChanges") : t("ReverseProxyRuleModal.createUrl")}
          </button>
        </>
      }
    >
      {setupContext?.default_dns_target_type && setupContext?.default_dns_target_value && (
        <div className={styles.noticeInfo}>
          <p>
            <strong>{t("ReverseProxyRuleModal.autoHandledLabel")}</strong>
            {t("ReverseProxyRuleModal.autoHandledBody", {
              type: setupContext.default_dns_target_type,
              value: setupContext.default_dns_target_value,
            })}
          </p>
        </div>
      )}

      <div data-guide="proxy-rule-resource">
      {fixedResource ? (
        <div className={styles.field}>
          <span>{t("ReverseProxyRuleModal.boundVm")}</span>
          <div className={styles.fixedVm}>
            <MIcon name="dns" size={16} />
            {fixedResource.name
              ? `${fixedResource.name}（VM ${fixedResource.vmid}）`
              : `VM ${fixedResource.vmid}`}
          </div>
        </div>
      ) : (
        <label className={styles.field}>
          <span>{t("ReverseProxyRuleModal.selectYourVm")}</span>
          <select value={form.vmid} onChange={(e) => set("vmid", e.target.value)}>
            <option value="">{loadingResources ? t("ReverseProxyRuleModal.loadingVmList") : t("ReverseProxyRuleModal.selectAVm")}</option>
            {resources.map((r) => (
              <option key={r.vmid} value={String(r.vmid)}>
                {r.name}（VM {r.vmid}）
              </option>
            ))}
          </select>
          {!loadingResources && resources.length === 0 && (
            <em className={styles.fieldHint}>{t("ReverseProxyRuleModal.noVmHint")}</em>
          )}
        </label>
      )}
      </div>

      <div className={styles.fieldRow} data-guide="proxy-rule-domain">
        <label className={styles.field}>
          <span>{t("ReverseProxyRuleModal.hostnamePrefixLabel")}</span>
          <input
            value={form.hostnamePrefix}
            onChange={(e) => set("hostnamePrefix", e.target.value)}
            placeholder={t("ReverseProxyRuleModal.hostnamePrefixPlaceholder")}
          />
        </label>
        <label className={styles.field}>
          <span>{t("ReverseProxyRuleModal.domainSuffixLabel")}</span>
          <select value={form.zoneId} onChange={(e) => set("zoneId", e.target.value)}>
            <option value="">{t("ReverseProxyRuleModal.selectDomainSuffix")}</option>
            {zones.map((zone) => (
              <option key={zone.id} value={zone.id}>{zone.name}</option>
            ))}
          </select>
        </label>
      </div>

      <label className={styles.field} data-guide="proxy-rule-port">
        <span>{t("ReverseProxyRuleModal.portLabel")}</span>
        {!form.useCustomPort ? (
          <select value={form.port} onChange={(e) => set("port", e.target.value)}>
            {COMMON_PORTS.map((p) => (
              <option key={p.value} value={p.value}>{t(p.labelKey)}</option>
            ))}
          </select>
        ) : (
          <input
            type="number"
            min={1}
            max={65535}
            value={form.customPort}
            onChange={(e) => set("customPort", e.target.value)}
            placeholder={t("ReverseProxyRuleModal.customPortPlaceholder")}
          />
        )}
        <button
          type="button"
          className={styles.linkBtn}
          onClick={() => set("useCustomPort", !form.useCustomPort)}
        >
          {form.useCustomPort ? t("ReverseProxyRuleModal.backToCommonPorts") : t("ReverseProxyRuleModal.portNotListed")}
        </button>
      </label>

      <label className={styles.checkRow}>
        <input
          type="checkbox"
          checked={form.enableHttps}
          onChange={(e) => set("enableHttps", e.target.checked)}
        />
        <span>{t("ReverseProxyRuleModal.enableHttpsLabel")}</span>
      </label>

      {previewDomain && form.vmid && (
        <div className={styles.noticeInfo}>
          <p>
            <strong>{t("ReverseProxyRuleModal.previewLabel")}</strong>
            {t("ReverseProxyRuleModal.previewBody", {
              scheme: form.enableHttps ? "https" : "http",
              domain: previewDomain,
              vmid: form.vmid,
              port: effectivePort,
            })}
          </p>
        </div>
      )}
    </Modal>
  );
}
