import { useState, useRef, useEffect, useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth }  from "../../contexts/AuthContext";
import styles from "./Sidebar.module.scss";
import MIcon from "../MIcon";

const navGroups = [
  {
    key: "personal",
    label: "個人",
    icon: "person",
    items: [
      { key: "dashboard",    label: "首頁",  icon: "dashboard" },
      { key: "my-resources", label: "我的資源", icon: "inventory_2" },
      { key: "my-requests",  label: "我的申請", icon: "assignment" },
    ],
  },
  {
    key: "network",
    label: "網路",
    icon: "router",
    items: [
      { key: "firewall",      label: "防火牆",     icon: "security" },
      { key: "reverse-proxy", label: "反向代理",   icon: "swap_horiz" },
      { key: "domain",        label: "網域管理",   icon: "domain" },
      { key: "ip-management", label: "IP 管理",    icon: "lan" },
      { key: "gateway",       label: "閘道 VM",    icon: "dns" },
    ],
  },
  {
    key: "resource",
    label: "資源",
    icon: "storage",
    items: [
      { key: "resource-mgmt",  label: "資源管理", icon: "storage" },
      { key: "templates",      label: "模板管理", icon: "library_books" },
      { key: "gpu-mgmt",       label: "GPU 管理", icon: "memory" },
      { key: "request-review", label: "申請審核", icon: "fact_check" },
      { key: "batch-review",   label: "批量審核", icon: "library_add_check" },
    ],
  },
  {
    key: "ai",
    label: "AI 服務",
    icon: "smart_toy",
    items: [
      { key: "ai-api",        label: "AI API 申請", icon: "psychology" },
      { key: "ai-api-review", label: "AI API 審核", icon: "rate_review" },
      { key: "ai-api-keys",   label: "AI API 金鑰", icon: "vpn_key" },
      { key: "ai-monitoring", label: "AI 監控",     icon: "monitor_heart" },
      { key: "ai-management", label: "AI 管理",     icon: "admin_panel_settings" },
    ],
  },
  {
    key: "teaching",
    label: "教學",
    icon: "school",
    items: [
      { key: "classroom", label: "虛擬教室", icon: "cast_for_education" },
      { key: "teaching",  label: "教學面板", icon: "grid_view" },
    ],
  },
  {
    key: "system",
    label: "系統管理",
    icon: "tune",
    items: [
      { key: "groups",    label: "群組",       icon: "groups" },
      { key: "admin",     label: "使用者管理", icon: "admin_panel_settings" },
      { key: "settings",  label: "系統設定",   icon: "settings" },
      { key: "quotas",    label: "配額管理",   icon: "data_usage" },
      { key: "monitoring", label: "資源監控",  icon: "monitor_heart" },
      { key: "migration", label: "Migration Jobs", icon: "move_down" },
      { key: "jobs",      label: "背景任務",   icon: "task_alt" },
      { key: "deploy-logs", label: "部署日誌", icon: "terminal" },
      { key: "audit",     label: "Audit Logs", icon: "receipt_long" },
    ],
  },
];

function NavGroup({ group, active, onSelect, collapsed, onExpand }) {
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
        title={collapsed ? group.label : undefined}
      >
        <MIcon name={group.icon} size={20} />
        {!collapsed && (
          <>
            <span className={styles.groupLabel}>{group.label}</span>
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
          {group.items.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`${styles.navItem} ${active === item.key ? styles.active : ""}`}
              onClick={() => onSelect(item.key)}
            >
              <span className={styles.navLabel}>{item.label}</span>
            </button>
          ))}
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

/** 通用彈出選單，供外觀與語言共用 */
function SelectPopup({ options, value, onSelect, onClose, triggerRef, closing }) {
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      const inPopup = ref.current?.contains(e.target);
      const inTrigger = triggerRef?.current?.contains(e.target);
      if (!inPopup && !inTrigger) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose, triggerRef]);

  return (
    <div className={`${styles.appearancePopup} ${closing ? styles.popupClosing : styles.popupOpening}`} ref={ref}>
      {options.map((opt) => (
        <button
          key={opt.key}
          type="button"
          className={`${styles.appearanceOption} ${value === opt.key ? styles.appearanceOptionActive : ""}`}
          onClick={() => { onSelect(opt.key); onClose(); }}
        >
          {opt.flag
            ? <span className={styles.optionFlag}>{opt.flag}</span>
            : <MIcon name={opt.icon} size={18} />
          }
          <span>{opt.label}</span>
        </button>
      ))}
    </div>
  );
}

