<!-- changes: -->
**PLACEHOLDER TEXT, NOT LEGAL LANGUAGE.** This is a stand-in until the final wording is reviewed. The list of services below describes how the software works today.

## Service Providers (beta)

These outside services are used to run BYO Publication Bank.

- **Railway** hosts the website, the database and the stored files.
- **Anthropic** writes the AI answers and reads papers to fill the extraction table. Your question and the relevant passages are sent to it. The organization's own API key is used.
- **Resend** sends sign-in emails. It receives your email address and the sign-in link.
- **PubMed, PubMed Central and Crossref** (US National Library of Medicine and Crossref) supply paper details and open-access text when papers are added. Paper identifiers are sent to them, not your questions.
- **GitHub** stores the program's code. It holds no papers, questions or accounts.

The search model that turns text into numbers runs on the installation itself, so papers are not sent out for that step.
