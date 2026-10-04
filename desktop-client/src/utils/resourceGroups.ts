export interface CourseResourceGroup {
  id: string;
  title: string;
  resources: SkyLabResource[];
  runningCount: number;
  nodeLabel: string;
}

export interface GroupedResources {
  courseGroups: CourseResourceGroup[];
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
  resources: SkyLabResource[] = []
): GroupedResources {
  const courseMap = new Map<string, SkyLabResource[]>();
  const personalResources: SkyLabResource[] = [];

  for (const resource of resources) {
    if (resource.teaching_class_id) {
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

  return {
    courseGroups,
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
