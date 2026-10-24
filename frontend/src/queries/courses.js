import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { CourseAdminService, CoursesService } from "../services/courses";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

/* ── 學生端 ── */

export const courseKeys = Object.freeze({
  all: ["courses"],
  schedule: ["courses", "schedule"],
  reminders: ["courses", "reminders"],
  paths: ["courses", "paths"],
  path: (pathId) => ["courses", "path", String(pathId)],
  aiAssignments: (pathId) => ["courses", "aiAssignments", String(pathId)],
  weeklyTasks: (pathId) => ["courses", "weeklyTasks", String(pathId)],
  practiceMachines: (pathId) => ["courses", "practiceMachines", String(pathId)],
});

/** 我的課表（學生首頁、課程頁共用） */
export function useCourseScheduleQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({ queryKey: courseKeys.schedule, queryFn: () => CoursesService.listSchedule(), enabled, refetchInterval });
}

/** 課程提醒（JobsProvider 的提醒通知） */
export function useCourseRemindersQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, refetchOnWindowFocus } = {}) {
  return useQuery({
    queryKey: courseKeys.reminders,
    queryFn: () => CoursesService.listReminders(),
    enabled,
    refetchInterval,
    /* 只有呼叫端有指定才覆寫；明寫 undefined 會蓋掉全站預設 */
    ...(refetchOnWindowFocus === undefined ? null : { refetchOnWindowFocus }),
  });
}

/** 我的學習路徑清單 */
export function useCoursePathsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({ queryKey: courseKeys.paths, queryFn: () => CoursesService.listPaths(), enabled, refetchInterval });
}

export function useCoursePathQuery(pathId, { enabled } = {}) {
  return useQuery({ queryKey: courseKeys.path(pathId), queryFn: () => CoursesService.getPath(pathId), enabled: enabled ?? (pathId != null) });
}

export function useAiAssignmentsQuery(pathId, { enabled } = {}) {
  return useQuery({ queryKey: courseKeys.aiAssignments(pathId), queryFn: () => CoursesService.getAiAssignments(pathId), enabled: enabled ?? (pathId != null) });
}

export function useWeeklyTasksQuery(pathId, { enabled } = {}) {
  return useQuery({ queryKey: courseKeys.weeklyTasks(pathId), queryFn: () => CoursesService.getWeeklyTasks(pathId), enabled: enabled ?? (pathId != null) });
}

export function usePracticeMachinesQuery(pathId, { enabled, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: courseKeys.practiceMachines(pathId),
    queryFn: () => CoursesService.getPracticeMachines(pathId),
    enabled: enabled ?? (pathId != null),
    refetchInterval,
  });
}

export function useCourseCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: courseKeys.all });
    },
    invalidatePath(pathId) {
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: courseKeys.path(pathId) }),
        queryClient.invalidateQueries({ queryKey: courseKeys.aiAssignments(pathId) }),
        queryClient.invalidateQueries({ queryKey: courseKeys.weeklyTasks(pathId) }),
      ]);
    },
  };
}

/* ── 老師端（課程 CMS） ── */

export const courseAdminKeys = Object.freeze({
  all: ["courseAdmin"],
  paths: ["courseAdmin", "paths"],
  pathProgress: (pathId) => ["courseAdmin", "pathProgress", String(pathId)],
  rooms: (pathId) => ["courseAdmin", "rooms", String(pathId)],
  tasks: (roomId) => ["courseAdmin", "tasks", String(roomId)],
  questions: (taskId) => ["courseAdmin", "questions", String(taskId)],
});

/** 老師的學習路徑清單（課程 CMS 的內容樹、進度選單）；編輯頁面預設不輪詢，改動後由呼叫端 invalidatePaths */
export function useAdminCoursePathsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({ queryKey: courseAdminKeys.paths, queryFn: () => CourseAdminService.listPaths(), enabled, refetchInterval });
}

