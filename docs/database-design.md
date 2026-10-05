# SkyLab database design and normalization

> **English** | [繁體中文](./database-design.zh-TW.md)

This document describes how the SkyLab PostgreSQL schema is normalized, which redundancy is kept on purpose, and how that redundancy is kept consistent. The models live in `backend/app/models/`; the normalization pass is migration `norm3nf01_normalize_schema`.

## Target: third normal form

The schema is kept in third normal form (3NF). Each step removes a class of problems:

| Normal form | Removes | Rule in SkyLab |
| --- | --- | --- |
| 1NF | Repeating groups and multi-valued columns | A list that is read, compared or edited element by element gets a child table. A column never holds a comma-separated list. |
| 2NF | Partial dependencies on part of a composite key | Tables use a single-column surrogate key (UUID or integer). Composite natural keys are `UNIQUE` constraints, and every other column depends on the whole key. |
| 3NF | Transitive dependencies (a non-key column determined by another non-key column) | A value that can be reached through a foreign key is read through that key and is not copied onto the row. |
| BCNF | Anomalies left over when a determinant is not a candidate key | Not pursued table by table; the 3NF rules above cover the cases that matter here. |

An unnormalized table causes three kinds of anomalies:

- **Insert anomaly**: a fact cannot be recorded without an unrelated fact. For example, a class machine mapping that also stored the machine's vmid could not exist until the vmid was known.
- **Delete anomaly**: deleting one fact loses another. For example, clearing a JSON list of completed items to remove one item.
- **Update anomaly**: a fact stored twice is updated in one place only. This is the most common problem in practice. Before `norm3nf01`, deleting a class machine cleared `batch_provision_tasks.vmid` through its foreign key, but the copy in `teaching_class_student_machines.vmid` kept pointing at the deleted VM.

## What `norm3nf01_normalize_schema` changed

### 1NF: multi-valued columns became child tables

| Before | After | Notes |
| --- | --- | --- |
| `subnet_config.dns_servers` (comma-separated string) | `subnet_dns_servers (subnet_config_id, position, address)` | Order is kept in `position`. The API still accepts and returns a comma-separated string, and every entry must be an IP address. |
| `subnet_config.extra_blocked_subnets` (comma/newline-separated text) | `subnet_blocked_subnets (subnet_config_id, position, cidr)` | Duplicates are removed and the first occurrence is kept. |
| `teacher_judge_student_submissions.completed_item_ids` (JSON array) | `teacher_judge_submission_items (submission_id, item_id)` | One row per completed rubric item. |
| `wireguard_peers.allowed_endpoints` (JSON array of objects) | `wireguard_peer_endpoints (peer_id, position, vmid, name, service, host, port)` | `WireGuardPeer.allowed_endpoints` remains a property that reads and replaces the rows. |

### 3NF: copies of values reachable through a foreign key were removed

| Removed column | Now read from | Anomaly it caused |
| --- | --- | --- |
| `teacher_judge_script_runs.teaching_class_id` | `artifact.teaching_class_id` (the run loads its artifact) | A run could name a different class than its script. |
| `teacher_judge_student_submissions.teaching_class_id` | `artifact.teaching_class_id` | Same as above. |
| `teacher_judge_student_submissions.is_ready` | `ready_at IS NOT NULL` | The flag and the timestamp could disagree. |
| `teacher_judge_session_attachments.message_id` | `teacher_judge_message_attachments (attachment_id, message_id)` | The message already determines the session, so an attachment row no longer stores both. A pending attachment has no link row. |
| `quick_practice_session_machines.name / role / resource_type / sort_order` | The node with the same `node_key` in the session's published (immutable) environment version | Copies of an immutable source. |
| `teaching_class_student_machines.vmid / status / error` | The batch task referenced by `batch_task_id` | Deleted machines kept a stale vmid. "Reclaimed" is now derived: a completed task whose vmid was cleared. |

### Constraints that make the remaining redundancy safe

| Constraint | Guarantees |
| --- | --- |
| `ck_<table>_resource_vmid_matches` on `audit_logs`, `deletion_requests`, `spec_change_requests`, `ip_allocation` | `resource_vmid` is either `NULL` or equal to `vmid` (see "vmid snapshot plus resource link" below). |
| `fk_teacher_judge_artifacts_session_same_class`, `fk_teacher_judge_artifacts_file_same_class` | An artifact's optional session and source file belong to the artifact's class. |
| `fk_teacher_judge_sessions_week_same_class`, `fk_teacher_judge_sessions_file_same_class` | A session's optional week and selected file belong to the session's class. |
| `fk_ai_api_usage_credential_owner` | A usage row recorded with an API key is attributed to the key's owner. |

These composite foreign keys reference new `UNIQUE (id, <parent>)` constraints on the parent tables. The original single-column foreign keys stay in place, so deleting the referenced row still sets the optional column to `NULL`. The composite key does not check rows whose optional column is `NULL`.

