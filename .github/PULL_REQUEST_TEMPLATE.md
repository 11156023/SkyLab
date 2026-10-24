## What and why

<!-- What does this PR change, and why? Link the related issue, e.g. "Closes #123". -->

## How it was tested

<!-- Commands you ran, pages you checked, screenshots for UI changes. -->

## Checklist

- [ ] Every commit is signed off (`git commit -s`) and carries no AI co-author trailer
- [ ] Tests added or updated for changed behaviour (`backend/tests/`, Vitest next to the service file)
- [ ] Model changes come with an Alembic migration
- [ ] New UI strings are in all three locales (zh-TW, en, ja)
- [ ] Docs under `website/` updated, English and `zh-TW/` together
- [ ] Added or removed direct dependencies reflected in `THIRD_PARTY_NOTICES.md`; desktop client changes belong in [SkyLab-Connect](https://github.com/SkyLab-tw/SkyLab-Connect)
- [ ] No `.env`, credentials, real student data or internal addresses in the diff
