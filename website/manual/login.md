# Signing in

How to get into SkyLab: signing in with a password, a campus (LDAP) account or Google, the two-factor step, resetting a forgotten password, registering a new account, and the screens that can appear right after you sign in (the service check, forced two-factor setup and the first-time setup tour). The sign-in page is not in the sidebar. Path: `/login`.

## Who can use it

Everyone. The page itself needs no account; what you see on it depends on how the platform is configured:

- **Email** sign-in (username and password) is always shown.
- A **Campus Account** tab appears next to **Email** only when an administrator has enabled LDAP.
- A Google sign-in button appears below an **or** divider only when the deployment has a Google client ID configured.
- **Don't have an account? Sign up now** appears only when self-registration is enabled.
- An "I am human" check (Cloudflare Turnstile) appears above the **Log in** button only when an administrator has configured it.

After sign-in, the service check screen looks different for administrators (real service names and error details) and for students and teachers (a short checklist in plain words).

## Before you start

- You need an account: one an administrator created for you, an LDAP campus account (when LDAP is enabled), a Google account (already registered in SkyLab, or created at the first Google sign-in when Google sign-up is enabled), or one you registered yourself.
- If your account requires two-factor authentication, keep the phone with your authenticator app at hand.

## Step by step

### Sign in with email and password

