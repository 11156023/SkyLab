/**
 * 資源與審核群組：資源管理、申請審核、GPU 管理、班級審核（皆為管理員頁面）。
 * 資源管理的 /resource-mgmt/:vmid 詳情頁走 UserGuide.jsx 的 RESOURCE_DETAIL_GUIDE，不在這裡。
 */
export const RESOURCE_REVIEW_GUIDES = {
  "resource-management": {
    titleKey: "UserGuide.resourceMgmt.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.resourceMgmt.intro.title", textKey: "UserGuide.resourceMgmt.intro.text" },
      { selector: '[data-page-guide="actions"]', titleKey: "UserGuide.resourceMgmt.create.title", textKey: "UserGuide.resourceMgmt.create.text" },
      /* 工具列（DataToolbar）沒有掛點，指向狀態切換的「全部」鈕，說明一併涵蓋搜尋框 */
      { selector: '[data-guide="resource-mgmt-toolbar"]', conditionSelector: '[data-guide="resource-mgmt-toolbar"]', deferred: true, titleKey: "UserGuide.resourceMgmt.filters.title", textKey: "UserGuide.resourceMgmt.filters.text" },
      { selector: '[data-guide="resource-mgmt-table"]', conditionSelector: '[data-guide="resource-mgmt-table"]', deferred: true, titleKey: "UserGuide.resourceMgmt.table.title", textKey: "UserGuide.resourceMgmt.table.text" },
      /* 沒有課程／快速練習環境時整步不列入，不必等逾時 */
      { selector: '[data-guide="resource-mgmt-env-group"]', optional: true, titleKey: "UserGuide.resourceMgmt.envGroup.title", textKey: "UserGuide.resourceMgmt.envGroup.text" },
      /* 整列點擊會跳到詳情頁，只指著說明，不當 activateSelector */
      { selector: '[data-guide="resource-mgmt-row"]', conditionSelector: '[data-guide="resource-mgmt-row"]', deferred: true, titleKey: "UserGuide.resourceMgmt.row.title", textKey: "UserGuide.resourceMgmt.row.text" },
      { selector: '[data-guide="resource-mgmt-row-select"]', conditionSelector: '[data-guide="resource-mgmt-row-select"]', deferred: true, titleKey: "UserGuide.resourceMgmt.select.title", textKey: "UserGuide.resourceMgmt.select.text" },
      { selector: '[data-guide="resource-mgmt-row-actions"]', conditionSelector: '[data-guide="resource-mgmt-row-actions"]', deferred: true, titleKey: "UserGuide.resourceMgmt.actions.title", textKey: "UserGuide.resourceMgmt.actions.text" },
    ],
  },
  "request-review": {
    titleKey: "UserGuide.requestReview.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.requestReview.intro.title", textKey: "UserGuide.requestReview.intro.text" },
      { selector: '[data-guide-tab="request-review-pending"]', activateSelector: '[data-guide-tab="request-review-pending"]', titleKey: "UserGuide.requestReview.tabs.title", textKey: "UserGuide.requestReview.tabs.text" },
      { selector: '[data-guide="request-review-list"]', titleKey: "UserGuide.requestReview.list.title", textKey: "UserGuide.requestReview.list.text" },
      /* 勾選框只在有待審申請時出現 */
      { selector: '[data-guide="request-review-row-select"]', conditionSelector: '[data-guide="request-review-row-select"]', deferred: true, titleKey: "UserGuide.requestReview.batch.title", textKey: "UserGuide.requestReview.batch.text" },
      /* 點第一筆只是選取查看（會讀取資源評估），不會送出審核 */
      { selector: '[data-guide="request-review-detail"]', activateSelector: '[data-guide="request-review-row"]', titleKey: "UserGuide.requestReview.detail.title", textKey: "UserGuide.requestReview.detail.text" },
      { selector: '[data-guide="request-review-decision"]', conditionSelector: '[data-guide="request-review-row"]', deferred: true, titleKey: "UserGuide.requestReview.decision.title", textKey: "UserGuide.requestReview.decision.text" },
    ],
  },
  "gpu-management": {
    titleKey: "UserGuide.gpuMgmt.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.gpuMgmt.intro.title", textKey: "UserGuide.gpuMgmt.intro.text" },
      { selector: '[data-guide="gpu-mgmt-search"]', titleKey: "UserGuide.gpuMgmt.search.title", textKey: "UserGuide.gpuMgmt.search.text" },
      { selector: '[data-guide="gpu-mgmt-table"]', conditionSelector: '[data-guide="gpu-mgmt-table"]', deferred: true, titleKey: "UserGuide.gpuMgmt.table.title", textKey: "UserGuide.gpuMgmt.table.text" },
      { selector: '[data-guide="gpu-mgmt-pci"]', conditionSelector: '[data-guide="gpu-mgmt-pci"]', deferred: true, titleKey: "UserGuide.gpuMgmt.pci.title", textKey: "UserGuide.gpuMgmt.pci.text" },
      { selector: '[data-guide="gpu-mgmt-capacity"]', conditionSelector: '[data-guide="gpu-mgmt-capacity"]', deferred: true, titleKey: "UserGuide.gpuMgmt.capacity.title", textKey: "UserGuide.gpuMgmt.capacity.text" },
      { selector: '[data-guide="gpu-mgmt-usage"]', conditionSelector: '[data-guide="gpu-mgmt-usage"]', deferred: true, titleKey: "UserGuide.gpuMgmt.usage.title", textKey: "UserGuide.gpuMgmt.usage.text" },
      /* 刪除鈕會直接刪 PVE 上的 mapping，只指著說明 */
      { selector: '[data-guide="gpu-mgmt-delete"]', conditionSelector: '[data-guide="gpu-mgmt-delete"]', deferred: true, titleKey: "UserGuide.gpuMgmt.delete.title", textKey: "UserGuide.gpuMgmt.delete.text" },
    ],
  },
  "batch-review": {
    titleKey: "UserGuide.batchReview.title",
    steps: [
      { selector: '[data-page-guide="header"]', titleKey: "UserGuide.batchReview.intro.title", textKey: "UserGuide.batchReview.intro.text" },
      { selector: '[data-guide-tab="batch-review-pending"]', activateSelector: '[data-guide-tab="batch-review-pending"]', titleKey: "UserGuide.batchReview.tabs.title", textKey: "UserGuide.batchReview.tabs.text" },
      { selector: '[data-guide="batch-review-search"]', titleKey: "UserGuide.batchReview.search.title", textKey: "UserGuide.batchReview.search.text" },
      { selector: '[data-guide="batch-review-list"]', titleKey: "UserGuide.batchReview.list.title", textKey: "UserGuide.batchReview.list.text" },
      /* 點第一筆只是選取查看，核准／駁回另有確認框，導覽不會碰 */
      { selector: '[data-guide="batch-review-detail"]', activateSelector: '[data-guide="batch-review-row"]', titleKey: "UserGuide.batchReview.detail.title", textKey: "UserGuide.batchReview.detail.text" },
      { selector: '[data-guide="batch-review-decision"]', conditionSelector: '[data-guide="batch-review-row"]', deferred: true, titleKey: "UserGuide.batchReview.decision.title", textKey: "UserGuide.batchReview.decision.text" },
    ],
  },
};
