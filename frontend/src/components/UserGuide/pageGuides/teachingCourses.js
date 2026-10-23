/**
 * 教學課程相關頁面的專屬導覽：課程管理（CMS）、教學環境列表與編輯器、班級管理、建立班級精靈。
 * 導覽只示範不產生副作用：會存檔、送出、建立的按鈕一律只指著說明，不用 activateSelector 去點。
 */

/* 教學環境編輯器的「查看機器配置」鈕：草稿與已發布各掛一個錨點，
   讓「發布」那步能用草稿錨點判斷要不要列入（已發布的環境沒有發布鈕） */
const TPL_EDITOR_TO_MACHINES = '[data-guide="course-tpl-editor-to-machines"], [data-guide="course-tpl-editor-view-machines"]';

export const TEACHING_COURSE_GUIDES = {
  "course-cms": {
    titleKey: "UserGuide.courseCms.title",
    /* 導覽會切到「學生進度」分頁，結束時切回預設的「內容編輯」 */
    cleanupSelector: '[data-guide-tab="course-cms-tab-editor"]',
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.courseCms.intro.title",
        textKey: "UserGuide.courseCms.intro.text",
      },
      {
        selector: '[data-guide="course-cms-tree"]',
        activateSelector: '[data-guide-tab="course-cms-tab-editor"]',
        titleKey: "UserGuide.courseCms.tree.title",
        textKey: "UserGuide.courseCms.tree.text",
      },
      {
        selector: '[data-guide="course-cms-add-path"]',
        activateSelector: '[data-guide-tab="course-cms-tab-editor"]',
        titleKey: "UserGuide.courseCms.addPath.title",
        textKey: "UserGuide.courseCms.addPath.text",
      },
      {
        selector: '[data-guide="course-cms-workspace"]',
        activateSelector: '[data-guide-tab="course-cms-tab-editor"]',
        titleKey: "UserGuide.courseCms.workspace.title",
        textKey: "UserGuide.courseCms.workspace.text",
      },
      {
        selector: '[data-guide-tab="course-cms-tab-progress"]',
        activateSelector: '[data-guide-tab="course-cms-tab-progress"]',
        titleKey: "UserGuide.courseCms.progressTab.title",
        textKey: "UserGuide.courseCms.progressTab.text",
      },
      {
        selector: '[data-guide="course-cms-progress-toolbar"]',
        activateSelector: '[data-guide-tab="course-cms-tab-progress"]',
        deferred: true,
        titleKey: "UserGuide.courseCms.progressToolbar.title",
        textKey: "UserGuide.courseCms.progressToolbar.text",
      },
      {
        selector: '[data-guide="course-cms-progress"]',
        activateSelector: '[data-guide-tab="course-cms-tab-progress"]',
        deferred: true,
        titleKey: "UserGuide.courseCms.progressTable.title",
        textKey: "UserGuide.courseCms.progressTable.text",
      },
    ],
  },
  "course-templates": {
    titleKey: "UserGuide.courseTemplates.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.courseTemplates.intro.title",
        textKey: "UserGuide.courseTemplates.intro.text",
      },
      {
        selector: '[data-page-guide="actions"]',
        titleKey: "UserGuide.courseTemplates.create.title",
        textKey: "UserGuide.courseTemplates.create.text",
      },
      {
        selector: '[data-guide="course-tpl-controls"]',
        titleKey: "UserGuide.courseTemplates.controls.title",
        textKey: "UserGuide.courseTemplates.controls.text",
      },
      {
        selector: '[data-guide="course-tpl-table"]',
        conditionSelector: '[data-guide="course-tpl-table"]',
        deferred: true,
        titleKey: "UserGuide.courseTemplates.table.title",
        textKey: "UserGuide.courseTemplates.table.text",
      },
      {
        selector: '[data-guide="course-tpl-row-actions"]',
        conditionSelector: '[data-guide="course-tpl-row-actions"]',
        deferred: true,
        titleKey: "UserGuide.courseTemplates.rowActions.title",
        textKey: "UserGuide.courseTemplates.rowActions.text",
      },
    ],
  },
  /* 新建（/new）與編輯（/:templateId）共用：基本資料分頁的步驟在使用者停在機器配置時不存在、
     也沒有切回去的鈕，會自動略過；已發布環境沒有發布鈕，用 conditionSelector 排除 */
  "course-template-editor": {
    titleKey: "UserGuide.courseTemplateEditor.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.courseTemplateEditor.intro.title",
        textKey: "UserGuide.courseTemplateEditor.intro.text",
      },
      {
        selector: '[data-guide="course-tpl-editor-name"]',
        titleKey: "UserGuide.courseTemplateEditor.name.title",
        textKey: "UserGuide.courseTemplateEditor.name.text",
      },
      {
        selector: '[data-guide="course-tpl-editor-usage"]',
        titleKey: "UserGuide.courseTemplateEditor.usage.title",
        textKey: "UserGuide.courseTemplateEditor.usage.text",
      },
      {
        selector: '[data-guide="course-tpl-editor-files"]',
        titleKey: "UserGuide.courseTemplateEditor.files.title",
        textKey: "UserGuide.courseTemplateEditor.files.text",
      },
      {
        selector: '[data-guide="course-tpl-editor-save-basics"]',
        optional: true,
        titleKey: "UserGuide.courseTemplateEditor.saveBasics.title",
        textKey: "UserGuide.courseTemplateEditor.saveBasics.text",
      },
      {
        selector: '[data-guide="course-tpl-editor-add-bar"]',
        activateSelector: TPL_EDITOR_TO_MACHINES,
        titleKey: "UserGuide.courseTemplateEditor.addBar.title",
        textKey: "UserGuide.courseTemplateEditor.addBar.text",
      },
      {
        selector: '[data-guide="course-tpl-editor-topology"]',
        activateSelector: TPL_EDITOR_TO_MACHINES,
        deferred: true,
        titleKey: "UserGuide.courseTemplateEditor.topology.title",
        textKey: "UserGuide.courseTemplateEditor.topology.text",
      },
      {
        selector: '[data-guide="course-tpl-editor-publish"]',
        activateSelector: TPL_EDITOR_TO_MACHINES,
        conditionSelector: '[data-guide="course-tpl-editor-to-machines"], [data-guide="course-tpl-editor-publish"]',
        deferred: true,
        titleKey: "UserGuide.courseTemplateEditor.publish.title",
        textKey: "UserGuide.courseTemplateEditor.publish.text",
      },
    ],
  },
  "class-management": {
    titleKey: "UserGuide.classManagement.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.classManagement.intro.title",
        textKey: "UserGuide.classManagement.intro.text",
      },
      {
        selector: '[data-page-guide="actions"]',
        titleKey: "UserGuide.classManagement.create.title",
        textKey: "UserGuide.classManagement.create.text",
      },
      {
        selector: '[data-guide="class-mgmt-toolbar"]',
        titleKey: "UserGuide.classManagement.filters.title",
        textKey: "UserGuide.classManagement.filters.text",
      },
      {
        selector: '[data-guide="class-mgmt-archived"]',
        optional: true,
        titleKey: "UserGuide.classManagement.archived.title",
        textKey: "UserGuide.classManagement.archived.text",
      },
      {
        selector: '[data-guide="class-mgmt-ticket"]',
        conditionSelector: '[data-guide="class-mgmt-ticket"]',
        deferred: true,
        titleKey: "UserGuide.classManagement.ticket.title",
        textKey: "UserGuide.classManagement.ticket.text",
      },
      {
        selector: '[data-guide="class-mgmt-ticket"]',
        conditionSelector: '[data-guide="class-mgmt-ticket"]',
        deferred: true,
        titleKey: "UserGuide.classManagement.batch.title",
        textKey: "UserGuide.classManagement.batch.text",
      },
    ],
  },
  /* 精靈一次只畫目前這一步，換步要先存檔，導覽不能代為切換：
     各步驟專屬的區塊都標 optional，開導覽時停在哪一步就只介紹那一步 */
  "class-setup": {
    titleKey: "UserGuide.classSetup.title",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.classSetup.intro.title",
        textKey: "UserGuide.classSetup.intro.text",
      },
      {
        selector: '[data-guide="class-setup-stepper"]',
        titleKey: "UserGuide.classSetup.stepper.title",
        textKey: "UserGuide.classSetup.stepper.text",
      },
      /* 第 1 步：班級與課表 */
      {
        selector: '[data-guide="class-setup-basic"]',
        optional: true,
        titleKey: "UserGuide.classSetup.basic.title",
        textKey: "UserGuide.classSetup.basic.text",
      },
      {
        selector: '[data-guide="class-setup-advanced"]',
        optional: true,
        titleKey: "UserGuide.classSetup.advanced.title",
        textKey: "UserGuide.classSetup.advanced.text",
      },
      /* 第 2 步：學生名單 */
      {
        selector: '[data-guide="class-setup-emails"]',
        optional: true,
        titleKey: "UserGuide.classSetup.emails.title",
        textKey: "UserGuide.classSetup.emails.text",
      },
      {
        selector: '[data-guide="class-setup-csv"]',
        optional: true,
        titleKey: "UserGuide.classSetup.csv.title",
        textKey: "UserGuide.classSetup.csv.text",
      },
      {
        selector: '[data-guide="class-setup-group-import"]',
        optional: true,
        titleKey: "UserGuide.classSetup.groupImport.title",
        textKey: "UserGuide.classSetup.groupImport.text",
      },
      {
        selector: '[data-guide="class-setup-roster"]',
        optional: true,
        titleKey: "UserGuide.classSetup.roster.title",
        textKey: "UserGuide.classSetup.roster.text",
      },
      /* 第 3 步：教學環境 */
      {
        selector: '[data-guide="class-setup-env-list"]',
        optional: true,
        titleKey: "UserGuide.classSetup.envList.title",
        textKey: "UserGuide.classSetup.envList.text",
      },
      {
        selector: '[data-guide="class-setup-template-pause"]',
        optional: true,
        titleKey: "UserGuide.classSetup.templatePause.title",
        textKey: "UserGuide.classSetup.templatePause.text",
      },
      /* 第 4 步：每週任務 */
      {
        selector: '[data-guide="class-setup-publish-weeks"]',
        optional: true,
        titleKey: "UserGuide.classSetup.publishWeeks.title",
        textKey: "UserGuide.classSetup.publishWeeks.text",
      },
      {
        selector: '[data-guide="class-setup-weeks"]',
        optional: true,
        titleKey: "UserGuide.classSetup.weeks.title",
        textKey: "UserGuide.classSetup.weeks.text",
      },
      /* 第 5 步：確認建立 */
      {
        selector: '[data-guide="class-setup-readiness"]',
        optional: true,
        titleKey: "UserGuide.classSetup.readiness.title",
        textKey: "UserGuide.classSetup.readiness.text",
      },
      {
        selector: '[data-guide="class-setup-summary"]',
        optional: true,
        titleKey: "UserGuide.classSetup.summary.title",
        textKey: "UserGuide.classSetup.summary.text",
      },
      /* 底部按鈕：第 1–4 步是「儲存並下一步」，第 5 步換成送出建機 */
      {
        selector: '[data-guide="class-setup-next"]',
        optional: true,
        titleKey: "UserGuide.classSetup.next.title",
        textKey: "UserGuide.classSetup.next.text",
      },
      {
        selector: '[data-guide="class-setup-provision"]',
        optional: true,
        titleKey: "UserGuide.classSetup.provision.title",
        textKey: "UserGuide.classSetup.provision.text",
      },
      {
        selector: '[data-guide="class-setup-draft"]',
        optional: true,
        titleKey: "UserGuide.classSetup.draft.title",
        textKey: "UserGuide.classSetup.draft.text",
      },
    ],
  },
};
