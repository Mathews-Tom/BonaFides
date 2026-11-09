# Create résumés and cover letters

The **Documents** page has two workspaces, chosen with the buttons at the top:

- **Base résumé**: a general résumé built from your profile. No AI needed.
- **Tracked application documents**: a résumé and cover letter written for one job you're tracking. This needs an AI model to be set up; see [Optional AI tailoring](ai-tailoring.md).

Both kinds go through the same steps: create, check, approve the exact version, then export a PDF.

## Create a base résumé

You need a profile with at least one supported entry (see [The one rule that matters most](profile.md#the-one-rule-that-matters-most)). An older single-statement entry whose complete phrase is not supported by its pinned source and excerpt may be left out; supported entries remain. If no usable entries remain, the build is refused and names the unsupported entries. It does not create an empty résumé or replace an existing document.

1. Open **Documents** and choose **Base résumé**.
2. Under **Saved profile revision**, keep the current revision (or choose an earlier one).
3. Under **Built-in presentation**, choose a layout:
   - **Evidence · English · A4** or **Evidence · English · Letter**
   - **Compact · English · A4** or **Compact · English · Letter**

   A4 is the usual paper size outside North America; Letter is the usual size in the US and Canada. All layouts are in English.
4. Select **Create base résumé**.

The résumé is made directly from your entries; no AI is involved.

Your saved résumé doesn't change when you edit your profile later. If the profile has moved on, the résumé shows that its inputs are out of date. To pick up the changes, select **Rebuild base from selected profile**.

### Entries left out of a base résumé

**Entries omitted from this saved revision** is a persistent warning above the document when a build left out unsupported single-statement entries. It lists each exact entry statement and its **Claim revision** identifier. The omission list belongs to that saved document revision, not to your current profile.

To fix an omission, open **Profile**, use **Edit … entry *N***, and save a supported new entry revision with **Save entry revision**. The whole statement must appear in both a genuine **Source excerpt** and its selected supporting **Evidence revision**. `SQL` cannot be supported by `PostgreSQL` alone. Editing a source creates a new revision but never updates an entry's existing source pin; select the intended revision explicitly when saving the entry.

Return to **Documents**, select the updated **Saved profile revision**, then choose **Rebuild base from selected profile**. Existing wording, source pins, omission lists, approvals and PDFs remain tied to their saved revisions; neither a source edit nor opening an old document rebuilds it. Documents saved before omission lists were recorded do not gain a retroactive list.


## Check every statement

Every factual statement in a document is linked to one profile entry. Under each statement you'll see **Linked claim** (the entry it comes from) and, when expanded, **Statement sources**, the evidence behind it.

BonaFides checks each statement automatically. A statement is accepted only when:

- it cites exactly one entry from your profile,
- its wording is that entry's wording, or the entry's wording cut short at a natural break, and
- the entry's wording appears in the entry's own source.

Anything that fails is listed under **Export blocked: unresolved factual or source issues**. Select an issue to jump to it. You can't approve or export the document until every issue is fixed, and **approving a document can't override an issue**.

| Message | What it means | How to fix it |
|---|---|---|
| It cites a claim that is not in your profile. | The entry was removed or never existed. | Remove the statement, or add the entry and rebuild. |
| Its claim's text is not contained in the claim's own evidence. | The entry's wording isn't in its pinned source. | On the Profile page, save a supported entry revision whose genuine source excerpt and selected source contain the statement, then explicitly rebuild. Changing source text alone does not update the entry's pin. |
| Not part of its claim: its wording is not contained in its claim's text. | The statement says something its entry doesn't. | Change the wording back, or change the entry (and its source) and rebuild. |
| Cuts into the middle of its claim | The statement uses part of an entry, but cut mid-phrase. | Use the entry's full wording or cut it at a natural break. |
| It states a fact without citing a claim. | A sentence with no entry behind it. | Remove it, or add an entry with evidence and rebuild. |
| It cites more than one claim. | One sentence mixes two entries. | Remove the statement, or rebuild or regenerate the document. |
| Its evidence is not its claim's own evidence. | The statement points to a source that belongs to another entry. | Rebuild a base résumé, or generate a tailored pair again. |
| It is not one of the declared non-factual phrases, so it counts as an unsupported fact. | Linking wording, such as a greeting, that BonaFides doesn't recognize. | Remove or simplify it. |
| It is not one of the fixed document headings. | A heading BonaFides didn't create. | Use the original heading. |
| It still contains a placeholder marker. | Something was left unfinished. | Fill it in or remove it. |
| It contains disallowed control characters. | Invisible characters, often from copy and paste. | Retype the text. |

The checker confirms that each statement matches your entries and sources. It doesn't judge whether the statement is true, or whether shortening it changed its meaning: cutting "Reduced support costs by 20% in the pilot team" to "Reduced support costs by 20%" passes, even though it drops a limit. Read every statement yourself before you approve.

## Edit a document

Change the **Document title**, any **Section heading** or any **Block text**, or select **Remove block** to drop a statement. Then select **Save document revision**. Each save creates a new revision, and the checks run again.

Editing is best for trimming and reordering. To say something new, add or change the entry in your profile, then rebuild. Text that isn't backed by an entry is blocked.

To abandon your edits, select **Discard document changes**.

## Fit a tailored résumé to one or two pages

For a new tailored résumé, choose **Initial résumé page target (optional)** before previewing and authorizing generation. The original résumé and cover letter are saved first; a separate task then tries to fit the résumé. The cover letter and your profile are unchanged.

For an existing tailored résumé, save your edits, choose **Saved résumé page target**, then select **Fit saved résumé to target**. This uses the built-in PDF renderer, not AI. It keeps wording and source links intact and, when needed, leaves out whole blocks from the end. The target is a goal, not a guarantee: even the last remaining block can exceed it. **Page fit and omitted blocks** shows the measured page count and recoverable omissions.

- Select **Restore block …** to bring back its exact saved text and source links in their original relative position, keeping surrounding edits.
- Select **Omit block …** to save a recoverable omission immediately. **Undo omit block …** reverses that last omission; the inventory also offers restoration later.
- **Remove block …** remains a separate editor deletion, committed only when you save. It does not put the block in the recoverable inventory.

Fitting, omission and restoration create saved revisions that need fresh review. Later edits or restores never cut content automatically, and they can take you above the target. A changed document has no current page measurement until you explicitly fit it again. Use **Load document history** to preview or restore earlier wording; the omission inventory also travels with owner-data exports and imports.

Unsaved edits and historical previews cannot be fitted. A failed or cancelled fit leaves the saved document and application available; an intervening save or application withdrawal prevents an old task from publishing cuts. A fit blocked by temporary renderer saturation remains retryable. Measurement PDFs are private working data, not downloads. Resolve the displayed error before requesting another fit.

## Approve the exact version

Once there are no issues:

1. Read each factual statement and its sources.
2. Tick **I checked block … against its frozen sources for this exact saved revision** for each one. Greetings and statements of intent don't need a tick.
3. Select **Accept this exact revision**. The button changes to **Exact revision reviewed**.

Your approval covers that exact saved version. If you change anything afterwards, you'll need to check and approve the new version.

## Export a PDF

1. Select **Render reviewed PDF**. This is available only for an approved version with no issues and no unsaved changes.
2. When it's ready, select **Download rendered PDF for revision *N***.

The PDF stays downloadable from that saved version until you delete the document's data. It contains your personal details, so store any copy you save carefully. Nothing is sent anywhere.

If rendering fails with a message about glyphs or text the renderer can't preserve, the document contains characters the built-in fonts can't print, for example emoji or Chinese, Japanese or Korean characters. Edit those characters out, save, approve again and retry.

The built-in renderer also compares selectable PDF text with the saved visible content. This is an internal machine-readability check, not certification by an external applicant-tracking system, a visual layout review or a factual check.

## Download all saved application PDFs together

In **Tracked application documents**, use **Download all saved application PDFs** to make one private ZIP, grouped into company folders. You do not need to select an application first. This includes every current saved tailored résumé and cover letter across all your applications, even archived applications. Base résumés, email drafts, deleted documents and earlier revisions are not included.

1. Save your local edits with **Save document revision**, or explicitly choose **Discard document changes**. The archive never includes unsaved wording or silently discards it.
2. Review and accept each current saved document you want to keep in the set. Every included document must pass the same source, factual-support and review requirements as an individual PDF. A missing partner is not invented: an application with only a saved résumé contributes that résumé.
3. Select **Prepare archive preview**. The preview counts the whole saved-document set, not the selected job. The limits are **100 documents** and **64 MiB for PDF data plus the ZIP**.
4. Select **Create all-document ZIP** to use that exact preview. The task reports recorded **completed of total** document counts, not an estimated percentage. It uses the built-in renderer without an AI call and changes no saved documents, reviews, profile entries or history.
5. After the whole task succeeds, select **Download all saved application PDFs (ZIP)**. The ready archive shows its expiry time and is available for **24 hours**. The temporary server copy is removed automatically; your downloaded copy is independent and contains personal information, so store it carefully.

This export is **all-or-nothing**. An empty set is refused. An unreviewed or otherwise blocked document, unsupported glyphs, a size limit, or a change to the document set, sources, reviews, presentation or company/job labels prevents the whole ZIP from being published. No blocked document is silently skipped and there is no partial download. Correct the named problem, select **Prepare archive preview** or **Refresh archive preview**, and explicitly create a new ZIP. Changes after success can also invalidate the download before its expiry; prepare a new archive in that case.

**Cancel document task** revokes publication of the ZIP; a task may still be stopping. If a temporary failure permits **Retry frozen archive task**, that retry keeps the original saved-document set, cumulative attempt limits and deadline. It does not refresh the preview. For an unknown enqueue outcome, **Retry exact archive request** retains the original preview and request key rather than silently creating a different request. **Reconnect document task** reloads the same task after a monitoring failure and does not replace your selected document or local edits.

Downloads are checked before the ZIP is handed to your browser. A refused or expired download shows the problem and withdraws the old download button; resolve the problem and explicitly prepare a new preview. If an uncertain create request needs authentication, sign in again. Preserve any editor text before the connection panel requires an explicit workspace reload, then return to **Tracked application documents** and select **Retry exact archive request**. The original request survives that reload for the same owner and installation; no retry is automatic. Keep this page open: recovery is only in memory, not browser storage, and signing out or changing owner or installation discards it. A definitive refusal lets you prepare a fresh preview instead.

After privacy erasure, a monitored archive task can lose its inputs or disappear. Use **Clear unavailable archive task** to stop monitoring it without replacing your selected document or local edits. This is not confirmation of server cancellation. Save or copy any local edits before reloading the current workspace.

Archive PDFs receive the same saved-text and glyph checks as individual PDFs. These are internal machine-readability checks, not external ATS certification, visual layout inspection or factual proof. The ZIP is a temporary convenience download, not a replacement for an owner-data backup. Existing individual PDF downloads remain unchanged.

## Look back or restore an earlier version

Select **Load document history**, then choose a version to preview it. Previewing changes nothing; select **Return to current saved revision** to go back. To bring an old version back, tick **Restore this historical revision as a new current revision, retaining displaced history** and select **Restore historical revision**. Nothing is deleted.

The historical preview shows that revision's own **Entries omitted from this saved revision** list, if it has one, not the current document's omissions. Restoring copies the historical wording, source pins and omission list into a new revision; it does not pick up current profile or source edits. Use **Rebuild base from selected profile** explicitly to build from an updated profile.


## Tailored résumé and cover letter

A tailored pair is a résumé and cover letter written by an AI model for one specific job. The model works from the posting, its reviewed requirements and your profile entries, and every factual sentence must come from your entries. Every sentence goes through the same checks as above.

### Before you start

You need all four:

1. **An AI model set up for this installation.** This is a one-off technical setup; see [Optional AI tailoring](ai-tailoring.md#setting-it-up). Under **Tracked application documents**, the line **Generation route: …** shows its status. Without a model it reads "Generation route: disabled"; generation is available only once the route is qualified.
2. **A saved posting.** See [Find and review jobs](opportunities.md#add-a-job-posting).
3. **Reviewed requirements for that posting.** See [Review what the job requires](opportunities.md#review-what-the-job-requires).
4. **A tracked application for it.** Generating documents never creates one; see [Track your applications](applications.md#start-tracking-an-application).

### Generate the pair

1. Open **Documents** and choose **Tracked application documents**.
2. Select **Reload tracked applications** and choose one under **Tracked application**.
3. Choose a **Writing tone**: **Professional** or **Concise**. For a new résumé, optionally choose **Initial résumé page target (optional)**; see [page fitting](#fit-a-tailored-résumé-to-one-or-two-pages).
4. Select **Preview reviewed generation context**. BonaFides shows exactly what it will send to the model, where it will send it, and what it leaves out. See [what the model receives](ai-tailoring.md#what-the-model-receives).
5. If you're happy with it, tick **I reviewed this exact projection, route, frozen inputs and privacy limitation for this one task**.
6. Select **Generate résumé and cover letter pair**.

Generation runs in the background; a status line shows progress, and you can select **Cancel document task**. When it finishes, check, approve and export each document as described above.

If generation fails, nothing is created and your existing documents are untouched. The message explains why. Unsupported profile entries are named and refuse tailored generation; they are not silently omitted as they may be in a base build. Save supported entry revisions before previewing again. If your profile, the posting or your settings changed after you previewed, preview and generate again.

### Rewrite one section

For a tailored document, you can ask the model to rewrite a single section:

1. Under **Saved section regeneration**, choose the **Section to regenerate** and a **Section writing tone**.
2. Select **Preview selected-section model projection** to see exactly what will be sent.
3. Tick the confirmation and select **Request section proposal**.
4. When the proposal is ready, select it to compare the original and the proposed section side by side.
5. To use it, tick **I compared the original and proposal and explicitly choose to apply only this section as a new draft revision** and select **Apply proposal as new draft revision**.

A proposal never replaces your text by itself. If the document has changed since you asked, the proposal can't be applied; request a new one.

## Remembered defaults

Under **Remembered defaults and retention**, select **Save remembered defaults** to remember your preferred layout and tone for next time. Defaults only fill in future choices; they never change existing documents.
