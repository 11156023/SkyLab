# Username policy

The login user that SkyLab writes into a new Linux machine (the cloud-init `ciuser`) is validated against one policy file, `backend/config/username_policy.yaml`. The backend loads it at start-up; the request form fetches the same rules through `GET /api/v1/policies/username` and checks a name live with `POST /api/v1/policies/username/check`, so the form and the server never disagree. An invalid file stops the backend instead of loosening the rules.

Windows templates are not covered: Proxmox ignores `ciuser` for Windows guests and the account is fixed by `cloudbase-init.conf` inside the template.

## Rules

- The name must match the pattern: lower-case letters, digits, `_` and `-`, starting with a letter, 1 to 32 characters.
- Names reserved by the platform (`skylab`, `ansible`, `provision`) are refused.
- Names and prefixes used by system accounts on common distributions (`root`, `daemon`, `www-data`, `systemd-…`, …) are refused; the full list is below.
- Default users of cloud images (`ubuntu`, `debian`, `ec2-user`, … in `default_user_warn`) are allowed but produce a warning.
- Lists are de-duplicated on load.

A name that breaks a rule is rejected by the form before the request is submitted, and again by the backend before provisioning; warnings never block.

## The policy file

<<< ../../backend/config/username_policy.yaml{yaml}

## Related

- [Requesting a machine](/manual/my-requests) — where the login user is chosen
- [Templates](/manual/templates) — why a template without cloud-init keeps its own account
