# Security Policy

> **English** | [繁體中文](./SECURITY.zh-TW.md)

SkyLab manages hypervisors, student machines, gateway firewalls and AI API credentials, so we take vulnerability reports seriously.

## Supported versions

Only the `main` branch is supported. There are no maintained release lines; deploy from `main` and keep up to date.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Use GitHub's private vulnerability reporting instead: open the **Security** tab of this repository and choose **Report a vulnerability**. This creates a private advisory that only the maintainers can see.

Include as much of the following as you can:

- the affected component (backend route, frontend page, gateway script, desktop client, vLLM / LiteLLM stack)
- steps to reproduce, or a proof of concept
- the impact you believe it has (for example privilege escalation between roles, access to another user's VM, credential disclosure)
- the commit or version you tested against

We aim to acknowledge reports within a week and to keep you informed while we work on a fix. Please give us reasonable time to ship a fix before disclosing publicly.

## Scope notes

- Secrets must never be committed. `SECRET_KEY` both signs tokens and derives the key that encrypts stored credentials; rotate it with `backend/scripts/rotate_secret_key.py`, never by editing `.env` alone.
- Proxmox, LDAP and gateway credentials are stored encrypted in the database and are configured through the UI, not through `.env`.
- Hardening guidance for operators (trusted proxy, rootless Docker source IPs, certificate handling) is in [`docs/deployment.md`](docs/deployment.md).

Thank you for helping keep SkyLab and its users safe.
