# SOP: Building, Publishing and Using Multi-Machine Teaching Environments

This SOP is the product and development baseline for multi-machine teaching environments in SkyLab. It applies to administrators, teachers, students, developers and operators. Screen-by-screen instructions are in the user manual: [Templates](/manual/templates), [Teaching Environments](/manual/course-templates), [Quick Practice](/manual/quick-practice) and [Create Class](/manual/class-setup).

## 1. Purpose

This SOP governs the following end-to-end process:

1. A teacher or administrator prepares a single-machine template.
2. A teacher composes one to three VMs / LXCs into a reusable multi-machine teaching environment.
3. The teacher publishes the environment, which locks its machine configuration.
4. Students use it as a quick practice environment, or as the environment assigned to a formal class.
5. The system clones the whole set, applies the network, starts the machines, stops them on expiry and reclaims them, all in the background.

The core principle of this SOP is:

> Teachers build and publish environments; students only launch, enter and use them. Students do not manage templates and never operate template cloning directly.

## 2. Scope and Exclusions

### 2.1 In scope

- Source management of single-machine VM / LXC templates.
- Building multi-machine environments of one to three machines.
- Machine roles, fixed specifications and connection topology.
- Drafts, publishing and version locking.
- Quick practice.
- Formal class environments.
- Student environment launch, use, expiry and reclamation.
- Permissions, security, failure handling, auditing and acceptance.

### 2.2 Out of scope

- Editing course materials, answering, grading and learning progress.
- General research-type or personally customised VM requests.
- Students creating, converting or cloning single-machine templates themselves. (Students may still request one machine from a template whose visibility is "Visible to Everyone", but they cannot manage templates and cannot call the clone API.)

General research-type machines use the separate resource request flow and are not mixed with the quick practice environments governed by this SOP.

## 3. Terminology and Data Model

| User-facing term | System model | Definition | Visible to students? |
| --- | --- | --- | --- |
| Template / base image | `VMTemplate`, or a PVE VM / LXC image not registered as a template | A single source machine with the OS and tools already installed, used as the source of environment machines | Templates are private by default; only those set to "Visible to Everyone" appear in the student request form |
| Teaching environment | `CourseEnvironment` | A reusable set of one to three fixed machines plus their connections and published services | Name, description and machine roles are visible |
| Environment version | `CourseEnvironmentVersion` | The machines, specifications and topology locked at publish time | Version number is visible; not modifiable |
| Quick practice environment | A published environment with `usage_scope = quick_practice` or `both` | A short-lived multi-machine environment any signed-in user can launch | Visible and launchable |
| Course environment | A published environment with `usage_scope = course` or `both` | An environment a teacher applies to a formal class | Usable only by class members |
| Practice session | `QuickPracticeSession` | The lifecycle of one launch of the whole environment by one user | Students only see their own |
| Student machine | `Resource` (built from a `VMRequest` for quick practice, or from a batch provisioning task for a class) | The actual VM / LXC the system creates on PVE from a published version | Students only see their own or class-authorised resources |

### 3.1 Terminology rules

- The teacher side uses "Templates", "Teaching Environments" and "Publish and lock".
- The student side uses "Quick Practice", "Launch", "Courses", "My Resources" and "End Practice".
- The student side must not use infrastructure terms such as "clone", "full clone", "linked clone" or "PVE template ID".
- "Copy" only means the system creating machines internally from an environment version; it is not a student feature.

## 4. Roles and Permissions

| Operation | Administrator | Teacher | Student |
| --- | :---: | :---: | :---: |
| Create and maintain single-machine templates | Yes | Yes; only from their own machines, and only their own templates | No |
| View template technical details | All templates | Their own and those "Visible to Everyone" | No |
| Create a teaching environment draft | Yes | Yes | No |
| Edit drafts | All environments | Their own | No |
| Publish an environment version | Yes | Their own; takes effect immediately | No |
| Change name, usage and description after publishing | Yes | Their own | No |
| Delete an environment no class or session has used | Yes | Their own | No |
| Launch a quick practice environment | For testing | For testing | Yes |
| Use a formal class environment | Per management permission | Per teaching permission | Only own class environments |
| View other students' environments | Yes | Only classes they teach | No |
| Call the single-machine template clone API directly | Yes | Yes, for ready templates they can see | No (`403`) |
| Request one machine from a template in the request form | Yes | Yes | Yes; only "Visible to Everyone" templates, through normal review; the template sets the source and type, the requester picks CPU and memory |

