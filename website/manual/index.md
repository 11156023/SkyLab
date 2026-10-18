# User manual

One page per feature of the web interface, written for the person who uses it. Every page has the same shape: what the page is for, who can open it, step-by-step tasks, the fields and options, and the limits you will run into. Labels are quoted exactly as the interface shows them.

Pick your role:

## Students

Everything you need from the first login to a running machine.

- [Signing in](/manual/login) — password, Google or LDAP login, two-step verification, the service check, the first-login tour
- [Home page](/manual/home) — what the home page shows for each role
- [Account settings](/manual/account) — profile, password, two-step verification, appearance, About
- [Requesting a machine](/manual/my-requests) — the request form, time windows, quotas, request states
- [My resources](/manual/my-resources) — the list of your machines and their power actions
- [Machine details and console](/manual/resource-detail) — console, snapshots, backups, firewall, resize, extend, passwords
- [Firewall](/manual/firewall) — rules for your machines and what you can see of others
- [Quick practice](/manual/quick-practice) — launching a teacher-published environment without a request
- [Courses](/manual/courses) — weekly content, lab machines, progress
- [AI API credentials](/manual/ai-api) — requesting and using a `ccai_*` key

## Teachers

Everything a student can do, plus:

- [Templates](/manual/templates) — turning a prepared machine into a platform template
- [Creating a class](/manual/class-setup) — roster, timetable, environment, batch provisioning
- [Class list](/manual/class-management)
- [Class workspace](/manual/class-workspace) — students, machines, monitoring, broadcast, weekly content
- [AI-assisted checking](/manual/ai-judge) — checklists, generated scripts, running checks on student machines
- [Multi-machine environments](/manual/course-templates) — building and publishing topologies; see also the [SOP](/guides/multi-machine-environment-sop)
- [Course content](/manual/course-cms) — learning paths and weekly material

## Administrators

Everything above, plus the platform itself:

- Platform: [Users and registration](/manual/admin-users), [PVE connections](/manual/pve-connections), [IP management](/manual/ip-management), [Gateway VM](/manual/gateway), [Domains](/manual/domain), [LDAP / Active Directory](/manual/ldap)
- Policies: [Scheduler](/manual/scheduler), [Governance](/manual/governance) (field reference: [Governance configuration](/reference/governance-config))
- Capacity: [Storage](/manual/storage), [GPU management](/manual/gpu-management)
- Review and operations: [Resource management](/manual/resource-management), [Request review](/manual/request-review), [Batch review](/manual/batch-review), [Monitoring and alerts](/manual/monitoring), [Background jobs](/manual/jobs), [Audit log](/manual/audit)
- AI: [AI API review](/manual/ai-api-review), [AI API keys](/manual/ai-api-keys), [AI monitoring](/manual/ai-monitoring)

Installing and operating the server is covered by [Getting started](/getting-started) and the Operations section ([Deployment](/guides/deployment), [Upgrading](/operations/upgrade), [Backup](/operations/backup), [Monitoring](/guides/monitoring)).
