import { useMemo, useState } from "react";
import rawData from "virtual:templates";
import { useAuth } from "../../../contexts/AuthContext";
import MIcon from "../../../components/MIcon";
import styles from "./DashboardPage.module.scss";

const QUICK_TEMPLATE_SLUGS = ["postgresql", "mongodb", "grafana", "homepage", "wordpress"];

const CATEGORY_LABELS = {
  database: "資料庫",
  monitoring: "監控",
  web: "網站",
};

const QUICK_TEMPLATE_META = {
  postgresql: { category: "database", fallbackName: "PostgreSQL", icon: "database" },
  mongodb: { category: "database", fallbackName: "MongoDB", icon: "database" },
  grafana: { category: "monitoring", fallbackName: "Grafana", icon: "monitoring" },
  homepage: { category: "web", fallbackName: "Homepage", icon: "dashboard_customize" },
  wordpress: { category: "web", fallbackName: "WordPress", icon: "language" },
};

const TEMPLATES = Object.entries(rawData)
  .filter(([key]) => !["metadata.json", "versions.json", "github-versions.json"].includes(key))
  .map(([, value]) => value)
  .filter(Boolean);

function getTemplate(slug) {
  return TEMPLATES.find((template) => template.slug === slug);
}

function getDescription(template) {
  return template?.description_zh || template?.description || "快速建立一個可立即使用的練習環境。";
}

function getResources(template) {
  return template?.install_methods?.[0]?.resources ?? {};
}

function TemplateLogo({ template, icon }) {
  const [failed, setFailed] = useState(false);
  if (template?.logo && !failed) {
    return <img src={template.logo} alt="" className={styles.templateLogo} onError={() => setFailed(true)} />;
  }
  return (
    <span className={styles.templateIcon}>
      <MIcon name={icon} size={22} />
    </span>
  );
}

function QuickTemplateCard({ item, onUse }) {
  const resources = getResources(item.template);
  const cpu = resources.cpu ? Math.min(Number(resources.cpu), 2) : null;
  const ram = resources.ram ? Math.min(Number(resources.ram), 4096) : null;
  const disk = resources.hdd ? Math.min(Math.max(Number(resources.hdd), 8), 32) : null;
  return (
    <button type="button" className={styles.templateCard} onClick={() => onUse(item.slug)}>
      <div className={styles.templateCardTop}>
        <TemplateLogo template={item.template} icon={item.icon} />
        <span className={styles.templateCategory}>{CATEGORY_LABELS[item.category]}</span>
      </div>
      <div className={styles.templateMain}>
        <h3 className={styles.templateName}>{item.name}</h3>
        <p className={styles.templateDesc}>{item.description}</p>
      </div>
      <div className={styles.templateSpecs}>
        {cpu && <span>{cpu} CPU</span>}
        {ram && <span>{ram} MB</span>}
        {disk && <span>{disk} GB</span>}
      </div>
      <div className={styles.templateAction}>
        <MIcon name="bolt" size={15} />
        <span>快速使用</span>
      </div>
    </button>
  );
}

export default function DashboardPage({ onNavigate }) {
  const { user } = useAuth();
  const [category, setCategory] = useState("all");

  const quickTemplates = useMemo(
    () =>
      QUICK_TEMPLATE_SLUGS.map((slug) => {
        const template = getTemplate(slug);
        const meta = QUICK_TEMPLATE_META[slug];
        return {
          slug,
          ...meta,
          template,
          name: template?.name || meta.fallbackName,
          description: getDescription(template),
        };
      }),
    [],
  );

  const filteredTemplates =
    category === "all"
      ? quickTemplates
      : quickTemplates.filter((template) => template.category === category);

  function handleUseTemplate(slug) {
    onNavigate?.("my-requests", {
      view: "create",
      quickTemplateSlug: slug,
    });
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <h1 className={styles.greeting}>
            歡迎，{user?.full_name || user?.email || "使用者"}
          </h1>
          <p className={styles.subtitle}>選擇常用模板快速啟動練習環境，或前往申請頁建立完整資源。</p>
        </div>
        <div className={styles.quickStats}>
          <span className={styles.quickStatLabel}>快速模板</span>
          <span className={styles.quickStatValue}>{quickTemplates.length}</span>
        </div>
      </header>

      <section className={styles.quickSection} aria-labelledby="quick-templates-heading">
        <div className={styles.sectionHeader}>
          <div>
            <h2 id="quick-templates-heading" className={styles.sectionTitle}>快速使用模板</h2>
            <p className={styles.sectionSubtitle}>不需人工審核，系統會以快速模板流程建立短時段 LXC 環境。</p>
          </div>
          <button
            type="button"
            className={styles.secondaryButton}
            onClick={() => onNavigate?.("my-requests", { view: "create" })}
          >
            <MIcon name="add" size={16} />
            完整申請
          </button>
        </div>

        <div className={styles.categoryTabs} role="tablist" aria-label="快速模板分類">
          {[
            ["all", "全部"],
            ["database", "資料庫"],
            ["monitoring", "監控"],
            ["web", "網站"],
          ].map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={category === key}
              className={`${styles.categoryTab} ${category === key ? styles.categoryTabActive : ""}`}
              onClick={() => setCategory(key)}
            >
              {label}
            </button>
          ))}
        </div>

        <div className={styles.templateGrid}>
          {filteredTemplates.map((item) => (
            <QuickTemplateCard key={item.slug} item={item} onUse={handleUseTemplate} />
          ))}
        </div>
      </section>
    </div>
  );
}