Hiding features in the frontend does not count as access control. Every restriction is re-validated by the backend API; unauthorised requests return `403`, and resources that do not exist or are not visible return `404` according to the information-disclosure policy.

## 5. Standard End-to-End Flow

```text
Teacher / administrator prepares a single-machine template
          ↓
Teacher creates a teaching environment draft
          ↓
Add machines, specifications, roles and connections (the draft autosaves)
          ↓
Server-side validation → publish and lock the version
          ↓
     ┌────┴────┐
     ↓         ↓
Quick practice   Applied to a formal class
     ↓         ↓
Users launch themselves   Class review and provisioning
     └────┬────┘
          ↓
Students use all machines as one environment group
          ↓
End / expiry / class archived
          ↓
Whole set stopped and deleted; IPs, VMIDs and capacity released
```

## 6. SOP-A: Preparing a Single-Machine Template

The page and its fields are described in [Templates](/manual/templates).

### 6.1 Executing role

Teachers and administrators. A teacher can only use their own machines as the source.

### 6.2 Prerequisites

- PVE nodes, storage and network are healthy.
- The operating system, licences and teaching software come from legitimate sources.
- The source machine has had security updates and the required tools installed.
- The VM / LXC type, default user and boot method have been confirmed.

### 6.3 Steps

1. Prepare a fully installed source VM / LXC.
2. Remove personal data, fixed SSH host keys, temporary files and credentials that must not be copied.
3. Confirm that networking, the guest agent or the LXC boot settings produce a working new machine. For a VM whose login password the platform should set, install cloud-init (cloudbase-init on Windows) and give it a cloud-init drive in PVE.
4. On the Templates page, create the template from the source machine with a name, description, visibility and, optionally, default CPU and memory. The type, operating system and disk come from the source machine, which is shut down and protected on PVE; it is not converted into a PVE template.
5. Once the system marks the template **Ready**, run one test clone and confirm it boots, obtains an IP, can be logged into and can be deleted.

### 6.4 Completion criteria

- The template status is `ready`.
- The test machine can be created and booted within the allowed time.
- It contains no test account passwords, API keys, student data or other sensitive information.
- A template cannot be deleted while a teaching environment (including drafts and retired versions), an unprovisioned request, an unfinished batch job or a clone still uses it. To take it out of the request form, set it to "Private".

### 6.5 Opening the template to student requests (optional)

Whether students can request one pre-installed application environment themselves (for example n8n or Jupyter) is decided by the template's **visibility**: "Visible to Everyone" (`global`) opens it; "Private" keeps it out of every student list.

- Only templates that are `ready` and "Visible to Everyone" appear in the "Environment Templates by Teacher / Official" group of the student request form (`GET /templates/catalog`).
- Requests are always for a single machine. The template only decides the source and type (VM / LXC); CPU and memory are chosen by the requester and the personal quota applies as usual; disk is automatically raised to no less than the template's own size (a clone can only grow).
- The request still goes through the normal review flow, is never auto-approved, and counts towards the personal quota.
- Private templates never appear in any student list: `GET /vm/templates` returns only unregistered base images to non-teachers.
- At creation time the backend re-validates that the template exists, is `ready` and is visible to everyone; frontend list filtering does not count as access control.

## 7. SOP-B: Teacher Builds a Multi-Machine Teaching Environment

The editor is described step by step in [Teaching Environments](/manual/course-templates).

### 7.1 Entry point

The teacher opens **Teaching → Teaching Environments** and clicks **Create teaching environment**.

### 7.2 Fill in basic information

- Environment name: use a name students can understand. A draft may leave it blank; publishing requires it.
- Environment purpose: describe the purpose, prerequisites and expected outcome.
- Usage:
  - `course` (**Formal courses only**): offered in class setup.
  - `quick_practice` (**Quick practice only**): offered on the Quick Practice page.
  - `both`: offered in both places.
- Documents (optional): handouts attached to the environment itself, not to a version.

An environment offered for quick practice is visible to every signed-in user; it cannot be limited to certain classes. The editor has no environment-wide concurrency cap; the per-user limits of one active session and three launches per 24 hours always apply. (The backend still enforces `max_concurrent_sessions` when it is set through the API.)

### 7.3 Add machines

1. Add one to three VMs / LXCs.
2. For each machine, choose a ready template the environment's owner can see, or a base image. The machine type must match the template.
3. Set the machine name and role, for example "Web", "Database", "Client".
4. Set CPU, RAM and disk; the disk cannot be smaller than the source.
5. Confirm that the total resources of the set fit the platform capacity and the students' quotas; both are checked when a student launches or a class is set up.

