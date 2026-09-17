/**
 * machineKind.js
 * 機器來源徽章的純邏輯：後端的 machine_kind + class_relation → 要顯示哪一種徽章。
 *
 * 七種徽章，每種有自己的圖示與顏色（色票在 _themes.scss 的 --color-kind-*）：
 * - personal       個人申請機器
 * - shared         共享給我的機器（帶擁有者）
 * - class_student  班級機器，我是這班的學生（機器分給我）
 * - class_teacher  學生機器，我是這班的老師（帶學生姓名）
 * - quick_practice 快速練習機器
 * - course         課程實驗機器
 * - teacher_open   老師開放給班級的機器（防火牆拓撲的可連線節點，帶老師姓名）
 */

export const KIND_META = {
  personal:       { icon: "person",     variant: "personal", labelKey: "MachineKind.personal",      hintKey: "MachineKind.personalHint" },
  shared:         { icon: "group",      variant: "shared",   labelKey: "MachineKind.shared",        hintKey: "MachineKind.sharedHint",       showOwner: true },
  class_student:  { icon: "school",     variant: "class",    labelKey: "MachineKind.classMine",     hintKey: "MachineKind.classMineHint" },
  class_teacher:  { icon: "co_present", variant: "student",  labelKey: "MachineKind.classStudent",  hintKey: "MachineKind.classStudentHint", showOwner: true },
  quick_practice: { icon: "bolt",       variant: "practice", labelKey: "MachineKind.quickPractice", hintKey: "MachineKind.quickPracticeHint" },
  course:         { icon: "menu_book",  variant: "course",   labelKey: "MachineKind.course",        hintKey: "MachineKind.courseHint" },
  teacher_open:   { icon: "lock_open",  variant: "open",     labelKey: "MachineKind.teacherOpen",   hintKey: "MachineKind.teacherOpenHint",  showOwner: true },
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
