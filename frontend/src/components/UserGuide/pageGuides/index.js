/**
 * 各頁專屬導覽，以 routeGuides.js 的路由 id 為鍵。
 *
 * 值是導覽物件（titleKey、steps，選填 cleanupSelector 等，欄位同 UserGuide.jsx 的 PAGE_GUIDES），
 * 或是 (pathname) => 導覽物件，給同一路由底下有多個分頁區段的頁面依網址挑步驟。
 * id、icon、profile 由 routeGuides.js 帶入，這裡不用重寫。
 */
import { CLASS_WORKSPACE_GUIDES } from "./classWorkspace";
import { PERSONAL_GUIDES } from "./personal";
import { RESOURCE_REVIEW_GUIDES } from "./resourceReview";
import { SYSTEM_OPS_GUIDES } from "./systemOps";
import { SYSTEM_SETTINGS_GUIDES } from "./systemSettings";
import { TEACHING_COURSE_GUIDES } from "./teachingCourses";
import { TEMPLATES_AND_AI_GUIDES } from "./templatesAndAi";

export const ROUTE_DETAILED_GUIDES = {
  ...PERSONAL_GUIDES,
  ...RESOURCE_REVIEW_GUIDES,
  ...TEMPLATES_AND_AI_GUIDES,
  ...TEACHING_COURSE_GUIDES,
  ...CLASS_WORKSPACE_GUIDES,
  ...SYSTEM_SETTINGS_GUIDES,
  ...SYSTEM_OPS_GUIDES,
};
