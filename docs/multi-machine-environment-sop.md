# SOP: Building, Publishing and Using Multi-Machine Teaching Environments

> **English** | [繁體中文](./multi-machine-environment-sop.zh-TW.md)

| Item | Content |
| --- | --- |
| Document version | v1.0 |
| Effective date | 2026-08-31 (Asia/Taipei) |
| Document status | Official product and development baseline |
| Applicable system | SkyLab / Campus Cloud |
| Applicable roles | System administrators, teachers, students, development and operations staff |
| Precedence | This document takes precedence over the existing quick-template, single-machine clone and multi-machine environment design drafts; where an older document conflicts with this one, this document prevails |

## 1. Purpose

This SOP uniformly governs the following end-to-end process:

1. An administrator or teacher prepares a single-machine base template.
2. A teacher composes several VMs / LXCs into a reusable multi-machine teaching environment.
3. The teacher publishes a fixed, immutable version of the environment.
4. Students use a public practice environment, or the environment assigned to a formal class.
5. The system clones the whole set, applies the network, starts the machines, stops them on expiry and reclaims them, all in the background.

The core principle of this SOP is:

> Teachers build and publish environments; students only launch, enter and use them. Students do not manage base templates and never operate template cloning directly.

## 2. Scope and Exclusions

### 2.1 In scope

- Source management of single-machine VM / LXC base templates.
- Building multi-machine environments of one to three machines.
- Machine roles, fixed specifications, networking and connection topology.
- Drafts, publishing, version locking and creating new versions.
- Public quick practice.
- Formal course class environments.
- Student environment launch, use, expiry and reclamation.
- Permissions, security, failure handling, auditing and acceptance.

### 2.2 Out of scope

- Editing course materials, answering, grading and learning progress.
- General research-type or personally customised VM requests.
- Students creating, converting or cloning single-machine base templates themselves. (Students may still "request" one machine from an application template a teacher has opened up, but they cannot manage templates and cannot call the clone API directly.)

If general research-type machines are still needed, they must use the separate "resource request" flow and must not be mixed with the public practice environments governed by this SOP.

## 3. Terminology and Data Model

| User-facing term | System model | Definition | Visible to students? |
| --- | --- | --- | --- |
| Single-machine base template / machine image | `VMTemplate` or a PVE VM / LXC image | A single source machine with the OS and tools already installed, used as the source for building environments | Private by default; only templates whose visibility a teacher sets to "visible to everyone" appear under resource settings in the student request form |
| Multi-machine teaching environment | `CourseEnvironment` | A reusable environment composed of one to three fixed machines plus a topology | Name, description and machine roles are visible |
| Environment version | `CourseEnvironmentVersion` | A snapshot of nodes, specifications and topology locked at publish time | Version number is visible; not modifiable |
| Public practice environment | A published version with `usage_scope = quick_practice` or `both` | A short-lived multi-machine environment students can launch themselves | Visible and launchable |
| Course environment | A published version with `usage_scope = course` or `both` | A fixed environment a teacher applies to a formal class | Usable only by class members |
| Practice instance | `QuickPracticeSession` | The lifecycle of one student launching the full multi-machine environment once | Students only see their own |
| Student machine | `Resource` / `VMRequest` | The actual VM / LXC the system creates on PVE from a published version | Students only see their own or class-authorised resources |

### 3.1 Terminology rules

- The teacher side may use "base template", "environment template" and "create new version".
- The student side may only use "public practice environment", "course environment", "launch environment", "enter environment" and "end practice".
- The student side must not use infrastructure terms such as "clone", "full clone", "linked clone" or "PVE template ID".
- "Copy" only means the system creating machines internally from an environment version; it is not a student feature.

## 4. Roles and Permissions

