# Putting Publication Bank online on Railway (one installation per organization)

This is the plan for the DADA2 preview. The same steps are how any organization's own copy gets deployed later (on Render the idea is identical).
Railway's screens change from time to time: follow the idea, not the exact button names.

**What goes online:** the program, the database (papers' text, extractions, search passages), the organization's settings.
**What stays on your laptop:** the 1.1 GB of PDF files, your `.env`, the Postgres install. Nothing is uploaded that is not listed here.

## Before you start (once)

1. The foundation's OK to host their library on your account while testing: **done**.
2. A Railway account connected to GitHub: **done**.
3. **In the Anthropic Console, set a monthly spending limit** on the key you will use. This is your safety net.
4. **A private GitHub repository** holding the code (see step 1). The code contains no keys, papers, databases or organization settings (checked; `.gitignore` keeps them out).

## 1. Put the code on GitHub

1. On github.com choose **New repository**, name it (for example `publication-bank`), choose **Private**, and create it **empty** (no README, no .gitignore, no license).
2. Give Claude the repository address (like `https://github.com/yourname/publication-bank.git`). It will connect the project folder to it and upload (push) the code. The first push may open a browser window asking you to sign in to GitHub: that is normal.

## 2. Make the two files you will carry over

On the laptop, in the project folder:

```bash
.venv/Scripts/python.exe scripts/backup_db.py --for-host
```
Writes `data/backups/<name>_for_host.dump`: the database without this laptop's accounts.

Start the app locally, open the home page, and under **Move an organization** press **Download DADA2 settings**. That is a zip of the organization's name, colors, logo and template. It never contains a key.

## 3. Create the database FIRST, and load it

The order matters: the app creates empty tables the moment it starts, and a backup cannot be loaded on top of them. So the database is created and loaded before the app exists.

1. In Railway: **New Project → Provision PostgreSQL** (empty project with only the database). If Railway lets you choose the version, choose the newest (17 or 18).
2. Open the database service → **Variables** → copy the value of **`DATABASE_PUBLIC_URL`** (the address that works from outside Railway). Treat it like a password.
3. From the laptop:
   ```bash
   .venv/Scripts/python.exe scripts/restore_db.py --dump data/backups/<name>_for_host.dump --url "<paste DATABASE_PUBLIC_URL>" --only "DADA2 Papers" --clear-questions
   ```
   - `--only` leaves out the VCP test papers; `--clear-questions` leaves out the laptop's test questions.
   - It refuses to run if the database already holds papers, and ends by printing counts. **Expect: papers 537, chunks 17942, passage vectors 17942.** If it says the data did not arrive completely, do not continue: delete the database service, create a new one, and try again.
   - A few "harmless version differences" messages are normal if Railway's Postgres is older than 18.

## 4. Add the app

1. In the same project: **New → GitHub Repo →** pick the private repository. Railway finds the `Dockerfile` (and `railway.json`) and builds it. The first build takes several minutes. (It has not been test-built on the laptop, because Docker is not installed there: if it fails, send me the build log.)
2. Open the app service → **Variables** and add:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (a reference to the database service; if your database service has a different name, use that name)
   - `ANTHROPIC_API_KEY` = the key
   - `PUBBANK_HOSTED` = `1`
   - `PUBBANK_SETUP_CODE` = any long random text you make up (needed once, to create the first account)
   - `PUBBANK_BLOCK` = `full_run` while extraction is on hold (switches off the full-library run; add `,topic_ai,sample_run,schema_ai` to switch off every paid setup step)
3. Attach a **volume** to the app service, mounted at `/data`. Organization settings live there.
4. **Settings → Networking → Generate Domain.** The site is https automatically.
5. When the deploy finishes, open the domain. The deploy log should include `Applied database migrations: 001_baseline, 002_accounts`.

## 5. Create the owner account and bring in the settings

1. Open the site. It asks for the first account: your name, email, a password (at least 10 characters) and the **setup code** from step 4.
2. On the home page, under **Move an organization**, type the folder name `dada2`, choose the settings zip, and press **Import settings**.
3. Open the DADA2 workspace and ask one Quick question to confirm everything works (about 1 cent).
4. Under **People**, invite the foundation's staff. You get a link to send them yourself; the link works once. Administrators set things up and can see questions; members only use the workspace.

## 6. Check that it is locked down

```bash
.venv/Scripts/python.exe scripts/check_access.py https://your-site-address
```
It tries every address the app has without signing in and reports anything that opens. The right answer is `0 problems`.

## How big a plan you need

Measured on the laptop with the DADA2 library (537 papers, 17,942 passages): the web app uses about 350 MB of memory once it has loaded
the search model and all passage vectors (about 150 MB for the program, 135 MB for the model, 30 MB for the vectors), with brief peaks to about 420 MB.
- **Web service:** 0.5 GB of memory is enough for the demo but leaves little room; 1 GB is comfortable. Two CPUs is plenty (a question needs about one second of computing for the search step).
- **Database service:** 0.5 GB of memory is enough. The database is about 150 MB, so give its volume at least 1 GB (loading the backup temporarily needs extra room for building indexes).
- A much larger library grows this roughly in proportion to the number of passages (about 1.5 MB per 1,000 passages for the vectors).

## Things to know

- **Who can see what:** an administrator of an organization can see every question its members ask; members are told so on the workspace page. You (the owner) can open every organization on the installation.
- **Spending:** questions are capped per organization per day (Review & Launch → daily limit). Paid setup steps can be switched off with `PUBBANK_BLOCK`. The Anthropic Console limit is the last line of defense.
- **PDFs** are not on the server: "Open the PDF" links will not work online, and the PDF folder import is switched off on a hosted installation. Uploading PDFs through the website needs the PDF reader, which is not part of this small image.
- **Background jobs** live in the running program's memory. If the site restarts during a job (a topic check, for instance), the job stops and can be run again. A full-library extraction survives restarts, because its state is in the database.
- **Handing over:** the foundation will later deploy its own fresh copy from the same code under *its* hosting account, enter its own Anthropic key, and run extraction itself. Delete this test installation when that is done.
- **Backups:** turn on backups for the database service if your plan offers them. The laptop's `scripts/backup_db.py` still works against the laptop copy.
