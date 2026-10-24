/**
 * 系統管理的設定頁導覽：使用者管理、PVE 管理、資源排程、資源政策（治理＋配額）、LDAP、儲存空間。
 * 表單類頁面的卡片要等設定載入後才出現，一律標 deferred；打開的對話框只做示範，離開該步就關掉。
 */
export const SYSTEM_SETTINGS_GUIDES = {
  admin: {
    titleKey: "UserGuide.adminUsers.title",
    cleanupSelector: '[data-guide="admin-user-modal-close"]',
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.adminUsers.intro.title", textKey: "UserGuide.adminUsers.intro.text" },
      { selector: '[data-page-guide="actions"]', titleKey: "UserGuide.adminUsers.add.title", textKey: "UserGuide.adminUsers.add.text" },
      { selector: '[data-guide="admin-groups"]', titleKey: "UserGuide.adminUsers.groups.title", textKey: "UserGuide.adminUsers.groups.text" },
      { selector: '[data-guide="admin-registration"]', titleKey: "UserGuide.adminUsers.registration.title", textKey: "UserGuide.adminUsers.registration.text" },
      { selector: '[data-guide="admin-pending"]', titleKey: "UserGuide.adminUsers.pending.title", textKey: "UserGuide.adminUsers.pending.text" },
      { selector: '[data-guide="admin-summary"]', titleKey: "UserGuide.adminUsers.summary.title", textKey: "UserGuide.adminUsers.summary.text" },
      { selector: '[data-guide="admin-user-row"]', deferred: true, titleKey: "UserGuide.adminUsers.row.title", textKey: "UserGuide.adminUsers.row.text" },
      /* 打開第一列的編輯視窗介紹「強制兩步驟驗證」，只示範不儲存，離開這步就關掉 */
      {
        selector: '[data-guide="admin-user-security"]',
        activateSelector: '[data-guide="admin-user-edit"]',
        deactivateSelector: '[data-guide="admin-user-modal-close"]',
        deferred: true,
        titleKey: "UserGuide.adminUsers.security.title",
        textKey: "UserGuide.adminUsers.security.text",
      },
    ],
  },
  "pve-connections": {
    titleKey: "UserGuide.pveConnections.title",
    cleanupSelector: '[data-guide="pve-connection-form-close"]',
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.pveConnections.intro.title", textKey: "UserGuide.pveConnections.intro.text" },
      /* 「新增連線」有連線時在頁首、沒有時在空狀態，兩處掛同一個錨點；載入完才出現 */
      { selector: '[data-guide="pve-add-connection"]', deferred: true, titleKey: "UserGuide.pveConnections.add.title", textKey: "UserGuide.pveConnections.add.text" },
      {
        selector: '[data-guide="pve-connection-resources"]',
        activateSelector: '[data-guide="pve-add-connection"]',
        deactivateSelector: '[data-guide="pve-connection-form-close"]',
        deferred: true,
        titleKey: "UserGuide.pveConnections.resources.title",
        textKey: "UserGuide.pveConnections.resources.text",
      },
      { selector: '[data-guide="pve-connection-row"]', conditionSelector: '[data-guide="pve-connection-row"]', deferred: true, titleKey: "UserGuide.pveConnections.row.title", textKey: "UserGuide.pveConnections.row.text" },
      { selector: '[data-guide="pve-connection-actions"]', conditionSelector: '[data-guide="pve-connection-actions"]', deferred: true, titleKey: "UserGuide.pveConnections.actions.title", textKey: "UserGuide.pveConnections.actions.text" },
      { selector: '[data-guide="pve-nodes"]', titleKey: "UserGuide.pveConnections.nodes.title", textKey: "UserGuide.pveConnections.nodes.text" },
    ],
  },
  scheduler: {
    titleKey: "UserGuide.scheduler.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.scheduler.intro.title", textKey: "UserGuide.scheduler.intro.text" },
      { selector: '[data-guide="scheduler-placement"]', deferred: true, titleKey: "UserGuide.scheduler.placement.title", textKey: "UserGuide.scheduler.placement.text" },
      { selector: '[data-guide="scheduler-thresholds"]', deferred: true, titleKey: "UserGuide.scheduler.thresholds.title", textKey: "UserGuide.scheduler.thresholds.text" },
      { selector: '[data-guide="scheduler-boot"]', deferred: true, titleKey: "UserGuide.scheduler.boot.title", textKey: "UserGuide.scheduler.boot.text" },
      { selector: '[data-guide="scheduler-practice"]', deferred: true, titleKey: "UserGuide.scheduler.practice.title", textKey: "UserGuide.scheduler.practice.text" },
      { selector: '[data-guide="scheduler-save"]', deferred: true, titleKey: "UserGuide.scheduler.save.title", textKey: "UserGuide.scheduler.save.text" },
    ],
  },
  governance: {
    titleKey: "UserGuide.governance.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.governance.intro.title", textKey: "UserGuide.governance.intro.text" },
      { selector: '[data-guide="governance-alerts"]', deferred: true, titleKey: "UserGuide.governance.alerts.title", textKey: "UserGuide.governance.alerts.text" },
      { selector: '[data-guide="governance-ttl"]', deferred: true, titleKey: "UserGuide.governance.ttl.title", textKey: "UserGuide.governance.ttl.text" },
      { selector: '[data-guide="governance-idle"]', deferred: true, titleKey: "UserGuide.governance.idle.title", textKey: "UserGuide.governance.idle.text" },
      { selector: '[data-guide="governance-mining"]', deferred: true, titleKey: "UserGuide.governance.mining.title", textKey: "UserGuide.governance.mining.text" },
      { selector: '[data-guide="governance-save"]', deferred: true, titleKey: "UserGuide.governance.save.title", textKey: "UserGuide.governance.save.text" },
      { selector: '[data-guide="governance-quota-global"]', deferred: true, titleKey: "UserGuide.governance.quotaGlobal.title", textKey: "UserGuide.governance.quotaGlobal.text" },
      { selector: '[data-guide="governance-quota-overrides"]', titleKey: "UserGuide.governance.quotaOverrides.title", textKey: "UserGuide.governance.quotaOverrides.text" },
    ],
  },
  ldap: {
    titleKey: "UserGuide.ldap.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.ldap.intro.title", textKey: "UserGuide.ldap.intro.text" },
      { selector: '[data-guide="ldap-login"]', deferred: true, titleKey: "UserGuide.ldap.login.title", textKey: "UserGuide.ldap.login.text" },
      { selector: '[data-guide="ldap-service-account"]', deferred: true, titleKey: "UserGuide.ldap.service.title", textKey: "UserGuide.ldap.service.text" },
      { selector: '[data-guide="ldap-role-mapping"]', deferred: true, titleKey: "UserGuide.ldap.roles.title", textKey: "UserGuide.ldap.roles.text" },
      { selector: '[data-guide="ldap-actions"]', deferred: true, titleKey: "UserGuide.ldap.actions.title", textKey: "UserGuide.ldap.actions.text" },
    ],
  },
  storage: {
    titleKey: "UserGuide.storage.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.storage.intro.title", textKey: "UserGuide.storage.intro.text" },
      { selector: '[data-guide="storage-filters"]', titleKey: "UserGuide.storage.filters.title", textKey: "UserGuide.storage.filters.text" },
      /* 以下都在 Storage 列上，資料載入後才出現；指向第一列 */
      { selector: '[data-guide="storage-row"]', conditionSelector: '[data-guide="storage-row"]', deferred: true, titleKey: "UserGuide.storage.row.title", textKey: "UserGuide.storage.row.text" },
      { selector: '[data-guide="storage-speed-tier"]', conditionSelector: '[data-guide="storage-speed-tier"]', deferred: true, titleKey: "UserGuide.storage.speed.title", textKey: "UserGuide.storage.speed.text" },
      { selector: '[data-guide="storage-priority"]', conditionSelector: '[data-guide="storage-priority"]', deferred: true, titleKey: "UserGuide.storage.priority.title", textKey: "UserGuide.storage.priority.text" },
      { selector: '[data-guide="storage-enabled"]', conditionSelector: '[data-guide="storage-enabled"]', deferred: true, titleKey: "UserGuide.storage.enabled.title", textKey: "UserGuide.storage.enabled.text" },
    ],
  },
};