1. Open `/login`. If LDAP is enabled, make sure the **Email** tab is selected.
2. In **Username**, enter your username (your email address).
3. In **Password**, enter your password. The eye icon in the field shows or hides what you typed.
4. If an "I am human" box is shown, complete it. **Log in** stays disabled until it is done.
5. Click **Log in**.
6. If your account has two-factor authentication, continue with [Enter the two-factor code](#enter-the-two-factor-code). Otherwise you go straight to the service check.

![Sign in with email and password (numbers match the steps above)](/manual-shots/en/login/s01.webp)

### Sign in with a campus account (LDAP)

1. Select the **Campus Account** tab.
2. Enter your student ID or staff account in **Campus Account** and your campus password in **Password**.
3. Complete the "I am human" box if it is shown.
4. Click **Log in**.

![Sign in with a campus account (LDAP) (numbers match the steps above)](/manual-shots/en/login/s02.webp)

### Sign in with Google

1. Click the Google sign-in button under **or**.
2. Choose your Google account in the pop-up window.
3. While SkyLab checks the account, **Signing in with Google…** is shown under the button.

If the Google address has no SkyLab account yet, the first sign-in creates one when an administrator has enabled Google sign-up; otherwise the page says the account is not registered and asks you to create an account first. If the account has not been activated (for example it is still waiting for administrator approval), the page tells you to wait for approval.

![Sign in with Google (numbers match the steps above)](/manual-shots/en/login/s03.webp)

### Enter the two-factor code

When your account has two-factor authentication enabled, a **Two-factor authentication** step follows the first sign-in step (password, campus account or Google).

1. Open your authenticator app (Google Authenticator or another one) and find the SkyLab entry.
2. Type the 6-digit code into **Verification code**. Spaces are ignored.
3. Click **Verify and log in**. The button stays disabled until all 6 digits are entered.
4. To start over, click **Back to log in**.

If you have lost your phone, ask an administrator to reset two-factor authentication for your account.

![Enter the two-factor code (numbers match the steps above)](/manual-shots/en/login/s04.webp)

### Reset a forgotten password

1. On the sign-in form, click **Forgot your password?**
2. Enter your **Email** and click **Send Reset Link**.
3. Open the email and click the link. It brings you back to SkyLab on the **Reset Password** form.
4. Enter a **New password** that meets every rule in the checklist under the field, and enter it again in **Confirm new password**.
5. Click **Update Password**, then **Go to Log In** and sign in with the new password.

The reset link also works if you are currently signed in. If the link has expired, the page tells you to request a new one.

![Reset a forgotten password (numbers match the steps above)](/manual-shots/en/login/s05.webp)

### Register a new account

Only available when **Sign up now** is shown under the sign-in form.

1. Click **Sign up now**.
2. Enter your **Full Name** and **Email**, complete the "I am human" box if shown, and click **Create Account**.
3. Open the verification email and click the link within 24 hours.
4. On **Verify your email**, set a **Password** that meets every rule and repeat it in **Confirm Password**.
5. Click **Verify and complete registration**.
6. Depending on the platform's registration policy, you can either sign in right away, or you see a message that an administrator must approve your registration first.

If the verification link is invalid or expired, register again with the same email to get a new link.

![Register a new account (numbers match the steps above)](/manual-shots/en/login/s06.webp)

### Confirm a new email address

When you change your email in [Account Settings](/manual/account), SkyLab sends a link to the new address.

1. Open the email at the new address and click the link.
2. The **Verify email** page shows whether the change worked.
3. Click **Continue** to return to Account Settings (or to the sign-in page if you are signed out).

![Confirm a new email address (numbers match the steps above)](/manual-shots/en/login/s07.webp)

### Authorize the SkyLab Connect desktop app

The first time you open the SkyLab Connect desktop app, it asks for your school's SkyLab address, the same one you use in the browser (for example `school.skylab-tw.com`); you can change it later in the app's settings. When you click sign-in in the app, it opens the SkyLab sign-in page in your browser.

1. If you are not signed in yet, the page says **Log in to connect SkyLab Connect**. Sign in as usual.
2. On **Authorize desktop app sign-in**, check the account shown under **Account**.
3. Click **Authorize this device** only if you started the sign-in from SkyLab Connect yourself. If someone else sent you the link, click **Cancel**.
4. When **Connection Authorized** appears, close the page and go back to the desktop app.

If authorization fails (for example the link expired), go back to SkyLab Connect and sign in again to get a new link.

![Authorize the SkyLab Connect desktop app (numbers match the steps above)](/manual-shots/en/login/s08.webp)

### Get through the service check after signing in

Every time you sign in, SkyLab checks the services it depends on before showing the system: the database, Redis, the background worker, Proxmox VE, the gateway and the AI gateway.

- **Students and teachers** see a checklist ("Opening your adventure log", "Preparing your gear" and so on), each with a one-line hint of the features it covers. If everything passes, you enter SkyLab automatically.
- If something fails, the failing items get a red cross. You can click **Check again**, **Sign out**, or **Continue anyway**. Features marked with a cross may not work for now; please let an administrator know.
- **Administrators** see the real service names, their status (**Healthy**, **Failing**, **Not configured**) and details such as latency. On failure they can **Check again**, **Open Resource Monitoring** or **Skip and continue**.

![Get through the service check after signing in](/manual-shots/en/login/s09.webp)

### Set up two-factor authentication when it is required

If an administrator requires two-factor authentication for your account and you have not set it up, you only see the **Set up two-factor authentication** screen after signing in.

1. Install an authenticator app on your phone (Google Authenticator, Microsoft Authenticator, Authy or similar).
2. Scan the QR code shown on the page. If you cannot scan it, type the key shown under the QR code into the app manually (the copy button copies it).
3. Enter the 6-digit code shown in the app.
4. Click **Enable and continue**. You are taken into SkyLab.

To leave without setting it up, click **Log out** at the top left.

![Set up two-factor authentication when it is required (numbers match the steps above)](/manual-shots/en/login/s10.webp)

### Complete the first-time setup tour

The first time you sign in, a short setup tour appears before the main pages.

1. **Welcome to SkyLab**: choose the interface language and click **Continue**.
2. **Profile**: optionally drop or choose an image for your **Avatar**, and enter your **Name** (up to 30 characters). Click **Next**.
3. **Appearance**: pick a color theme and the interface style and color mode (**Light**, **Dark** or **Follow system**). Click **Next**.
4. **Two-factor auth**: click **Set up two-factor authentication** to link an authenticator app now, or **Skip for now**.
5. **Done**: review the summary of your profile, language, appearance and two-factor status, then click **Start using SkyLab**.

**Set up later** in the top right corner closes the tour at any step. Either way the tour does not appear again; everything in it can be changed later in [Account Settings](/manual/account).

![Complete the first-time setup tour (numbers match the steps above)](/manual-shots/en/login/s11.webp)

## Fields and options

| Field | Meaning | Notes |
| --- | --- | --- |
| Username | Your SkyLab username, which is your email address | Email tab |
| Campus Account | Student ID or staff account | Only when LDAP is enabled |
| Password | Your SkyLab or campus password | Not checked against the complexity rules at sign-in |
| Verification code | 6-digit code from your authenticator app | Two-factor step |
| Email (forgot password) | The address registered in SkyLab | The reset link is sent there |
| New password / Password | The password you are setting | Must meet the rules below |
| Full Name, Email (registration) | Your name and the address to verify | The password is set after verifying the email |

Password rules for setting or resetting a SkyLab password (the checklist under the field turns each rule green as you meet it):

| Rule | Requirement |
| --- | --- |
| Length | 8 to 128 characters |
| Uppercase letter | At least one A–Z |
| Lowercase letter | At least one a–z |
| Number | At least one 0–9 |
| Symbol | At least one character that is not a letter, digit or space |

## Limits and behaviour to know

- Sign-in attempts are rate-limited per account (10 per minute by default) and per network address. After too many attempts you have to wait a minute before trying again.
- In the two-factor step, after three wrong codes the step becomes invalid and you are sent back to sign in with your password again; five wrong codes within 15 minutes lock two-factor sign-in for that account for 15 minutes. The two-factor step itself expires after 5 minutes; if it does, you are returned to the sign-in form.
- If your phone's clock is wrong, codes will be rejected. Check the time on your phone first.
- Password reset emails are limited to three per address per minute. The reset link is valid for a limited time (48 hours by default).
- The registration verification link is valid for 24 hours. The link for confirming a new email address is valid for 1 hour.
- Old passwords that do not meet the current rules still work for signing in; the rules apply the next time you change the password.
- The service check never blocks you: students and teachers can continue with a warning, and administrators can skip it.

## Related

- [Home](/manual/home) — the page you land on after signing in
- [Account Settings](/manual/account) — change your password, email, two-factor authentication and appearance later
- [User Management](/manual/admin-users) — where administrators require two-factor authentication, reset it and approve registrations
- [LDAP](/manual/ldap) — enabling campus account sign-in
- [Registration and Google login](/guides/registration-and-google-login) — how self-registration and Google sign-in are configured
- [SkyLab Connect architecture](/guides/wireguard-desktop-architecture) — what the desktop app does after you authorize it
