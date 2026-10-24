# Background Jobs

The Background Tasks page shows the progress and result of the work SkyLab runs in the background: VM request provisioning, spec changes, deletions, machine templates, resets, batch provisioning, backups and restores. Sidebar: Monitoring & Logs → Background Jobs. Path: `/jobs`.

## Who can use it

Everyone who is signed in. Administrators see every task on the site; teachers and students see only their own. VMIDs in the task details are shown to administrators and teachers only.

The **Background Jobs** button with the bell icon in the sidebar is a quick view of the same information and is also where desktop notifications are turned on (see below).

## Step by step

### Follow your tasks

1. Open the page. The table lists the tasks of the last 30 days and refreshes on its own.
2. Each row shows **Task** (the task name with its ID underneath), **Type**, **Status**, **Progress**, **Created At**, **Updated At** and **Applicant**.
3. The three cards at the top, **In Progress**, **Completed** and **Failed**, are counted from the list as it is currently filtered (each card says "Current filter"), not from the whole site.

![Follow your tasks (numbers match the steps above)](/manual-shots/en/jobs/s01.webp)

### Filter the list

1. Use the type bar to show one kind of task: **All Types**, **VM Request**, **Spec Change**, **Deletion**, **Template**, **Reset**, **Batch provisioning**, **Backup** or **Restore backup**.
2. Use the status bar to show one state: **All Statuses**, **In Progress**, **Pending**, **Running**, **Completed**, **Failed**, **Blocked** or **Cancelled**. **In Progress** covers Pending, Running and Blocked.
3. If nothing matches, the page says "No tasks match the filter"; click **Clear filters** to go back to all tasks.

![Filter the list (numbers match the steps above)](/manual-shots/en/jobs/s02.webp)

### Open the details of a task

1. Click any row. A dialog opens with the task name as its title and the task ID underneath.
2. The status line shows the status, the progress percentage and "Initiated by" with the applicant's email.
3. **Created**, **Updated** and **Completed** show the timing.
4. **Message** shows the latest message from the task, **Details** lists the task's own fields (for example node, name, requested CPU and memory, expiry date), **Error** shows the error output of a failed task and **Output** shows its output.
5. While the task is still pending, running or blocked, the dialog refreshes every 3 seconds. Close it with the close button.

![Open the details of a task (numbers match the steps above)](/manual-shots/en/jobs/s03.webp)

### Use the quick view in the sidebar

1. Click **Background Jobs** (bell icon) near the bottom of the sidebar. The count badge adds up running tasks and unread reminders.
2. The **Jobs** tab lists running tasks; click one to open the same details dialog.
3. The **Reminders** tab lists machine expirations, review results and upcoming class tasks. Click a reminder to open the related page, or **Mark all as read**.
4. **View all jobs and history** opens this page.

![Use the quick view in the sidebar (numbers match the steps above)](/manual-shots/en/jobs/s04.webp)

### Turn on desktop notifications

1. In the sidebar quick view, click the settings icon (**Notification settings**).
2. Tick **Desktop notifications** and allow notifications when the browser asks. When this tab is in the background, finished jobs and new reminders then appear as system notifications.
3. Read the line under the switch. "Background push is on" means you are notified even after the tab is closed. Other messages explain why only open tabs are notified (the browser does not support it, the server has not enabled background push, or the subscription failed; turn the switch off and on to retry).
4. Administrators also see **Only notify my jobs**, which stops notifications about other users' tasks.

![Turn on desktop notifications (numbers match the steps above)](/manual-shots/en/jobs/s05.webp)

## Fields and options

| Field | Meaning | Notes |
| --- | --- | --- |
| Type | What kind of work the task does | VM Request, Spec Change, Deletion, Template, Reset, Batch provisioning, Backup, Restore backup |
| Status | **Pending**, **Running**, **Completed**, **Failed**, **Blocked** or **Cancelled** | **Blocked** means the task waits for a person, for example an approved spec change the applicant still has to apply |
| Progress | Percentage reported by the task | — |
| Applicant | Email of the person who started the task | — |
| Desktop notifications | System notifications while the tab is in the background or closed | Needs the site to be opened over https or localhost |
| Only notify my jobs | Limits notifications to your own tasks | Administrators only |

## Limits and behaviour to know

- Only the last 30 days are listed, and at most 200 tasks at a time.
- The page has no retry or cancel button. To run a failed task again, start the operation again from its own page (for example the request, the resource or the class).
- Background push (notifications after the tab is closed) only covers your own tasks, and only works when the site is opened over https or on localhost.
- If the browser has blocked notifications for the site, the switch is disabled and the message tells you to allow them again in the site settings.

## Related

- [My Requests](/manual/my-requests) — VM requests whose provisioning appears here
- [Resource detail](/manual/resource-detail) — resets, backups, restores and spec changes
- [Class Workspace](/manual/class-workspace) — batch provisioning of class machines
- [Account](/manual/account) — personal settings
