import { useState, useRef, useEffect, useCallback, useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth }  from "../../contexts/AuthContext";
import { useUnsavedChanges } from "../../contexts/UnsavedChangesContext";
import { SUPPORTED_LANGUAGES, setLanguage } from "../../i18n";
import styles from "./Sidebar.module.scss";
import MIcon from "../MIcon";
import Avatar from "../Avatar/Avatar";
import JobsButton from "../Jobs/JobsButton";

const topItems = [
  { key: "dashboard", labelKey: "Sidebar.topDashboard", icon: "dashboard" },
  { key: "courses", labelKey: "Sidebar.topCourses", icon: "school", studentOnly: true },
];

const navGroups = [
  {
    key: "resource",
    labelKey: "Sidebar.groupResource",
    icon: "storage",
    items: [
      { key: "my-resources",  labelKey: "Sidebar.itemMyResources",    icon: "inventory_2" },
      { key: "my-requests",   labelKey: "Sidebar.itemMyRequests",    icon: "assignment" },
      { key: "resource-mgmt", labelKey: "Sidebar.itemResourceMgmt",    icon: "storage", adminOnly: true },
      { key: "templates",     labelKey: "Sidebar.itemTemplates",    icon: "library_books", instructorOnly: true },
      { key: "gpu-mgmt",      labelKey: "Sidebar.itemGpuMgmt",    icon: "memory", adminOnly: true },
    ],
  },
  {
    key: "review",
    labelKey: "Sidebar.groupReview",
    icon: "fact_check",
    items: [
      { key: "request-review", labelKey: "Sidebar.itemRequestReview", icon: "fact_check", adminOnly: true },
      { key: "batch-review",   labelKey: "Sidebar.itemBatchReview", icon: "library_add_check", adminOnly: true },
      { key: "ai-api-review",  labelKey: "Sidebar.itemAiApiReview", icon: "rate_review", adminOnly: true },
    ],
  },
  {
    key: "network",
    labelKey: "Sidebar.groupNetwork",
    icon: "router",
    items: [
      { key: "firewall",      labelKey: "Sidebar.itemFirewall",     icon: "security" },
      /* 對外網址已併入「網域管理」（管理員）；使用者從防火牆拓撲頁或資源詳情「進階設定 › 防火牆」的連線對話框發布 */
    ],
  },
  {
    key: "ai",
    labelKey: "Sidebar.groupAi",
    icon: "smart_toy",
    items: [
      { key: "ai-api",        labelKey: "Sidebar.itemAiApi",   icon: "psychology" },
      { key: "ai-api-keys",   labelKey: "Sidebar.itemAiApiKeys", icon: "vpn_key", adminOnly: true },
      { key: "ai-monitoring", labelKey: "Sidebar.itemAiMonitoring", icon: "monitor_heart", adminOnly: true },
      /* PVE 維運助手不放側欄：管理者首頁就是它的入口，那裡同時看得到待處理的問題 */
    ],
  },
  {
    key: "teaching",
    labelKey: "Sidebar.groupTeaching",
    icon: "school",
    items: [
      { key: "class-management", labelKey: "Sidebar.itemClassManagement", icon: "groups_2", instructorOnly: true },
      { key: "course-template-management", labelKey: "Sidebar.itemCourseTemplateManagement", icon: "view_quilt", instructorOnly: true },
    ],
  },
  {
    key: "monitoring",
    labelKey: "Sidebar.groupMonitoring",
    icon: "insights",
    items: [
      { key: "monitoring",    labelKey: "Sidebar.itemMonitoring",       icon: "monitor_heart", adminOnly: true },
      { key: "jobs",          labelKey: "Sidebar.itemJobs",       icon: "task_alt" },
      { key: "audit",         labelKey: "Sidebar.itemAudit",     icon: "receipt_long", adminOnly: true },
    ],
  },
];