Students cannot modify any of the above at launch time.

### 7.4 Configure the topology

Machines reach each other only over the connections the teacher draws; with no connection they are isolated from each other. Outbound Internet access is open by default. For each connection the teacher sets:

- Source machine and target machine.
- One-way or bidirectional.
- Protocol and port.

A machine's service can also be published to the outside, at a URL (the teacher's prefix plus 16 random characters per student) or on an external port; see [Teaching Environments](/manual/course-templates).

The backend rejects:

- Source and target being the same machine.
- Connections pointing to a machine that does not exist.
- A connection already covered by another one (a bidirectional connection includes the reverse direction).
- A missing port for TCP, UDP or SCTP, and invalid protocols or ports.
- More than six connections or six published services, and the same port of one machine published twice.

### 7.5 Deletion

- Deletion (`DELETE /course-environments/{id}`) is allowed only when no class or practice session, including ended ones, has ever used the environment.
- To stop offering a used environment for quick practice, change its usage to **Formal courses only**; class setup then lists it only for its owner and administrators. There is no unpublish or archive state.
- Before a template is deleted, the system lists the teaching environments that use it (including drafts and retired versions) and refuses the deletion if there are any.

### 7.6 Drafts

- Every change to a draft is saved automatically, and a draft can be edited repeatedly.
- A draft is not shown to students and cannot be applied to a formal class.
- A draft cannot be test-deployed. To try an environment end to end, publish it for quick practice and launch it yourself; this makes it visible to every signed-in user at once.

### 7.7 Build completion checklist

- [ ] Name and description are complete.
- [ ] Usage is correct.
- [ ] Every machine has a source, a role and fixed specifications.
- [ ] The machine count is between one and three.
- [ ] All source templates are `ready`.
- [ ] The topology has no duplicate connections or invalid ports.
- [ ] Total CPU, RAM, disk, IPs and machine count are within the allowed range.
- [ ] No passwords, tokens or personal data are included.

## 8. SOP-C: Teacher Publishes the Environment

### 8.1 Pre-publish validation

The server re-runs validation and does not trust the frontend alone:

1. The user owns the environment or is an administrator.
2. The latest version is still a draft.
3. The name is filled in and there are one to three machines.
4. Every source template is ready, visible to the environment's owner and of the matching type.
5. Connections and published services pass the checks in section 7.4.

Capacity and quota are checked later, when a student launches or a class is set up. High-risk capabilities have no administrator review yet (section 17).

### 8.2 Where a published environment is offered

| Usage | Offered in |
| --- | --- |
| `course` | The environment list of class setup, for the environment's owner and administrators |
| `quick_practice` | The Quick Practice page and the student home page, for every signed-in user |
| `both` | Both places |

Usage can be changed at any time, before or after publishing (`PATCH /course-environments/{id}/basics`). The change affects only new launches and new classes; running sessions and existing classes continue to their own end. The quick practice list and launch API re-check the usage and return `404` for environments that are not offered.

### 8.3 Publish action

1. On **Machine Configuration**, the teacher clicks **Publish**.
2. The teacher confirms **Publish and lock**.
3. The system runs the validation in section 8.1, records a configuration hash and the publish time, and changes the version from `draft` to `published`.
4. A published version can never be modified.

### 8.4 Changing a published environment

- Name, usage, description and documents can still be changed.
- Machines, specifications, connections and published services are frozen. To change them, build a separate environment; there is no "new version".
- Classes and sessions keep the version they were created with.

## 9. SOP-D: Using a Quick Practice Environment

The screens are described in [Quick Practice](/manual/quick-practice).

### 9.1 What students see

The Quick Practice page (`/quick-create`) and the "Quick Practice Environments" section of the student home page show, for each environment:

- Name and description.
- Each machine's name, role and type.
- The combined CPU and memory, and how many hours it can be used.

The launch confirmation page adds each machine's specification, the environment total that counts against the quota, and the version number.

The screens do not show the source template ID, PVE node selection, clone mode, storage names or template operations.

### 9.2 Launch steps

