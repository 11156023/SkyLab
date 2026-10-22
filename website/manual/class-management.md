# Class Management

Class Management lists the classes you teach as tickets, from planning through review and provisioning to ready for class. From here you create a class, resume one that is still being set up, open a class's workspace, and archive classes in bulk. Sidebar: Teaching → Class Management. Path: `/class-management`.

## Who can use it

- **Teachers** see the classes they own.
- **Administrators** see every class.
- Students do not see this page; opening the address sends them back to the home page.

## Before you start

- To set up a class you need registered student accounts and a published teaching environment (see [Teaching Environments](/manual/course-templates)). You can also create the environment during class setup.

## Step by step

### Create a class

1. Click **Create class**.
2. The five-step [Class setup](/manual/class-setup) opens: **Class & Schedule**, **Student List**, **Teaching Environment**, **Weekly Tasks** and **Confirm Creation**. The address `/class-management/new` leads to the same place.
3. Saved steps are kept as a draft; the class appears here in **Planning** and you can come back to it later.

![Create a class (numbers match the steps above)](/manual-shots/en/class-management/s01.webp)

### Filter and search

1. Use the status tabs; the number on each is the count:
   - **All**
   - **Planning** — still being set up.
   - **Building** — pending review or provisioning.
   - **Needs attention** — appears only when some class has machines that failed to build.
   - **Ready to teach** — the class is active.
2. Type a class name in **Search classes**.
3. Ended classes are hidden. Tick **Show ended (N)** to list them; the option appears only when there are ended classes.

![Filter and search (numbers match the steps above)](/manual-shots/en/class-management/s02.webp)

### Read a class ticket

Each ticket shows:

1. The status: **Planning**, **Pending review**, **Provisioning**, **Needs attention**, **Ready for class** or **Ended**, and the term.
2. The class name. A class that needs attention also shows **Some machines failed to build**.
3. **Class time** (weekday and time) with **Boots N min early**, and **Term dates**.
4. On the stub, a barcode and a count:
   - For a class in planning, **Class setup** counts finished setup items out of 3: students added, an environment chosen, at least one week with a topic.
   - For other classes, **Machine provisioning** counts machines already built out of the total.

![Read a class ticket (numbers match the steps above)](/manual-shots/en/class-management/s03.webp)

### Open a class

1. Click a ticket.
2. A class in **Planning** opens class setup at the next unfinished step.
3. Any other class opens its [Class workspace](/manual/class-workspace), where you manage students, machines, weekly content, monitoring and checks.

![Open a class (numbers match the steps above)](/manual-shots/en/class-management/s04.webp)

### Archive and reclaim classes in bulk

1. Tick the box in the top-right corner of one or more tickets. Ended classes cannot be selected. Shift-click selects a range.
2. The toolbar turns into a batch bar showing **N selected**, with a select-all box and **Archive and reclaim**.
3. Click **Archive and reclaim**. The confirmation lists the classes (the first five names, then "and N more") and the total number of machines.
4. Confirm. Progress shows as **Archiving… N of M**.
5. When it finishes:
   - **Archived N class(es); reclaim has been requested** — done.
   - If some machines could not be sent for reclaim, open the class and choose **Retry reclamation**.
   - If some classes failed to archive, they stay selected so you can try again.

While tickets are selected, clicking a ticket toggles its selection instead of opening it. Clear the selection to go back to normal.

![Archive and reclaim classes in bulk (numbers match the steps above)](/manual-shots/en/class-management/s05.webp)

## Fields and options

| Field | Meaning | Notes |
| --- | --- | --- |
| Status | Where the class is in its life cycle | **Planning**, **Pending review**, **Provisioning**, **Needs attention**, **Ready for class**, **Ended** |
| Class time | Weekly class day and time | With the boot lead time |
| Term dates | First and last date of the class | |
| Class setup | Setup items done out of 3 | Planning classes only |
| Machine provisioning | Machines built out of the total | All other classes |

## Limits and behaviour to know

- Archiving stops upcoming schedules, cancels unfinished provisioning and reclaims every machine in the class. It cannot be undone; class and course records are kept.
- A class that has been submitted for provisioning no longer opens the setup wizard; its settings are locked and it opens in the workspace.
- When you return from another page with a success message (for example after pausing class setup), it is shown once as a notification.

## Related

- [Class setup](/manual/class-setup) — create a class step by step
- [Class workspace](/manual/class-workspace) — manage one class
- [Teaching Environments](/manual/course-templates) — the machine sets a class can use
- [Class Review](/manual/batch-review) — where administrators approve class provisioning