/* 系統管理已移出主導覽分類：由底部「管理員設定」進入專屬核心側欄（僅管理員），路由沿用原本的網址 */
const adminSettingsItems = [
  { key: "admin",           labelKey: "Sidebar.itemAdmin",          icon: "admin_panel_settings" },
  { key: "ip-management",   labelKey: "Sidebar.itemIpManagement",   icon: "lan" },
  { key: "domain",          labelKey: "Sidebar.itemDomain",         icon: "domain" },
  { key: "gateway",         labelKey: "Sidebar.itemGateway",        icon: "dns" },
  /* 原「系統設定」的七個分頁，2026-09 各自升格為獨立頁面 */
  { key: "pve-connections", labelKey: "Sidebar.itemPveConnections", icon: "device_hub" },
  { key: "scheduler",       labelKey: "Sidebar.itemScheduler",      icon: "settings_input_component" },
  { key: "governance",      labelKey: "Sidebar.itemGovernance",     icon: "policy" },
  { key: "quotas",          labelKey: "Sidebar.itemQuotas",         icon: "data_usage" },
  { key: "ldap",            labelKey: "Sidebar.itemLdap",           icon: "badge" },
  { key: "nodes",           labelKey: "Sidebar.itemNodes",          icon: "lock" },
  { key: "storage",         labelKey: "Sidebar.itemStorage",        icon: "storage" },
];

/** 釘選狀態存 localStorage，跨 session 保留（不可用時僅本次瀏覽生效） */
const PIN_STORAGE_KEY = "skylab.sidebarPins";

function loadPinnedKeys() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(PIN_STORAGE_KEY) ?? "[]");
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

function savePinnedKeys(keys) {
  try {
    window.localStorage.setItem(PIN_STORAGE_KEY, JSON.stringify(keys));
  } catch {
    // localStorage 不可用時釘選僅本次瀏覽生效
  }
}

