/**
 * machineKind.js
 * 機器來源徽章的純邏輯：後端的 machine_kind + class_relation → 要顯示哪一種徽章。
 *
 * 七種徽章各有圖示與文字，但顏色只編碼「跟我的關係」兩群（類別辨識交給 icon＋label，
 * 七個色相既記不住又跟語意色打架）：
 * - mine  我的機器：personal（個人申請）、class_student（班級機，我是學生）、
 *         quick_practice（快速練習）、course（課程實驗）
 * - other 別人的機器：shared（共享給我，帶擁有者）、class_teacher（學生機器，
 *         我是老師，帶學生姓名）、teacher_open（老師開放，拓撲可連線節點，帶老師姓名）
 */

export const KIND_META = {
  personal:       { icon: "person",     variant: "mine",  labelKey: "MachineKind.personal",      hintKey: "MachineKind.personalHint" },
  shared:         { icon: "group",      variant: "other", labelKey: "MachineKind.shared",        hintKey: "MachineKind.sharedHint",       showOwner: true },
  class_student:  { icon: "school",     variant: "mine",  labelKey: "MachineKind.classMine",     hintKey: "MachineKind.classMineHint" },
  class_teacher:  { icon: "co_present", variant: "other", labelKey: "MachineKind.classStudent",  hintKey: "MachineKind.classStudentHint", showOwner: true },
  quick_practice: { icon: "bolt",       variant: "mine",  labelKey: "MachineKind.quickPractice", hintKey: "MachineKind.quickPracticeHint" },
  course:         { icon: "menu_book",  variant: "mine",  labelKey: "MachineKind.course",        hintKey: "MachineKind.courseHint" },
  teacher_open:   { icon: "lock_open",  variant: "other", labelKey: "MachineKind.teacherOpen",   hintKey: "MachineKind.teacherOpenHint",  showOwner: true },
};

/**
 * @param {object} input
 * @param {string} [input.kind]          後端 machine_kind，或前端自用的 teacher_open
 * @param {string|null} [input.classRelation]  班級機：student / teacher
 * @returns {keyof KIND_META}
 */
export function resolveKind({ kind, classRelation } = {}) {
  if (kind === "teaching_class") {
    return classRelation === "teacher" ? "class_teacher" : "class_student";
  }
  return KIND_META[kind] ? kind : "personal";
}