1. The user selects an environment on the Quick Practice page or the student home page.
2. On **Launch Quick Practice** (`/quick-template/{id}`), the user reviews the fixed machine set, the usage hours and the rules.
3. The user clicks **Launch `<n>` Machines**. The only parameter submitted is the environment ID.
4. The system locks the user's launch flow so simultaneous clicks cannot bypass the limits.
5. The system re-validates the published version and usage, the prerequisites of published services (Gateway, domain, public port pool), the per-user limits, the environment cap if one is set, the quota and the cluster capacity.
6. In one transaction, the system reserves every IP of the set and creates one session plus one auto-approved machine request per machine, then queues provisioning.
7. After all machines are built, the system applies the firewall topology and the published services.
8. The environment appears in **My Resources** as one group and is usable once the session is `ready`.

### 9.3 Rules enforced by the system

- Students cannot change the machine count, type, CPU, RAM, disk, source image or deadline.
- One user may have at most one active quick practice environment.
- One user may launch at most three within a rolling 24-hour window. Launches that failed and were reclaimed do not count; ending early does not give a launch back.
- The duration comes from the setting `practice_session_hours` (default 3 hours, 1–24, under Resource Scheduling).
- Quick practice machines cannot be extended.
- Limits are counted per session; three machines still count as one practice.

### 9.4 How it is used

- My Resources shows one group row; expanding it shows each machine's name, role, status, IP and console entry, and the row shows the expiry time.
- Users may start, shut down and open their own machines.
- The group menu offers **Start all**, **Shut down all** and **End Practice**.
- Per-machine operations do not change the session's fixed deadline or the whole-set reclamation.

### 9.5 Readiness criteria

The session becomes `ready` only when:

- Every machine has been created and has a VMID.
- The topology firewall rules have been applied.

Published URLs or ports that are not finished yet are retried in the background every two minutes and do not hold back `ready`. No connectivity test or in-guest health check is run (section 17).

## 10. SOP-E: Formal Class Environments

The screens are described in [Create Class](/manual/class-setup) and [Class Workspace](/manual/class-workspace).

### 10.1 Teacher sets up the class

1. Create the class and its fixed weekly schedule.
2. Add the students.
3. Select a published environment whose usage is `course` or `both`.
4. Optionally set the weekly tasks.
5. The system calculates "number of students × one full machine set per student" and checks CPU, RAM, disk, IP, storage and node capacity.
6. The teacher submits the class for provisioning, which reserves that capacity.
7. An administrator approves the class in Class Review.

### 10.2 System provisioning

1. An independent environment group is created for each student.
2. Each student receives machines, specifications and topology from the same published version.
3. Each student's dedicated IPs and isolation rules are applied.
4. All topology edges in the environment version are applied.
5. When everything succeeds, the class becomes `active`.
6. On partial failure the class becomes `partial_failed` and is not shown as completed. The failed machines can be retried; a class in which no machine was created can go back to editing.

### 10.3 Student use

- Students only see their own class and the machines under their own name.
- Students enter from **Courses** or **My Resources**, never through a template list.
- Students cannot switch environment versions, add machines or modify fixed specifications.
- Whether machines can be started, stopped or extended outside class hours is decided by class and platform policy.

### 10.4 Class end

- At the class's end date and time the system archives the class automatically; the teacher can also choose **Archive and reclaim** earlier.
- Archiving stops future scheduling, cancels unfinished provisioning, releases the reserved capacity and queues every machine of the class for deletion. There is no retention period; to keep the machines longer, extend the class.
- Remind students to export their work before the end date.
- Failed deletions are retried every 15 minutes; the class records when all its resources are gone.

## 11. Session States and Failure Handling

### 11.1 State machine

```text
creating → ready → stopping → reclaiming → reclaimed
    │        ↑                    ↑
    └→ partial_failed ────────────┘
```

| State | Meaning | Shown to the user as |
| --- | --- | --- |
| `creating` | IPs reserved, machines being built, topology not yet applied | Provisioning |
| `ready` | All machines built and the topology applied | Running |
| `partial_failed` | A machine failed, or the topology could not be applied yet | Needs Attention |
| `stopping` | Expired; the machines are shut down and the 30-minute grace period is running | Preparing to Reclaim |
| `reclaiming` | Deleting the whole set | Reclaiming |
| `reclaimed` | Machines, IPs and requests released | Removed from the list |

A failed machine sends the session to reclamation on the next scheduler run. A topology failure is retried and the session returns to `ready` when it succeeds; if it still fails 15 minutes after the machines were ready, the whole set is reclaimed. **End Practice** moves a session straight to `reclaiming`.

### 11.2 All-or-nothing principle

