# Storage

Storage lists the PVE storages synced from every PVE connection and lets an administrator decide which of them the platform may use for new machines' disks, and in what order. When a machine is created, placement uses the enabled state, speed tier and priority set here to pick the storage that holds its disk. Sidebar: Admin Settings → Storage. Path: `/storage`.

## Who can use it

Administrators only. The page lives in the administrator settings sidebar, and reading or changing storage settings is refused for anyone else.

## Before you start

- The list comes from the last sync. Storages only appear or update after you click **Sync** on the [PVE Management](/manual/pve-connections) page; this page does not read PVE directly.
- If nothing has been synced yet, the page shows **No storage configuration yet**.

## Step by step

### Find a storage

1. Use the switch at the top: **All**, **Enabled** or **Disabled**. The number on each button is the count for that state.
2. Type in the search box (**Search by name, node, or type**). It matches the storage name, its node (every node, for shared storage), the connection name and the storage type.
3. If nothing matches, **No storage found** is shown with a button to clear the filters.

![Find a storage (numbers match the steps above)](/manual-shots/en/storage/s01.webp)

### Read a storage row

1. The first line shows the storage name and where it lives:
   - a node badge for storage local to one node, or
   - **Shared · N nodes** for shared storage; hover over it to see the node names.
   - When you have more than one PVE connection, a badge with the connection name is added.
2. The second line shows the storage type, used / total capacity in GB, and its purposes: **VM**, **LXC**, **ISO**, **Backup**, or **No purpose**.
3. Only storage marked **VM** is chosen for VM disks, and only storage marked **LXC** for container disks.

![Read a storage row (numbers match the steps above)](/manual-shots/en/storage/s02.webp)

### Set the speed tier

1. Open **Speed tier** on the row and choose **NVMe**, **SSD**, **HDD** or **Unknown**.
2. The choice is saved immediately and a message confirms it ("… updated").
3. Placement compares speed tier first (NVMe → SSD → HDD → Unknown) and then usage. Newly synced storage starts as **Unknown**, so set the correct tier after each sync that adds storage.

![Set the speed tier (numbers match the steps above)](/manual-shots/en/storage/s03.webp)

### Set the user priority

1. Type a whole number from 1 to 10 in **User priority**. Lower numbers come first.
2. Press Enter or click elsewhere to save. Nothing is saved while you are still typing.
3. Values outside 1–10, blanks or decimals revert to the saved value. If saving fails, the field also reverts.
4. Priority only breaks ties when speed tier and usage are about the same.

![Set the user priority (numbers match the steps above)](/manual-shots/en/storage/s04.webp)

### Enable or disable a storage

1. Tick or untick **Enabled** on the row. The change is saved immediately.
2. A disabled storage is never chosen for new machines' disks.

![Enable or disable a storage (numbers match the steps above)](/manual-shots/en/storage/s05.webp)

### Change shared storage

1. Change the speed tier, priority or **Enabled** on a shared storage row as usual.
2. The change is applied to every node in that cluster where the storage is mounted, and the message says how many nodes it was applied to ("… updated (applied to N nodes)").

![Change shared storage (numbers match the steps above)](/manual-shots/en/storage/s06.webp)

## Fields and options

| Field | Meaning | Notes |
| --- | --- | --- |
| Speed tier | NVMe, SSD, HDD or Unknown | Compared first; new storage defaults to Unknown |
| User priority | 1–10, lower first | Only breaks ties; saved on Enter or when leaving the field |
| Enabled | Whether placement may use this storage | Saved immediately |
| Purposes | VM, LXC, ISO, Backup as reported by PVE | Only VM / LXC storage holds machine disks |
| Shared · N nodes | Storage mounted on several nodes of one cluster | Shown once per cluster; changes apply to all its nodes |
| Used / total | Capacity in GB from the last sync | Refreshed by Sync on PVE Management |

## Limits and behaviour to know

- Capacity figures and the storage list are only as fresh as the last **Sync** on PVE Management.
- Every change is recorded in the audit log.
- Whether a machine can take snapshots is decided by PVE and checked live for each machine on its detail page; this page does not change it. For machines that cannot use snapshots, the detail page offers backups instead when a backup storage is set for that connection on PVE Management.

## Related

- [PVE Management](/manual/pve-connections) — sync storage and set each connection's backup storage
- [Resource Scheduling](/manual/scheduler) — placement settings that work together with storage choice
- [Resource Detail](/manual/resource-detail) — snapshots and backups on each machine
- [Audit Log](/manual/audit) — the record of storage changes
