Subject: DADA2 Publication Bank: progress update and decisions needed before the full extraction

Hi [Name],

Here is where the DADA2 Publication Bank stands and what I need from you before the next step. The short version: the pilot library is loaded and organized, a 15-paper test of the AI extraction worked well, and I would like your sign-off on a few choices before I process the whole library.

**1. The library**
- You sent 537 PDFs. After checking them, 416 papers are in the bank.
- The other 121 are set aside, not deleted: 34 duplicate copies, 35 papers about DADA2 as a software or sequencing-tool name rather than the disease, 41 items that are not research papers (for example errata, posters and supplements), and 11 scanned files that need a text-recognition step first.
- I attached the list of set-aside files with the reason for each. Please skim it and tell me if anything should be in the bank.
- 359 of the 416 papers matched a PubMed record, which gives clean titles, authors and journals.

**2. What the AI extracts**
For every paper, the system pulls out ten things: study type, gene or protein focus (ADA2 vs ADA1), variants studied, model system, techniques, sample source, reagents (with catalog numbers), corresponding authors, treatments, and clinical features. Publication year, all authors and last author come from PubMed at no AI cost.

Each answer shows:
- one of three outcomes: **Found**, **Not found** (the paper should say it but doesn't) or **Not applicable** (for example, reagents for a review article), so there are no unexplained blanks;
- the exact quote from the paper that supports it, with the page, which is checked automatically against the paper's text;
- a High, Medium or Low confidence rating and a one-sentence reason.

Variants are standardized (so p.R169Q, Arg169Gln and p.Arg169Gln match), and clinical features are matched to the Human Phenotype Ontology, the standard medical vocabulary for rare disease.

**3. The test: 15 papers, reviewed by hand**
I tested on 15 representative papers (case reports, cohorts, animal studies, a trial, reviews and a guideline) and checked the answers against the papers myself. I reviewed 69 of the 150 answers, covering every field and every kind of paper except where noted below:
- 64 were correct, 3 were correct but incomplete, 1 was partly wrong, 1 I could not confirm, and none were wrong.
- "Not applicable" was right 17 of 17 times, and "Not found" was right 10 of 11 times.
- I did not review the other 81 answers, so this is a sample, not a full audit. Five papers were not reviewed.

The review found real problems, which are now fixed:
- **Methods sections not found in some papers.** The PDF reader missed the methods section in a number of papers (for example one headed "Material and Methods"), so the AI read only the abstract. I fixed the reader, and now the AI reads the whole paper whenever a key section is missing. In the paper where this showed up, the model systems went from one (mouse) to four (mouse, healthy donors, a cell line and primary human cells).
- **Corresponding authors.** An email address or a starred name now counts as a contact. One review article that had been marked "Not found" now lists both authors.
- **Treatments** are now one entry per drug (so corticosteroids are listed as prednisone, dexamethasone and so on, not lumped together).
- **Supporting quotes** now come from what the paper did or found, rather than a textbook definition.
- Study type now has a short design note (for example "retrospective, national cohort with long-term follow-up").

One remaining weak spot: for a few papers the "gene focus" quote is still a general sentence about the disease rather than the paper's own aims. The answer is right, but the quote could be better.

**4. Limits to be aware of**
- The AI reads the paper text, not supplementary files, so reagent details that only appear in a supplement will show as "Not found" with a note saying so.
- 11 scanned PDFs cannot be read until they go through text recognition; I plan to do that after the main run. A paper with no full text would appear as abstract-only.
- The AI can still make mistakes. That is why every answer carries its quote, page and confidence, and why I recommend a periodic spot-check of Medium and Low confidence answers.

**5. Cost and next step**
Running all 416 papers is the first real AI spend. My early estimate is roughly $20 to $45 in AI usage, depending on whether I use the discounted batch service (slower, about half the price). I will confirm an exact figure before running anything. After that, the next stage is the search and question-answering layer, so staff can ask questions in plain language and get cited answers.

**6. What I need from you**
1. Are the 416 papers the right library? (Please look at the attached set-aside list.)
2. Are the ten fields the right ones, or is there anything you want added or removed before I run everything? A change after the full run means paying to redo it.
3. Do you have a view on the treatment and clinical-feature groupings you would like to see as search filters?
4. May I go ahead with the full extraction once I send the exact cost? If so, who should approve the spend?

I would like to set up a short call to go through this. Would [day/time options] work?

Thank you,
[Your name]

Attachments: suggested exclusions list; example extraction spreadsheet for the 15 test papers (with supporting quotes).