function UserPopup({ user, onLogout, onSettings, onClose, triggerRef, closing }) {
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      const inPopup = ref.current?.contains(e.target);
      const inTrigger = triggerRef?.current?.contains(e.target);
      if (!inPopup && !inTrigger) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose, triggerRef]);

  return (
    <div className={`${styles.userPopup} ${closing ? styles.popupClosing : styles.popupOpening}`} ref={ref}>
      <div className={styles.userPopupHeader}>
        <div className={styles.userPopupAvatar}>
          {user?.full_name?.[0]?.toUpperCase() ?? user?.email?.[0]?.toUpperCase() ?? "U"}
        </div>
        <div className={styles.userPopupInfo}>
          <span className={styles.userName}>{user?.full_name ?? "—"}</span>
          <span className={styles.userEmail}>{user?.email ?? "—"}</span>
        </div>
      </div>
      <div className={styles.userPopupDivider} />
      <button type="button" className={styles.userPopupItem} onClick={() => { onClose(); onSettings(); }}>
        <MIcon name="settings" size={18} />
        <span>User Settings</span>
      </button>
      <button
        type="button"
        className={`${styles.userPopupItem} ${styles.userPopupItemDanger}`}
        onClick={() => { onClose(); onLogout(); }}
      >
        <MIcon name="logout" size={18} />
        <span>Log Out</span>
      </button>
    </div>
  );
}

const LANG_OPTIONS = [
  { key: "zh-TW", label: "繁體中文", flag: "🇹🇼" },
  { key: "en",    label: "English",  flag: "🇬🇧" },
  { key: "ja",    label: "日本語",   flag: "🇯🇵" },
];

export default function Sidebar({ collapsed, mobileOpen, onToggle, onClose }) {
  const navigate = useNavigate();
  const location = useLocation();
  const active   = location.pathname.split("/")[1] || "dashboard";
  const [lang, setLang] = useState("zh-TW");
  const langPopup  = usePopup();
  const userPopup  = usePopup();
  const langBtnRef = useRef(null);
  const userBtnRef = useRef(null);
  const { user, logout } = useAuth();

  const cls = [
    styles.sidebar,
    collapsed && styles.collapsed,
    mobileOpen && styles.mobileOpen,
  ]
    .filter(Boolean)
    .join(" ");

  const handleNav = (key) => {
    navigate(`/${key}`);
    onClose?.();
  };

  return (
    <aside className={cls}>
      {/* ===== Brand ===== */}
      <div className={styles.brand} onClick={() => window.innerWidth >= 768 && onToggle?.()}>
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
      <nav className={styles.nav}>
        {navGroups.map((group) => (
          <NavGroup
            key={group.key}
            group={group}
            active={active}
            onSelect={handleNav}
            collapsed={collapsed}
            onExpand={onToggle}
          />
        ))}
      </nav>

      {/* ===== Bottom section ===== */}
      <div className={styles.bottom}>
        {/* 語言選擇 */}
        <div className={styles.appearanceWrap}>
          {langPopup.open && (
            <SelectPopup
              options={LANG_OPTIONS}
              value={lang}
              onSelect={setLang}
              onClose={langPopup.close}
              triggerRef={langBtnRef}
              closing={langPopup.closing}
            />
          )}
          <button
            ref={langBtnRef}
            type="button"
            className={`${styles.navItem} ${langPopup.open && !langPopup.closing ? styles.active : ""}`}
            onClick={langPopup.toggle}
            title={collapsed ? "語言" : undefined}
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
            />
          )}
          <button
            ref={userBtnRef}
            type="button"
            className={`${styles.user} ${userPopup.open && !userPopup.closing ? styles.userActive : ""}`}
            onClick={userPopup.toggle}
            title={collapsed ? (user?.full_name ?? user?.email) : undefined}
          >
            <div className={styles.avatar}>
              {user?.full_name?.[0]?.toUpperCase() ?? user?.email?.[0]?.toUpperCase() ?? "U"}
            </div>
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
