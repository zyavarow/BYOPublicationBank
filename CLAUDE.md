# Publication Bank

A research tool for rare disease patient advocacy organizations. An organization loads its library of scientific papers; the system extracts structured data from every paper and provides a chat interface that answers questions from that library with plain-language, fully cited answers.

## Working with me (read first)

- I'm a scientist (PhD pharmacology), not a software engineer. Explain technical decisions in plain language, briefly. Don't assume I know terminal, git, or Docker conventions — tell me exactly what to run and why.
- **Plan before building.** For each phase, propose a short plan and wait for my approval before writing code.
- **Never spend API money without asking.** Before any call to the Claude API beyond a tiny test (1–3 papers), show me an estimated token count and dollar cost and wait for a yes.
- Never commit papers, extracted text, databases, or API keys to git. Keys live in `.env` only.
- Work in small, testable steps. After each step, tell me how to check that it worked.
- Use the term **"organization"** for the group being set up — never "client."

## Current status

Pilot with one organization: a library of 537 PDFs (all obtained via university license or author permission; this is a private workspace for that organization). PDFs go in `data/raw/<org-slug>/`.

## Architecture decisions (already made)

- **Language:** Python for everything.
- **API server:** FastAPI.
- **Database:** Postgres + pgvector — one database holding a structured extraction table, text chunks, embeddings, and a keyword (full-text) index. Every row carries an `org_id` so multiple organizations can share one install. **Pilot deviation (decided with the user, Oct 2026):** on the pilot laptop (Windows 11 ARM64, Smart App Control enforcing) pgvector can't be installed, so embeddings are stored in Postgres as plain arrays and similarity is computed in Python; pgvector arrives with the Docker deployment. **Driver note:** never install `psycopg-binary` (Smart App Control blocks its bundled libpq); always import psycopg via `pubbank/db.py`, which uses pure-Python mode with the signed `libpq.dll` from the Postgres install. Prefer signed/pure-Python dependencies; compiled unsigned binaries may be blocked. **DLL-clash note:** `pubbank/db.py` exposes only `libpq.dll` (never put the Postgres folder on PATH: its zlib/libxml2/libzstd copies break SciPy, lxml and the PDF parser), and in any process that parses PDFs, `import docling_parse.pdf_parsers` must come before the first psycopg import (see top of `scripts/ingest.py`).
- **PDF parsing:** Docling (or GROBID if Docling struggles). Preserve section structure (Abstract, Methods, Results, Discussion) — the extraction schema depends on knowing which section text came from. Flag scanned PDFs needing OCR.
- **Metadata:** Resolve each paper's DOI/PMID from its text, then fetch clean metadata from PubMed (Biopython Entrez) or Crossref. Use this to deduplicate.
- **Extraction:** Claude API via the `anthropic` Python SDK, using the Batch API (50% cheaper) and prompt caching for the schema instructions. Default model: `claude-sonnet-5-5`. Schema defined as Pydantic models so outputs are validated (`pubbank/extraction_schema.py`; the editable human-facing version is `docs/extraction_schema_DADA2.xlsx`).
- **Embeddings:** A local open-source model via `sentence-transformers` (no second API key). Organizations should only ever need one API key (Anthropic).
- **Search:** Hybrid — keyword + vector search, then a reranker. Keyword search matters because variant names (e.g., p.R155H), gene symbols, and catalog numbers don't embed well.
- **Full text is kept.** Chunks (~500–800 tokens, split on section boundaries) are the search index; full text is used for extraction and for Deep-mode reading ("small-to-big" retrieval).
- **Hosting:** My own server for the pilot, via Docker Compose on a managed host (Railway or Render). Long-term, each organization deploys its own copy from a one-click template, so all org-specific settings live in one config file — no code changes per organization.
- **Accounts and delivery (decided Oct 2026):** one separate installation per organization (own server, database, Anthropic key, admins); no shared multi-tenant system, so the user holds no organization's data. Login = email + password (`pubbank/auth.py`, scrypt, DB sessions); roles per organization = admin / member; the installation owner ("super") is the first account. Migrations are numbered SQL files in `pubbank/migrations/` run at start-up. Groups either deploy themselves or the user deploys for them; organizations opt in to sharing usage with the user (levels in `.claude/plans/`). Master-admin "control plane" is a later, separate app. Online steps: `docs/DEPLOY.md`; access rules: `app/access.py`, checked by `scripts/check_access.py`.
- **API key security:** The organization's API key never reaches the browser. All model calls go through the server, with rate limits and a spending cap.

## Product requirements

**Inputs (publication bank):** folder of PDFs; reference manager export (e.g., Zotero); PubMed ID list. PMID-only papers get metadata + abstract from PubMed and open-access full text from PubMed Central when available; otherwise marked "abstract only," and that label follows the paper into answers.

**Extraction schema (per organization, AI-proposed from onboarding call transcripts, then edited by me).** Pilot fields (DADA2, schema `dada2-v1`): study type, gene/protein focus (ADA2 vs ADA1), mutations studied, model system, techniques, sample source, reagents (including catalog numbers), corresponding author(s), treatments/interventions, clinical features reported; plus derived from PubMed/Crossref metadata with no AI cost: publication year, all authors, last author. Every extracted value stores its supporting quote and section/page. Reagents matter because antibody differences can explain contradictory results between papers.

**Answers must:**
- default to plain language (a technical format is also available), always fully cited at the passage level;
- show a confidence indicator (based on number of independent supporting papers, evidence type, and agreement);
- include a "Not covered by this library" callout when retrieval is weak or a part of the question isn't addressed;
- respect an "Answering as" role: Patient/Caregiver, Clinician, Scientist/Researcher, Advocacy Leader (draft roles).

**Search depth (user-selectable, with estimated cost shown):**
- Quick — one hybrid search, ~8 chunks, fast model (`claude-haiku-4-5-20251001`).
- Standard — query rewriting (synonyms, variant nomenclature), ~20 chunks, rerank, `claude-sonnet-5`.
- Deep — agentic: decompose into sub-questions, multiple retrieval rounds, structured-table filters, read full sections, synthesize and note disagreements.
- Questions that are really filters ("which papers used patient-derived fibroblasts?") route to the structured table, not RAG.

**Three destinations:** private workspace (me), shareable view (organization's research and patient network), embeddable widget (organization's website).

## The mockup

`docs/mockup/` holds the visual design, one HTML file per screen (1–4 = setup wizard, 5–7 = the three destinations). These were built in a design tool and **won't render correctly on their own** — treat them as a reference for layout, content, wording, and visual style (fonts, colors, spacing are in each file's `<style>` and inline styles). Rebuild the real front end to match them; don't try to reuse the files directly.

## Build order

Build in this order (the reverse of the mockup's flow — the setup wizard comes last, because for the pilot I configure the organization by hand).

1. **Ingestion (no UI).** Parse all PDFs in `data/raw/<org>/`, resolve identifiers, fetch metadata, deduplicate, and write a report: total files, matched to PubMed, duplicates, scanned/OCR-needed, non-papers, failures. No API spending in this phase.
2. **Extraction on a 50-paper slice.** Tune the schema; output a spreadsheet (CSV/XLSX) I can review. Then, with my approval and a cost estimate, run the full library via Batch API.
3. **Chunking, embeddings, search, and the `/ask` endpoint**, wired to the private workspace screen (mockup 5). Test with a gold set of 20–30 real questions from the organization.
4. **Depth and role controls; the shareable view** (mockup 6).
5. **Self-service layer:** setup wizard (mockups 1–4), Docker Compose packaging, deploy template, embeddable widget (mockup 7).
