# Governance configuration

The governance configuration is one settings record that controls the automatic policies the scheduler applies every 60 seconds. Administrators edit it on the [Governance](/manual/governance) page; the API is `GET` / `PUT /api/v1/governance/config`. This page lists every field with its default, its allowed range and what it does. Changing a value takes effect on the next scheduler cycle; nothing has to be restarted.

## Resource alerts

An alert is opened when a cluster, node or machine sample reaches a threshold. It is shown on the Monitoring page and e-mailed to every active administrator. The master switch, the cooldown and the check interval also apply to platform health alerts (scheduler, worker, Redis, PVE connections).

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `alerts_enabled` | `true` | | Master switch for resource and platform health alerts. |
| `alert_cpu_threshold` | `90` | 50–100 (%) | CPU usage above which an alert is raised. |
| `alert_memory_threshold` | `90` | 50–100 (%) | Memory usage threshold. |
| `alert_disk_threshold` | `90` | 50–100 (%) | Disk usage threshold. |
| `alert_cooldown_minutes` | `30` | 1–1440 | Minimum time between two alerts for the same target and metric. |
| `alert_check_interval_seconds` | `60` | 15–3600 | How often usage is sampled. |
| `alert_email_enabled` | `true` | | Also send the alert to the administrators by e-mail (needs SMTP). |

## TTL lifecycle

For every machine with an expiry date, the lifecycle task warns the owner before it, stops the machine at expiry and queues it for deletion after the grace period. Extending the expiry resets the warning.

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `ttl_enabled` | `true` | | Master switch. When off, expired machines are left running. |
| `expiry_warn_days` | `3` | 1–30 | Days before expiry at which the owner is notified. |
| `expiry_grace_delete_days` | `7` | 0–90 | Days after expiry before the stopped machine is queued for deletion. `0` deletes at expiry. |

## Idle detection

A running machine that has been up for the whole observation window and whose average CPU over that window stays below the threshold is marked idle. Both timers below count from that mark: the owner is notified first, and the machine is stopped when the grace period ends. Any load or a reboot clears the mark. Machines with an open mining incident are skipped.

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `idle_detection_enabled` | `true` | | Master switch. |
| `idle_cpu_threshold_percent` | `1.0` | 0.1–20 (%) | Average CPU below which the machine counts as idle. |
| `idle_window_hours` | `48` | 1–720 | Length of the observation window. |
| `idle_notify_after_hours` | `12` | 1–720 | Hours of continuous idleness before the owner is notified. Must be smaller than `idle_grace_hours`; the API rejects the update otherwise. |
| `idle_grace_hours` | `24` | 1–720 | Hours of continuous idleness before the machine is stopped. |
| `idle_scan_batch_size` | `20` | 1–200 | Machines examined per scheduler cycle. Raise it on large clusters. |

## Workload advisor

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `workload_advisor_enabled` | `true` | | Shows the "VM or container?" recommendation in the request form (`POST /api/v1/vm-requests/advise`). When off, the endpoint and requests in auto mode are rejected. |

## Mining detection

Sustained CPU saturation over the window is treated as a suspected mining incident: an evidence snapshot is taken, the machine is paused, an alert is opened and the administrators and the teachers of the owner's classes are e-mailed. Class machines and GPU machines get the evidence snapshot but are never paused automatically. Suspending the account is always a manual decision on the Monitoring page.

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `mining_detection_enabled` | `true` | | Master switch. |
| `mining_cpu_threshold_percent` | `90` | 50–100 (%) | CPU usage that counts as saturation. |
| `mining_window_hours` | `6` | 1–72 | Hours of continuous saturation before an incident is opened. |
| `mining_scan_batch_size` | `20` | 1–200 | Machines examined per cycle. |
| `mining_auto_suspend` | `true` | | Take the evidence snapshot and pause the machine automatically when an incident is opened. When off, the incident only produces the alert and the e-mail; all action is manual. |

## Provisioning

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `provision_max_concurrency` | `4` | 1–16 | Machine-request and quick-practice clones allowed to run at the same time in one worker process (with several worker processes the limit multiplies). Further clones wait in the queue. A class batch job creates its machines one after another and does not use this limit. |

## Snapshots and backups

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `snapshot_cleanup_enabled` | `true` | | Delete snapshots older than the retention automatically. The initial `skylab-init` snapshot is never removed; mining evidence snapshots are kept until 30 days after their incident is closed. |
| `snapshot_retention_days` | `7` | 1–90 | Age after which a snapshot is removed by the cleanup. |
| `student_snapshot_max_count` | `3` | 1–10 | Snapshots a non-administrator (student or teacher) may keep per machine. `skylab-init` and mining evidence snapshots do not count. |
| `student_backup_max_count` | `2` | 1–10 | Backups a non-administrator may keep per machine. Backups are the restore point for machines whose storage has no snapshot support, and use far more space, hence the separate limit. |

## Related

- [Governance page](/manual/governance) — the form that edits these values
- [Concepts](/concepts#background-work) — the scheduler loop that applies them
- [Monitoring](/manual/monitoring) — where alerts and mining incidents appear
