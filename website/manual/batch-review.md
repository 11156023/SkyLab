# Class Review

Class Review is where an administrator approves or rejects the batch provisioning requests that teachers submit to create machines for a whole class, and then follows how the machines are being created. Sidebar: Review → Class Review. Path: `/batch-review`.

## Who can use it

Administrators only. Teachers submit the batches from their class (see [Class Setup](/manual/class-setup) and [Class Workspace](/manual/class-workspace)); they cannot open this page.

## Before you start

- A teacher must have submitted a class for provisioning. Until then the **Pending** tab is empty (**No batch requests**).
- Approving starts creating machines immediately, so make sure the PVE connections and the templates the class uses are ready.

## Step by step

### Pick a list

1. The page opens on **Pending**: batches waiting for your decision.
2. **Approved** holds batches you approved, including those that are **Waiting**, **Provisioning**, **Completed** or **Failed**.
3. **Rejected** holds batches you rejected.
4. **All** shows everything, including **Cancelled** batches, which appear only here.
5. The number on each tab is the count for that tab. The list refreshes automatically in the background.

![Pick a list (numbers match the steps above)](/manual-shots/en/batch-review/s01.webp)

### Find a batch

1. Type in the search box (**Search batch, applicant or course**). It matches the batch name, applicant name and email, course name, resource type and status.
2. In the list, a graduation-cap icon marks a class batch and a server icon marks a single batch that is not tied to a class.
3. Each row shows the number of VMs and the applicant, with the status badge and submission time on the right. Rows are sorted newest first.
4. Machine nodes from the same class with the same status are merged into one row, titled "class name · N nodes". Approving or rejecting that row handles the whole class at once.

![Find a batch (numbers match the steps above)](/manual-shots/en/batch-review/s02.webp)

### Look at a batch

1. Click a batch in the list. The panel on the right shows:
   - **Request Type**: **Class Batch** or **Single Batch**.
   - **Course**, **Resource Type** (VM / LXC) and **VM Count**.
   - **Spec / Summary** and **OS / Template**. When the class has several machine nodes with different specs or templates, each one is listed.
   - **Submitted At** and **Applicant Email**.
   - **Provisioning Progress**: a bar with created machines in one colour and failed ones in another, and "created / total". If any failed, "N failed to provision" is shown below.
   - **Batch Members (N)**: each student with the VMID of their machine, or the status of their task if no machine exists yet.
2. The panel only shows the batch you clicked; it does not jump to another one when the list refreshes.

![Look at a batch (numbers match the steps above)](/manual-shots/en/batch-review/s03.webp)

### Preview a recurring schedule

1. If the batch has a recurring schedule, the panel shows **Recurring Schedule**, for example "Weekly on Mon, Wed at 09:00", with the raw rule below it.
2. Click **View upcoming windows** to load the next power-on windows (start ～ end). Click it again to fold the list.
3. **No scheduled windows** means the rule has no further occurrences.

![Preview a recurring schedule (numbers match the steps above)](/manual-shots/en/batch-review/s04.webp)

### Approve a batch

1. Click a pending batch and check the details.
2. Optionally write the reason in **Review Notes**.
3. Click **Approve**. A confirmation dialog (**Approve Batch Request**) names what you are approving, for example "the 3 machine node(s) in "Class A"".
4. Confirm. The system immediately starts creating the machines in the background. The batch moves to the **Approved** tab, where its status goes from **Waiting** to **Provisioning** to **Completed** (or **Failed**), and **Provisioning Progress** fills up.

![Approve a batch (numbers match the steps above)](/manual-shots/en/batch-review/s05.webp)

### Reject a batch

1. Click a pending batch.
2. Write the reason in **Review Notes**.
3. Click **Reject** and confirm in **Reject Batch Request**.
4. No machines are created. For a class batch where no machine has been created yet, the class returns to planning so the teacher can edit and submit it again.

![Reject a batch (numbers match the steps above)](/manual-shots/en/batch-review/s06.webp)

### Follow failures

1. Open the **Approved** tab and click the batch.
2. **Provisioning Progress** shows how many machines failed, and **Batch Members** shows which students have no VMID and the status of their task.
3. Partial failures are retried by the teacher with **Retry failed items** in the class workspace. This page has no retry button; it shows the progress once the retry runs.
4. The provisioning run also appears as a job in [Background Jobs](/manual/jobs), where you can open its details.

![Follow failures (numbers match the steps above)](/manual-shots/en/batch-review/s07.webp)

### Check a past decision

1. Click a batch in **Approved**, **Rejected** or **All**.
2. **Review Notes** shows the note and review time (or **No notes entered**), followed by the reviewer's email.

![Check a past decision (numbers match the steps above)](/manual-shots/en/batch-review/s08.webp)

## Fields and options

| Field | Meaning | Notes |
| --- | --- | --- |
| Pending Review | Waiting for an administrator | Only status with Approve / Reject |
| Approved | Approved; creation is being queued | Listed under Approved |
| Waiting | Queued for creation | Listed under Approved |
| Provisioning | Machines are being created | Listed under Approved |
| Completed | All machines finished | Listed under Approved |
| Failed | Provisioning failed | Listed under Approved; see the failed count |
| Rejected | Rejected by an administrator | |
| Cancelled | Withdrawn | Only under All |
| Review Notes | Your note to the teacher | Optional |

## Limits and behaviour to know

- Approval cannot be undone from this page; the confirmation dialog is the last check.
- A batch that someone else has already reviewed can no longer be approved or rejected; the action fails with an error message.
- The page loads the most recent 200 batches plus up to 200 pending ones, so older pending batches are never hidden behind newer ones.
- If you reject a class batch after some of its machines already exist, those machines stay and the class is marked as partly failed for the teacher to retry or clean up.

## Related

- [Class Setup](/manual/class-setup) — how teachers submit a class for provisioning
- [Class Workspace](/manual/class-workspace) — where teachers follow and retry provisioning
- [Request Review](/manual/request-review) — single-user machine requests
- [Background Jobs](/manual/jobs) — details of the provisioning run
- [Resource Management](/manual/resource-management) — the created machines, grouped by environment
