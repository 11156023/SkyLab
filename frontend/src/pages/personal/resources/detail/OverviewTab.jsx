import { useEffect, useState } from "react";
import rawData from "virtual:templates";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import { ResourcesService } from "../../../../services/resources";
import { useToast } from "../../../../hooks/useToast";

const TEMPLATES = Object.entries(rawData)
  .filter(([key]) => !["metadata.json", "versions.json", "github-versions.json"].includes(key))
  .map(([, value]) => value)
  .filter(Boolean);

const getTemplateBySlug = (slug) =>
  slug ? TEMPLATES.find((t) => t.slug === slug) : undefined;

const STATUS_BADGE = {
  running: { label: "執行中", cls: "badge_ok" },
  stopped: { label: "已關機", cls: "badge_muted" },
  paused:  { label: "已暫停", cls: "badge_muted" },
};

function templateNote(note) {
  if (typeof note === "string") return note;
  return note?.text_zh || note?.text || "";
}

export default function OverviewTab({ vmid }) {
  const toast = useToast();
  const [resource, setResource] = useState(null);
  const [sshKey, setSshKey] = useState(null);
  const [showPrivateKey, setShowPrivateKey] = useState(false);
  const [copied, setCopied] = useState("");
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    ResourcesService.get(vmid)
      .then((r) => {
        if (cancelled) return;
        setResource(r);
        if (r.ssh_public_key) {
          ResourcesService.getSshKey(vmid)
            .then((k) => !cancelled && setSshKey(k))
            .catch(() => {});
        }
      })
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [vmid]);

  const copy = async (text, label) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(""), 2000);
    } catch {
      toast.error("複製失敗");
    }
  };

  if (error) return <p className={styles.stateText}>無法載入資源資訊</p>;
  if (!resource) return <p className={styles.stateText}>載入中…</p>;

  const badge = STATUS_BADGE[resource.status] ?? {
    label: resource.status,
    cls: "badge_muted",
  };
  const template = getTemplateBySlug(resource.service_template_slug);
  const tplDescription = template?.description_zh || template?.description || "";
  const credentials = template?.default_credentials;
  const notes = Array.isArray(template?.notes)
    ? template.notes.map(templateNote).filter(Boolean)
    : [];

  return (
    <div className={styles.tabStack}>
      {/* 服務模板資訊 */}
      {template && (
        <div className={`${styles.card} ${styles.cardAccent}`}>
          <div className={styles.cardHeader}>
            <div className={styles.tplHead}>
              {template.logo ? (
                <img
                  src={template.logo}
                  alt={template.name ?? ""}
                  className={styles.tplLogo}
                  onError={(e) => {
                    e.currentTarget.style.display = "none";
                  }}
                />
              ) : (
                <span className={styles.tplLogoFallback}>
                  <MIcon name="deployed_code" size={28} />
                </span>
              )}
              <div>
                <h2 className={styles.cardTitle}>
                  {template.name || resource.service_template_slug}
                  {template.interface_port ? (
                    <span className={styles.portChip}>:{template.interface_port}</span>
                  ) : null}
                </h2>
                {tplDescription && <p className={styles.cardDesc}>{tplDescription}</p>}
              </div>
            </div>
          </div>
          <div className={styles.cardBody}>
            {(template.documentation || template.website) && (
              <div className={styles.linkRow}>
                {template.documentation && (
                  <a
                    className={styles.linkBtn}
                    href={template.documentation}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <MIcon name="menu_book" size={14} />
                    Documentation
                    <MIcon name="open_in_new" size={12} />
                  </a>
                )}
                {template.website && (
                  <a
                    className={styles.linkBtn}
                    href={template.website}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <MIcon name="public" size={14} />
                    Website
                    <MIcon name="open_in_new" size={12} />
                  </a>
                )}
              </div>
            )}

            {credentials && (credentials.username || credentials.password) && (
              <div className={styles.noteBox}>
                <div className={styles.noteBoxTitle}>
                  <MIcon name="key" size={12} />
                  預設帳密
                </div>
                {credentials.username && (
                  <p className={styles.noteBoxLine}>
                    <span className={styles.mutedText}>Username: </span>
                    <code>{credentials.username}</code>
                  </p>
                )}
                {credentials.password && (
                  <p className={styles.noteBoxLine}>
                    <span className={styles.mutedText}>Password: </span>
                    <code>{credentials.password}</code>
                  </p>
                )}
              </div>
            )}

            {notes.length > 0 && (
              <div>
                <div className={styles.noteBoxTitle}>
                  <MIcon name="info" size={12} />
                  注意事項
                </div>
                <ul className={styles.noteList}>
                  {notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 基本資訊 */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>
              <MIcon name="dns" size={18} />
              基本資訊
            </h2>
            <p className={styles.cardDesc}>資源的識別與所在位置</p>
          </div>
        </div>
        <div className={`${styles.cardBody} ${styles.factGrid}`}>
          <div className={styles.fact}>
            <span className={styles.factLabel}>VMID</span>
            <span className={styles.factValue}>{resource.vmid}</span>
          </div>
          <div className={styles.fact}>
            <span className={styles.factLabel}>名稱</span>
            <span className={styles.factValue}>{resource.name}</span>
          </div>
          <div className={styles.fact}>
            <span className={styles.factLabel}>類型</span>
            <span className={styles.factValue}>{String(resource.type).toUpperCase()}</span>
          </div>
          <div className={styles.fact}>
            <span className={styles.factLabel}>狀態</span>
            <span className={`${styles.badge} ${styles[badge.cls]}`}>{badge.label}</span>
          </div>
          <div className={styles.fact}>
            <span className={styles.factLabel}>節點</span>
            <span className={styles.factValue}>{resource.node}</span>
          </div>
          {resource.ip_address && (
            <div className={styles.fact}>
              <span className={styles.factLabel}>IP 位址</span>
              <span className={`${styles.factValue} ${styles.monoText}`}>
                {resource.ip_address}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* 資源配置 */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>
              <MIcon name="memory" size={18} />
              資源配置
            </h2>
            <p className={styles.cardDesc}>目前分配的運算資源</p>
          </div>
        </div>
        <div className={`${styles.cardBody} ${styles.specGrid}`}>
          <div className={styles.specTile}>
            <span className={styles.specIcon}>
              <MIcon name="memory" size={22} />
            </span>
            <div>
              <span className={styles.factLabel}>CPU</span>
              <span className={styles.specValue}>{resource.maxcpu}</span>
              <span className={styles.mutedText}>核心</span>
            </div>
          </div>
          <div className={styles.specTile}>
            <span className={styles.specIcon}>
              <MIcon name="sd_card" size={22} />
            </span>
            <div>
              <span className={styles.factLabel}>記憶體</span>
              <span className={styles.specValue}>
                {resource.maxmem ? (resource.maxmem / 1024 ** 3).toFixed(2) : "N/A"}
              </span>
              <span className={styles.mutedText}>GB</span>
            </div>
          </div>
        </div>
      </div>

      {/* 環境資訊 */}
      {(resource.environment_type || resource.os_info || resource.expiry_date) && (
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <h2 className={styles.cardTitle}>
                <MIcon name="event" size={18} />
                環境資訊
              </h2>
              <p className={styles.cardDesc}>作業系統與租期</p>
            </div>
          </div>
          <div className={`${styles.cardBody} ${styles.factGrid}`}>
            {resource.environment_type && (
              <div className={styles.fact}>
                <span className={styles.factLabel}>環境類型</span>
                <span className={styles.factValue}>{resource.environment_type}</span>
              </div>
            )}
            {resource.os_info && (
              <div className={styles.fact}>
                <span className={styles.factLabel}>作業系統</span>
                <span className={styles.factValue}>{resource.os_info}</span>
              </div>
            )}
            {resource.expiry_date && (
              <div className={styles.fact}>
                <span className={styles.factLabel}>到期日</span>
                <span className={styles.factValue}>
                  {new Date(resource.expiry_date).toLocaleDateString("zh-TW")}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* SSH 金鑰 */}
      {resource.ssh_public_key && (
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div>
              <h2 className={styles.cardTitle}>
                <MIcon name="key" size={18} />
                SSH 金鑰
              </h2>
              <p className={styles.cardDesc}>用於免密碼登入此資源</p>
            </div>
          </div>
          <div className={styles.cardBody}>
            <div className={styles.keyBlock}>
              <div className={styles.keyHead}>
                <span className={styles.factLabel}>公鑰</span>
                <button
                  type="button"
                  className={styles.ghostBtn}
                  onClick={() => copy(resource.ssh_public_key, "public")}
                >
                  <MIcon name={copied === "public" ? "check" : "content_copy"} size={14} />
                  {copied === "public" ? "已複製" : "複製"}
                </button>
              </div>
              <pre className={styles.keyPre}>{resource.ssh_public_key}</pre>
            </div>

            {sshKey?.ssh_private_key && (
              <div className={styles.keyBlock}>
                <div className={styles.keyHead}>
                  <span className={styles.factLabel}>私鑰</span>
                  <div className={styles.keyActions}>
                    <button
                      type="button"
                      className={styles.ghostBtn}
                      onClick={() => setShowPrivateKey((v) => !v)}
                    >
                      <MIcon name={showPrivateKey ? "visibility_off" : "visibility"} size={14} />
                      {showPrivateKey ? "隱藏" : "顯示"}
                    </button>
                    <button
                      type="button"
                      className={styles.ghostBtn}
                      onClick={() => copy(sshKey.ssh_private_key, "private")}
                    >
                      <MIcon name={copied === "private" ? "check" : "content_copy"} size={14} />
                      {copied === "private" ? "已複製" : "複製"}
                    </button>
                    <button
                      type="button"
                      className={styles.ghostBtn}
                      onClick={() => {
                        const blob = new Blob([sshKey.ssh_private_key], { type: "text/plain" });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = `id_ed25519_vm${vmid}`;
                        a.click();
                        URL.revokeObjectURL(url);
                      }}
                    >
                      <MIcon name="download" size={14} />
                      下載
                    </button>
                  </div>
                </div>
                {showPrivateKey ? (
                  <pre className={styles.keyPre}>{sshKey.ssh_private_key}</pre>
                ) : (
                  <div className={styles.keyHidden}>私鑰已隱藏，點「顯示」查看</div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