| Operation | Administrator | Teacher | Student |
| --- | :---: | :---: | :---: |
| Create, convert and maintain single-machine base templates | Yes | If authorised | No |
| View base template technical details | Yes | If authorised | No |
| Create a multi-machine environment draft | Yes | Yes | No |
| Edit own drafts | Yes | Yes | No |
| Publish an environment version | Yes | Yes; high-risk environments may require review | No |
| Create a new environment version | Yes | Yes | No |
| Unpublish or archive an environment | Yes | Own environments | No |
| Launch a public practice environment | For testing | For testing | Yes |
| Use a formal class environment | Per management permission | Per teaching permission | Only own class environments |
| View other students' environments | Yes | Only classes they teach | No |
| Call the single-machine template clone API directly | Administrative use | If authorised | No |
| Request a single machine from the application template catalog | Yes | Yes | Yes; limited to opened templates, specs decided by the template, goes through normal review |

Hiding features in the frontend does not count as access control. Every restriction must be re-validated by the backend API; unauthorised requests must return `403`, and resources that do not exist or are not visible return `404` according to the information-disclosure policy.

## 5. Standard End-to-End Flow

```text
Administrator / teacher prepares a single-machine base template
          ↓
Teacher creates a multi-machine environment draft
          ↓
Add machines, specifications, roles, network and topology
          ↓
Save → preview → validate → publish and lock the version
          ↓
     ┌────┴────┐
     ↓         ↓
Public quick practice   Applied to a formal class
     ↓         ↓
Students launch themselves   Class review and pre-provisioning
     └────┬────┘
          ↓
Students use all machines as one environment group
          ↓
End / expiry / course archived
          ↓
Whole set stopped, deleted; IPs, VMIDs and capacity released
```

## 6. SOP-A: Preparing a Single-Machine Base Template

### 6.1 Executing role

The primary executor is the administrator; a teacher may do this only when authorised.

### 6.2 Prerequisites

- PVE nodes, storage and network are healthy.
- The operating system, licences and teaching software come from legitimate sources.
- The base machine has had security updates and the required tools installed.
- The VM / LXC type, default user and boot method have been confirmed.

### 6.3 Steps

1. Create or select a fully installed source VM / LXC.
2. Remove personal data, fixed SSH host keys, temporary files and credentials that must not be copied.
3. Confirm that cloud-init, networking, the guest agent or the LXC boot settings can correctly produce a new machine.
4. Set the template name, operating system, resource type, storage location and default specification.
5. Run one test clone and confirm it boots, obtains an IP, can be logged into and can be cleaned up.
6. Mark the template as `ready` before allowing teachers to reference it.

### 6.4 Completion criteria

- The template status is `ready`.
- The test machine can be created and booted within the allowed time.
- It contains no test account passwords, API keys, student data or other sensitive information.
- A base template referenced by a published environment must not be hard-deleted directly; disable it first and complete a reference inventory.

### 6.5 Opening the template to student self-service requests (optional)

Tick "Allow students to request" only when students should be able to request one pre-installed application environment themselves (for example n8n or Jupyter):

- Only templates that are `ready` and ticked appear in the "Application templates" group of the student request form (`GET /templates/catalog`).
- Requests are always for a single machine. The template only decides the source and type (VM / LXC); CPU and memory are chosen by the requester and the personal quota applies as usual; disk is automatically raised to no less than the template's own size (a clone can only grow).
- The request still goes through the normal review flow, is never auto-approved, and counts towards the personal quota.
- Base templates that are not ticked never appear in any student list: `GET /vm/templates` returns only unregistered base images to non-teachers.
- At creation time the backend re-validates that the template exists, is `ready` and has the open flag; frontend list filtering does not count as access control.

## 7. SOP-B: Teacher Builds a Multi-Machine Teaching Environment

### 7.1 Entry point

The teacher opens "Multi-machine environment templates" and selects "Create multi-machine environment".

### 7.2 Fill in basic information

The teacher must fill in:

- Environment name: use a name students can understand.
- Environment description: describe the purpose, prerequisites and expected outcome.
- Availability:
  - `course`: used only for formal courses.
  - `quick_practice`: used only for quick practice.
  - `both`: usable for both.
