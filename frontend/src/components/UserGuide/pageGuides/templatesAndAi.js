/**
 * 機器範本與 AI 管理頁（範本、AI API 申請審核、金鑰管理、AI 用量監控）的專屬導覽。
 * 資料列相關步驟都標 deferred；依表格是否存在的步驟以 conditionSelector 過濾，免得空清單時逐步等逾時。
 */
export const TEMPLATES_AND_AI_GUIDES = {
  templates: {
    titleKey: "UserGuide.templates.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.templates.intro.title",
        textKey: "UserGuide.templates.intro.text",
      },
      {
        /* 有範本時按鈕在頁首、沒有時在空狀態，兩處共用同一個錨點；載入中都還不在 DOM */
        selector: '[data-guide="templates-create"]',
        deferred: true,
        titleKey: "UserGuide.templates.create.title",
        textKey: "UserGuide.templates.create.text",
      },
      {
        selector: '[data-guide="templates-table"]',
        conditionSelector: '[data-guide="templates-table"]',
        deferred: true,
        titleKey: "UserGuide.templates.table.title",
        textKey: "UserGuide.templates.table.text",
      },
      {
        selector: '[data-guide="templates-name"]',
        conditionSelector: '[data-guide="templates-table"]',
        deferred: true,
        titleKey: "UserGuide.templates.badges.title",
        textKey: "UserGuide.templates.badges.text",
      },
      {
        selector: '[data-guide="templates-status"]',
        conditionSelector: '[data-guide="templates-table"]',
        deferred: true,
        titleKey: "UserGuide.templates.status.title",
        textKey: "UserGuide.templates.status.text",
      },
      {
        selector: '[data-guide="templates-menu"]',
        conditionSelector: '[data-guide="templates-table"]',
        deferred: true,
        titleKey: "UserGuide.templates.menu.title",
        textKey: "UserGuide.templates.menu.text",
      },
      {
        /* 更新循環的選項也在同一個「⋯」選單裡，不打開選單（避免與點擊外部關閉的邏輯互相干擾） */
        selector: '[data-guide="templates-menu"]',
        conditionSelector: '[data-guide="templates-table"]',
        deferred: true,
        titleKey: "UserGuide.templates.cycle.title",
        textKey: "UserGuide.templates.cycle.text",
      },
    ],
  },
  "ai-api-review": {
    titleKey: "UserGuide.aiApiReview.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.aiApiReview.intro.title",
        textKey: "UserGuide.aiApiReview.intro.text",
      },
      {
        selector: '[data-guide="ai-review-tabs"]',
        titleKey: "UserGuide.aiApiReview.tabs.title",
        textKey: "UserGuide.aiApiReview.tabs.text",
      },
      {
        selector: '[data-guide="ai-review-table"]',
        conditionSelector: '[data-guide="ai-review-table"]',
        deferred: true,
        titleKey: "UserGuide.aiApiReview.table.title",
        textKey: "UserGuide.aiApiReview.table.text",
      },
      {
        selector: '[data-guide="ai-review-purpose"]',
        conditionSelector: '[data-guide="ai-review-table"]',
        deferred: true,
        titleKey: "UserGuide.aiApiReview.purpose.title",
        textKey: "UserGuide.aiApiReview.purpose.text",
      },
      {
        /* 只有待審核的列才有通過／拒絕鈕；已審核的列這一欄改顯示審核備註 */
        selector: '[data-guide="ai-review-actions"]',
        conditionSelector: '[data-guide="ai-review-table"]',
        deferred: true,
        titleKey: "UserGuide.aiApiReview.actions.title",
        textKey: "UserGuide.aiApiReview.actions.text",
      },
    ],
  },
  "ai-api-keys": {
    titleKey: "UserGuide.aiApiKeys.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.aiApiKeys.intro.title",
        textKey: "UserGuide.aiApiKeys.intro.text",
      },
      {
        selector: '[data-guide="ai-keys-admin-status"]',
        titleKey: "UserGuide.aiApiKeys.status.title",
        textKey: "UserGuide.aiApiKeys.status.text",
      },
      {
        selector: '[data-guide="ai-keys-admin-filter"]',
        titleKey: "UserGuide.aiApiKeys.filter.title",
        textKey: "UserGuide.aiApiKeys.filter.text",
      },
      {
        selector: '[data-guide="ai-keys-admin-search"]',
        titleKey: "UserGuide.aiApiKeys.search.title",
        textKey: "UserGuide.aiApiKeys.search.text",
      },
      {
        selector: '[data-guide="ai-keys-admin-table"]',
        conditionSelector: '[data-guide="ai-keys-admin-table"]',
        deferred: true,
        titleKey: "UserGuide.aiApiKeys.table.title",
        textKey: "UserGuide.aiApiKeys.table.text",
      },
      {
        selector: '[data-guide="ai-keys-admin-activity"]',
        conditionSelector: '[data-guide="ai-keys-admin-table"]',
        deferred: true,
        titleKey: "UserGuide.aiApiKeys.activity.title",
        textKey: "UserGuide.aiApiKeys.activity.text",
      },
      {
        /* 只有啟用中的金鑰才有「刪除」鈕 */
        selector: '[data-guide="ai-keys-admin-revoke"]',
        conditionSelector: '[data-guide="ai-keys-admin-table"]',
        deferred: true,
        titleKey: "UserGuide.aiApiKeys.revoke.title",
        textKey: "UserGuide.aiApiKeys.revoke.text",
      },
    ],
  },
  "ai-monitoring": {
    titleKey: "UserGuide.aiMonitoring.title",
    /* 導覽會切換明細分頁，結束時切回預設的「模型」分頁 */
    cleanupSelector: '[data-guide-tab="ai-monitoring-models"]',
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.aiMonitoring.intro.title",
        textKey: "UserGuide.aiMonitoring.intro.text",
      },
      {
        selector: '[data-page-guide="actions"]',
        titleKey: "UserGuide.aiMonitoring.actions.title",
        textKey: "UserGuide.aiMonitoring.actions.text",
      },
      {
        selector: '[data-guide="ai-monitoring-metrics"]',
        titleKey: "UserGuide.aiMonitoring.metrics.title",
        textKey: "UserGuide.aiMonitoring.metrics.text",
      },
      {
        selector: '[data-guide="ai-monitoring-trend"]',
        titleKey: "UserGuide.aiMonitoring.trend.title",
        textKey: "UserGuide.aiMonitoring.trend.text",
      },
      {
        /* 「系統狀態」面板只在載入中或有異常項目時出現 */
        selector: '[data-guide="ai-monitoring-health"]',
        optional: true,
        titleKey: "UserGuide.aiMonitoring.health.title",
        textKey: "UserGuide.aiMonitoring.health.text",
      },
      {
        selector: '[data-guide="ai-monitoring-detail"]',
        activateSelector: '[data-guide-tab="ai-monitoring-models"]',
        titleKey: "UserGuide.aiMonitoring.models.title",
        textKey: "UserGuide.aiMonitoring.models.text",
      },
      {
        selector: '[data-guide="ai-monitoring-detail"]',
        activateSelector: '[data-guide-tab="ai-monitoring-proxy"]',
        titleKey: "UserGuide.aiMonitoring.proxy.title",
        textKey: "UserGuide.aiMonitoring.proxy.text",
      },
      {
        selector: '[data-guide="ai-monitoring-detail"]',
        activateSelector: '[data-guide-tab="ai-monitoring-template"]',
        titleKey: "UserGuide.aiMonitoring.template.title",
        textKey: "UserGuide.aiMonitoring.template.text",
      },
    ],
  },
};