- The session, all its machine requests and all IP reservations are created in the same DB transaction.
- Provisioning is deduplicated per machine request; retries do not create duplicate VMs.
- Quota and cluster capacity are checked and every IP is reserved before launch; if anything is short, nothing is created.
- A quick practice session is not repaired machine by machine: a failed machine reclaims the whole set. Classes can retry their failed machines instead.
- If the topology cannot be applied within 15 minutes, the whole set is reclaimed and all reservations are released.
- Students are never left to deal with an incomplete multi-machine environment themselves.

### 11.3 User messages

Error messages must tell students what to do next, but must not expose PVE credentials, internal addresses, stack traces or sensitive errors. For example:

- Insufficient capacity: "There is not enough capacity available right now. No machines were created; please try again later."
- Creation failed: "The environment could not be completed and the system has reclaimed its resources; this attempt does not count towards your usage limit."

## 12. Expiry, End and Resource Reclamation

### 12.1 Quick practice

Standard flow:

```text
expires_at reached
→ machines shut down
→ 30-minute grace period (stopping)
→ every VM / LXC deleted through the deletion queue
→ IPs and machine requests released
→ Session = reclaimed
```

- The expiry time is shown on the group row in My Resources.
- **End Practice** first warns that unsaved content will be lost, then reclaims the set immediately.
- Reclamation always covers the whole set; the group offers no single-machine delete.
- Reclamation goes through the idempotent deletion queue. If a machine cannot be queued, the error is kept on the session and the next scheduler run tries again.

### 12.2 Formal classes

- Machines follow the class schedule: they start before each lesson and shut down after it.
- A lesson ending is not deletion; students' work stays on their machines between lessons.
- The whole set is reclaimed only when the class is archived (section 10.4).

## 13. Publishing and Version Governance Rules

1. A published version cannot be overwritten.
2. Classes and sessions stay on the version they were created with.
3. Changing the usage only affects new launches and new classes; running sessions continue until they expire. An administrator can end any session early (`POST /quick-practice/sessions/{id}/end`).
4. There is no emergency switch for an environment. In a security incident an administrator changes its usage, ends the running sessions, and records the reason, the operator and the scope of impact outside the system.
5. Before a template is deleted, all referencing environments and versions are listed; any reference blocks the deletion.
6. An environment used by any class or practice session, including ended ones, cannot be deleted.
7. Teachers manage only the environments they created; administrators can govern all environments.

## 14. Security, Privacy and Auditing

### 14.1 Required permission restrictions

- Students cannot open `/templates`; they are sent to the request form instead.
- `POST /api/v1/templates/{template_id}/clone` requires the template-management permission (teachers and administrators).
- `/course-template-management` and its editor routes are guarded on the teacher side; the backend still validates `InstructorUser`.
- Students may only obtain their own sessions, requests, resources and connection credentials.
- Teachers may only view student environments in the classes they teach, and every view and control operation must be audited.

### 14.2 Audit events

Record at least:

- Template creation, status changes and deletion.
- Environment draft creation, update, publish, usage change and deletion.
- Publisher, publish time, version and configuration hash.
- Student session launch, ready, failure, end, expiry and reclamation.
- Administrator force-stop, repair and deletion.
- Teachers or administrators opening a student's terminal / console.

Most of these are not recorded yet (section 17).

## 15. UI and API Mapping

### 15.1 Teacher side

| Task | UI route | API |
| --- | --- | --- |
| Teaching environment list | `/course-template-management` | `GET /api/v1/course-environments` |
| Create an environment (first autosave) | `/course-template-management/new` | `POST /api/v1/course-environments/drafts` |
| Autosave a draft | `/course-template-management/{id}` | `PUT /api/v1/course-environments/{id}/draft` |
| Publish and lock | Same as above | `POST /api/v1/course-environments/{id}/publish` |
| Change name, usage and description | Same as above | `PATCH /api/v1/course-environments/{id}/basics` |
| Upload or remove documents | Same as above | `POST /api/v1/course-environments/{id}/files`, `DELETE /api/v1/course-environments/{id}/files/{file_id}` |
| Delete an unused environment | `/course-template-management` | `DELETE /api/v1/course-environments/{id}` |
| Fetch environments available to classes | `/class-setup` | `GET /api/v1/course-environments/published` |

### 15.2 Student side

| Task | UI route | API |
| --- | --- | --- |
| Browse quick practice environments | `/quick-create`, `/dashboard` | `GET /api/v1/quick-practice/templates` |
| View environment details | `/quick-template/{id}` | `GET /api/v1/quick-practice/templates/{id}` |
| Launch the full environment | Same as above | `POST /api/v1/quick-practice/templates/{id}/launch` |
| View own sessions | `/my-resources` | `GET /api/v1/quick-practice/sessions/my` |
| End a practice early | `/my-resources` | `POST /api/v1/quick-practice/sessions/{id}/end` |

