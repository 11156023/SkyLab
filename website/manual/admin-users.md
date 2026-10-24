# User Management

User Management is where administrators manage every account on the platform: create and edit users, assign roles, enable or disable accounts, organize accounts into groups, review self-registrations and require two-factor authentication per account. Sidebar: Admin Settings → User Management. Path: `/admin`.

## Who can use it

Administrators only. The page is not available to students or teachers, and the sidebar entry only appears inside Admin Settings for administrators.

There is no separate "superuser" switch: an account with the **Administrator** role is the platform administrator.

## Before you start

- To let people sign up on their own, decide first whether new accounts need your approval (see [Registration settings and Google login](/guides/registration-and-google-login)).
- Accounts that sign in through LDAP are managed by the campus directory; their passwords cannot be set here. See [LDAP](/manual/ldap).

## Step by step

### Turn self-registration on or off

1. In the **Registration settings** panel, tick or untick **Allow self-registration**.
2. The change is saved immediately (a "Registration settings saved" notice appears); there is no separate save button.

When self-registration is on, any email address may register from the login page. Turning it off only stops email sign-ups; the approval and Google settings are kept, and existing accounts can still log in.

![Turn self-registration on or off (numbers match the steps above)](/manual-shots/en/admin-users/s01.webp)

### Require approval for new accounts

1. In **Registration settings**, tick **Require administrator approval**.
2. The change is saved immediately.

While sign-up or Google account creation is open and approval is on, every new account (including school accounts) must be approved before it can be used. Accounts already waiting in the pending list still need a decision even if you turn approval off.

![Require approval for new accounts (numbers match the steps above)](/manual-shots/en/admin-users/s02.webp)

### Allow or block account creation on first Google login

1. In **Registration settings**, tick or untick **Allow account creation on first Google login**.
2. The change is saved immediately.

This switch is separate from self-registration and needs no mail server. When it is on, a Google address that has no account yet gets a student account the first time it signs in with Google, and waits for approval if approval is required. When it is off, only existing accounts can sign in with Google. It only has an effect when the deployment has Google login configured.

![Allow or block account creation on first Google login (numbers match the steps above)](/manual-shots/en/admin-users/s03.webp)

### Approve or reject a pending registration

1. Look at the **Pending registrations** panel. The number in the title is how many accounts are waiting.
2. For each account, click **Approve** to let it sign in, or **Reject** to leave it unusable.
3. When nothing is pending, the panel shows "No registrations await review".

![Approve or reject a pending registration (numbers match the steps above)](/manual-shots/en/admin-users/s04.webp)

### Add a user

1. Click **Add User** in the page header (or in the empty state when there are no users yet).
2. Fill in **Email**, optionally **Name**, and **Password**.
3. Choose a **Role**: **Student**, **Teacher** or **Administrator**.
4. Leave **Enable account** ticked so the user can sign in right away.
5. Optionally tick **Require two-factor authentication**.
6. Click **Save**.

An account created here needs no email verification and no approval. The password must meet the platform's complexity rules; the rule list under the field turns red for any rule that is not met.

![Add a user (numbers match the steps above)](/manual-shots/en/admin-users/s05.webp)

### Edit a user

1. Click the pencil icon (**Edit**) on the user's row.
2. Change **Email**, **Name**, **Role** or the two check boxes as needed.
3. To change the password, type it into **New Password**; leave it blank to keep the current one.
4. Click **Save**.

For an LDAP account the password field shows "Managed by LDAP" and is locked; change that password in the directory instead.

![Edit a user (numbers match the steps above)](/manual-shots/en/admin-users/s06.webp)

### Disable or enable an account

1. Click **Edit** on the row.
2. Untick **Enable account** to disable it, or tick it to enable it again.
3. Click **Save**.

A disabled account cannot sign in until it is enabled again. **Enable account** is locked for accounts that have not finished registration (email not verified yet, awaiting approval, or rejected); use **Approve** in the pending list for accounts waiting for review.

![Disable or enable an account (numbers match the steps above)](/manual-shots/en/admin-users/s07.webp)

### Enable, disable or delete several accounts at once

1. Tick the check box on the left of each row you want. Shift-click selects a range. The search bar turns into a selection bar showing how many are selected.
2. Click **Enable** or **Disable** in the selection bar; **Delete** sits on the far right.
3. For **Disable** and **Delete**, confirm in the dialog, which lists the names of the affected accounts (up to five, then "and N more").
4. A progress counter shows while the accounts are processed.

Enable and Disable only change accounts that are not already in the target state; the others are skipped (hover the button to see how many apply). Delete follows the same rules as deleting a single user: the account cannot be restored, its email cannot register again, and accounts that still own provisioned resources are refused. If some operations fail, those accounts stay selected so you can try again. Selection only covers the current page, and you cannot select your own account.

![Enable, disable or delete several accounts at once (numbers match the steps above)](/manual-shots/en/admin-users/s08.webp)

### Create, rename or delete groups

A group is a named set of accounts, such as one school class (資管三甲). Teachers can import a group's students straight into a class roster when they set up a class (see [Create a Class](/manual/class-setup)); only administrators can create groups and change their members.

1. Click **Groups** in the page header.
2. Type a name under **New group** and click **Add**. Group names must be unique (case does not matter).
3. To rename a group, click the pencil icon on its row, edit the name and press Enter (Esc cancels).
4. To delete a group, click the trash icon and confirm. Only the group is removed: the accounts stay, and class rosters already imported from it do not change.

