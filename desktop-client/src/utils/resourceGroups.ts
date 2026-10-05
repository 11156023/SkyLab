export interface CourseResourceGroup {
  id: string;
  title: string;
  resources: SkyLabResource[];
  runningCount: number;
  nodeLabel: string;
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

function courseTitle(resources: SkyLabResource[], classId: string): string {
  return (
    resources.find(resource => resource.teaching_class_name)
      ?.teaching_class_name ||
    resources.find(resource => resource.course_environment_name)
      ?.course_environment_name ||
    resources.find(resource => resource.environment_type)?.environment_type ||
    `課程 ${classId.slice(0, 8)}`
  );
}

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
    .map(([classId, rows]) => {
      const sortedRows = [...rows].sort(resourceSort);
      const nodes = new Set(
        sortedRows.map(resource => resource.node).filter(Boolean)
      );
      return {
        id: classId,
        title: courseTitle(sortedRows, classId),
        resources: sortedRows,
        runningCount: sortedRows.filter(
          resource => resource.status === "running"
        ).length,
        nodeLabel:
          nodes.size === 1
            ? String([...nodes][0])
            : nodes.size > 1
              ? "多節點"
              : "配置中"
      };
    })
    .sort((a, b) => a.title.localeCompare(b.title, "zh-Hant"));

  const quickPracticeGroups = sessions
    .filter(session => quickPracticeMap.has(session.id))
    .map(session => {
      const sortedRows = [...quickPracticeMap.get(session.id)!].sort(
        resourceSort
      );
      const nodes = new Set(
        sortedRows.map(resource => resource.node).filter(Boolean)
      );
      return {
        id: `practice-${session.id}`,
        title: session.title || "Quick practice",
        resources: sortedRows,
        runningCount: sortedRows.filter(
          resource => resource.status === "running"
        ).length,
        nodeLabel: nodes.size === 1 ? String([...nodes][0]) : ""
      };
    });

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