- Maximum concurrent sessions (optional): the cap on parallel sessions for the whole environment; leave blank for unlimited. The per-student limit of "1 concurrent, 3 per 24 hours" still applies.
- Student audience (required when availability includes quick practice):
  - `class`: only enrolled students of the specified classes can see it; at least one of the teacher's own classes must be selected.
  - `campus`: every signed-in user can see it.
  - `owner`: not open yet; only the creator can see it, useful for self-verification before publishing.

### 7.3 Add machines

1. Add one to three VMs / LXCs.
2. For each machine, choose a ready base template or an allowed base image.
3. Set the machine name and role, for example "Web", "Database", "Client".
4. Set fixed CPU, RAM, disk and network.
5. Confirm that the total resources of the set comply with the platform and student quota policies.

Students must not be able to modify any of the above at launch time.

### 7.4 Configure the topology

The teacher configures machine connections according to the teaching needs:

- Source machine and target machine.
- One-way or two-way.
- Protocol.
- Port; no port is specified when the protocol is `any`.

The system must block:

- Source and target being the same machine.
- Links pointing to a node that does not exist.
- Duplicate connections.
- Invalid ports or protocols.
- Network settings the platform cannot actually apply.

### 7.5 Unpublishing and deletion

- Unpublishing (`POST /course-environments/{id}/retire`) switches all published versions to `retired`: the environment disappears immediately from student lists and from the class selection list, while existing sessions and classes run to their original deadlines. To reopen it, create a new version and publish it.
- Deletion (`DELETE /course-environments/{id}`) is allowed only for environments with no class or practice records referencing them; any reference causes a rejection with a request to unpublish instead.
- Before a base template is deleted, the system inventories multi-machine environment references (including drafts and retired versions) and refuses deletion if any exist.

### 7.6 Save a draft

- A draft can be edited repeatedly.
- A draft is not shown to students and cannot be applied to a formal class.
- A teacher may run a test deployment before publishing; test resources must not be treated as student environments.

### 7.7 Build completion checklist

- [ ] Name and description are complete.
- [ ] Availability is correct.
- [ ] The quick-practice audience is correct; when classes are specified, the list is accurate.
- [ ] Whether to set a concurrency cap has been assessed against cluster capacity.
- [ ] Every machine has a source, a role and fixed specifications.
- [ ] The machine count is between one and three.
- [ ] All base templates are `ready`.
- [ ] The topology has no orphan errors, duplicate connections or invalid ports.
- [ ] Total CPU, RAM, disk, IPs and machine count are within the allowed range.
- [ ] No passwords, tokens or personal data are included.

## 8. SOP-C: Teacher Publishes and Opens the Environment

### 8.1 Pre-publish validation

The system must re-run server-side validation and must not trust the frontend alone:

1. The teacher owns or has the right to manage the environment.
2. The latest version is still a draft.
3. All nodes and source templates are valid.
4. Specifications and topology are legal.
5. Capacity and security policies allow publishing.
6. If high-risk capabilities exceed the teacher's authority, administrator review has been completed.

High-risk capabilities include at least GPUs, privileged LXCs, public ports, high resource specifications and high-privilege startup scripts.

### 8.2 Audience

When publishing, "availability" and "audience" must be set separately:

| Audience | Definition | Status |
| --- | --- | --- |
| `owner` | Only the creator and administrators can test | Implemented |
| `class` | Only enrolled students of the specified formal classes | Implemented; the default for new environments |
| `campus` | All signed-in users | Implemented |
| `users` | Only specified students | Not yet implemented |
| `system` | Platform-wide environments maintained by system administrators | Not yet implemented |

`class` only honours the rosters of classes whose status is `active`; students who drop a class lose visibility immediately. Both the list and the launch API re-validate the audience, and always return 404 for environments that are not visible, never revealing whether the environment exists. When `campus` is selected, the publish screen must warn explicitly that "every signed-in student can see and launch this".

