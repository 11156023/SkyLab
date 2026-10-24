import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ClassroomService } from "../services/classroom";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const classroomKeys = Object.freeze({
  all: ["classroom"],
  students: (classId) => ["classroom", "students", String(classId)],
  broadcastSources: (classId) => ["classroom", "broadcastSources", String(classId)],
  sessions: ["classroom", "sessions"],
  live: ["classroom", "live"],
});

/** 班級學生與各自的機器（AI 核查、班級工作區）；上課中機器開關會變，預設 30 秒刷新 */
export function useClassStudentsQuery(classId, { enabled, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: classroomKeys.students(classId),
    queryFn: () => ClassroomService.listClassStudents(classId),
    enabled: enabled ?? (classId != null),
    refetchInterval,
    select,
  });
}

/** 老師可拿來直播的來源機器 */
export function useClassBroadcastSourcesQuery(classId, { enabled, refetchInterval = false } = {}) {
  return useQuery({
    queryKey: classroomKeys.broadcastSources(classId),
    queryFn: () => ClassroomService.listClassBroadcastSources(classId),
    enabled: enabled ?? (classId != null),
    refetchInterval,
  });
}

/** 進行中的直播 session（老師／管理員） */
export function useClassroomSessionsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: classroomKeys.sessions,
    queryFn: () => ClassroomService.listSessions(),
    enabled,
    refetchInterval,
  });
}

/**
 * 目前對自己生效的廣播（學生端）。信令 WS 才是主要訊號來源，這裡只負責初次與重連時校正，
 * 所以預設不輪詢、切回分頁也不重抓（事件已經推過來了）。
 * 學生端常駐層（ClassroomStudentLayer）傳 enabled: false，由信令事件決定何時 refetch，
 * 再把 refetch 回來的快照併進本地狀態。
 */
export function useClassroomLiveQuery({ enabled = true, refetchInterval = false, refetchOnWindowFocus = false } = {}) {
  return useQuery({
    queryKey: classroomKeys.live,
    queryFn: () => ClassroomService.getLive(),
    enabled,
    refetchInterval,
    refetchOnWindowFocus,
  });
}

export function useClassroomCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidateSessions() {
      return queryClient.invalidateQueries({ queryKey: classroomKeys.sessions });
    },
    invalidateStudents(classId) {
      return queryClient.invalidateQueries({ queryKey: classroomKeys.students(classId) });
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: classroomKeys.all });
    },
  };
}