function NavGroup({ group, active, onSelect, collapsed, onExpand, pinnedKeys, onTogglePin }) {
  const { t } = useTranslation("common");
  const [open, setOpen] = useState(
    group.items.some((i) => i.key === active)
  );

  const hasActive = group.items.some((i) => i.key === active);

  const handleHeaderClick = () => {
    if (collapsed) {
      onExpand();
      setOpen(true);
    } else {
      setOpen((o) => !o);
    }
  };

  return (
    <div className={styles.group}>
      <button
        type="button"
        className={`${styles.groupHeader} ${hasActive ? styles.groupHeaderActive : ""}`}
        onClick={handleHeaderClick}
        title={collapsed ? t(group.labelKey) : undefined}
        aria-label={t(group.labelKey)}
        aria-expanded={!collapsed && open}
      >
        <MIcon name={group.icon} size={20} />
        {!collapsed && (
          <>
            <span className={styles.groupLabel}>{t(group.labelKey)}</span>
            <span className={`${styles.groupChevron} ${open ? styles.open : ""}`}>
              <MIcon name="chevron_right" size={16} />
            </span>
          </>
        )}
      </button>

      <div
        className={`${styles.groupItems} ${!collapsed && open ? styles.groupItemsOpen : ""}`}
      >
        <div className={styles.groupItemsInner}>
          {group.items.map((item) => {
            const pinned = pinnedKeys.includes(item.key);
            return (
              <div key={item.key} className={styles.navItemRow}>
                <button
                  type="button"
                  className={`${styles.navItem} ${active === item.key ? styles.active : ""}`}
                  onClick={() => onSelect(item.key)}
                  aria-label={t(item.labelKey)}
                >
                  <span className={styles.navLabel}>{t(item.labelKey)}</span>
                </button>
                <button
                  type="button"
                  className={`${styles.pinBtn} ${pinned ? styles.pinBtnPinned : ""}`}
                  onClick={() => onTogglePin(item.key)}
                  title={pinned ? t("Sidebar.unpin") : t("Sidebar.pin")}
                  aria-label={pinned ? t("Sidebar.unpin") : t("Sidebar.pin")}
                  aria-pressed={pinned}
                >
                  <MIcon name="push_pin" size={14} filled={pinned} />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** 管理 popup 的開關，含 closing 動畫狀態 */
function usePopup(DURATION = 150) {
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const timerRef = useRef(null);

  const close = useCallback(() => {
    setClosing(true);
    timerRef.current = setTimeout(() => {
      setOpen(false);
      setClosing(false);
    }, DURATION);
  }, [DURATION]);

  const toggle = useCallback(() => {
    if (open && !closing) {
      close();
    } else if (!open) {
      clearTimeout(timerRef.current);
      setClosing(false);
      setOpen(true);
    }
  }, [open, closing, close]);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  return { open, closing, toggle, close };
}

/* 側欄有 overflow 裁切，彈窗一律 portal 到 body 再依觸發鈕定位：
   展開時蓋在觸發鈕上方同寬，收合時貼著側欄右緣飛出、底部對齊觸發鈕 */
function usePopupPosition(triggerRef, collapsed) {
  const [pos, setPos] = useState(null);

  const updatePos = useCallback(() => {
    const btn = triggerRef?.current;
    const rect = btn?.getBoundingClientRect();
    if (!rect) return;
    if (collapsed) {
      const anchorRight = btn.closest("aside")?.getBoundingClientRect().right ?? rect.right;
      setPos({ left: anchorRight + 8, bottom: window.innerHeight - rect.bottom, width: "max-content", minWidth: 190 });
    } else {
      setPos({ left: rect.left, bottom: window.innerHeight - rect.top + 8, width: rect.width });
    }
  }, [collapsed, triggerRef]);

  useLayoutEffect(() => {
    updatePos();
    window.addEventListener("resize", updatePos);
    return () => window.removeEventListener("resize", updatePos);
  }, [updatePos]);

  return pos;
}

/** 通用彈出選單，供外觀與語言共用 */
function SelectPopup({ options, value, onSelect, onClose, triggerRef, closing, collapsed }) {
  const ref = useRef(null);
  const pos = usePopupPosition(triggerRef, collapsed);

  useEffect(() => {
    const handler = (e) => {
      const inPopup = ref.current?.contains(e.target);
      const inTrigger = triggerRef?.current?.contains(e.target);
      if (!inPopup && !inTrigger) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose, triggerRef]);

  if (!pos) return null;
  return createPortal(
    <div className={`${styles.appearancePopup} ${closing ? styles.popupClosing : styles.popupOpening}`} ref={ref} style={pos}>
      {options.map((opt) => (
        <button
          key={opt.key}
          type="button"
          className={`${styles.appearanceOption} ${value === opt.key ? styles.appearanceOptionActive : ""} ${opt.disabled ? styles.appearanceOptionDisabled : ""}`}
          disabled={opt.disabled}
          onClick={() => { onSelect(opt.key); onClose(); }}
        >
          {opt.flag
            ? <span className={styles.optionFlag}>{opt.flag}</span>
            : <MIcon name={opt.icon} size={18} />
          }
          <span>{opt.label}</span>
          {opt.hint && <span className={styles.optionHint}>{opt.hint}</span>}
        </button>
      ))}
    </div>,
    document.body
  );
}

function UserPopup({ user, onLogout, onSettings, onClose, triggerRef, closing, collapsed }) {
  const { t } = useTranslation("common");
  const ref = useRef(null);
  const pos = usePopupPosition(triggerRef, collapsed);

  useEffect(() => {
    const handler = (e) => {
      const inPopup = ref.current?.contains(e.target);
      const inTrigger = triggerRef?.current?.contains(e.target);
      if (!inPopup && !inTrigger) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose, triggerRef]);

  if (!pos) return null;
  return createPortal(
    <div className={`${styles.userPopup} ${closing ? styles.popupClosing : styles.popupOpening}`} ref={ref} style={pos}>
      <div className={styles.userPopupHeader}>
        <Avatar user={user} size={32} />
        <div className={styles.userPopupInfo}>
          <span className={styles.userName}>{user?.full_name ?? "—"}</span>
          <span className={styles.userEmail}>{user?.email ?? "—"}</span>
        </div>
      </div>
      <div className={styles.userPopupDivider} />
      <button type="button" className={styles.userPopupItem} onClick={() => { onClose(); onSettings(); }}>
        <MIcon name="settings" size={18} />
        <span>{t("Sidebar.userSettings")}</span>
      </button>
      <button
        type="button"
        className={`${styles.userPopupItem} ${styles.userPopupItemDanger}`}
        onClick={() => { onClose(); onLogout(); }}
      >
        <MIcon name="logout" size={18} />
        <span>{t("Sidebar.logOut")}</span>
      </button>
    </div>,
    document.body
  );
}

const LANG_OPTIONS = [
  { key: "zh-TW", label: "繁體中文", flag: "🇹🇼" },
  { key: "en",    label: "English",  flag: "🇬🇧" },
  { key: "ja",    label: "日本語",   flag: "🇯🇵" },
];

export default function Sidebar({ collapsed, mobileOpen, onToggle, onClose }) {
  const { t, i18n } = useTranslation("common");
  const navigate = useNavigate();
  const location = useLocation();
  const active   = location.pathname.split("/")[1] || "dashboard";
  const lang = SUPPORTED_LANGUAGES.includes(i18n.language) ? i18n.language : "zh-TW";
  const langPopup  = usePopup();
  const userPopup  = usePopup();
  const langBtnRef = useRef(null);
  const userBtnRef = useRef(null);
  const { user, logout } = useAuth();
  const { confirmLeave } = useUnsavedChanges();
  const isAdmin = Boolean(user?.is_superuser || user?.role === "admin");
  const canTeach = isAdmin || user?.role === "teacher";
  /* 身在系統管理頁面時，整支側欄切換成「管理員設定」核心側欄 */
  const inAdminSettings = isAdmin && adminSettingsItems.some((item) => item.key === active);
  const visibleTopItems = topItems.filter((item) => !item.studentOnly || !canTeach);
  const visibleNavGroups = navGroups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) =>
        (!item.adminOnly || isAdmin) && (!item.instructorOnly || canTeach)
      ),
    }))
    .filter((group) => group.items.length > 0);

  const [pinnedKeys, setPinnedKeys] = useState(loadPinnedKeys);
  const togglePin = useCallback((key) => {
    setPinnedKeys((prev) => {
      const next = prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key];
      savePinnedKeys(next);
      return next;
    });
  }, []);
  // 只留權限內看得到的項目；沒權限的釘選保留在 storage，換帳號登入不會消失
  const visibleItems = visibleNavGroups.flatMap((group) => group.items);
  const pinnedItems = pinnedKeys
    .map((key) => visibleItems.find((item) => item.key === key))
    .filter(Boolean);

  const cls = [
    styles.sidebar,
    collapsed && styles.collapsed,
    mobileOpen && styles.mobileOpen,
  ]
    .filter(Boolean)
    .join(" ");

  const handleNav = async (key) => {
    if (!(await confirmLeave())) return;
    navigate(`/${key}`);
    onClose?.();
  };

  /* 收合／展開有寬度動畫，portal 彈窗的定位會跑掉，切換時直接收起 */
  useEffect(() => {
    if (langPopup.open) langPopup.close();
    if (userPopup.open) userPopup.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapsed]);

  return (
    <aside className={cls}>
      {/* ===== Brand ===== */}
      <div className={styles.brand} onClick={() => window.innerWidth >= 1024 && onToggle?.()}>
        <span className={styles.brandIcon}>
          <img src="/favicon.png" alt="SkyLab" />
        </span>
        {!collapsed && (
          <>
            <span className={styles.brandText}>SkyLab</span>
          </>
        )}
      </div>

      <div className={styles.brandDivider} />

      {/* ===== Main nav ===== */}
      {inAdminSettings ? (
        <nav className={styles.nav}>
          <button
            type="button"
            className={styles.navItem}
            onClick={() => handleNav("dashboard")}
            title={collapsed ? t("Sidebar.backToConsole") : undefined}
            aria-label={t("Sidebar.backToConsole")}
          >
            <MIcon name="arrow_back" size={20} />
            {!collapsed && <span className={styles.navLabel}>{t("Sidebar.backToConsole")}</span>}
          </button>
          {!collapsed && (
            <div className={styles.sectionTitle}>{t("Sidebar.adminSettings")}</div>
          )}
          {adminSettingsItems.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`${styles.navItem} ${active === item.key ? styles.active : ""}`}
              onClick={() => handleNav(item.key)}
              title={collapsed ? t(item.labelKey) : undefined}
              aria-label={t(item.labelKey)}
            >
              <MIcon name={item.icon} size={20} />
              {!collapsed && <span className={styles.navLabel}>{t(item.labelKey)}</span>}
            </button>
          ))}
        </nav>
      ) : (
      <nav className={styles.nav}>
        {visibleTopItems.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`${styles.navItem} ${active === item.key ? styles.active : ""}`}
            onClick={() => handleNav(item.key)}
            title={collapsed ? t(item.labelKey) : undefined}
            aria-label={t(item.labelKey)}
          >
            <MIcon name={item.icon} size={20} />
            {!collapsed && <span className={styles.navLabel}>{t(item.labelKey)}</span>}
          </button>
        ))}
        {/* 釘選的快速捷徑（保留釘選順序） */}
        {pinnedItems.map((item) => (
          <div key={`pinned-${item.key}`} className={styles.navItemRow}>
            <button
              type="button"
              className={`${styles.navItem} ${active === item.key ? styles.active : ""}`}
              onClick={() => handleNav(item.key)}
              title={collapsed ? t(item.labelKey) : undefined}
              aria-label={t(item.labelKey)}
            >
              <MIcon name={item.icon} size={20} />
              {!collapsed && <span className={styles.navLabel}>{t(item.labelKey)}</span>}
            </button>
            {!collapsed && (
              <button
                type="button"
                className={styles.pinBtn}
                onClick={() => togglePin(item.key)}
                title={t("Sidebar.unpin")}
                aria-label={t("Sidebar.unpin")}
              >
                <MIcon name="push_pin" size={14} filled />
              </button>
            )}
          </div>
        ))}
        {visibleNavGroups.map((group) => (
          <NavGroup
            key={group.key}
            group={group}
            active={active}
            onSelect={handleNav}
            collapsed={collapsed}
            onExpand={onToggle}
            pinnedKeys={pinnedKeys}
            onTogglePin={togglePin}
          />
        ))}
      </nav>
      )}

      {/* ===== Bottom section ===== */}
      <div className={styles.bottom}>
        {/* 管理員設定：系統管理頁面的入口，進入後側欄切換成核心側欄 */}
        {isAdmin && (
          <button
            type="button"
            className={`${styles.navItem} ${inAdminSettings ? styles.active : ""}`}
            onClick={() => handleNav("admin")}
            title={collapsed ? t("Sidebar.adminSettings") : undefined}
            aria-label={t("Sidebar.adminSettings")}
          >
            <MIcon name="admin_panel_settings" size={20} />
            {!collapsed && <span className={styles.navLabel}>{t("Sidebar.adminSettings")}</span>}
          </button>
        )}

        {/* 背景任務（全站入口，狀態由 DashboardLayout 的 JobsProvider 提供） */}
        <JobsButton collapsed={collapsed} />

        {/* 語言選擇 */}
        <div className={styles.appearanceWrap}>
          {langPopup.open && (
            <SelectPopup
              options={LANG_OPTIONS}
              value={lang}
              onSelect={setLanguage}
              onClose={langPopup.close}
              triggerRef={langBtnRef}
              closing={langPopup.closing}
              collapsed={collapsed}
            />
          )}
          <button
            ref={langBtnRef}
            type="button"
            className={`${styles.navItem} ${langPopup.open && !langPopup.closing ? styles.active : ""}`}
            onClick={langPopup.toggle}
            title={collapsed ? "語言" : undefined}
            aria-label="語言 / Language"
            aria-expanded={langPopup.open}
          >
            <MIcon name="language" size={20} />
            {!collapsed && <span className={styles.navLabel}>語言 / Language</span>}
            {!collapsed && <span className={styles.navHint}>{LANG_OPTIONS.find(o => o.key === lang)?.label}</span>}
          </button>
        </div>

        {/* 使用者資料 */}
        <div className={styles.appearanceWrap}>
          {userPopup.open && (
            <UserPopup
              user={user}
              onLogout={logout}
              onSettings={() => handleNav("account")}
              onClose={userPopup.close}
              triggerRef={userBtnRef}
              closing={userPopup.closing}
              collapsed={collapsed}
            />
          )}
          <button
            ref={userBtnRef}
            type="button"
            className={`${styles.user} ${userPopup.open && !userPopup.closing ? styles.userActive : ""}`}
            onClick={userPopup.toggle}
            title={collapsed ? (user?.full_name ?? user?.email) : undefined}
            aria-label={t("Sidebar.userMenuAriaLabel", { name: user?.full_name ?? user?.email ?? "" })}
            aria-expanded={userPopup.open}
          >
            <Avatar user={user} size={32} className={styles.avatar} />
            {!collapsed && (
              <>
                <div className={styles.userInfo}>
                  <span className={styles.userName}>{user?.full_name ?? "—"}</span>
                  <span className={styles.userEmail}>{user?.email ?? "—"}</span>
                </div>
                <MIcon name={userPopup.open && !userPopup.closing ? "expand_more" : "unfold_more"} size={16} />
              </>
            )}
          </button>
        </div>
      </div>
    </aside>
  );
}
