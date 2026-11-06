These are the outside services Zaris Advising uses to run the version of BYO Publication Bank that Zaris hosts. Each one receives only what it needs for its job. We'll update this page when the list changes.

## Railway (hosting)

- **What it does:** hosts the website, the database, uploaded files and backups.
- **What it holds:** everything stored in BYO Publication Bank, including accounts, sign-in records, questions and answers, feedback, uploads and extracted information.

## Anthropic (AI)

- **What it does:** reads papers to pull out the extraction fields, reads onboarding transcripts and notes to propose those fields, and writes cited answers to questions.
- **What it receives:** the text of papers being read, onboarding transcripts and notes when an admin uses them, and each question with the passages relevant to it. It doesn't receive your name or email address.
- **Which account:** each publication bank uses either the Organization's own Anthropic API key or, during the beta, a shared key held by Zaris.
- **How Anthropic handles it:** Anthropic's API is covered by its commercial terms. Under them, Anthropic doesn't use what's sent through the API to train its models, and deletes it from its systems within 30 days. Anthropic may keep data longer when the law requires it, or to enforce its usage policy. Interactions its safety systems flag can be kept for up to 2 years.

## Resend (email)

- **What it does:** sends sign-in links, invitations and password reset emails.
- **What it receives:** your email address and the content of those emails, including the link.

## PubMed, PubMed Central and Crossref (paper details)

- **What they do:** supply titles, authors, journal details and open-access text when papers are added. PubMed and PubMed Central are run by the US National Library of Medicine, and Crossref is a nonprofit registry of scholarly publications.
- **What they receive:** paper identifiers, such as PubMed IDs and DOIs. They never receive questions or anything about members.

## GitHub (code)

- **What it does:** stores the BYO Publication Bank software code.
- **What it holds:** no papers, questions, accounts or other data from the site.

## Done on our own server

The search model that turns passages into numbers for search runs on our own server, so papers aren't sent to another provider for that step. Feedback is stored only in our own database and isn't sent to any provider.
