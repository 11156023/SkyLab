export interface CourseResourceGroup {
  id: string;
  /** 課程名稱；沒有任何可用名稱時為空字串，由畫面補「未命名課程」 */
  title: string;
  resources: SkyLabResource[];
  runningCount: number;
}

export interface GroupedResources {
  courseGroups: CourseResourceGroup[];
  quickPracticeGroups: CourseResourceGroup[];
  personalResources: SkyLabResource[];
}

function resourceSort(a: SkyLabResource, b: SkyLabResource): number {
  const nameCompare = String(a.name ?? "").localeCompare(
    String(b.name ?? ""),
    "zh-Hant"
  );
  if (nameCompare !== 0) return nameCompare;
  return Number(a.vmid ?? 0) - Number(b.vmid ?? 0);
}

function courseTitle(resources: SkyLabResource[]): string {
  return (
    resources.find(resource => resource.teaching_class_name)
      ?.teaching_class_name ||
    resources.find(resource => resource.course_environment_name)
      ?.course_environment_name ||
    resources.find(resource => resource.environment_type)?.environment_type ||
    ""
  );
}

function toGroup(
  id: string,
  title: string,
  rows: SkyLabResource[]
): CourseResourceGroup {
  const resources = [...rows].sort(resourceSort);
  return {
    id,
    title,
    resources,
    runningCount: resources.filter(resource => resource.status === "running")
      .length
  };
}

/**
 * 把機器分成三類：快速練習（依啟動的 session 一組一個資料夾）、課程（依班級）、個人。
 * 同一個範本啟動兩次是兩個 session，各自一個資料夾；session 清單載入失敗時機器仍留在個人區。
 */
export function groupResourcesByCourse(
  resources: SkyLabResource[] = [],
  sessions: SkyLabQuickPracticeSession[] = []
): GroupedResources {
  const courseMap = new Map<string, SkyLabResource[]>();
  const quickPracticeMap = new Map<string, SkyLabResource[]>();
  const personalResources: SkyLabResource[] = [];
  const sessionByRequest = new Map<string, SkyLabQuickPracticeSession>();

  for (const session of sessions) {
    for (const machine of session.machines ?? []) {
      if (machine.request_id) {
        sessionByRequest.set(String(machine.request_id), session);
      }
    }
  }

  for (const resource of resources) {
    const practiceSession = resource.request_id
      ? sessionByRequest.get(String(resource.request_id))
      : undefined;
    if (practiceSession) {
      const rows = quickPracticeMap.get(practiceSession.id) ?? [];
      rows.push(resource);
      quickPracticeMap.set(practiceSession.id, rows);
    } else if (resource.teaching_class_id) {
      const classId = String(resource.teaching_class_id);
      const rows = courseMap.get(classId) ?? [];
      rows.push(resource);
      courseMap.set(classId, rows);
    } else {
      personalResources.push(resource);
    }
  }

  const courseGroups = [...courseMap.entries()]
    .map(([classId, rows]) => toGroup(classId, courseTitle(rows), rows))
    .sort((a, b) => a.title.localeCompare(b.title, "zh-Hant"));

  const quickPracticeGroups = sessions
    .filter(session => quickPracticeMap.has(session.id))
    .map(session =>
      toGroup(
        `practice-${session.id}`,
        session.title || "",
        quickPracticeMap.get(session.id)!
      )
    );

  return {
    courseGroups,
    quickPracticeGroups,
    personalResources: [...personalResources].sort(resourceSort)
  };
}

export function findResourceForTunnel(
  tunnel: SkyLabTunnelInfo,
  resources: SkyLabResource[]
): SkyLabResource | undefined {
  return resources.find(
    resource => Number(resource.vmid) === Number(tunnel.vmid)
  );
}
