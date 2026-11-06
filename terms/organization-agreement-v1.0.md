## About this agreement

This agreement is between Zaris Advising LLC, a Wyoming limited liability company ("Zaris," "we," "us"), and the organization creating a publication bank ("the Organization"). It covers the version of BYO Publication Bank that Zaris hosts. An organization that runs its own copy of the open-source software isn't covered by it.

The person who accepts this agreement confirms they're allowed to do so for the Organization. Everyone who uses the Organization's workspace, including its admins, also accepts the Member Terms. The Privacy Notice and Service Providers page are part of this agreement.

If the Organization has a separate signed agreement with Zaris, that agreement wins where the two conflict.

## A beta service

BYO Publication Bank is in beta. Features may change, pause or be removed, and the site may sometimes be unavailable or have errors.

## Admins and members

The Organization's admins decide who is invited and can remove anyone's access at any time. The Organization is responsible for activity in its workspace, and for how it shares answers or other outputs outside BYO Publication Bank.

Admins can see each member's questions with their name, the answers given, and usage and cost by person, and they can download the question log. Members are told this in the Member Terms.

## Uploads are the Organization's responsibility

Zaris is not responsible for anything uploaded to BYO Publication Bank. This includes papers, reference lists, onboarding transcripts, notes and any other files. The Organization is responsible for everything it uploads or that is uploaded on its behalf, including by Zaris at its direction.

The Organization confirms that it holds all the rights and permissions needed to:

- store each upload in BYO Publication Bank
- process it with AI, including reading it, pulling out information, indexing it and answering questions from it
- make it available to the Organization's members

For papers, those rights might come from open access, a subscription or university license, or the authors' permission. Where BYO Publication Bank shows license information, it's for convenience only. It isn't a legal review or a confirmation that a paper may be used.

If a rights holder objects to an upload, the Organization will remove it promptly. Zaris may also remove or disable access to an upload in response to a credible complaint.

## Onboarding transcripts and notes

Admins may upload transcripts or notes from onboarding calls so that AI can propose the extraction fields. Uploading them is at the admin's discretion. The Organization is responsible for having permission from the people on those calls to record them, share them with Zaris and have them processed by AI. Zaris doesn't take responsibility for what transcripts contain.

Transcripts are sent to Anthropic for this step. Under Anthropic's commercial terms, they aren't used to train Anthropic's models and are deleted from Anthropic's systems within 30 days, except where Anthropic must keep them longer for safety reviews or by law. Please remove details about patients or families that aren't needed before uploading.

## No identifiable health information

The Organization agrees not to upload documents containing identifiable health information about any person, such as medical records or patient registry data. Published papers, including case reports, are fine.

## Confidentiality

Zaris keeps the Organization's uploads, extracted information and question log confidential. We share them only with the service providers listed on the Service Providers page, so they can run the service, or when the law requires it. We don't use one organization's papers, extracted information or questions inside another organization's publication bank.

## Ownership and what Zaris may use

The Organization owns its uploads and the information extracted from them. Zaris doesn't own the Organization's papers or data. The papers themselves still belong to their authors and publishers.

The Organization gives Zaris a license to use its uploads, extracted information, settings and question log to:

- run, support and troubleshoot BYO Publication Bank
- improve the software, including by comparing how it works across organizations
- reuse the Organization's extraction field names and categories (for example, fields recommended for a particular disease) in the shared set of fields offered to all organizations, without any of the Organization's content
- use de-identified questions and aggregate findings, such as how many questions were asked or what topics came up most, in talks, case studies, papers and grant reports

De-identified means no person or organization can be identified from it. Zaris won't name the Organization or quote its members publicly without the Organization's consent.

Zaris built the BYO Publication Bank software under a contract with Biohub, and it's open source under the MIT license. The Organization may use answers and its extracted information for its own work, subject to the papers' licenses.

## API keys and costs

During the beta, some publication banks run on a shared Anthropic API key held by Zaris, and Zaris covers those costs. An Organization can also bring its own Anthropic key, and Anthropic then bills the Organization directly for that usage. Zaris isn't responsible for charges on an Organization's own key. Cost estimates shown in BYO Publication Bank are estimates only, and actual charges may differ. Daily spending limits may apply.

If the Organization enters its own key, we store it encrypted. We never show it again after it's entered (only its last four characters), and we never send it to a browser or write it to logs. The Organization can replace or remove the key at any time in BYO Publication Bank, and can revoke it or set spending limits in its Anthropic account.

## How uploads are processed

- **Reading and extraction:** each paper's text is sent to Anthropic to pull out the fields set up for the publication bank.
- **Search index:** papers are indexed on our own server, and a search model on that server turns passages into numbers for search. Papers aren't sent to another provider for this step.
- **Answers:** a member's question and the relevant passages are sent to Anthropic to write a cited answer. Answers show only a short excerpt from any one paper and link to the original. They never show a paper's full text.
- **Paper details:** paper identifiers (such as PubMed IDs and DOIs) are sent to PubMed, PubMed Central and Crossref to look up titles, authors and open-access text.
- **Storage:** uploads, extracted information and the question log are stored on Railway, our hosting provider.

## Deletion

The Organization can ask Zaris to delete its publication bank, its uploads, extracted information or question log at any time by writing to zollie@zarisadvising.com. We complete deletion requests within 30 days. Before deleting, we can give the Organization an export of its data, including the paper list, extracted information and question log.

If this agreement ends, we delete the Organization's data within 30 days unless the Organization asks for an export first. Copies in backups are removed as those backups are replaced.

## Security

We use reasonable measures to protect the Organization's data, including encrypted connections, encrypted storage of API keys and access limited to people who are approved or invited. If we learn of a breach affecting the Organization's data, we'll tell the Organization without undue delay.

## Indemnification

The Organization will defend and indemnify Zaris against claims arising from anything uploaded to its publication bank, including onboarding transcripts, or from its own or its members' misuse of BYO Publication Bank.

## No warranties

To the extent the law allows, BYO Publication Bank is provided "as is" and "as available," without warranties of any kind. Answers and extracted information are AI-generated, may contain errors, are for research use only and are not medical advice.

## Limits of liability

To the extent the law allows, Zaris is not liable for decisions made based on answers or extracted information, or for any indirect, incidental, special or consequential damages, including lost data or lost profits. Zaris's total liability under this agreement is limited to $100.

## Ending the agreement

The Organization can end this agreement at any time by writing to Zaris. Zaris may suspend or end access during the beta, with reasonable notice where possible. The sections on uploads, onboarding transcripts, confidentiality, ownership, deletion, indemnification, warranties and liability continue after the agreement ends.

## Changes to this agreement

When we update this agreement, the Organization's admins will see what changed and be asked to accept the new version.

## Governing law

This agreement is governed by the laws of the State of Wyoming, USA.

## Contact

zollie@zarisadvising.com
