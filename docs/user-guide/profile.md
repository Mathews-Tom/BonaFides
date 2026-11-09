# Build your profile

Your profile is the foundation for everything else in BonaFides. Every résumé and cover letter is built from it, and every factual sentence in those documents has to come from one of its entries.

A profile has three parts:

- **Personal details**: your name, a tagline and the contact details printed on your documents.
- **Sources**: the material that supports your facts.
- **Entries**: the facts themselves, each linked to a source.

Open **Profile** in the menu. The **Profile details** tab holds all three. (The **Résumé imports** tab is covered in [Import an existing résumé](resume-import.md).)

> **Already have a résumé?** [Importing it](resume-import.md) is often the quickest start. It turns each line into a proposed entry for you to check. You can then refine those entries here.

## Personal details

Under **Personal details**, fill in:

- **Display name** (required, up to 120 characters).
- **Highlight / tagline (optional)**: a one-line summary, up to 300 characters.
- **Description (optional)**: up to 2,000 characters. This text is kept as written; it isn't turned into profile facts.
- **Printable email**, **Printable phone**, **Printable location** and **Printable postal address** (all optional).

Select **Save profile**. You'll see "Saved as revision *N*".

Contact details are printed on your documents but are **never sent to an AI model**. Saving new details doesn't change documents you've already created; rebuild a document to pick them up.

## Add a source

You need at least one source before you can add an entry.

1. Under the entry sections, choose **Sources**.
2. Select **Add source or owner attestation**.
3. Fill in:
   - **Source text**: the supporting text, up to 8,000 characters. Paste the relevant part of a document, or write your own statement.
   - **Source reference**: where it comes from, such as "2023 performance review" or "Certificate no. 1234" (up to 500 characters).
   - **Source type**: Owner statement, Document, Work sample, Web page, Credential or Other.
   - **Privacy class**: Public, Private or Confidential.
   - Tick **This is my explicit owner attestation, not independent verification** if the source is simply your own statement.
   - Tick **My employer owns this content** if the text belongs to an employer, for example an internal report.
4. Select **Save source revision**.

**Privacy:** the text of a Confidential or employer-owned source is never sent to an AI model. Entries that cite it can still be used, but only the entry's own wording is sent. See [Optional AI tailoring](ai-tailoring.md#what-the-model-receives).

To change a source's privacy class or employer-owned flag later, select **Change classification *N*** under that source, change the values, then select **Save source classification**. This applies straight away to every version of that source.

## Add an entry

Entries are grouped by kind: **Skills**, **Work experience**, **Projects**, **Education**, **Languages**, **Certifications**, **Identity facts**, **Preferences** and **Other facts**.

1. Choose a kind, for example **Skills**.
2. Select **Add skills entry** (the button names the kind you chose).
3. Write the fact. How you do this depends on the kind; see below.
4. Fill in any other fields that apply: **Organization**, **Role or qualification**, **Start date**, **End date** and **Proficiency or level**. Projects also have **Project reference URL (optional)**.
   - Dates can be a year (`2021`), a month (`2021-06`) or a full date (`2021-06-30`). Leave out dates you don't know; BonaFides never guesses them.
   - For a level, include the scale: "CEFR B2" says more than "B2".
