<!-- Parked 2026-10-14. Pick this up when you have an email sender and a domain. -->
**Status: PARKED.** Decisions so far: magic links only (passwords eventually removed), a link with no typed code, built behind a `PUBBANK_LOGIN` switch
(`password` default, then `both`, then `link`), with an owner break-glass page. Phase A can be built before email exists; Phase B needs the email sender and a domain.
Note: since this plan was written, `scripts/make_reset_link.py` was added as the host-side password reset for any account.

# Plan: magic-link sign-in, built now behind a switch, turned on when email exists

## Context
The user asked how accounts and sign-in work today and what moving to magic links (sign in by clicking a one-time link emailed to you, no password) would involve. They chose **magic links only** (passwords eventually removed) and **link only** (no typed code). They do **not yet have a domain or an email sender**, so the plan is split: everything that does not need email can be built, tested and committed now, but stays **switched off** on the live site until email delivery works. Nothing has been built yet; this plan is for approval.

## How it works today (`pubbank/auth.py`, `app/auth_routes.py`)
- **Sign-in:** email + password at `/login`. Passwords are stored only as salted scrypt hashes (10+ characters). Unknown emails take the same time as wrong passwords, so the page never reveals who has an account.
- **Sessions:** a random token in the `pb_session` cookie (14 days, HttpOnly, secure when hosted). Only its hash is in the `sessions` table. Changing a password signs the person out everywhere else.
- **Brute-force limits:** 8 wrong tries per email and 60 per network address per 15 minutes (in memory).
- **Roles:** per organization `admin` or `member`; the installation owner ("super") is an admin of everything.
- **Getting an account:** an admin makes a one-time invitation link (72 hours) on the People page and sends it by hand; the person chooses a password. The owner is created at `/first-run` with the host's setup code.
- **Forgot password:** admins make 24-hour reset links by hand. A self-service emailed reset exists but only shows when email is configured; on Railway Hobby outgoing SMTP is blocked, so it is off.
- **Reusable pieces:** `make_link` / `get_link` / `use_link` and the `invites` table (`purpose` column), `new_session`, `_set_cookie`, the hourly limit and same-reply behaviour of `/forgot`, `mailer.py`, and the fake-mail test launcher.

## What the magic-link version looks like
1. **Sign-in page:** one "Email" box; the reply is identical whether or not an account exists.
2. **The link:** valid about 15 minutes, usable once, stored only as a hash.
3. **Opening the link:** shows a page with a **"Sign in" button** (a POST); a plain GET never signs in, because email scanners pre-open links and would use up a one-time link.
4. **Invitations:** accepting one asks only for a name and signs the person in (no password).
5. **People page:** "Reset link" becomes "Sign-in link", the manual fallback an admin sends by hand.
6. **Limits:** reuse the `/forgot` pattern (3 per address and 10 per visitor per hour, via `audit_log`); keep the 14-day session.

## The lockout risk, and the protections
With passwords gone and email required, a broken email setup could lock out everyone, including the owner. Built in:
- **Owner break-glass `/recover`:** the owner enters the host's `PUBBANK_SETUP_CODE` plus their email to get a session with no email needed (rate-limited, every use written to `audit_log`).
- **Admin manual links** on the People page for everyone else.
- **Local laptop mode:** when `PUBBANK_HOSTED` is not set and no email is configured, the sign-in page shows the link on screen. Never when hosted.

## Phase A: build now (no domain or email sender needed)
A new setting `PUBBANK_LOGIN` chooses the mode: `password` (today's behaviour, **the default**), `both`, or `link`. The live site stays on `password` until the user changes it, so nothing about the current Railway site changes when this ships.
1. **Migration** in `pubbank/migrations/`: `users.password_hash` becomes optional. No data lost.
2. **Auth code** in `pubbank/auth.py`: login-link functions (reuse `make_link` with `purpose='login'` and a 15-minute expiry).
3. **Routes** in `app/auth_routes.py`: email-only `POST /login` step, `GET /signin/{token}` (confirmation page), `POST /signin/{token}` (creates the session via `new_session`), `/recover`; add them to `app/access.py` as public.
4. **Name-only invitation and first run** (when the mode is `link` or `both`).
5. **Templates:** new `signin.html` and `recover.html`; `login.html`, `invite.html`, `first_run.html`, `account.html`, `people.html` follow the mode.
6. **Manual sign-in links work with no email at all** (an admin copies a link by hand, exactly like today's invitations), so `both` or `link` mode is usable even before an email sender exists, just not convenient.
7. **Tests with the fake-mail launcher**, and `scripts/check_access.py` extended with the new addresses.

## Phase B: switch on later (needs an email sender)
1. Build the earmarked HTTPS email option in `pubbank/mailer.py` (Resend, SendGrid, Mailgun or Postmark; choose with the user). Some providers let you verify just a single sender address without owning a domain, which is enough for testing, but a domain is strongly preferable for real use because mail from a bare Gmail or similar address often lands in spam.
2. Decide whose domain sends the emails (one for the whole product, or each organization's own).
3. Set `PUBBANK_LOGIN=both` on Railway, watch it for a while, then `link`; later drop the password code and column in a cleanup migration.
4. Update `docs/DEPLOY.md` (email becomes a required deployment step), the handoff notes, project memory and the `CLAUDE.md` accounts line.

Estimated size: Phase A about 1 day including tests; Phase B about half a day plus whatever setting up the email account takes.

## Files that would change in Phase A
`pubbank/auth.py`, `app/auth_routes.py`, `app/access.py`, `pubbank/migrations/` (new file), templates `login.html`, `invite.html`, `first_run.html`, `account.html`, `people.html` and new `signin.html`, `recover.html`, `scripts/check_access.py`.

## Verification
- On the scratch copy with the fake-mail launcher, in `link` mode: request a link, read it from the captured file, open it, confirm a plain GET does not sign in, press the button, confirm the right role.
- One-time use, expiry (expired and reused links), identical reply for unknown addresses, the hourly limits, member vs admin vs owner access, name-only invitation, `/recover` with a right and a wrong setup code, local on-screen link, and that a link for one email cannot be used for another.
- Confirm `password` mode (the default) behaves exactly as today: sign-in, invitation, reset, change password.
- `scripts/check_access.py` on the scratch copy with member and admin cookies, and against the live site after a deploy (with the live site still in `password` mode).
- A lockout drill on the scratch copy: email broken, owner recovers with `/recover`.
