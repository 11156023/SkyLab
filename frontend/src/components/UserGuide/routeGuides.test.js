import { describe, expect, it } from "vitest";
import { getRouteGuide, ROUTE_GUIDES } from "./routeGuides";

describe("route guide catalog", () => {
  it("covers every primary application route family", () => {
    const paths = [
      "/dashboard", "/courses", "/courses/linux", "/courses/linux/weeks/1",
      "/quick-create", "/quick-template/12", "/my-resources", "/my-resources/101", "/my-requests", "/account",
      "/resource-mgmt", "/resource-mgmt/101", "/request-review", "/gpu-mgmt", "/batch-review", "/templates",
      "/ai-api", "/ai-api-review", "/ai-api-keys", "/ai-monitoring",
      "/course-cms", "/course-template-management", "/course-template-management/new",
      "/course-template-management/template-1", "/class-management", "/class-setup",
      "/class-management/class-1", "/class-management/class-1/machines", "/class-management/class-1/ai",
      "/admin", "/pve-connections", "/scheduler", "/governance", "/quotas", "/ldap", "/nodes", "/storage",
      "/monitoring", "/ip-management", "/audit", "/jobs", "/firewall", "/domain", "/gateway", "/reverse-proxy",
    ];

    expect(paths.filter((path) => !getRouteGuide(path))).toEqual([]);
  });

  it("keeps generic page guides opt-in", () => {
    for (const route of ROUTE_GUIDES) {
      expect(route.autoStart).not.toBe(true);
    }
  });

  it("does not force users through a guide on every page", () => {
    expect(getRouteGuide("/dashboard").autoStart).toBeFalsy();
    expect(getRouteGuide("/class-management/class-1").autoStart).toBeFalsy();
    expect(getRouteGuide("/monitoring").autoStart).toBeFalsy();
  });

  it("provides an empty-state simulation profile for every generic route", () => {
    const supportedProfiles = new Set([
      "resource", "review", "configure", "monitor", "teaching",
      "learning", "workflow", "request", "explore",
    ]);
    for (const route of ROUTE_GUIDES) {
      const samplePath = route.id === "resource-detail" ? "/my-resources/101" : null;
      expect(supportedProfiles.has(route.profile)).toBe(true);
      if (samplePath) expect(getRouteGuide(samplePath).generic).toBe(true);
    }
  });
});