When at least one group exists, a group menu appears next to the search box; choose a group to list only its members. Each row in **Groups** shows how many members and how many students the group has. A group can hold any role, but only its student accounts are added when a teacher imports it.

![Create, rename or delete groups (numbers match the steps above)](/manual-shots/en/admin-users/s09.webp)

### Add group members from a CSV file

1. In **Groups**, click the upload icon on the group's row and choose a `.csv` file.
2. The first column of each row is read as an email or a student ID, using the same rules as class rosters: a bare student ID gets the **Student email domain** from Registration settings appended (when no domain is set, the whole file is rejected and every row must be a full email address), a header row of `email`, `學號` or `帳號` is skipped, and the file must be UTF-8 or Big5 and at most 1 MB.
3. A notice reports how many were added and how many accounts were not found.

![Add group members from a CSV file (numbers match the steps above)](/manual-shots/en/admin-users/s10.webp)

### Add or remove selected accounts from a group

1. Tick the accounts in the user list.
2. Click **Add to group** or **Remove from group** in the selection bar.
3. Choose the group and click **Add** or **Remove**.

![Add or remove selected accounts from a group (numbers match the steps above)](/manual-shots/en/admin-users/s11.webp)

### Require two-factor authentication for an account

1. Click **Edit** on the row.
2. Tick **Require two-factor authentication**.
3. Click **Save**.

If the user has not set up two-factor authentication yet, they must finish setting it up right after their next sign-in. Afterwards they cannot turn it off themselves.

![Require two-factor authentication for an account (numbers match the steps above)](/manual-shots/en/admin-users/s12.webp)

### Reset a user's two-factor authentication

Use this when a user has lost the phone with their authenticator app.

1. Click **Edit** on the row. The **Two-factor authentication** line shows whether it is enabled.
2. Click **Reset two-factor authentication** (only shown when it is enabled).
3. Confirm in the dialog.

All of the user's sessions are signed out immediately. Their next sign-in only needs the password, and they can set up two-factor authentication again themselves (or are asked to, if it is required for the account).

![Reset a user's two-factor authentication (numbers match the steps above)](/manual-shots/en/admin-users/s13.webp)

### Delete a user

1. Click the trash icon (**Delete**) on the row.
2. Read the confirmation and click **Delete**.

After deletion the account cannot sign in or use any service, and its AI API keys are revoked. Usage, request and audit records are kept. The account cannot be restored, and its email cannot be registered again.

![Delete a user (numbers match the steps above)](/manual-shots/en/admin-users/s14.webp)

### Find a user

1. Type in the search box ("Search by name, email, or role").
2. The list filters as you type. To look within one group, choose it in the group menu next to the search box. Click **Clear search** in the empty state to reset both.

The list shows 50 users per page; use **Previous** and **Next** below the list to move between pages.

![Find a user (numbers match the steps above)](/manual-shots/en/admin-users/s15.webp)

## Fields and options

| Field | Meaning | Notes |
| --- | --- | --- |
| Email | Sign-in address of the account | Required, up to 255 characters, must be unique |
| Name | Display name | Optional, up to 255 characters |
| Password / New Password | Local sign-in password | Required when adding; blank keeps the current one when editing; up to 128 characters; must meet the complexity rules; locked for LDAP accounts |
| Role | Student, Teacher or Administrator | You cannot change your own role |
| Enable account | Whether the account may sign in | You cannot disable your own account; locked until registration is complete |
| Require two-factor authentication | Forces the user to set up and keep an authenticator app | Shown with an explanation when ticked |
| Allow self-registration | Lets anyone register from the login page | Saved immediately |
| Require administrator approval | New registrations wait in Pending registrations | Saved immediately |
| Allow account creation on first Google login | Lets a Google address without an account create a student account at its first sign-in | Saved immediately; independent of self-registration |
| Student email domain | Domain added after a bare student ID in class roster and group CSV files, for example `school.edu.tw` | Saved with its **Save** button; a leading `@` and upper case are dropped; leave it empty to require full email addresses in every roster |
| Group name | Name of a group in **Groups** | Required, up to 120 characters, unique regardless of case |

Each row shows the name and email, an **LDAP** tag for directory-managed accounts, a **2FA** tag when two-factor authentication is set up, the role badge, the status (**Active**, **Inactive**, **Email not verified**, **Awaiting approval** or **Registration rejected**) and the creation date.

The summary cards above the list show **Total Users**, **Active**, **Teachers** and **Administrators** across all accounts; they ignore the search.

## Limits and behaviour to know

- You cannot delete, disable or change the role of your own account, and your own row has no selection box.
- The last active administrator cannot be disabled or given another role, so the platform always keeps at least one administrator.
- Deleting a user fails while the user still owns provisioned machines, or while a teacher still owns teaching classes (disable the account or hand the class over first).
- An account's email cannot be changed while its registration is not yet approved.
- LDAP accounts get their role from directory groups at every LDAP sign-in when group mapping is configured, so a role you change here can be overwritten. See [LDAP](/manual/ldap).
- Administrators are not limited by quotas; per-user quotas for other users are set on [Resource Policies](/manual/governance).
- Deleted accounts no longer count as members of any group.
- The list refreshes itself in the background while the page is open.

## Related

- [Resource Policies](/manual/governance) — global and per-user quotas
- [LDAP](/manual/ldap) — directory sign-in and role mapping
- [Audit Log](/manual/audit) — every account change is recorded
- [Account Settings](/manual/account) — what users see when they set up two-factor authentication
- [Registration settings and Google login](/guides/registration-and-google-login) — how the three registration switches interact with Google sign-in