Environments that existed before this revision keep `campus` so that existing practice does not disappear; teachers should change each one to the correct audience.

### 8.3 Publish action

1. The teacher reviews the publish summary, including machine count, total specifications, topology, deadline and audience.
2. The teacher clicks "Publish and lock".
3. The system creates a configuration hash and records the publisher and publish time.
4. The version status changes from `draft` to `published`.
5. A published version can never be modified directly.

### 8.4 Publish result

- `course`: appears in the teacher's environment selection list for formal classes.
- `quick_practice`: appears in the public practice list of students who match the audience.
- `both`: appears in both places.

### 8.5 Modifying, unpublishing and archiving

- To modify published content, a new draft version must be created from the existing version.
- The old version keeps serving bound classes and existing sessions and must not be overwritten by the new version.
- Unpublishing only blocks new launches; it must not immediately delete environments students are using.
- Before archiving, confirm there are no in-progress sessions, class references or pending resources.
- Deleting a base template or an environment always starts with a reference check; the default is soft delete / retired.

## 9. SOP-D: Students Use a Public Practice Environment

### 9.1 What students see

The "Quick practice environments" section on the student home page shows only:

- Environment name and description.
- Machine count, names and roles.
- A summary of total CPU, RAM and disk.
- Usage period.
- Status: available, creating, full or paused.

It must not show the source base template ID, PVE node selection, clone mode, administrative storage names or base template operations.

### 9.2 Launch steps

1. The student signs in to the home page.
2. The student selects an available public practice environment.
3. The student reviews the fixed machine set, usage period and data reclamation notice.
4. The student clicks "Launch environment". The only required parameter the student submits is the environment ID.
5. The system locks the user's launch flow to prevent duplicate clicks.
6. The system re-validates the publish status, usage, audience, personal limits and total quota.
7. The system reserves the full set of capacity, IPs, VMIDs and required network resources in one step.
8. The system creates one session and creates all machines from the fixed version.
9. After all machines are complete, the system applies the topology and runs health checks.
10. Once the session status is `ready`, the student enters from "My environments".

### 9.3 Rules enforced by the system

- Students cannot change the machine count, type, CPU, RAM, disk, source image or deadline.
- One student may have at most one public quick practice environment at a time.
- One student may create at most three sets within a rolling 24-hour window.
- The practice duration comes from the system setting `practice_session_hours`, currently defaulting to three hours.
- Quick practice cannot be extended by the student; if this is allowed in future, a separate explicit policy must be created.
- Limits are counted per session; three machines still count as one practice.

### 9.4 How it is used

- "My environments" presents one parent environment group.
- Expanding it shows each machine's name, role, status, IP and terminal or console entry.
- Students may start, shut down normally and open their own machines as authorised.
- Per-machine operations must not break the session's fixed deadline or the whole-set reclamation rules.
- Dedicated session operations "Start all", "Stop all" and "End practice" are recommended.

### 9.5 Readiness criteria

"Environment ready" may be shown to the student only when all of the following hold:

- All machines have been created.
- All required machines have obtained an IP.
- Topology and firewall rules have been applied.
- The required inter-machine connectivity tests pass.
- The student has terminal / console permission on their own machines.

## 10. SOP-E: Formal Class Environments

### 10.1 Teacher sets up the class

1. Create the class and its timetable.
2. Add existing student accounts.
3. Select a published, fixed version whose usage is `course` or `both`.
4. The system calculates "number of students × one full machine set per student".
5. The system pre-checks CPU, RAM, disk, IP, VMID, node and time-window capacity.
6. The teacher confirms and submits provisioning.
7. The class enters administrator review or the platform approval flow.

### 10.2 System provisioning

1. An independent environment group is created for each student.
2. Each student receives machines, specifications and topology from the same published version.
3. Each student's dedicated IPs and isolation rules are applied.
4. All topology edges in the environment version are applied.
5. When everything succeeds, the class becomes `active`.
6. On partial failure the class becomes `partial_failed` and must not be shown as completed.

