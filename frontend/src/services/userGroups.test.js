import { beforeEach, describe, expect, test, vi } from "vitest";
import { UserGroupsService } from "./userGroups";

const jsonRes = (body = {}) => ({
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

describe("UserGroupsService", () => {
  test("lists, creates, renames and deletes groups", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonRes({ data: [], count: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await UserGroupsService.list();
    await UserGroupsService.create("資管三甲");
    await UserGroupsService.rename("g1", "資管三乙");
    await UserGroupsService.delete("g1");

    const calls = fetchMock.mock.calls.map(([url, init]) => [url, init.method ?? "GET", init.body]);
    expect(calls[0][0]).toContain("/api/v1/user-groups/");
    expect(calls[0][1]).toBe("GET");
    expect(calls[1][1]).toBe("POST");
    expect(JSON.parse(calls[1][2])).toEqual({ name: "資管三甲" });
    expect(calls[2][0]).toContain("/api/v1/user-groups/g1");
    expect(calls[2][1]).toBe("PATCH");
    expect(JSON.parse(calls[2][2])).toEqual({ name: "資管三乙" });
    expect(calls[3][0]).toContain("/api/v1/user-groups/g1");
    expect(calls[3][1]).toBe("DELETE");
  });

  test("adds members by ids or emails and removes by ids", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonRes({ added: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    await UserGroupsService.addMembers("g1", { userIds: ["u1"] });
    await UserGroupsService.addMembers("g1", { emails: ["a@x.edu"] });
    await UserGroupsService.removeMembers("g1", ["u1"]);
    await UserGroupsService.members("g1");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/user-groups/g1/members");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ user_ids: ["u1"], emails: [] });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ user_ids: [], emails: ["a@x.edu"] });
    expect(fetchMock.mock.calls[2][0]).toContain("/api/v1/user-groups/g1/members/remove");
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ user_ids: ["u1"] });
    expect(fetchMock.mock.calls[3][0]).toContain("/api/v1/user-groups/g1/members");
  });

  test("uploads a roster CSV as multipart", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonRes({ added: 2 }));
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["email\na@x.edu\n"], "group.csv", { type: "text/csv" });
    await UserGroupsService.importMembers("g1", file);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/user-groups/g1/members/import-csv");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.body.get("file").name).toBe("group.csv");
  });
});
