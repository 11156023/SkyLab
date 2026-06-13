/* ── Static course data ── */
export const COURSES = [
  {
    id: "db-design",
    title: "資料庫設計與應用",
    description:
      "學習關聯式資料庫設計、SQL 語法、資料正規化與交易控制，部署 MySQL、PostgreSQL 或 MariaDB 進行上機實作。",
    subjects: ["資料庫設計", "後端開發", "SQL"],
    teacher: "王建明",
    classGroup: "資工系 113-A",
    icon: "storage",
    accent: "#5471bf",
  },
  {
    id: "linux-ops",
    title: "Linux 系統實作",
    description:
      "掌握 Linux 指令列操作、檔案系統管理、程序控制與基礎網路設定，在獨立容器環境中安全練習。",
    subjects: ["作業系統", "系統管理", "DevOps"],
    teacher: "李怡萱",
    classGroup: "資工系 113-B",
    icon: "terminal",
    accent: "rgb(43, 112, 152)",
  },
  {
    id: "data-science",
    title: "資料科學與機器學習",
    description:
      "使用 Jupyter Notebook 進行資料清理、視覺化分析與機器學習模型訓練，支援 Python 完整科學運算環境。",
    subjects: ["資料科學", "機器學習", "Python"],
    teacher: "陳文彬",
    classGroup: "資科系 113-A",
    icon: "science",
    accent: "#5471bf",
  },
  {
    id: "web-dev",
    title: "網頁應用開發",
    description:
      "建立完整的網站開發環境，部署前後端應用、CMS 或靜態網站，適合 Web 開發實作課程。",
    subjects: ["Web 開發", "網站架設", "前後端整合"],
    teacher: "林佳穎",
    classGroup: "資管系 113-A",
    icon: "public",
    accent: "rgb(43, 112, 152)",
  },
  {
    id: "db-design",
    title: "資料庫設計與應用",
    description:
      "學習關聯式資料庫設計、SQL 語法、資料正規化與交易控制，部署 MySQL、PostgreSQL 或 MariaDB 進行上機實作。",
    subjects: ["資料庫設計", "後端開發", "SQL"],
    teacher: "王建明",
    classGroup: "資工系 113-A",
    icon: "storage",
    accent: "#5471bf",
  },
  {
    id: "linux-ops",
    title: "Linux 系統實作",
    description:
      "掌握 Linux 指令列操作、檔案系統管理、程序控制與基礎網路設定，在獨立容器環境中安全練習。",
    subjects: ["作業系統", "系統管理", "DevOps"],
    teacher: "李怡萱",
    classGroup: "資工系 113-B",
    icon: "terminal",
    accent: "rgb(43, 112, 152)",
  },
];

/* ── Quick start templates ── */
export const TEMPLATE_CATEGORIES = [
  { id: "databases",    title: "資料庫",   desc: "關聯式資料庫與 NoSQL" },
  { id: "data-science", title: "資料科學", desc: "資料分析與機器學習" },
  { id: "monitoring",   title: "監控",     desc: "系統指標收集與視覺化" },
  { id: "automation",   title: "自動化",   desc: "工作流程與服務整合" },
  { id: "ai-devtools",  title: "AI 工具",  desc: "LLM 部署與 AI 應用" },
  { id: "webservers",   title: "網站服務", desc: "網頁應用與入口儀表板" },
];

export const CATEGORY_ACCENT = {
  databases:      "#5471bf",
  "data-science": "#7b92d0",
  monitoring:     "#5471bf",
  automation:      "#3f8f7b",
  "ai-devtools":  "#7b92d0",
  webservers:     "#5471bf",
};

const LOGO_BASE = "https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp";

export const TEMPLATES = [
  { slug: "postgresql",      name: "PostgreSQL",       icon: "storage",    logo: `${LOGO_BASE}/postgres.webp`,     categoryId: "databases",
    desc: "功能強大的開源關聯式資料庫，支援豐富的資料型別與複雜查詢。" },
  { slug: "mariadb",         name: "MariaDB",          icon: "storage",    logo: `${LOGO_BASE}/mariadb.webp`,      categoryId: "databases",
    desc: "MySQL 相容的開源分支，社群維護快速更新。" },
  { slug: "mongodb",         name: "MongoDB",          icon: "dns",        logo: `${LOGO_BASE}/mongodb.webp`,      categoryId: "databases",
    desc: "彈性的文件型 NoSQL 資料庫，適合非結構化資料。" },
  { slug: "redis",           name: "Redis",            icon: "memory",     logo: `${LOGO_BASE}/redis.webp`,        categoryId: "databases",
    desc: "記憶體型鍵值資料庫，提供極致的存取效能。" },
  { slug: "jupyternotebook", name: "Jupyter Notebook", icon: "menu_book",  logo: `${LOGO_BASE}/jupyter.webp`,      categoryId: "data-science",
    desc: "資料科學與機器學習的互動式環境，支援 Python 科學運算。" },
  { slug: "grafana",         name: "Grafana",          icon: "show_chart", logo: `${LOGO_BASE}/grafana.webp`,      categoryId: "monitoring",
    desc: "強大的監控指標視覺化儀表板，支援多種資料來源。" },
  { slug: "n8n",             name: "n8n",              icon: "account_tree", logo: `${LOGO_BASE}/n8n.webp`,        categoryId: "automation",
    desc: "視覺化工作流程自動化工具，可串接 API、資料來源與外部服務。" },
  { slug: "openwebui",       name: "Open WebUI",       icon: "psychology", logo: `${LOGO_BASE}/open-webui.webp`,   categoryId: "ai-devtools",
    desc: "本地 LLM 對話介面，支援多種開源模型。" },
  { slug: "wordpress",       name: "WordPress",        icon: "article",    logo: `${LOGO_BASE}/wordpress.webp`,    categoryId: "webservers",
    desc: "全球最受歡迎的開源內容管理系統。" },
  { slug: "homepage",        name: "Homepage",         icon: "home",       logo: `${LOGO_BASE}/homepage.webp`,     categoryId: "webservers",
    desc: "簡潔優雅的個人入口儀表板。" },
];

export const CATEGORY_BY_ID = Object.fromEntries(
  TEMPLATE_CATEGORIES.map((c) => [c.id, c]),
);
