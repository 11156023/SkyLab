# Key Management

See every AI API key issued on the platform: who holds it, what it was requested for, when it was last used and whether it still works, and delete keys that should no longer be used. Sidebar: AI Services → Key Management. Path: `/ai-api-keys`.

## Who can use it

Administrators only. Other roles do not see the page in the sidebar.

## Before you start

- Keys can only be issued by approving a request on [AI API review](/manual/ai-api-review). You cannot create a key on this page.
- Administrators cannot read a user's full key here or anywhere else; only the key name and its first characters (the prefix) are shown.

## Step by step

### Switch between active and disabled keys

1. The **Active** tab is selected when you open the page.
2. Choose **Disabled** to see keys that expired, were revoked or were deleted, or **All** to ignore the status.
3. The number on each tab follows the current search and filters.

![Switch between active and disabled keys (numbers match the steps above)](/manual-shots/en/ai-api-keys/s01.webp)

### Filter by role and creation time

1. Click **Filter**.
2. Under **User role**, tick **Student**, **Teacher** and/or **Administrator**.
3. Under **Created**, choose **Any time**, **Last 7 days** or **Last 30 days**.
4. Changes apply immediately. The button shows how many conditions are in effect, for example **Filter 2**.
5. Click **Clear filters** to remove them. Click outside the panel or press Esc to close it.

![Filter by role and creation time (numbers match the steps above)](/manual-shots/en/ai-api-keys/s02.webp)

### Find a specific key

1. Type in the search box: a key name, user name, email or key prefix.
2. The list updates as you type. Click the × in the box to clear the search.

If someone reports that a key leaked, search for the prefix they give you to find exactly which key it is.

![Find a specific key (numbers match the steps above)](/manual-shots/en/ai-api-keys/s03.webp)

### Check how a key is used

Read the **Activity** column. It shows the time of the key's most recent call, or **Never used**. Keys that have been unused for a long time are good candidates for deletion. For call-by-call details, use [AI Usage Monitoring](/manual/ai-monitoring).

![Check how a key is used](/manual-shots/en/ai-api-keys/s04.webp)

### Delete a key

1. Find the key in the **Active** tab and click **Delete** in its **Actions** column.
2. Read the confirmation and click **Confirm delete**.

New calls with the key fail immediately and the key disappears from the user's AI API page. Calls already in progress finish, and the management and usage records are kept. This cannot be undone; if the user still needs access, they submit a new request.

![Delete a key (numbers match the steps above)](/manual-shots/en/ai-api-keys/s05.webp)

### Page through a long list

The list shows 50 keys per page, newest first. When there are more, use **Previous** and **Next** at the bottom; the text next to them shows the current page and the total.

![Page through a long list](/manual-shots/en/ai-api-keys/s06.webp)

## Fields and options

| Column | Meaning | Notes |
| --- | --- | --- |
| Key | Key name and the holder's email | |
| Owner | Full name of the holder | |
| Purpose | The purpose written in the original request | |
| Activity | Time of the most recent call | **Never used** if the key was never called |
| Status | **Active**, **Disabled** or **Deleted** | Disabled covers expired and revoked keys |
| Created | Date the key was issued | |
| Actions | **Delete** | Only on active keys; other rows show "—" |

## Limits and behaviour to know

- **Delete** only appears on active keys.
- A key that a user regenerated shows up as a disabled key next to its active replacement.
- The list refreshes automatically while the page is open.
- Deleting a key is recorded in the audit log as an action taken on behalf of the user.
- The models behind the AI API and the upstream LiteLLM gateway are not configured on this page; see the [LiteLLM guide](/guides/litellm).

## Related

- [AI API review](/manual/ai-api-review) — approve requests to issue keys
- [AI API](/manual/ai-api) — what users see and do with their keys
- [AI Usage Monitoring](/manual/ai-monitoring) — calls, tokens and errors per key, model and user
- [LiteLLM](/guides/litellm) — the gateway that serves the models
