import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["src/utils/resourceGroups.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm"
});
const { groupResourcesByCourse } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

const resources = [
  { vmid: 203, request_id: "personal", name: "personal-vm", status: "stopped" },
  { vmid: 205, request_id: "first", name: "practice-lxc-1", status: "running" },
  { vmid: 206, request_id: "second", name: "practice-lxc-2", status: "running" },
  { vmid: 207, teaching_class_id: "class-1", name: "class-vm", status: "running" }
];
const sessions = [
  {
    id: "session-1",
    title: "SkyLab 課程活動環境",
    machines: [{ request_id: "first" }, { request_id: "second" }]
  }
];

test("machines from one quick-practice session appear in one folder", () => {
  const grouped = groupResourcesByCourse(resources, sessions);
  assert.deepEqual(grouped.quickPracticeGroups.map(group => group.title), [
    "SkyLab 課程活動環境"
  ]);
  assert.deepEqual(
    grouped.quickPracticeGroups[0].resources.map(resource => resource.vmid),
    [205, 206]
  );
  assert.equal(grouped.quickPracticeGroups[0].runningCount, 2);
  assert.deepEqual(grouped.personalResources.map(resource => resource.vmid), [203]);
  assert.deepEqual(grouped.courseGroups[0].resources.map(resource => resource.vmid), [207]);
});

test("a session without listed resources adds no empty folder", () => {
  const grouped = groupResourcesByCourse([resources[0]], sessions);
  assert.deepEqual(grouped.quickPracticeGroups, []);
  assert.deepEqual(grouped.personalResources.map(resource => resource.vmid), [203]);
});

test("separate launches of one template remain separate folders", () => {
  const secondSession = {
    id: "session-2",
    title: sessions[0].title,
    machines: [{ request_id: "third" }]
  };
  const grouped = groupResourcesByCourse(
    [...resources, { vmid: 208, request_id: "third", name: "other-launch", status: "running" }],
    [...sessions, secondSession]
  );
  assert.deepEqual(
    grouped.quickPracticeGroups.map(group => group.id),
    ["practice-session-1", "practice-session-2"]
  );
  assert.deepEqual(
    grouped.quickPracticeGroups.map(group => group.resources.map(resource => resource.vmid)),
    [[205, 206], [208]]
  );
});

test("resources stay visible if session loading fails", () => {
  const grouped = groupResourcesByCourse(resources, []);
  assert.deepEqual(grouped.personalResources.map(resource => resource.vmid), [203, 205, 206]);
});