## Redundancy kept on purpose

Denormalization is acceptable when it serves performance or records history, provided the duplicated value has a single writer or a database constraint. Each kept case is listed with how it is controlled.

| Where | Why it is kept | How it stays consistent |
| --- | --- | --- |
| `batch_provision_jobs.total / done / failed_count` | Counters read by every class status refresh (comparable to stock levels or point balances) | Recomputed from the job's tasks with `sum(...)` rather than incremented blindly (`class_provision_service`, `batch_provision_service`). |
| `ai_api_usage.source` | Derivable from `credential_id IS NOT NULL`, but it leads the hot index `ix_ai_usage_user_source_created` used by every usage dashboard | `ck_ai_api_usage_source_credential` makes the two agree. |
| `ai_api_usage.user_id` on API-key calls | Usage queries filter by user on a high-volume table | `fk_ai_api_usage_credential_owner`. |
| vmid snapshot plus resource link (`audit_logs`, `deletion_requests`, `spec_change_requests`, `ip_allocation`) | Proxmox reuses VMIDs. `vmid` records which number was used at the time; `resource_vmid` links to that specific resource and becomes `NULL` when the resource is deleted. `resource_vmid` cannot be derived from `vmid`. | `ck_<table>_resource_vmid_matches`; writers use `linked_resource_vmid()`. |
| Snapshot columns: `spec_change_requests.current_*`, `deletion_requests.name / node / resource_type`, `mining_incidents.node / resource_type`, `vm_requests.vmid` | Record what was true when the request or incident was created; the source may later change or disappear | Written once and never updated; they are history, not a cache. |
| `nat_rule.vm_ip`, `reverse_proxy_rule.vm_ip` | The address that was validated against the IP allocation and written to the gateway. Re-deriving it would trust guest-agent IPs again and would make gateway sync depend on Proxmox being reachable. `vmid → vm_ip` does not hold by design. | Validated on creation by `publish_target_policy`. |
| `teaching_class_machine_nodes` (copied from course environment nodes) | A class adapts the version's nodes, for example raising `disk_gb` to the template's size or recording its batch job | Rebuilt as a whole when the class switches course versions. |
| `class_capacity_reservations` totals | A reservation is a commitment made at a point in time | Replaced as a whole when re-reserved. |
| `proxmox_storages` per node | A synchronized copy of the Proxmox storage inventory, keyed by `(node_name, storage)` for placement queries | Rewritten by `sync_storages`. Settings for shared storage are applied to all nodes by `update_storage_settings`. |
| `ai_api_credentials.api_key_name / rate_limit` | Copied from the request when the key is issued and editable afterwards, so they are the key's own attributes | Not a dependency on the request. |
| `resources.expiry_date` and `vm_requests.end_at` | Two different deadlines (resource lifetime and approved usage window); the effective one is the earlier | Extensions update both (see the request flow). |
| `resource_networks.ip_address` and `ip_allocation.ip_address` | The observed address (guest agent cache) and the allocated address are different facts | `resource_networks` is a cache with `cached_at`. |
| `resources.allocation_scope` | It stays `teaching_class` after the class link is cleared, so it is not determined by `teaching_class_id` | Set together with `control_policy` by the resource repository. |
| JSON documents (`task_records.payload / result`, `teacher_judge_*_json`, `batch_provision_jobs.template_params`, `course_environment_versions.draft_data`, `resources.guest_os`) | Opaque documents stored and returned as a whole, not queried element by element | Treated as single values; anything that becomes queried per element moves to a child table. |

Singleton configuration tables (`governance_config`, `proxmox_config`, `quota_config`, `ldap_config`, and others) have many columns but one row with `id = 1`, so they have no dependencies between rows.

## Checklist for schema changes

1. Before adding a column, check whether the value can be reached through an existing foreign key. If it can, read it through the key.
2. If you must copy a value (history or a hot path), list it in the table above and add a constraint or a single writer that keeps it consistent.
3. Do not store lists in a string or JSON column if anything filters, compares or edits single elements. Use a child table with a `position` column when order matters.
4. An optional reference to something that belongs to the same parent should use a composite foreign key on `(child_ref, parent_id)`.
5. Every model change needs an Alembic migration (revision id of 32 characters or fewer). Data-moving migrations must check existing data first and fail with a clear message, not silently rewrite it.

## Upgrading an existing database to `norm3nf01`

The migration runs in one transaction. Before changing anything it checks existing data and stops with a list of problems if:

- a quick-practice machine has no matching node in its environment version;
- a class machine mapping has no batch task but its vmid is an existing resource;
- a stored JSON list is not an array;
- an attachment is bound to a message of another session;
- any row would violate one of the new constraints.

Class machine mappings that have no batch task and whose vmid has no resource are already dangling. They are not treated as errors. The migration logs their count as a warning, and afterwards they read as not provisioned. `downgrade` restores the old columns from the new tables.
