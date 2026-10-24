# Courses

The student side of a class: the list of courses you are enrolled in, one course's weekly tasks, progress and class machines, and the page for a single week with check feedback, machines and materials. Sidebar: Teaching → Courses. Path: `/courses`, `/courses/<pathId>` and `/courses/<pathId>/weeks/<weekId>`.

## Who can use it

- **Students** use these pages for every class they belong to.
- **Administrators** can also open them.
- **Teachers** do not see **Courses** in the sidebar and are sent back to the home page if they open the address; they manage classes in [Class Management](/manual/class-management).

## Before you start

- There is no sign-up button. You join a course when a teacher adds your account (your email) to the class roster.
- A course appears in your list only once its learning path is published. This happens automatically the first time the class becomes ready for class; a teacher can also publish or unpublish it in [Course Management](/manual/course-cms).
- Class machines appear only after the administrator has approved the class and the machines have been built.

## Step by step

### Find your course

1. In the sidebar, open **Teaching → Courses**. The **My Courses** page lists one ticket per course.
2. Each ticket shows:
   - A status label: **In class now**, **Later today**, **Open for practice**, **Done for today** or **Ready to start** (no session scheduled).
   - The course title and the teacher.
   - **Date**, **Time** and **Room** of the next session, when the teacher has set them.
   - On the stub, a barcode and a count such as `4 / 8` with **questions done** (or **All done**). If the teacher has not published any practice questions you see **No practice questions yet**.
3. Click a ticket to open the course.

If you see **No courses yet**, your teacher has not published a course for a class you belong to.

![Find your course (numbers match the steps above)](/manual-shots/en/courses/s01.webp)

### Read the course page

The course page has two columns.

1. **All Tasks Due Today** (left) lists every week of the course and every task published up to today, oldest first. Each row shows the week number, title, date, the number of check items and, when present, a **Teacher replied** badge.
2. **This week** (right) shows the current week (the last week whose session date is today or earlier; before the term starts, the first week):
   - The class time, teacher and location.
   - **N / M checks passed**, or **No checks this week**.
   - The teacher's reply, if any.
   - **Open this week** opens the week page.
3. **Your Class Machines** (right, below) lists the machines of this course.
4. **Back to My Courses** returns to the course list.

![Read the course page (numbers match the steps above)](/manual-shots/en/courses/s02.webp)

### Mark a week as done

1. In **All Tasks Due Today**, tick the checkbox at the right end of the week row (**Mark week done**).
2. A message confirms **Weekly task marked complete**. Tick it again to undo it.

Ticking only tells your teacher you have finished; it does not start an AI check and does not change the **checks passed** count. The checkbox appears only on weeks that have tasks to complete.

![Mark a week as done (numbers match the steps above)](/manual-shots/en/courses/s03.webp)

### Read an AI task's requirements

Some tasks are not attached to a week. Their rows expand in place instead of opening a week page.

1. Click the row to expand it.
2. Read the **AI Summary of Task Highlights** and the numbered check items.
3. Each item has a badge that tells you how it will be checked: **AI can suggest checks**, **AI-assisted + teacher confirmation** or **Teacher Confirmation Required**.

![Read an AI task's requirements (numbers match the steps above)](/manual-shots/en/courses/s04.webp)

### Enter a class machine

1. In **Your Class Machines**, click the machine card. The label on the right tells you what will happen:
   - **Enter Machine** — the machine is running.
   - **Start and Enter** — the machine is off; it is started first.
   - **Starting…** — the machine is booting.
   - **Environment being configured** — the machine has not been built yet (**Not configured yet**).
2. If the machine was off, SkyLab starts it and waits for it to finish booting. When it is running, a terminal opens for a container (LXC) and a console opens for a virtual machine.
3. If it is still booting after about 90 seconds you see **The machine is still starting up. Please try again shortly.** Click the card again later.
4. Click the **info** button next to a card to open the machine's full page in [My Resources](/manual/resource-detail).

![Enter a class machine (numbers match the steps above)](/manual-shots/en/courses/s05.webp)

### Open a week

1. Click a week row in **All Tasks Due Today**, or **Open this week** in **This week**.
2. The header shows the course name, the week title, the week number and the date. **Back to course** returns to the course page.

![Open a week (numbers match the steps above)](/manual-shots/en/courses/s06.webp)

### Read check feedback

The **Check feedback** panel shows your latest result for each checkpoint of the week.

1. At the top, **Teacher's reply** shows the teacher's note for this week (one reply per week; a new reply replaces the old one).
2. Each checkpoint shows its title, description and, for multi-machine environments, **Checked on "`<machine>`"**.
3. The status on the right is one of **No feedback yet**, **Waiting**, **Checking**, **Passed**, **Partially complete**, **Teacher review needed**, **Not passed**, **Cancelled** or **Environment problem, check again later**.
4. Below the status you see the score (for example `3 / 3 points`) and the comment.

Opening the page only shows results; it does not run a check again.

![Read check feedback (numbers match the steps above)](/manual-shots/en/courses/s07.webp)

### Use the machines and materials of a week

1. **Class machines** lists all machines available in the course, with role and VMID.
   - **Open website** opens a service the teacher published at a URL.
   - A `host:port` entry is an external port forwarded into the machine (hover it to see the inner port). If only the port is known you see **External port N (ask your teacher for the entry host)**.
   - **URL not published** means the machine has no public URL or port.
   - **Machine and console** opens the machine's page in My Resources.
2. **Week materials** lists the files for the week. Click a file to open it in a new tab.

![Use the machines and materials of a week (numbers match the steps above)](/manual-shots/en/courses/s08.webp)

## Fields and options

| Field | Meaning | Notes |
| --- | --- | --- |
| Ticket status | Where the course is today | **In class now**, **Later today**, **Open for practice**, **Done for today**, **Ready to start** |
| Questions done | Practice questions you completed out of the total | Shown on the ticket stub |
| Check items | Number of items in the week's tasks | Shown on each week row |
| Checks passed | Checkpoints of the current week that passed | Moves only after a check runs |
| Machine label | What clicking the machine will do | **Enter Machine**, **Start and Enter**, **Starting…**, **Environment being configured** |

## Limits and behaviour to know

- Only tasks published up to today are listed; future tasks stay hidden until their publish date.
- If some information cannot be loaded you see **Latest information is temporarily unavailable**; you can still go to Courses or My Resources.
- If a week has not been published or was changed you see **Week not found**.
- You cannot start or stop a class machine by hand from the course page; clicking the card starts it if needed. Scheduled boot and shutdown around class time are set by the teacher.
- During the page's guided tour, a dashed course marked **Safe demo** is added. It only demonstrates the layout; nothing is saved and no machine is started.

## Related

- [Home](/manual/home) — today's classes and reminders
- [Resource detail](/manual/resource-detail) — the full page of a class machine
- [Quick Practice](/manual/quick-practice) — practice environments outside a class
- [Class workspace](/manual/class-workspace) — where teachers publish weekly content and run checks
