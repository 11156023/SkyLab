/* 班級頁的分頁：設定步驟＋班級啟用後才出現的上課進度、AI 檢查。
   班級工作頁與 AI 檢查頁共用，AI 頁才看得出自己是班級的一個分頁 */

export const POST_ACTIVE_TABS = ["progress", "ai"];

export const CLASS_WORKFLOW_TABS = [
  ["overview", "dashboard", "ClassWorkspacePage.tabOverviewLabel"],
  ["students", "groups", "ClassWorkspacePage.tabStudentsLabel"],
  ["machines", "account_tree", "ClassWorkspacePage.tabMachinesLabel"],
  ["weekly", "calendar_view_week", "ClassWorkspacePage.tabWeeklyLabel"],
  ["progress", "cast_for_education", "ClassWorkspacePage.tabProgressLabel"],
  ["ai", "auto_awesome", "ClassWorkspacePage.tabAiLabel"],
];