### 10.3 Student use

- Students only see their own class and the machines under their own name.
- Students enter from "My courses" or "My environments", never through the template list.
- Students cannot switch environment versions, add machines or modify fixed specifications.
- Whether machines can be started, stopped or extended outside class hours is decided by class and platform policy.

### 10.4 Class end

- Before a teacher archives a class, the system shows the machines still running and the data impact.
- Students are notified to export their work first, according to the retention policy.
- When the retention period ends, the system stops and deletes the class resources in bulk.
- The class may be marked as fully archived only after resource release is complete.

## 11. Session States and Failure Handling

### 11.1 Target state machine

```text
creating → ready → stopping → reclaiming → reclaimed
    │         │
    └→ partial_failed → repairing ─┘
                    └→ reclaiming
```

| State | Meaning | Student UI |
| --- | --- | --- |
| `creating` | Reserving and creating the full machine set | Show progress; relaunch not allowed |
| `ready` | All machines and topology complete | Can enter the environment |
| `partial_failed` | At least one machine failed or the topology is incomplete | Show "being processed"; must not claim it is usable |
| `repairing` | The system is idempotently rebuilding failed nodes | Show "repairing" |
| `stopping` | Expired or ended by the user; shutting down normally | New operations disabled |
| `reclaiming` | Deleting the full resource set | Show "reclaiming" |
| `reclaimed` | Machines, IPs, VMIDs and reservations released | Removed from the in-progress list; an audit summary may be kept |

### 11.2 All-or-nothing principle

- The session and all its machine requests must be created in the same DB transaction.
- PVE provisioning uses a per-machine idempotency key; retries must not create duplicate VMs.
- The full resource set must be reserved in one step before launch; if any resource is insufficient, provisioning does not start.
- On partial failure, prefer rebuilding only the failed nodes.
- If repair cannot complete within the policy time, the system must reclaim the whole set and release all reservations.
- Students must never be left to deal with an incomplete multi-machine environment themselves.

### 11.3 User messages

Error messages must tell students what to do next, but must not expose PVE credentials, internal addresses, stack traces or sensitive errors. For example:

- Insufficient capacity: "There is not enough capacity available right now. No machines were created; please try again later."
- Creation failed: "The environment was not fully created and the system is repairing it; your usage count will not be charged twice."
- Repair failed: "The environment could not be completed and the system has reclaimed its resources; this attempt does not count towards your usage limit."

## 12. Expiry, End and Resource Reclamation

### 12.1 Public quick practice

Standard flow:

```text
Reminder before expiry
→ expires_at reached
→ Whole set shut down normally
→ 15–30 minute reclamation buffer
→ Delete all VMs / LXCs
→ Remove topology and firewall rules
→ Release IPs, VMIDs and capacity reservations
→ Session = reclaimed
```

- When a student clicks "End practice", the system must state clearly that data will be deleted.
- Expiry reclamation is done for the whole set; deleting only one machine is not allowed.
- Reclamation jobs must be retryable and idempotent.
- If any resource deletion fails, keep the error record and hand it to operations for retry.

### 12.2 Formal classes

- Follow the timetable shutdown and the class data retention policy.
- A class pause is not deletion; a student's long-term work must not be deleted because one lesson ended.
- Final whole-set reclamation happens only after the class is archived or the retention period ends.

## 13. Teacher Publishing, Unpublishing and Version Governance Rules

1. A published version cannot be overwritten.
2. A new version affects only subsequent new sessions or new classes; existing users stay on their original version.
3. After unpublishing, no new sessions can be created, but existing sessions may be used until they expire unless an administrator force-stops them due to a security incident.
4. When a serious vulnerability is found, an administrator may disable the environment urgently and must record the reason, the operator and the scope of impact.
5. Before retiring a base template, all referencing environments and versions must be listed.
6. Versions that are published, referenced by a class or still have sessions must not be hard-deleted.
7. Teachers may manage only the environments they created; administrators may govern all environments.

