/**
 * 班級工作頁（/class-management/:classId/:section）與 AI導師檢查頁的專屬導覽。
 * 班級工作頁依網址的區段各給一套導覽，各自記「已看過」；分頁切換會換網址，所以不拿它當 activateSelector。
 */

const P = "UserGuide.classWorkspace";

/* 各區段共用：頁首（班名、學期、課表）與分頁列 */
const HEADER_STEP = {
  selector: '[data-page-guide="header"]',
  titleKey: `${P}.common.header.title`,
  textKey: `${P}.common.header.text`,
};

/* 班級啟用後分頁列才是 SegmentedControl（每顆都帶 data-guide-tab="class-section"），
   準備中的步驟列是共用 Stepper、沒有掛點，所以這步是 optional，只指出目前所在的那一顆 */
const SECTION_TAB_STEP = {
  selector: '[data-guide-tab="class-section"][aria-pressed="true"]',
  optional: true,
  titleKey: `${P}.common.tabs.title`,
  textKey: `${P}.common.tabs.text`,
};

function step(selector, key, extra = {}) {
  return { selector, titleKey: `${P}.${key}.title`, textKey: `${P}.${key}.text`, ...extra };
}

const CLASS_SECTION_GUIDES = {
  overview: {
    id: "class-workspace-overview",
    titleKey: `${P}.overview.title`,
    steps: [
      HEADER_STEP,
      SECTION_TAB_STEP,
      step('[data-page-guide="actions"]', "overview.actions"),
      step('[data-guide="class-overview-status"]', "overview.status"),
      step('[data-guide="class-overview-week"]', "overview.week"),
      step('[data-guide="class-overview-machines"]', "overview.machines"),
      step('[data-guide="class-overview-info"]', "overview.info"),
    ],
  },
  students: {
    id: "class-workspace-students",
    titleKey: `${P}.students.title`,
    steps: [
      HEADER_STEP,
      SECTION_TAB_STEP,
      step('[data-guide="class-students-actions"]', "students.actions"),
      step('[data-guide="class-students-list"]', "students.list"),
      /* 移除鈕只在準備中的班級出現 */
      step('[data-guide="class-students-remove"]', "students.remove", { optional: true }),
    ],
  },
  machines: {
    id: "class-workspace-machines",
    titleKey: `${P}.machines.title`,
    steps: [
      HEADER_STEP,
      SECTION_TAB_STEP,
      /* 準備中：選環境；送出建機後：唯讀的已套用環境，兩者擇一 */
      step('[data-guide="class-machines-choose"]', "machines.choose", { optional: true }),
      step('[data-guide="class-machines-create"]', "machines.create", { optional: true }),
      step('[data-guide="class-machines-applied"]', "machines.applied", { optional: true }),
      step('[data-guide="class-machines-publications"]', "machines.publications", { optional: true }),
      step('[data-guide="class-machines-topology"]', "machines.topology", { optional: true }),
    ],
  },
  weekly: {
    id: "class-workspace-weekly",
    titleKey: `${P}.weekly.title`,
    steps: [
      HEADER_STEP,
      SECTION_TAB_STEP,
      step('[data-guide="class-weekly-list"]', "weekly.list"),
      step('[data-guide="class-weekly-row"]', "weekly.row", { optional: true }),
      /* 檔案上傳區與儲存鈕在封存後不顯示 */
      step('[data-guide="class-weekly-files"]', "weekly.files", { optional: true }),
      step('[data-guide="class-weekly-visibility"]', "weekly.visibility", { optional: true }),
      step('[data-guide="class-weekly-save"]', "weekly.save", { optional: true }),
    ],
  },
  progress: {
    id: "class-workspace-progress",
    titleKey: `${P}.progress.title`,
    steps: [
      HEADER_STEP,
      SECTION_TAB_STEP,
      /* 班級還沒啟用時整區只有鎖定說明 */
      step('[data-guide="class-locked"]', "progress.locked", { optional: true }),
      step('[data-guide="class-progress-broadcast"]', "progress.broadcast", { optional: true }),
      step('[data-guide="class-progress-power"]', "progress.power", { optional: true }),
      step('[data-guide="class-progress-toolbar"]', "progress.toolbar", { optional: true }),
      step('[data-guide="class-progress-tiles"]', "progress.tiles", { optional: true }),
      step('[data-guide="class-progress-attention"]', "progress.attention", { optional: true }),
    ],
  },
};

/* 沒帶區段的班級連結：已啟用的班級會被導到 /progress（換網址後導覽也跟著換），其餘停在總覽；
   舊網址 /classroom 等同課堂監看 */
function classWorkspaceGuide(pathname) {
  const section = pathname.match(/^\/class-management\/[^/]+\/([^/]+)$/)?.[1];
  const key = section === "classroom" ? "progress" : section;
  return CLASS_SECTION_GUIDES[key] ?? CLASS_SECTION_GUIDES.overview;
}

const J = "UserGuide.aiJudge";

/* 三個分頁的切換都只換畫面、只做讀取，可以當 activateSelector；
   沒有任何檢查時分頁列不存在，用 conditionSelector 整步略過 */
const JUDGE_TAB = '[data-guide-tab="ai-judge-tab-rubrics"]';

function judgeStep(selector, key, extra = {}) {
  return { selector, titleKey: `${J}.${key}.title`, textKey: `${J}.${key}.text`, ...extra };
}

export const CLASS_WORKSPACE_GUIDES = {
  "class-workspace": classWorkspaceGuide,
  "ai-judge": {
    titleKey: `${J}.title`,
    /* 導覽會切到腳本總覽、導師核查，結束時回到預設的檢查設定 */
    cleanupSelector: JUDGE_TAB,
    steps: [
      judgeStep('[data-page-guide="header"]', "header"),
      judgeStep('[data-guide-tab="class-section"][aria-pressed="true"]', "tabs", { optional: true }),
      judgeStep('[data-guide="ai-judge-locked"]', "locked", { optional: true }),
      judgeStep('[data-guide="ai-judge-sessions"]', "sessions", { optional: true }),
      judgeStep('[data-guide="ai-judge-empty"]', "empty", { optional: true }),
      judgeStep('[data-guide="ai-judge-rubric-items"]', "rubric", {
        activateSelector: JUDGE_TAB,
        conditionSelector: JUDGE_TAB,
        deferred: true,
      }),
      judgeStep('[data-guide="ai-judge-assistant"]', "assistant", {
        activateSelector: JUDGE_TAB,
        conditionSelector: JUDGE_TAB,
        deferred: true,
      }),
      /* 檢查表還沒有任何項目時不會出現「儲存並製作」 */
      judgeStep('[data-guide="ai-judge-save-build"]', "saveBuild", {
        activateSelector: JUDGE_TAB,
        conditionSelector: '[data-guide="ai-judge-save-build"]',
        deferred: true,
      }),
      judgeStep('[data-guide="ai-judge-scripts"]', "scripts", {
        activateSelector: '[data-guide-tab="ai-judge-tab-scripts"]',
        conditionSelector: JUDGE_TAB,
        deferred: true,
      }),
      judgeStep('[data-guide="ai-judge-review"]', "review", {
        activateSelector: '[data-guide-tab="ai-judge-tab-review"]',
        conditionSelector: JUDGE_TAB,
        deferred: true,
      }),
    ],
  },
};