5. Link the evidence: select **Add evidence link**, then:
   - **Evidence revision**: choose the source.
   - **Support type**: **Direct source (owner supplied)** or **Owner attestation (unverified)**. **Context only** links are kept for your reference but never count as support.
   - **Source excerpt**: the words from the source that support this entry. What to paste depends on the kind of entry; see [The one rule that matters most](#the-one-rule-that-matters-most).

   An entry can have up to 16 evidence links.
6. Select **Save entry revision**.

### Writing the fact

**Most kinds** (skills, education, languages and so on) have a single **Entry statement** box. Write the fact as you'd want it to appear, for example `SQL` or `Bachelor of Science in Biology`.

**Work experience and Projects** use a **Structured claim** instead, so BonaFides can tell how much responsibility you're claiming:

- **Ownership verb**: what you did, such as *led*, *managed*, *built*, *maintained* or *wrote*. The list runs from the strongest ownership to the weakest. Pick the one your evidence actually supports. A source saying you *contributed to* a project doesn't support *led*.
- **Subject**: what you did it to, such as `Linux batch processing runbooks`.
- **Scope (optional)**: extra context, such as `for the payments team`.
- **Metric value (optional)**: a number, with its unit, size and direction (for example a 20% reduction).
- **Period start** and **Period end (optional)**: when it happened, as `YYYY` or `YYYY-MM`. Give both or neither.

BonaFides turns these into one English sentence and shows it under **Rendering (English)**. For example, the verb *Maintained* with the subject `Linux batch processing runbooks` becomes "Maintained Linux batch processing runbooks".

### The one rule that matters most

**A single-statement entry must appear as a whole phrase in its pinned supporting source and excerpt.** The source excerpt shows BonaFides where:

- **Most kinds** (skills, education, languages and so on): copy a genuine passage from the selected source revision into **Source excerpt**. The passage and the full source must both contain the complete **Entry statement** as a whole phrase. The excerpt may include surrounding words. For example, `SQL` is supported by `Used SQL for reporting`, but not by `PostgreSQL` alone: part of a longer word is not a whole phrase.
- **Work experience and Projects**: copy a passage word for word from the source text. It must contain the whole sentence shown under **Rendering (English)**.

At least one supporting link must use **Direct source (owner supplied)** or **Owner attestation (unverified)**. **Context only** never establishes support.

An unsupported single-statement entry is refused when you select **Save entry revision** or approve it in a résumé import. The error names the entry, and a refusal does not replace your saved profile.

Unedited Work experience and Projects imports are checked statement by statement. Excluded contact details between source statements do not prevent importing the supported statements.

If you're backing a fact with your own word, write an owner-attestation source that includes the wording. For the entry "Maintained Linux batch processing runbooks", a source reading "Maintained Linux batch processing runbooks for the operations team, 2020–2023." works. A source reading "I looked after our Linux runbooks" doesn't, even though it means something similar.

### An older entry needs attention

An older unsupported single-statement entry shows **Needs attention: this statement is not present as a whole phrase in a pinned supporting source and excerpt.** The warning also says **The original saved entry is unchanged.** Opening the profile does not rewrite or delete it.

Use **Edit … entry *N*** to correct the statement or choose a supporting **Evidence revision** and genuine **Source excerpt**, then select **Save entry revision**. Merely editing a source creates a new source revision; it does not update the entry's existing pin. Archiving a source does not repair that pin either.

This warning differs from **Needs attention: this statement is still free text. Edit the entry to restructure it as a claim.** That separate message applies to older Work experience or Projects entries that need structured fields, not to every single-statement entry.

A new base résumé may leave out unsupported single-statement entries and names each one under **Entries omitted from this saved revision**. A build with no usable entries is refused and names the unsupported entries instead of creating an empty résumé. Tailored generation refuses unsupported entries rather than silently leaving them out. After saving a supported entry revision, choose the updated **Saved profile revision** and select **Rebuild base from selected profile** to update the document; existing document words and omission lists do not change by themselves.

## Change, reorder or archive entries

Each saved entry has its own buttons:

- **Edit … entry *N***: change it. Saving creates a new revision; the old one stays in history.
- **Move … entry *N* up** and **Move … entry *N* down**: change the order.
- **Archive … entry *N***: remove it from your active profile. Select **Confirm archive** to go ahead, or **Keep active record** to cancel. Archived entries stay in your history and can be brought back by restoring an earlier profile revision.

Changing your profile doesn't rewrite documents you've already created. Those keep the exact wording and sources they were built from, and show that newer profile facts exist.

## Prepare for interview questions

Under a saved entry, select **Interview preparation for skills entry 1** (the button names the kind and entry number). **Source-specific interview questions** asks about your contribution, the limits of the evidence and how an outcome was measured. Each group quotes that entry's own saved source excerpt and shows its source reference, privacy, exact revisions and content hash. An owner assertion is labelled **Owner-asserted support; not independently verified.**

This is model-free preparation: no AI setup is needed, no answers or qualifications are invented, and nothing is saved or sent to a model. Select the same button to close the questions. Save or discard open edits before opening preparation; it uses saved entries, not unsaved wording.

Public and Private supporting sources are eligible. Confidential and employer-owned sources never supply interview questions. A mixed entry uses only its eligible sources; Context only links do not count. **Interview preparation unavailable** explains missing support or an unsupported older entry instead of supplying unrelated questions.

Editing a source does not update an entry's pin. If preparation says **Review and explicitly re-link the entry**, use **Edit … entry *N*** to choose the intended **Evidence revision** and genuine **Source excerpt**, then **Save entry revision**. Loading a changed profile, including a classification change or restore, closes the old questions. Reopen them to inspect the current source state; restoring history does not undo current privacy classifications.

## See where an entry is used

Select **Show where … is used** under an entry to list the documents that state it. Once the list appears, select **Include earlier document revisions** to include older versions too. Only statements that passed the evidence check count; blocked statements aren't listed.

### Read application outcomes

The same **Used in** panel includes **Application outcomes**. Each application attempt counts once, even when both its résumé and cover letter use the entry. Archived applications and attempts still in preparation are included. Base résumés and earlier document revisions do not add to this count; selecting **Include earlier document revisions** changes the document list, not the outcome totals.

The figures use the entry's checked links in current saved document revisions. An older entry revision still counts when a current document pins it. Editing the entry does not rewrite those pins. Saving or restoring a document can change which entries it currently uses.

Stage figures describe the furthest progress in your recorded history, not just the application's current stage. Moving an application back to preparation does not erase an earlier interview; correcting a stage event can change the figures. A rejection without recorded positive progress is counted as inferred Applied progress and identified separately. Withdrawal alone does not imply an application was submitted. The calculation uses all recorded events, including events with future effective dates.

Each reach rate shows how many attempts reached at least that stage out of the full application count, including preparing attempts. It is not a response rate. A zero application count gives unavailable rates rather than 0%. Fewer than five attempts triggers a small-sample warning; even larger samples do not prove that an entry caused an outcome. These are your manual records, not verified employer responses or delivery receipts.

After changing documents or tracking stages, select **Refresh where … is used** to read the current figures. Switching screens does not refresh a previously loaded panel. Reading outcomes does not save changes, alter tracking or call a model.

## Look back or restore an earlier version

BonaFides keeps every saved version of your profile.

1. Choose **History**, then select **Load profile history**.
2. Select a version to preview it. Previewing changes nothing.
3. To bring it back, tick **Restore this exact saved profile and source state as a new revision; retain displaced history**, then select **Restore as new profile revision**.

Restoring saves the old version as a *new* revision. Nothing in between is deleted. It restores that version's source pins, not the newest source text, and does not silently repair unsupported entries or rebuild documents.

## If you have BonaFides open in two tabs

You're the only user, but you might have BonaFides open in two browser tabs. If you try to save after the other tab saved a newer version, BonaFides keeps your text and asks you to compare:

- **Compare current revision** shows what's saved now.
- **Keep my text against this version** keeps your edits and lets you save them on top of the newer version.
- **Replace editor with current revision** drops your edits and loads the saved version.

Entries and sources work the same way, with **Load latest profile for comparison** and **Use latest version and retain my editor**.
