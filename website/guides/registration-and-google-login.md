# Registration Settings and Google Login

## The two registration switches

After logging in, an administrator can change two independent switches on the "User Management" page:

- **Allow self-registration** controls the sign-up entry on the login page, new email registrations, and automatic account creation on a first Google login. While it is on, any email domain is accepted; turning it off keeps existing accounts working, and administrators can still add users by hand.
- **Require administrator approval after registration** keeps its value even while registration is closed. With registration open and approval on, every new account that finishes email verification, or logs in with Google for the first time, goes to "Pending registrations" and can log in only after an administrator approves it; the administrator can also reject it. Reopening registration reuses the approval setting in force at that time.

Even with manual approval off, a self-service email registration still has to follow the link in the verification email and set a password. A first Google login also counts as a new registration. Changing the approval switch only affects accounts verified or created afterwards; accounts already in the pending list still need an administrator's decision.

## Before opening registration

- The backend must have a working SMTP configuration before the registration switch can be turned on.
- Configure Cloudflare Turnstile in production first.
- Run the database migrations after the first deployment or an update.
- Until the settings have been saved in the admin UI once, "Allow self-registration" starts from `ENABLE_SIGNUP` in `.env`; after saving, the admin UI setting is authoritative.

## Setting up Google login

1. In the Google Cloud Console, under **Google Auth Platform → Clients**, create or pick a Web application OAuth client and set the site's Authorized JavaScript origins.
2. Put the OAuth **Client ID** into `GOOGLE_CLIENT_ID` in `.env`, rebuild the frontend and restart the backend. It is not an API key, and the client secret never goes into the frontend.

The backend verifies the Google ID token's audience and verified email, but does not filter accounts by school or education domain. Which Google accounts may authorise is decided under **Google Auth Platform → Audience**:

- **Internal:** only accounts in the school's Google Workspace / Cloud Identity organisation can log in. The Google Cloud project must belong to that organisation; personal projects cannot use internal mode.
- **External:** any Google account, including personal Gmail, can authorise. To restrict who gets in, combine it with "Require administrator approval after registration" above.