## 16. Acceptance Criteria

### 16.1 Permission acceptance

- [ ] The student sidebar does not show "Templates" or "Teaching Environments".
- [ ] A student typing the management routes above directly is redirected.
- [ ] A student calling the single-machine clone API receives `403`.
- [ ] Students cannot see other students' environments and sessions.
- [ ] Teachers cannot edit other teachers' environments.

### 16.2 Teacher build and publish acceptance

- [ ] An environment draft with one to three machines can be created.
- [ ] Invalid templates, nodes, topologies and ports are rejected by the backend.
- [ ] The machine configuration cannot be modified after publishing.
- [ ] Changing the usage after publishing does not affect existing classes and sessions.
- [ ] `usage_scope` correctly separates the formal class list from the quick practice list.
- [ ] Publishing asks for confirmation that the version will be locked.

### 16.3 Quick practice acceptance

- [ ] A student can launch the full environment by submitting only the environment ID.
- [ ] Tampering with the frontend cannot change specifications, count, source template or deadline.
- [ ] Multiple machines count as a single session.
- [ ] Duplicate clicks do not create duplicate sessions or machines.
- [ ] No machines are created when quota or capacity is insufficient.
- [ ] `ready` is shown only after all machines and the topology are complete.
- [ ] When any machine fails, the whole set is reclaimed automatically.
- [ ] After expiry the whole set is deleted and all resources are released.

### 16.4 Formal class acceptance

- [ ] Only published versions whose usage is `course` / `both` can be selected.
- [ ] Each student receives an independent, identically specified full machine set.
- [ ] Each student's topology edges are actually applied successfully.
- [ ] Students can operate only their own class environment.
- [ ] A partially failed class enters `partial_failed` and can become `active` only after its failed machines are retried successfully.
- [ ] Archiving the class, manually or at its end, reclaims every machine; failed deletions are retried.

## 17. Current Gaps

This section records where the code falls short of this SOP; it does not lower the requirements above.

| Gap | Current state |
| --- | --- |
| No application-level health check | `ready` means every machine was created and the firewall topology was applied; no connectivity test or in-guest health check runs. Before going live on production PVE, real-machine E2E must confirm that every VM / LXC combination passes the service connectivity the environment requires once the rules are applied |
| No publish review | Publishing takes effect immediately. High-risk capabilities should go through administrator review; they include at least GPUs, privileged LXCs, public ports, high resource specifications and high-privilege startup scripts |
| No private trial run | Publishing for quick practice makes an environment visible to every signed-in user at once |
| Published machines are frozen | There is no new version; changing machines means building a separate environment |
| Concurrency cap not in the editor | `max_concurrent_sessions` is enforced only when set through the API. There is no queueing or estimated wait when an environment or the cluster is full |
| Incomplete audit trail | Of the events in section 14.2, only the creation of each quick practice machine (`quick_practice_machine_create`) and general machine actions (start, stop, delete…) are recorded |
| No failure alerts | Administrators see every quick practice group in Resource Management, but creation, topology and reclamation failures raise no alert |
| No emergency disable | See rule 4 in section 13 |

## 18. Go-Live Approval Form

For every environment prepared for student use, the publisher should complete the following approval form:

| Check item | Result | Notes |
| --- | --- | --- |
| Template security and licensing check | Pass / Fail |  |
| All one to three machines can be created normally | Pass / Fail |  |
| Total resources and concurrency capacity are acceptable | Pass / Fail |  |
| IP and network topology validated | Pass / Fail |  |
| Required inter-machine connectivity tested | Pass / Fail |  |
| Students cannot modify fixed specifications | Pass / Fail |  |
| Usage is correct | Pass / Fail |  |
| Expiry and whole-set reclamation tested | Pass / Fail |  |
| Reclamation after a failed launch tested | Pass / Fail |  |
| Publisher / reviewer | Name and time |  |

## 19. Document Maintenance

- Whenever product roles, publishing rules, the session lifecycle or the permission model change, this SOP must be updated in step.
- When API or UI names change but the flow does not, update the mapping table in section 15.
- Before every production release, run the regression acceptance in section 16.
- Once a gap in section 17 is closed, remove it from that section; it must not be silently treated as a permanent exception.
