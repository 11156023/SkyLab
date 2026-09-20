/**
 * 全站導覽目錄。
 *
 * 每個可到達的主畫面都有可重播的入口，但不會要求使用者逐頁看導覽。
 * 真正需要跨層操作的任務由 UserGuide 的詳細設定接手。
 */
const ROUTE_GUIDES = [
  { match: /^\/dashboard$/, id: "dashboard", icon: "space_dashboard", profile: "explore" },
  { match: /^\/courses$/, id: "courses", icon: "school", profile: "explore" },
  { match: /^\/courses\/[^/]+$/, id: "course", icon: "menu_book", profile: "learning" },
  { match: /^\/courses\/[^/]+\/weeks\/[^/]+$/, id: "course-week", icon: "event_note", profile: "learning" },
  { match: /^\/dashboard\/course\/[^/]+$/, id: "student-course", icon: "menu_book", profile: "learning" },
  { match: /^\/quick-create$/, id: "quick-create", icon: "bolt", profile: "explore" },
  { match: /^\/quick-template\/[^/]+$/, id: "quick-practice", icon: "bolt", profile: "configure" },
  { match: /^\/my-resources$/, id: "my-resources", icon: "computer", profile: "resource" },
  { match: /^\/my-resources\/[^/]+$/, id: "resource-detail", icon: "dns", profile: "resource" },
  { match: /^\/my-requests$/, id: "my-requests", icon: "assignment", profile: "request" },
  { match: /^\/account$/, id: "account", icon: "manage_accounts", profile: "configure" },

  { match: /^\/resource-mgmt$/, id: "resource-management", icon: "storage", profile: "resource" },
  { match: /^\/resource-mgmt\/[^/]+$/, id: "managed-resource-detail", icon: "dns", profile: "resource" },
  { match: /^\/request-review$/, id: "request-review", icon: "fact_check", profile: "review" },
  { match: /^\/gpu-mgmt$/, id: "gpu-management", icon: "memory", profile: "monitor" },
  { match: /^\/batch-review$/, id: "batch-review", icon: "library_add_check", profile: "review" },
  { match: /^\/templates$/, id: "templates", icon: "library_books", profile: "configure" },

  { match: /^\/ai-api$/, id: "ai-api", icon: "psychology", profile: "request" },
  { match: /^\/ai-api-review$/, id: "ai-api-review", icon: "rate_review", profile: "review" },
  { match: /^\/ai-api-keys$/, id: "ai-api-keys", icon: "vpn_key", profile: "resource" },
  { match: /^\/ai-monitoring$/, id: "ai-monitoring", icon: "monitor_heart", profile: "monitor" },

  { match: /^\/course-cms$/, id: "course-cms", icon: "school", profile: "teaching" },
  { match: /^\/course-template-management$/, id: "course-templates", icon: "view_quilt", profile: "teaching" },
  { match: /^\/course-template-management\/(?:new|[^/]+)$/, id: "course-template-editor", icon: "edit_note", profile: "teaching" },
  { match: /^\/class-management$/, id: "class-management", icon: "groups_2", profile: "teaching" },
  { match: /^\/class-setup$/, id: "class-setup", icon: "tune", profile: "teaching" },
  { match: /^\/class-management\/[^/]+\/ai$/, id: "ai-judge", icon: "rule", profile: "workflow" },
  { match: /^\/class-management\/[^/]+(?:\/[^/]+)?$/, id: "class-workspace", icon: "co_present", profile: "teaching" },

  { match: /^\/admin$/, id: "admin", icon: "admin_panel_settings", profile: "configure" },
  { match: /^\/pve-connections$/, id: "pve-connections", icon: "device_hub", profile: "configure" },
  { match: /^\/scheduler$/, id: "scheduler", icon: "settings_input_component", profile: "configure" },
  { match: /^\/governance$/, id: "governance", icon: "policy", profile: "configure" },
  { match: /^\/quotas$/, id: "quotas", icon: "data_usage", profile: "configure" },
  { match: /^\/ldap$/, id: "ldap", icon: "badge", profile: "configure" },
  { match: /^\/nodes$/, id: "nodes", icon: "hub", profile: "monitor" },
  { match: /^\/storage$/, id: "storage", icon: "storage", profile: "monitor" },
  { match: /^\/monitoring$/, id: "monitoring", icon: "monitor_heart", profile: "monitor" },
  { match: /^\/ip-management$/, id: "ip-management", icon: "lan", profile: "configure" },
  { match: /^\/audit$/, id: "audit", icon: "receipt_long", profile: "monitor" },
  { match: /^\/jobs$/, id: "jobs", icon: "task_alt", profile: "monitor" },

  { match: /^\/firewall$/, id: "firewall", icon: "security", profile: "workflow" },
  { match: /^\/domain$/, id: "domain", icon: "domain", profile: "configure" },
  { match: /^\/gateway$/, id: "gateway", icon: "dns", profile: "configure" },
  { match: /^\/reverse-proxy$/, id: "reverse-proxy", icon: "swap_horiz", profile: "workflow" },
];

export const GENERIC_TOUR_STEPS = [
  {
    selector: '[data-page-guide="header"]',
    titleKey: "UserGuide.generic.headerTitle",
    textKey: "UserGuide.generic.headerText",
  },
  {
    selector: 'main [role="tablist"], main form, main table, main section, main article',
    titleKey: "UserGuide.generic.workspaceTitle",
    textKey: "UserGuide.generic.workspaceText",
    optional: true,
  },
  {
    selector: 'main button:not([data-user-guide-trigger]), main a[href]',
    titleKey: "UserGuide.generic.actionTitle",
    textKey: "UserGuide.generic.actionText",
    optional: true,
  },
];

export function getRouteGuide(pathname) {
  const route = ROUTE_GUIDES.find((item) => item.match.test(pathname));
  if (!route) return null;
  return {
    ...route,
    generic: true,
    guideVersion: "v8",
    steps: GENERIC_TOUR_STEPS,
  };
}

export { ROUTE_GUIDES };
