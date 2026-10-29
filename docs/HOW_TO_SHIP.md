# How to ship a change (Test first, then live)

Two copies of the site run on Railway in the same project:

| | Test | Live (production) |
|---|---|---|
| Railway environment | `test` | `production` |
| Git branch that deploys | `test` | `main` |
| Database and `/data` volume | its own | its own |
| Anthropic key | a separate key with a low monthly limit | the real one |
| Banner | red **TEST ENVIRONMENT** bar | none |
| Email | only to addresses in `PUBBANK_TEST_EMAIL_ALLOW` | everyone |

The Test site shares nothing with the live one: no users, questions or logs are copied across.

## Every change, in order

1. **Build on the `test` branch.** Claude works there and never on `main`.
2. **Push `test`.** Railway rebuilds the Test site by itself (a few minutes).
3. **Click through it.** Sign in with a Test account (emailed link, sent only to the allowlisted test addresses). Try what changed, and try it as a *member* as well as an admin.
4. **Does the change touch the database?** (a new file in `pubbank/migrations/`.) If yes, the migration already ran on Test when it started, so check Test still works. Then **before merging, back up the live database** (see below).
5. **Merge `test` into `main`.** Railway rebuilds the live site. Migrations run automatically when it starts.
6. **Open the live site and check one thing** (sign in, open the workspace).

If step 3 fails, nothing live was touched: tell Claude, fix on `test`, push again.

## Backing up the live database before a database change

Railway: Postgres service (production) > Backups > create a backup, or from your laptop:

```bash
.venv/Scripts/python.exe scripts/backup_db.py
```
(the second needs the live database's public address; paste it only at the prompt, never in chat).

## Setting up the Test site (once)

1. Railway project > add an environment named `test`. Give its Postgres service and its app service **their own volume** at `/data` (not shared with production).
2. App service variables in `test`:
   - `PUBBANK_ENV` = `test`
   - `PUBBANK_TEST_EMAIL_ALLOW` = your test email addresses, comma-separated (plus-addresses like `you+owner@gmail.com` work)
   - `ANTHROPIC_API_KEY` = a **separate** key. In the Anthropic Console set a low monthly limit on it first.
   - `PUBBANK_HOSTED` = `1`, `PUBBANK_SETUP_CODE` = a new random text, `PUBBANK_LOGIN` = `link`
   - `PUBBANK_BASE_URL` = the Test site's own address
   - `PUBBANK_BLOCK` = `full_run,topic_ai,sample_run,schema_ai` (switches off the paid setup steps)
   - Remove or change anything copied from production that points at live data (`DATABASE_URL` must reference the Test Postgres).
   - Keep the same `RESEND_API_KEY` and `PUBBANK_MAIL_FROM`; the allowlist stops stray emails.
3. Settings > the app service in `test` deploys from the `test` branch; the production service deploys from `main`.
4. Let the Test site start once (it creates its tables). Then load the sample and test accounts from your laptop:
   ```bash
   .venv/Scripts/python.exe scripts/seed_test.py --ids data/test_sample_ids.json --owner you+owner@gmail.com --admin you+admin@gmail.com --member you+member@gmail.com
   ```
   It asks for the **Test** database's public address (hidden). It refuses to run on a database that already holds papers or questions.
5. On the Test site: Home > Move an organization, folder `dada2`, import the DADA2 settings zip.