## 14. Security, Privacy and Auditing

### 14.1 Required permission restrictions

- Students must not access the base template management and clone features under `/templates`.
- `/api/v1/templates/{template_id}/clone` must be restricted to administrators / authorised teachers.
- `/course-template-management` and the related create / edit routes must have a teacher-side route guard; the backend must still validate `InstructorUser`.
- Students may only obtain their own sessions, requests, resources and connection credentials.
- Teachers may only view student environments in the classes they teach, and every view and control operation must be audited.

### 14.2 Audit events

Record at least:

- Base template creation, status changes and retirement.
- Environment draft creation, update, publish, unpublish and new version creation.
- Publisher, publish time, version and configuration hash.
- Student session launch, ready, failure, end, expiry and reclamation.
- Administrator force-stop, repair and deletion.
- Teachers or administrators opening a student's terminal / console.

## 15. UI and API Mapping

### 15.1 Teacher side

| Task | UI route | Existing API |
| --- | --- | --- |
| Multi-machine environment list | `/course-template-management` | `GET /api/v1/course-environments` |
| Create environment | `/course-template-management/new` | `POST /api/v1/course-environments` |
| Update draft | `/course-template-management/{id}` | `PUT /api/v1/course-environments/{id}` |
| Publish and lock | Same as above | `POST /api/v1/course-environments/{id}/publish` |
| Create new version | Same as above | `POST /api/v1/course-environments/{id}/versions` |
| Fetch versions available to formal courses | Class settings | `GET /api/v1/course-environments/published` |

### 15.2 Student side

| Task | UI route | Existing API |
| --- | --- | --- |
| Browse public practice environments | `/dashboard` | `GET /api/v1/quick-practice/templates` |
| View environment details | `/quick-template/{id}` | `GET /api/v1/quick-practice/templates/{id}` |
| Launch the full environment | Same as above | `POST /api/v1/quick-practice/templates/{id}/launch` |
| View own sessions | `/my-resources` | `GET /api/v1/quick-practice/sessions/my` |

Future student-side routes and copy may rename `quick-template` to `practice-environment`; even before the rename it must not be presented as students cloning a template.

## 16. Acceptance Criteria

### 16.1 Permission acceptance

- [ ] The student sidebar does not show "Machine templates" or "Multi-machine environment templates".
- [ ] A student typing the management routes above directly is redirected or shown "no permission".
- [ ] A student calling the single-machine clone API receives `403`.
- [ ] Students cannot see other students' environments and sessions, or environments outside their audience.
- [ ] Teachers cannot edit other teachers' environments.

### 16.2 Teacher build and publish acceptance

- [ ] An environment draft with one to three machines can be created.
- [ ] Invalid base templates, nodes, topologies and ports are rejected by the backend.
- [ ] A version cannot be modified after publishing.
- [ ] Creating a new version does not affect existing classes and sessions.
- [ ] `usage_scope` correctly separates the formal class list from the quick practice list.
- [ ] A complete summary of resources, deadline and audience is visible before publishing.

### 16.3 Public practice acceptance

- [ ] A student can launch the full environment by submitting only the environment ID.
- [ ] Tampering with the frontend cannot change specifications, count, source template or deadline.
- [ ] Multiple machines count as a single session.
- [ ] Duplicate clicks do not create duplicate sessions or machines.
- [ ] No machines are created when quota or capacity is insufficient.
- [ ] `ready` is shown only after all machines and the topology are complete.
- [ ] When any machine fails, it can be rebuilt or the whole set is reclaimed automatically.
- [ ] After expiry the whole set is deleted and all resources are released.

### 16.4 Formal class acceptance

- [ ] Only published versions whose usage is `course` / `both` can be selected.
- [ ] Each student receives an independent, identically specified full machine set.
- [ ] Each student's topology edges are actually applied successfully.
- [ ] Students can operate only their own class environment.
- [ ] A partially failed class enters `partial_failed` and can become `active` only after repair.
- [ ] Class archiving reclaims resources according to the retention policy.

