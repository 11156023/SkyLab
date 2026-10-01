import { describe, expect, test } from "vitest";
import { findActiveBackupJob, formatBackupSize } from "./backupFormat";

describe("formatBackupSize", () => {
  test.each([
    [5832173897, "5.4 GB"],
    [1024 ** 3, "1.0 GB"],
    [300 * 1024 ** 2, "300 MB"],
    [1024 ** 2, "1 MB"],
    [508, "< 1 MB"],
    [0, "< 1 MB"],
  ])("%d → %s", (bytes, expected) => {
    expect(formatBackupSize(bytes)).toBe(expected);
  });

  test.each([[null], [undefined], ["123"], [Number.NaN], [-1]])("沒有可用數值（%s）顯示 —", (value) => {
    expect(formatBackupSize(value)).toBe("—");
  });
});

describe("findActiveBackupJob", () => {
  const jobs = [
    { id: "template:1", kind: "template", meta: { vmid: 105 } },
    { id: "resource_reset:2", kind: "resource_reset", meta: { vmid: 105 } },
    { id: "resource_backup:3", kind: "resource_backup", meta: { vmid: 999 } },
    { id: "resource_restore:4", kind: "resource_restore", meta: { vmid: "105" } },
  ];

  test("只認這台機器的備份／還原任務", () => {
    expect(findActiveBackupJob(jobs, 105)?.id).toBe("resource_restore:4");
    expect(findActiveBackupJob(jobs, 999)?.id).toBe("resource_backup:3");
  });

  test("沒有相符任務或清單不存在時回 null", () => {
    expect(findActiveBackupJob(jobs, 1)).toBeNull();
    expect(findActiveBackupJob(null, 105)).toBeNull();
    expect(findActiveBackupJob([{ kind: "resource_backup" }], 105)).toBeNull();
  });
});
