/**
 * 系統維運頁的專屬導覽：資源監控、IP 管理、稽核日誌、背景任務、閘道 VM。
 * 只示範不操作：分頁切換用 activateSelector，結束時把分頁切回第一頁。
 */
export const SYSTEM_OPS_GUIDES = {
  monitoring: {
    titleKey: "UserGuide.monitoring.title",
    /* 導覽會切到「挖礦事件」分頁，結束時切回「活動警告」 */
    cleanupSelector: '[data-guide-tab="monitoring-alerts"]',
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.monitoring.intro.title",
        textKey: "UserGuide.monitoring.intro.text",
      },
      {
        /* 監控套件有啟用才有 Grafana 連結 */
        selector: '[data-guide="monitoring-grafana"]',
        optional: true,
        titleKey: "UserGuide.monitoring.grafana.title",
        textKey: "UserGuide.monitoring.grafana.text",
      },
      {
        selector: '[data-guide="monitoring-health"]',
        titleKey: "UserGuide.monitoring.health.title",
        textKey: "UserGuide.monitoring.health.text",
      },
      {
        /* PVE 概況載入完才出現；PVE 連不上時等不到就自動跳過 */
        selector: '[data-guide="monitoring-overview"]',
        conditionSelector: '[data-guide="monitoring-overview"]',
        deferred: true,
        titleKey: "UserGuide.monitoring.overview.title",
        textKey: "UserGuide.monitoring.overview.text",
      },
      {
        selector: '[data-guide="monitoring-panels"]',
        activateSelector: '[data-guide-tab="monitoring-alerts"]',
        titleKey: "UserGuide.monitoring.alerts.title",
        textKey: "UserGuide.monitoring.alerts.text",
      },
      {
        selector: '[data-guide="monitoring-panels"]',
        activateSelector: '[data-guide-tab="monitoring-mining"]',
        titleKey: "UserGuide.monitoring.mining.title",
        textKey: "UserGuide.monitoring.mining.text",
      },
      {
        selector: '[data-guide="monitoring-nodes"]',
        conditionSelector: '[data-guide="monitoring-nodes"]',
        deferred: true,
        titleKey: "UserGuide.monitoring.nodes.title",
        textKey: "UserGuide.monitoring.nodes.text",
      },
      {
        selector: '[data-guide="monitoring-top"]',
        conditionSelector: '[data-guide="monitoring-top"]',
        deferred: true,
        titleKey: "UserGuide.monitoring.top.title",
        textKey: "UserGuide.monitoring.top.text",
      },
    ],
  },

  "ip-management": {
    titleKey: "UserGuide.ipManagement.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.ipManagement.intro.title",
        textKey: "UserGuide.ipManagement.intro.text",
      },
      {
        /* 尚未設定子網時才有（空狀態裡的建立鈕） */
        selector: '[data-guide="ip-create-subnet"]',
        optional: true,
        titleKey: "UserGuide.ipManagement.createSubnet.title",
        textKey: "UserGuide.ipManagement.createSubnet.text",
      },
      {
        /* 設定好子網後才出現在頁首 */
        selector: '[data-guide="ip-edit-subnet"]',
        optional: true,
        titleKey: "UserGuide.ipManagement.editSubnet.title",
        textKey: "UserGuide.ipManagement.editSubnet.text",
      },
      {
        selector: '[data-guide="ip-stats"]',
        titleKey: "UserGuide.ipManagement.stats.title",
        textKey: "UserGuide.ipManagement.stats.text",
      },
      {
        selector: '[data-guide="ip-toolbar"]',
        titleKey: "UserGuide.ipManagement.toolbar.title",
        textKey: "UserGuide.ipManagement.toolbar.text",
      },
      {
        selector: '[data-guide="ip-table"]',
        conditionSelector: '[data-guide="ip-table"]',
        deferred: true,
        titleKey: "UserGuide.ipManagement.table.title",
        textKey: "UserGuide.ipManagement.table.text",
      },
    ],
  },

  audit: {
    titleKey: "UserGuide.audit.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.audit.intro.title",
        textKey: "UserGuide.audit.intro.text",
      },
      {
        /* 摘要另一支 API 抓，抓不到時整排不顯示 */
        selector: '[data-guide="audit-summary"]',
        deferred: true,
        titleKey: "UserGuide.audit.summary.title",
        textKey: "UserGuide.audit.summary.text",
      },
      {
        selector: '[data-guide="audit-filters"]',
        titleKey: "UserGuide.audit.filters.title",
        textKey: "UserGuide.audit.filters.text",
      },
      {
        selector: '[data-guide="audit-export"]',
        titleKey: "UserGuide.audit.export.title",
        textKey: "UserGuide.audit.export.text",
      },
      {
        selector: '[data-guide="audit-table"]',
        conditionSelector: '[data-guide="audit-table"]',
        deferred: true,
        titleKey: "UserGuide.audit.table.title",
        textKey: "UserGuide.audit.table.text",
      },
    ],
  },

  jobs: {
    titleKey: "UserGuide.jobs.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.jobs.intro.title",
        textKey: "UserGuide.jobs.intro.text",
      },
      {
        selector: '[data-guide="jobs-stats"]',
        titleKey: "UserGuide.jobs.stats.title",
        textKey: "UserGuide.jobs.stats.text",
      },
      {
        selector: '[data-guide="jobs-kind-filter"]',
        titleKey: "UserGuide.jobs.kindFilter.title",
        textKey: "UserGuide.jobs.kindFilter.text",
      },
      {
        selector: '[data-guide="jobs-status-filter"]',
        titleKey: "UserGuide.jobs.statusFilter.title",
        textKey: "UserGuide.jobs.statusFilter.text",
      },
      {
        /* 有任務才有表格；沒有任務時等不到就自動跳過 */
        selector: '[data-guide="jobs-table"]',
        conditionSelector: '[data-guide="jobs-table"]',
        deferred: true,
        titleKey: "UserGuide.jobs.table.title",
        textKey: "UserGuide.jobs.table.text",
      },
    ],
  },

  gateway: {
    titleKey: "UserGuide.gateway.title",
    /* 導覽會逐一切分頁，結束時回到「連線設定」 */
    cleanupSelector: '[data-guide-tab="gateway-connection"]',
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.gateway.intro.title",
        textKey: "UserGuide.gateway.intro.text",
      },
      {
        selector: '[data-guide="gateway-ssh-form"]',
        activateSelector: '[data-guide-tab="gateway-connection"]',
        deferred: true,
        titleKey: "UserGuide.gateway.sshForm.title",
        textKey: "UserGuide.gateway.sshForm.text",
      },
      {
        selector: '[data-guide="gateway-ssh-key"]',
        activateSelector: '[data-guide-tab="gateway-connection"]',
        deferred: true,
        titleKey: "UserGuide.gateway.sshKey.title",
        textKey: "UserGuide.gateway.sshKey.text",
      },
      /* 以下分頁的內容要經 SSH 讀取、未設定連線時只有空狀態，所以指向整個內容區 */
      {
        selector: '[data-guide="gateway-content"]',
        activateSelector: '[data-guide-tab="gateway-install"]',
        titleKey: "UserGuide.gateway.install.title",
        textKey: "UserGuide.gateway.install.text",
      },
      {
        selector: '[data-guide="gateway-content"]',
        activateSelector: '[data-guide-tab="gateway-certificate"]',
        titleKey: "UserGuide.gateway.certificate.title",
        textKey: "UserGuide.gateway.certificate.text",
      },
      {
        selector: '[data-guide="gateway-content"]',
        activateSelector: '[data-guide-tab="gateway-platform"]',
        titleKey: "UserGuide.gateway.platform.title",
        textKey: "UserGuide.gateway.platform.text",
      },
      {
        selector: '[data-guide="gateway-content"]',
        activateSelector: '[data-guide-tab="gateway-nginx"]',
        titleKey: "UserGuide.gateway.nginx.title",
        textKey: "UserGuide.gateway.nginx.text",
      },
      {
        selector: '[data-guide="gateway-content"]',
        activateSelector: '[data-guide-tab="gateway-wireguard"]',
        titleKey: "UserGuide.gateway.wireguard.title",
        textKey: "UserGuide.gateway.wireguard.text",
      },
    ],
  },
};
