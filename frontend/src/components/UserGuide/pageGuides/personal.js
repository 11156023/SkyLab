/**
 * 個人區塊的專屬導覽：管理員／教師首頁、快速練習清單與啟動確認頁、帳號設定。
 * dashboard-admin／dashboard-teacher 不是 routeGuides 的 id，是 UserGuide 依角色替 /dashboard 挑的鍵。
 */
export const PERSONAL_GUIDES = {
  "dashboard-admin": {
    titleKey: "UserGuide.adminHome.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.adminHome.intro.title", textKey: "UserGuide.adminHome.intro.text" },
      /* 放大對話時頁首右側會收起來 */
      { selector: '[data-page-guide="actions"]', optional: true, titleKey: "UserGuide.adminHome.freshness.title", textKey: "UserGuide.adminHome.freshness.text" },
      /* 還沒取得 PVE 概況時不顯示統計卡 */
      { selector: '[data-guide="admin-home-stats"]', optional: true, titleKey: "UserGuide.adminHome.stats.title", textKey: "UserGuide.adminHome.stats.text" },
      { selector: '[data-guide="admin-home-assistant"]', titleKey: "UserGuide.adminHome.assistant.title", textKey: "UserGuide.adminHome.assistant.text" },
      /* 待辦兩欄在第一次檢查完成前是「正在確認」，載入後才出現 */
      { selector: '[data-guide="admin-home-urgent"]', deferred: true, titleKey: "UserGuide.adminHome.urgent.title", textKey: "UserGuide.adminHome.urgent.text" },
      { selector: '[data-guide="admin-home-today"]', deferred: true, titleKey: "UserGuide.adminHome.today.title", textKey: "UserGuide.adminHome.today.text" },
    ],
  },
  "dashboard-teacher": {
    titleKey: "UserGuide.teacherHome.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.teacherHome.intro.title", textKey: "UserGuide.teacherHome.intro.text" },
      { selector: '[data-page-guide="actions"]', titleKey: "UserGuide.teacherHome.createClass.title", textKey: "UserGuide.teacherHome.createClass.text" },
      { selector: '[data-guide="teacher-home-metrics"]', titleKey: "UserGuide.teacherHome.metrics.title", textKey: "UserGuide.teacherHome.metrics.text" },
      { selector: '[data-guide="teacher-home-classes"]', titleKey: "UserGuide.teacherHome.classes.title", textKey: "UserGuide.teacherHome.classes.text" },
    ],
  },
  "quick-create": {
    titleKey: "UserGuide.quickCreate.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.quickCreate.intro.title", textKey: "UserGuide.quickCreate.intro.text" },
      /* 沒有可用環境（空狀態、載入失敗）時不會有資料夾清單，以下各步直接不列入，免得逐步等逾時 */
      { selector: '[data-guide="quick-template-grid"]', conditionSelector: '[data-guide="quick-template-grid"]', titleKey: "UserGuide.quickCreate.grid.title", textKey: "UserGuide.quickCreate.grid.text" },
      { selector: '[data-guide="quick-template-sheet"]', conditionSelector: '[data-guide="quick-template-grid"]', titleKey: "UserGuide.quickCreate.sheets.title", textKey: "UserGuide.quickCreate.sheets.text" },
      /* 資源都沒填（CPU、記憶體、時數皆 0）的環境不顯示這一列 */
      { selector: '[data-guide="quick-template-meta"]', optional: true, titleKey: "UserGuide.quickCreate.meta.title", textKey: "UserGuide.quickCreate.meta.text" },
      { selector: '[data-guide="quick-template-folder"]', conditionSelector: '[data-guide="quick-template-grid"]', titleKey: "UserGuide.quickCreate.open.title", textKey: "UserGuide.quickCreate.open.text" },
    ],
  },
  "quick-practice": {
    titleKey: "UserGuide.quickPractice.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.quickPractice.intro.title", textKey: "UserGuide.quickPractice.intro.text" },
      { selector: '[data-guide="quick-launch-summary"]', titleKey: "UserGuide.quickPractice.summary.title", textKey: "UserGuide.quickPractice.summary.text" },
      { selector: '[data-guide="quick-launch-machines"]', titleKey: "UserGuide.quickPractice.machines.title", textKey: "UserGuide.quickPractice.machines.text" },
      { selector: '[data-guide="quick-launch-rules"]', titleKey: "UserGuide.quickPractice.rules.title", textKey: "UserGuide.quickPractice.rules.text" },
      { selector: '[data-guide="quick-launch-actions"]', titleKey: "UserGuide.quickPractice.launch.title", textKey: "UserGuide.quickPractice.launch.text" },
    ],
  },
  /* 三個分頁逐頁看；結束時切回「個人資料」分頁 */
  account: {
    titleKey: "UserGuide.account.title",
    cleanupSelector: '[data-guide-tab="account-profile"]',
    steps: [
      { selector: '[data-guide="account-tabs"]', titleKey: "UserGuide.account.tabs.title", textKey: "UserGuide.account.tabs.text" },
      { selector: '[data-guide="account-profile-card"]', activateSelector: '[data-guide-tab="account-profile"]', titleKey: "UserGuide.account.profile.title", textKey: "UserGuide.account.profile.text" },
      { selector: '[data-guide="account-password"]', activateSelector: '[data-guide-tab="account-profile"]', titleKey: "UserGuide.account.password.title", textKey: "UserGuide.account.password.text" },
      { selector: '[data-guide="account-two-factor"]', activateSelector: '[data-guide-tab="account-profile"]', titleKey: "UserGuide.account.twoFactor.title", textKey: "UserGuide.account.twoFactor.text" },
      { selector: '[data-guide="account-delete"]', activateSelector: '[data-guide-tab="account-profile"]', titleKey: "UserGuide.account.delete.title", textKey: "UserGuide.account.delete.text" },
      { selector: '[data-guide="account-appearance-form"]', activateSelector: '[data-guide-tab="account-appearance"]', titleKey: "UserGuide.account.appearance.title", textKey: "UserGuide.account.appearance.text" },
      { selector: '[data-guide="account-theme-presets"]', activateSelector: '[data-guide-tab="account-appearance"]', titleKey: "UserGuide.account.presets.title", textKey: "UserGuide.account.presets.text" },
      { selector: '[data-guide="account-about"]', activateSelector: '[data-guide-tab="account-about"]', titleKey: "UserGuide.account.about.title", textKey: "UserGuide.account.about.text" },
    ],
  },
};
