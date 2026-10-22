# BYO Publication Bank

A research tool for rare disease patient advocacy organizations. An organization loads its own library of scientific papers; the system extracts structured data from every paper and offers a chat interface that answers questions from that library in plain language, with every statement cited to a passage in a paper.

"BYO" means *bring your own*: each organization brings its own papers and its own Anthropic API key, and runs its own separate installation. No organization's papers or data are shared with anyone else, including the people who wrote this software.

## What it does

- **Reads your library.** PDFs, reference-manager exports and PubMed ID lists are parsed, matched to PubMed/Crossref metadata and de-duplicated.
- **Extracts structured data** from each paper (study type, gene focus, mutations, model systems, techniques, reagents, clinical features and more). The fields are set per organization, and every extracted value keeps its supporting quote and location.
- **Answers questions** with hybrid search (keywords + meaning), at three depths (Quick, Standard, Deep). Answers show a confidence indicator and say plainly when the library does not cover something.
- **Keeps access private.** Email sign-in, administrator and member roles, and the API key never reaches the browser.

## Status

Early and in active development, piloted with one organization. Expect rough edges.

## Getting started

- Deploying your own copy online: see [docs/DEPLOY.md](docs/DEPLOY.md).
- Python 3, FastAPI, Postgres. Dependencies are listed in `requirements.txt` (local) and `requirements-web.txt` (web app).
- Copy `.env.example` to `.env` and fill in your own keys. **Never commit `.env`, papers, extracted text or databases.** The `.gitignore` is set up to keep them out.

## Your organization's files stay yours

Each organization's settings, extraction schema, interview notes and test questions live in a local `orgs/` folder (on a hosted install, on its own storage volume). That folder is deliberately excluded from this repository.

## License

[MIT](LICENSE). Use it, change it, share it.
