import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  courseNodeHasUsableSource,
  CourseEnvironmentsService,
  environmentPayload,
  normalizeCourseEnvironment,
} from "./courseEnvironments";

const jsonRes = (body) => ({
  ok: true,
  status: 200,
  json: async () => body,
});

beforeEach(() => {
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  });
});

describe("CourseEnvironmentsService", () => {
  test("unfinished drafts round-trip without changing input or server identity", async () => {
    const draft = { name: "", nodes: [{ id: "web", name: "", role: "", cpu: "", memory: "", disk: "", type: "lxc" }], edges: [] };
    const fetchMock = vi.fn().mockResolvedValue(jsonRes({
      id: "env-1", version_id: "v1", status: "draft",
      draft_data: { editor: { ...draft, id: "untrusted", status: "published" } },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const saved = await CourseEnvironmentsService.saveDraft(null, draft);
    expect(fetchMock.mock.calls[0][0]).toContain("/course-environments/drafts");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).editor).toEqual(draft);
    expect(saved.nodes).toEqual(draft.nodes);
    expect(saved.id).toBe("env-1");
    expect(saved.status).toBe("draft");
  });

  test("existing drafts update their own endpoint and preserve creation retry identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonRes({ id: "env-1", version_id: "v1", status: "draft" }));
    vi.stubGlobal("fetch", fetchMock);
    await CourseEnvironmentsService.saveDraft("env-1", { name: "", nodes: [], draftRequestId: "request-1" });
    expect(fetchMock.mock.calls[0][0]).toContain("/course-environments/env-1/draft");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).draft_id).toBe("request-1");
  });

  test("published list uses the classroom selection endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonRes([]));
    vi.stubGlobal("fetch", fetchMock);

    await CourseEnvironmentsService.listPublished();

    expect(fetchMock.mock.calls[0][0]).toContain(
      "/api/v1/course-environments/published",
    );
  });

  test("payload stores memory in MB and pins the PVE template", () => {
    const payload = environmentPayload({
      name: "Web Lab",
      description: "",
      usageScope: "both",
      nodes: [{
        id: "web",
        sourceTemplateId: "tpl-id",
        name: "Web",
        role: "server",
        type: "VM",
        cpu: 2,
        memory: 4,
        disk: 30,
        network: "lab-net",
      }],
    });

    expect(payload.usage_scope).toBe("both");
    expect(payload.nodes[0]).toEqual({
      node_key: "web",
      source_type: "template",
      source_template_id: "tpl-id",
      custom_image_ref: null,
      custom_username: null,
      custom_unprivileged: true,
      name: "Web",
      role: "server",
      resource_type: "qemu",
      cpu: 2,
      memory_mb: 4096,
      disk_gb: 30,
      network: "lab-net",
      position_x: 80,
      position_y: 120,
    });
  });

  test("payload supports a custom LXC node and firewall-style edge", () => {
    const payload = environmentPayload({
      name: "Network Lab",
      nodes: [
        { id: "fw", sourceType: "custom", customImageRef: "local:vztmpl/debian.tar.zst", customUnprivileged: true, name: "Firewall", role: "gateway", type: "lxc", cpu: 2, memory: 2, disk: 8, network: "lab-net" },
        { id: "web", sourceType: "custom", customImageRef: "9000", customUsername: "student", name: "Web", role: "server", type: "qemu", cpu: 2, memory: 4, disk: 20, network: "lab-net" },
      ],
      edges: [{ source: "fw", target: "web", direction: "one_way", protocol: "tcp", port: "443" }],
    });

    expect(payload.nodes[0].source_template_id).toBeNull();
    expect(payload.nodes[0].custom_image_ref).toContain("debian");
    expect(payload.edges[0]).toEqual({
      source_node_key: "fw",
      target_node_key: "web",
      direction: "one_way",
      protocol: "tcp",
      port: 443,
    });
  });

  test("payload keeps the class allow-list only for a class audience", () => {
    const base = {
      name: "Firewall Lab",
      usageScope: "quick_practice",
      audienceClassIds: ["class-a", "class-b"],
      nodes: [{ id: "fw", sourceTemplateId: "tpl-id", name: "FW", role: "gateway", type: "lxc", cpu: 1, memory: 1, disk: 8 }],
    };

    expect(environmentPayload({ ...base, audience: "class" }).audience_class_ids)
      .toEqual(["class-a", "class-b"]);
    expect(environmentPayload({ ...base, audience: "campus" }).audience_class_ids)
      .toEqual([]);
    expect(environmentPayload({ ...base, audience: "campus" }).audience).toBe("campus");
  });

  test("normalize defaults an environment without an audience to class scope", () => {
    const normalized = normalizeCourseEnvironment({
      id: "env-1",
      version_id: "ver-1",
      nodes: [],
      edges: [],
    });

    expect(normalized.audience).toBe("class");
    expect(normalized.audienceClassIds).toEqual([]);
  });

  test("classroom selection accepts both machine templates and custom images", () => {
    expect(courseNodeHasUsableSource({
      sourceType: "template",
      sourceTemplateId: "tpl-id",
    })).toBe(true);
    expect(courseNodeHasUsableSource({
      sourceType: "custom",
      customImageRef: "local:vztmpl/debian.tar.zst",
    })).toBe(true);
    expect(courseNodeHasUsableSource({
      sourceType: "custom",
      sourceTemplateId: null,
      customImageRef: "",
    })).toBe(false);
  });
});
