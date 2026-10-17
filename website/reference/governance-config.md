# Governance configuration

The governance configuration is one settings record that controls the automatic policies the scheduler applies every 60 seconds. Administrators edit it on the [Governance](/manual/governance) page; the API is `GET` / `PUT /api/v1/governance/config`. This page lists every field with its default, its allowed range and what it does. Changing a value takes effect on the next scheduler cycle; nothing has to be restarted.

## Resource alerts

Alerts are raised when a machine stays above a threshold, shown on the Monitoring page and e-mailed to the owner.

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `alerts_enabled` | `true` | | Master switch for resource alerts. |
| `alert_cpu_threshold` | `90` | 50–100 (%) | CPU usage above which an alert is raised. |
| `alert_memory_threshold` | `90` | 50–100 (%) | Memory usage threshold. |
| `alert_disk_threshold` | `90` | 50–100 (%) | Disk usage threshold. |
| `alert_cooldown_minutes` | `30` | 1–1440 | Minimum time between two alerts for the same machine and metric. |
| `alert_check_interval_seconds` | `60` | 15–3600 | How often usage is sampled. |
| `alert_email_enabled` | `true` | | Also send the alert by e-mail (needs SMTP). |

## TTL lifecycle

Every machine has an expiry date. The lifecycle task warns before it, stops the machine at expiry and deletes it after the grace period. Extending the expiry from the machine's detail page resets the cycle.

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `ttl_enabled` | `true` | | Master switch. When off, expired machines are left running. |
| `expiry_warn_days` | `3` | 1–30 | Days before expiry at which the owner is notified. |
| `expiry_grace_delete_days` | `7` | 0–90 | Days after expiry before the stopped machine is queued for deletion. `0` deletes at expiry. |

## Idle detection

A machine whose CPU stays below the threshold for the whole window is considered idle. The owner is notified first; after the grace period the machine is stopped.

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `idle_detection_enabled` | `true` | | Master switch. |
| `idle_cpu_threshold_percent` | `1.0` | 0.1–20 (%) | Average CPU below which the machine counts as idle. |
| `idle_window_hours` | `48` | 1–720 | Length of the observation window. |
| `idle_notify_after_hours` | `12` | 1–720 | Hours of continuous idleness before the owner is notified. Must be smaller than `idle_grace_hours`; the API rejects the update otherwise. |
| `idle_grace_hours` | `24` | 1–720 | Hours after the notification before the machine is stopped. |
| `idle_scan_batch_size` | `20` | 1–200 | Machines examined per scheduler cycle. Raise it on large clusters. |

## Workload advisor

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `workload_advisor_enabled` | `true` | | Shows the "VM or container?" recommendation in the request form (`POST /api/v1/vm-requests/advise`). |

## Mining detection

Sustained CPU saturation over the window is treated as a suspected mining incident: the machine's state is captured as evidence, the machine is paused and the owner and administrators are notified. Suspending the account is always a manual decision on the Monitoring page.

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `mining_detection_enabled` | `true` | | Master switch. |
| `mining_cpu_threshold_percent` | `90` | 50–100 (%) | CPU usage that counts as saturation. |
| `mining_window_hours` | `6` | 1–72 | Hours of continuous saturation before an incident is opened. |
| `mining_scan_batch_size` | `20` | 1–200 | Machines examined per cycle. |
| `mining_auto_suspend` | `true` | | Pause the machine automatically when an incident is opened. When off, only the evidence and the notification are produced. |

## Provisioning

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `provision_max_concurrency` | `4` | 1–16 | Clone jobs allowed to run at the same time across the worker. Batch provisioning of a class queues behind this limit. |

## Snapshots and backups

| Field | Default | Range | Effect |
| --- | --- | --- | --- |
| `snapshot_cleanup_enabled` | `true` | | Delete snapshots older than the retention automatically. |
| `snapshot_retention_days` | `7` | 1–90 | Age after which a snapshot is removed by the cleanup. |
| `student_snapshot_max_count` | `3` | 1–10 | Snapshots a student may keep per machine. |
| `student_backup_max_count` | `2` | 1–10 | Backups a student may keep per machine. Backups are the restore point for machines whose storage has no snapshot support, and use far more space, hence the separate limit. |

## Related

- [Governance page](/manual/governance) — the form that edits these values
- [Concepts](/concepts#background-work) — the scheduler loop that applies them
- [Monitoring](/manual/monitoring) — where alerts and mining incidents appear
