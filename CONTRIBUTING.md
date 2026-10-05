# Contributing to SkyLab

> **English** | [繁體中文](./CONTRIBUTING.zh-TW.md)

Thanks for taking the time to contribute. This page covers the house rules; the development setup lives in [`docs/development.md`](docs/development.md).

## Before you start

- **Bug reports and small fixes** (typos, lint warnings, a reproducible bug with a test) can go straight to a pull request.
- **Larger changes** (new features, schema changes, anything touching provisioning, networking or authentication) should start as a GitHub issue describing the problem and the intended approach, so we can agree on the design before you invest the time.
- The platform is deployed in real classrooms. Changes that alter existing behaviour (default values, API responses, gateway rules) need a migration path and a note in the relevant document under `docs/`.

## Development workflow

1. Fork the repository and create a branch from `main`.
2. Set up the stack following [`docs/development.md`](docs/development.md).
3. Make your change. Keep the architecture rules in mind:
   - Backend follows **Routes → Services → Infrastructure**. Routes are thin controllers, business logic lives in `services/`, and external systems (Proxmox, SSH, Redis, LLM) are only called from `infrastructure/`.
   - DB tables live in `backend/app/models/`, API schemas in `backend/app/schemas/`. Do not mix them.
   - Every change to a model needs an Alembic migration (`alembic revision --autogenerate`).
   - The frontend has no generated API client. Add or update the matching function in `frontend/src/services/*.js` together with its Vitest mock test.
   - User-facing strings go through react-i18next (`frontend/src/locales/`): add the key to all three locales (zh-TW, en, ja).
4. Run the checks before opening the PR:

   ```bash
   # backend
   cd backend
   uv run ruff check .
   uv run ruff format --check .
   bash ./scripts/test.sh

   # frontend
   cd frontend
   bun run test
   bun run build
   ```

   Pre-commit hooks are managed with prek: `uv run prek install -f` once, then they run on every commit.

## Pull requests

- Keep each PR focused on one change. Separate refactors from behaviour changes.
- Describe what changed and why, and reference the related issue.
- Add or update tests when behaviour changes. Backend tests are under `backend/tests/`, frontend service tests sit next to the service file.
- Update the documentation under `docs/` when you change configuration, deployment steps or an operator-facing workflow. English documents are the primary version; keep the `*.zh-TW.md` counterpart in sync when one exists.
- CI must pass (backend tests, frontend tests, migration check, CodeQL).

## Licensing of contributions

SkyLab is licensed under the AGPL-3.0, and the maintainers also offer it under commercial terms. So that both remain possible, every contribution is accepted under the following terms:

- You certify the [Developer Certificate of Origin](https://developercertificate.org/) for every commit by signing it off (`git commit -s`, which adds a `Signed-off-by: Your Name <email>` trailer). Pull requests with unsigned commits will be asked to be amended.
- By submitting a contribution you license it to the project under the AGPL-3.0 **and** grant the SkyLab maintainers a perpetual, worldwide, royalty-free right to distribute it as part of SkyLab under other license terms, including commercial licenses. You keep the copyright to your work.
- Only submit code you have the right to contribute. Code copied from elsewhere must carry a compatible license and be attributed in `NOTICE`.
- When you add or upgrade a direct dependency, add it to [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) (and to `desktop-client/THIRD_PARTY_NOTICES.txt` for the desktop client). Avoid licenses that are incompatible with the AGPL-3.0.

## Commit messages

- Use the conventional prefix already used in the history: `feat(scope): …`, `fix(scope): …`, `docs: …`, `refactor(scope): …`, `chore: …`.
- Write the subject in the imperative and keep it under about 72 characters. Chinese or English are both fine.
- Sign off every commit (`git commit -s`), see above.
- **Do not add AI co-author trailers or generated-by markers** (`Co-Authored-By: …`, "Generated with …" and similar). Commits carrying them will be asked to be rewritten.
- Never commit `.env` or any file containing credentials. Use `.env.example` as the template.

## Automated and AI-assisted contributions

You are welcome to use whatever tools help you work, including AI assistants. The contribution still has to reflect your own understanding: you should be able to explain every change in the PR and have run it locally. PRs that look auto-generated and have not been reviewed by their author will be closed.

## Questions

Open a GitHub issue or discussion in this repository.
