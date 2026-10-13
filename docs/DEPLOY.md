# Putting Publication Bank online (one installation per organization)

This is the plan for the DADA2 preview. The same steps are how any organization's own copy gets deployed later.
Screens on the hosting site change from time to time: follow the idea, not the exact button names.

**What goes online:** the program, the database (papers' text, extractions, search passages), the organization's settings.
**What stays on your laptop:** the 1.1 GB of PDF files, your `.env`, the Postgres install. Nothing is uploaded that is not listed here.

## Before you start (once)

1. **Get the foundation's OK in writing.** Their library will sit on a server in *your* account until you hand it over. A two-line email is enough ("OK to host our library on a private test site while we build the portal").
2. **A hosting account** (Railway is the plan; Render works the same way). Add a payment card; a small test site costs a few dollars a month (check the host's current prices).
3. **A private GitHub repository** for the code only (the `.gitignore` already keeps papers, databases and keys out). The host builds from it.
4. **In the Anthropic Console, set a monthly spending limit** on the key you will use. This is your safety net.

## 1. Make the three files you will carry over

On the laptop, in the project folder (each is one command):

```bash
.venv/Scripts/python.exe scripts/backup_db.py --for-host
```
Writes `data/backups/<name>_for_host.dump`: the database without this laptop's accounts.

Then start the app locally, open the home page, and under **Move an organization** press **Download DADA2 settings**. That is a zip of the organization's name, colors, logo and template. It never contains a key.

## 2. Create the site on the host

1. New project → **Deploy from GitHub repo** → pick the private repo. The host finds the `Dockerfile` and builds it. (The first build takes several minutes. It has not been test-built on the laptop, because Docker is not installed here: if it fails, send me the build log.)
2. Add a **PostgreSQL** database to the project. Choose **version 17 or 18** (the backup comes from version 18).
3. Add a **volume** to the app service, mounted at `/data`. Organization settings and anything uploaded live there.
4. In the app service's **Variables**, set:
   - `DATABASE_URL`: the database's connection address (Railway offers a "reference" to the database service)
   - `ANTHROPIC_API_KEY`: the key (stored by the host as a secret)
   - `PUBBANK_HOSTED` = `1`
   - `PUBBANK_SETUP_CODE` = any long random text (needed once, to create the first account)
   - `PUBBANK_BLOCK` = `full_run` while extraction is on hold (switches off the full-library run; add `topic_ai,sample_run,schema_ai` to switch off every paid setup step)
5. **Generate a public domain** for the app. The site is https automatically.

## 3. Load the database

The database must be loaded **before** the app starts for the first time (or while it is stopped). From the laptop:

```bash
.venv/Scripts/python.exe scripts/restore_db.py --dump data/backups/<name>_for_host.dump --url "<the database's PUBLIC address>" --only "DADA2 Papers" --clear-questions
```
- The public address is on the database service, under Connect / Public network. It is only used for this command.
- `--only` leaves out the VCP test papers; `--clear-questions` leaves out the laptop's test questions.
- It refuses to run if the database already holds papers, and prints the counts when it finishes (expect 537 papers, 17,942 passages).

Then start the app. It adds the account tables by itself.

## 4. Create the owner account and bring in the settings

1. Open the site. It asks for the first account: your name, email, a password (at least 10 characters) and the **setup code** from step 2.
2. On the home page, under **Move an organization**, type the folder name `dada2`, choose the settings zip, and press **Import settings**.
3. Open the DADA2 workspace and ask one Quick question to confirm everything works (about 1 cent).
4. Under **People** (inside any setup page of the organization), invite the foundation's staff. You get a link to send them yourself; the link works once. Administrators set things up and can see questions; members only use the workspace.

## 5. Check that it is locked down

```bash
.venv/Scripts/python.exe scripts/check_access.py https://your-site-address
```
It tries every address the app has without signing in and reports anything that opens. The right answer is `0 problems`.

## Things to know

- **Who can see what:** an administrator of an organization can see every question its members ask; members are told so on the People page. You (the owner) can open every organization on the installation.
- **Spending:** questions are capped per organization per day (Review & Launch → daily limit). Paid setup steps can be switched off with `PUBBANK_BLOCK`. The Anthropic Console limit is the last line of defense.
- **PDFs** are not on the server: "Open the PDF" links will not work online, and the PDF folder import is switched off on a hosted installation. Uploading PDFs through the website needs the PDF reader, which is not part of this small image.
- **Background jobs** live in the running program's memory. If the site restarts during a job (a topic check, for instance), the job stops and can be run again. A full-library extraction survives restarts, because its state is in the database.
- **Handing over:** the foundation will later deploy its own fresh copy from the same code under *its* hosting account, enter its own Anthropic key, and run extraction itself. Delete this test installation when that is done.
- **Backups:** the host's database service offers backups: turn them on. The laptop's `scripts/backup_db.py` still works against the laptop copy.
