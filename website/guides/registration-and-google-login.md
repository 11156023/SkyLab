# Registration Settings and Google Login

## The three registration switches

After logging in, an administrator can change three independent switches on the "User Management" page:

- **Allow self-registration** controls the sign-up entry on the login page and new email registrations. While it is on, any email domain is accepted; turning it off keeps existing accounts working, and administrators can still add users by hand.
- **Allow account creation on first Google login** controls whether a Google address that has no SkyLab account yet gets a student account the first time it logs in with Google. It is separate from email registration and needs neither SMTP nor Turnstile, because Google has already verified the address. While it is off, Google login keeps working for existing accounts, and a first-time Google login is told that the account is not registered. It only matters when Google login is configured (see below).
- **Require administrator approval after registration** keeps its value even while both switches above are off. With approval on, every new account that finishes email verification, or is created by a first Google login, goes to "Pending registrations" and can log in only after an administrator approves it; the administrator can also reject it. Reopening registration reuses the approval setting in force at that time.

Even with manual approval off, a self-service email registration still has to follow the link in the verification email and set a password. A first Google login with account creation on also counts as a new registration. Changing the approval switch only affects accounts verified or created afterwards; accounts already in the pending list still need an administrator's decision.

## Before opening registration

- The backend must have a working SMTP configuration before the self-registration switch can be turned on. The Google switch has no such requirement.
- Configure Cloudflare Turnstile in production first.
- Run the database migrations after the first deployment or an update. The migration that adds the Google switch copies the current value of "Allow self-registration" into it, so upgrading does not change who can log in.
- Until the settings have been saved in the admin UI once, "Allow self-registration" starts from `ENABLE_SIGNUP` in `.env`, and the Google switch starts from the same value; after saving, the admin UI settings are authoritative.

## Setting up Google login

1. In the Google Cloud Console, under **Google Auth Platform → Clients**, create or pick a Web application OAuth client and set the site's Authorized JavaScript origins.
2. Put the OAuth **Client ID** into `GOOGLE_CLIENT_ID` in `.env`, rebuild the frontend and restart the backend. It is not an API key, and the client secret never goes into the frontend.

The backend verifies the Google ID token's audience and verified email, but does not filter accounts by school or education domain. Which Google accounts may authorise is decided under **Google Auth Platform → Audience**:

- **Internal:** only accounts in the school's Google Workspace / Cloud Identity organisation can log in. The Google Cloud project must belong to that organisation; personal projects cannot use internal mode.
- **External:** any Google account, including personal Gmail, can authorise. To restrict who gets in, combine it with "Require administrator approval after registration" above, or turn off "Allow account creation on first Google login" and create the accounts by hand.