## 17. Current Gaps and Implementation Priority

This section records the gaps between the code as of 2026-08-31 and this SOP; it does not lower the requirements above.

### P0: Baseline completed on 2026-08-31

| Item | What was completed |
| --- | --- |
| Student single-machine template cloning closed | Student navigation and routes no longer offer base templates; template lists, attachments and the clone API are limited to teachers / administrators; the clone service re-validates permissions |
| Quick practice multi-machine topology | The firewall topology is applied from the published version's edges only after all machines are complete; the session does not become ready before that |
| Whole-set IP and request transaction | All concrete IPs are reserved in one step before launch; the session, machine requests and reservations use the same DB transaction |
| Partial failure compensation | A failed provisioning reclaims the machines of the same set that already succeeded; topology can be retried periodically, and if it still fails after fifteen minutes the whole set is reclaimed |
| Whole-set reclamation on expiry | On expiry the existing scheduler shuts the machines down first; after a thirty-minute buffer the idempotent deletion queue removes the VMs / LXCs and releases IPs and database resources |
| Teacher management route guard | `/templates`, multi-machine environment and class management routes are restricted to teachers / administrators, and the backend API validates in step |

Before going live on production PVE, real-machine E2E must still be completed: confirm that every VM / LXC combination passes the actual service connectivity tests required by the environment definition after rules are applied. At this stage `ready` means all machines have been created and the firewall topology API was applied successfully; no application-level health check is yet run actively from inside the guest.

### P1: Publishing governance

| Gap | Current state | Done when |
| --- | --- | --- |
| Only three audience levels | owner, class and campus are supported; both the list and the launch validate | Extend to finer audiences such as users and system as needed |
| No publish review | Teacher publishing takes effect immediately | High-risk capabilities go through administrator review |
| ~~No full unpublish / archive~~ | Unpublishing (versions become `retired`) and deletion when unreferenced are supported | Existing sessions run to their original deadlines |
| ~~No environment-wide concurrency cap~~ | `max_concurrent_sessions` is supported; blank = unlimited | Launch is blocked when full |
| ~~Insufficient base template reference protection~~ | Deleting a base template inventories multi-machine environment references and refuses | Switch to another source or unpublish the environment first |

### P2: Usage and operations experience

- ~~Add whole-set start, stop and end-practice APIs.~~ (`POST /quick-practice/sessions/{id}/end` is supported; students can end early and reclaim immediately)
- Show class and student identity in the administrator resource groups.
- Show session creation progress and the health of each node.
- Add environment-full, queueing and estimated wait time.
- Alerts and an operations panel for creation failures, topology failures and reclamation failures.

## 18. Go-Live Approval Form

For every environment prepared for student use, the publisher should complete the following approval form:

| Check item | Result | Notes |
| --- | --- | --- |
| Base template security and licensing check | Pass / Fail |  |
| All one to three machines can be created normally | Pass / Fail |  |
| Total resources and concurrency capacity are acceptable | Pass / Fail |  |
| IP and network topology validated | Pass / Fail |  |
| Required inter-machine connectivity tested | Pass / Fail |  |
| Students cannot modify fixed specifications | Pass / Fail |  |
| Audience and usage are correct | Pass / Fail |  |
| Expiry and whole-set reclamation tested | Pass / Fail |  |
| Failure rebuild or rollback tested | Pass / Fail |  |
| Publisher / reviewer | Name and time |  |

## 19. Document Maintenance

- Whenever product roles, publishing rules, the session lifecycle or the permission model change, this SOP must be updated in step.
- When API or UI names change but the flow does not, update the mapping table in section 15.
- Before every production release, run the regression acceptance in section 16.
- Once a gap in section 17 is closed, remove it from that section and keep it in the version history; it must not be silently treated as a permanent exception.
