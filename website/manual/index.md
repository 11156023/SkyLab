# User manual

One page per feature of the web interface, written for the person who uses it. Every page has the same shape: what the page is for, who can open it, step-by-step tasks, the fields and options, and the limits you will run into. Labels are quoted exactly as the interface shows them.

Pick your role:

## Students

Everything you need from the first login to a running machine.

- [Signing in](/manual/login) — password, Google or LDAP login, two-factor authentication, the service check, the first-login tour
- [Home](/manual/home) — what the home page shows for each role
- [Account Settings](/manual/account) — profile, password, two-factor authentication, appearance, About
- [My Requests](/manual/my-requests) — the request form, time windows, quotas, request states
- [My Resources](/manual/my-resources) — the list of your machines, their consoles and power actions
- [Resource Detail](/manual/resource-detail) — login details, monitoring, spec changes, snapshots, backups, firewall, extension, passwords, sharing
- [Firewall](/manual/firewall) — rules for your machines and what you can see of others
- [Quick Practice](/manual/quick-practice) — launching a teacher-published environment without a request
- [Courses](/manual/courses) — weekly content, lab machines, progress
- [AI API](/manual/ai-api) — requesting and using a `ccai_*` key

## Teachers

Everything a student can do, plus:

- [Templates](/manual/templates) — turning a prepared machine into a platform template
- [Create Class](/manual/class-setup) — roster, timetable, environment, batch provisioning
- [Class Management](/manual/class-management) — your class list, drafts and bulk archiving
- [Class workspace](/manual/class-workspace) — students, machines, monitoring, broadcast, weekly content
- [AI Check](/manual/ai-judge) — checklists, generated scripts, running checks on student machines
- [Teaching Environments](/manual/course-templates) — building and publishing multi-machine topologies; see also the [SOP](/guides/multi-machine-environment-sop)
- [Course Management](/manual/course-cms) — learning paths, tasks and questions

## Administrators

Everything above, plus the platform itself:

- Platform: [User Management](/manual/admin-users), [PVE Management](/manual/pve-connections), [IP Management](/manual/ip-management), [Gateway VM](/manual/gateway), [Domain Management](/manual/domain), [LDAP](/manual/ldap)
- Policies: [Resource Scheduling](/manual/scheduler), [Resource Policies](/manual/governance) (field reference: [Governance configuration](/reference/governance-config))
- Capacity: [Storage](/manual/storage), [GPU Management](/manual/gpu-management)
- Review and operations: [Resource Management](/manual/resource-management), [Request Review](/manual/request-review), [Class Review](/manual/batch-review), [Resource Monitoring](/manual/monitoring), [Background Jobs](/manual/jobs), [Audit Log](/manual/audit)
- AI: [AI API review](/manual/ai-api-review), [Key Management](/manual/ai-api-keys), [AI Usage Monitoring](/manual/ai-monitoring)

Installing and operating the server is covered by [Getting started](/getting-started) and the Operations section ([Deployment](/guides/deployment), [Upgrading](/operations/upgrade), [Backup](/operations/backup), [Monitoring](/guides/monitoring)).