/**
 * 學習路徑的學生進度報表。
 * @param {object} [options]
 * @param {boolean} [options.refetchOnWindowFocus] 有即時推播（WebSocket）時傳 false：推播才是主要訊號，
 *   切回分頁的重抓可能比推播觸發的快照舊。省略時用全站預設
 */
export function usePathProgressQuery(pathId, {
  enabled,
  refetchInterval = LIST_REFRESH_INTERVAL,
  refetchOnWindowFocus,
} = {}) {
  return useQuery({
    queryKey: courseAdminKeys.pathProgress(pathId),
    queryFn: () => CourseAdminService.getPathProgress(pathId),
    enabled: enabled ?? (pathId != null),
    refetchInterval,
    /* 只有呼叫端有指定才覆寫；明寫 undefined 會蓋掉全站預設 */
    ...(refetchOnWindowFocus === undefined ? null : { refetchOnWindowFocus }),
  });
}

/* cacheFirst：已經有人抓過（例如內容樹）就直接沿用，不因為多一個地方要看就再抓一次；
   寫入後的 invalidate 照樣會重抓 */
const CACHE_FIRST_OPTIONS = Object.freeze({ staleTime: Infinity });

/**
 * 路徑底下的關卡（內容樹展開時才抓：enabled 傳「展開中」）。
 * @param {object} [options]
 * @param {boolean} [options.cacheFirst] 見 CACHE_FIRST_OPTIONS（工作區讀選中的那一層用）
 */
export function useCourseRoomsQuery(pathId, { enabled, cacheFirst = false } = {}) {
  return useQuery({
    queryKey: courseAdminKeys.rooms(pathId),
    queryFn: () => CourseAdminService.listRooms(pathId),
    enabled: enabled ?? (pathId != null),
    ...(cacheFirst ? CACHE_FIRST_OPTIONS : null),
  });
}

/** 關卡底下的任務；cacheFirst 同 useCourseRoomsQuery */
export function useCourseTasksQuery(roomId, { enabled, cacheFirst = false } = {}) {
  return useQuery({
    queryKey: courseAdminKeys.tasks(roomId),
    queryFn: () => CourseAdminService.listTasks(roomId),
    enabled: enabled ?? (roomId != null),
    ...(cacheFirst ? CACHE_FIRST_OPTIONS : null),
  });
}

/** 任務底下的題目 */
export function useCourseQuestionsQuery(taskId, { enabled } = {}) {
  return useQuery({ queryKey: courseAdminKeys.questions(taskId), queryFn: () => CourseAdminService.listQuestions(taskId), enabled: enabled ?? (taskId != null) });
}

export function useCourseAdminCacheUpdater() {
  const queryClient = useQueryClient();
  /* 只包著 queryClient：固定成同一個物件，放進 useCallback／effect 的依賴也不會每次 render 都換 */
  return useMemo(() => ({
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: courseAdminKeys.all });
    },
    /** 新增／改名／刪除路徑後：等清單重抓完才 resolve（呼叫端接著要選中新路徑） */
    invalidatePaths() {
      return queryClient.invalidateQueries({ queryKey: courseAdminKeys.paths });
    },
    invalidateRooms(pathId) {
      return queryClient.invalidateQueries({ queryKey: courseAdminKeys.rooms(pathId) });
    },
    invalidateTasks(roomId) {
      return queryClient.invalidateQueries({ queryKey: courseAdminKeys.tasks(roomId) });
    },
    invalidateQuestions(taskId) {
      return queryClient.invalidateQueries({ queryKey: courseAdminKeys.questions(taskId) });
    },
    /**
     * 推播觸發的進度快照（呼叫端自己排好先後）寫進快取。
     * 先取消還在路上的查詢：比這份早送出的抓取晚回來時，不能把較新的快照蓋掉
     * （cancelQueries 要等它完成再寫，否則還原會蓋掉剛寫進去的資料）。
     */
    async setPathProgress(pathId, report) {
      const queryKey = courseAdminKeys.pathProgress(pathId);
      await queryClient.cancelQueries({ queryKey, exact: true });
      queryClient.setQueryData(queryKey, report);
    },
  }), [queryClient]);
}
