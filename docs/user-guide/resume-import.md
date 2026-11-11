# Import an existing résumé

If you already have a résumé, BonaFides can read it and propose profile entries from it. You check the proposals, fix anything that was misread, and approve the result. Nothing reaches your profile until you approve it.

## What it can read

- A **Word DOCX**, **UTF-8 Markdown (`.md`)**, **PDF with selectable text** (if you can highlight words, it has selectable text), or **UTF-8 plain-text file**: up to 10 MiB and about 80,000 extracted characters (also limited to 256 KiB of UTF-8 text). PDFs are limited to 50 pages. Password-protected files aren't accepted.
- Or text you paste in directly, with the same text limit.

Extraction happens entirely on your computer, in a sealed-off part of BonaFides with no network access or AI. There's no OCR (text recognition), so a scanned résumé saved as a picture won't work. Optional model structuring happens separately, only with your explicit choice below.

Word imports read visible body paragraphs and table cells, not page layout. Headers, footers and notes are omitted with a review warning. Macros, embedded objects and unsupported content are refused; copy the text into the paste field instead. Markdown headings, lists, emphasis, link labels and code become plain source text. Links and images are never fetched, and HTML stays inert. Check the extracted reading order before accepting any facts.

Word and Markdown list markers become ordinary bullets in the extracted preview; original numbering and layout are not preserved. Blank paragraph gaps in Markdown remain separate, so a later paragraph is not silently attached to a previous job. Check numbered lists against your original document before approving.

Recognizable contact details (email addresses, phone numbers and similar) are left out of the review. Add them under [Personal details](profile.md#personal-details) yourself.

Imported entries are *your* statements. Each one keeps the résumé text it came from as its source, recorded as your own assertion, not independent verification.

## Optional local model structuring

**Use qualified local model structuring (optional)** is off by default. Leave it off for extraction with no model calls. If your operator has enabled and separately qualified this feature, you can turn it on before uploading to suggest where exact source fragments belong.

Recognizable contact details are excluded, but fragments can still contain names or sensitive text: check your source before opting in. Only those fragments and their labels go to the configured local route, never the raw file. The model cannot supply replacement facts or verify your statements; you still review and approve the assembled profile.

Unavailable, unqualified, stale or live-blocked routes fail without automatic literal fallback. **Model structuring provenance** records the route used. The choice and provenance stay with that saved import; changing the choice requires a new upload. Start a separate import with the option off to choose model-free extraction instead.

## Import step by step

The progress bar along the top shows where you are: **Choose source → Extract → Review → Complete**.

1. Open **Profile** and choose the **Résumé imports** tab.
2. Under **Choose a local source**, either pick a file under **Résumé DOCX, Markdown, PDF or text file**, or paste into **Or paste résumé text**. Use one or the other, not both.
3. Leave **Use qualified local model structuring (optional)** off, or explicitly choose it after reading the disclosure, then select **Upload for local extraction**.
4. Wait while BonaFides reads it. You can leave the page and come back; nothing has been added to your profile yet.
5. When reading finishes, **Complete profile preview** appears. Go through the proposed entries (see below), selecting **Edit** on any entry you want to open.
6. Under **Approve assembled profile**, choose an **Import mode**:
   - **Merge only selected facts; keep unrelated profile facts** (the default) adds to your existing profile.
   - **Replace all non-identity profile facts with selected facts** swaps your current entries for the imported ones. Choosing this also asks you to tick **I separately confirm replacing all non-identity facts and archiving displaced sources**.
7. Tick **I approve this assembled profile and confirm its values are accurate owner assertions, not independent verification**.
8. Select **Save profile**.

When it's done you'll see "Complete. The updated profile is loaded; earlier history is retained."

If every entry turned out to be unchanged from what's already in your profile, select **Complete import without profile changes** instead.

## Reviewing the proposed entries

Each proposal in the list shows its status:

| Status | Meaning |
|---|---|
| **New entry** | Will be added as a new entry. You don't have to open it. |
| **Ready to update** | Will replace or add evidence to an existing entry you chose. |
| **Unchanged** | Already in your profile exactly as written. Nothing will change. |
| **Needs attention** | Something needs your decision: an unclear line, an invalid date, a missing destination, or a possible match with an existing entry. |
| **Excluded** | Won't be imported. |

Inside an open entry, the same status reads **Ready for batch approval**, **Unchanged · no profile changes**, **Needs attention before saving** or **Excluded**.

Select **Next entry needing attention** to jump to the ones that need you. In each entry you can:

- Choose where it goes with **Save to**, for example **Work experience**, **Education** or **Skills**. **Personal details · Full name** and **Personal details · Highlight / tagline** update your personal details instead.
- If it resembles something already in your profile, choose under **Existing profile entry** whether to **Add a new entry; keep existing entries** or **Replace an existing entry / attach this source evidence** (then choose which one under **Replacement target**).
- Compare individual fields and choose **Keep current …** or **Use imported**.
- Fix the text if it was misread.
- Select **Combine with previous entry** or **Combine with next entry** if one item was split across lines, or **Split into original source parts** to undo a combination.
- Select **Confirm extraction** when it's right, or **Exclude entry** to leave it out.

**Save profile** becomes available once at least one entry is ready, no entry still needs attention, and you've ticked the approval box.

Lines BonaFides couldn't fully understand stay as free text marked **Needs attention**. After importing, you can turn them into structured entries on the [Profile](profile.md#writing-the-fact) page.

## Coming back to an import later

The uploaded résumé and its proposed entries are saved, so you can come back to them. To pick one up again, select **Load saved imports**, choose it under **Saved import**, then select **Open import**. **Your review choices** (destinations, edits, combinations and exclusions) **aren't saved**: they're kept only in the open tab, and reopening the import or reloading the page starts the review again. Finish a review in one sitting where you can.

To abandon an import, select **Cancel import**. Cancelling changes nothing in your profile.

You can have up to four imports in progress at once.

## If something goes wrong

| Message | What to do |
|---|---|
| "Choose a DOCX, Markdown, PDF or UTF-8 plain text file." | Save your résumé in one of those formats, or paste its text. |
| "Import a non-empty source no larger than 10 MiB…" | The file is empty or too big. Paste the text instead. |
| "No entries were extracted. Nothing can be saved…" | BonaFides found no readable text, which is usual for scanned PDFs. Cancel the import and paste the text instead. |
| "A newer profile needs comparison…" | Your profile changed while you were reviewing. Select **Load current profile for comparison**, then **Use latest profile and retain review**. Your review choices are kept. |
| "Save or discard changes in the other profile editor before uploading or approving an import." | You have unsaved changes on the **Profile details** tab. Save or discard them first. |
| "Import stopped (…). A failed import does not partially update your profile." | Nothing was changed. Try again, or paste the text instead of uploading. |
| `import_invalid_docx`, `import_unsupported_docx` or `import_encrypted_docx` | The Word file is damaged, password-protected or contains unsupported content. Open it yourself and paste its visible text instead; renaming another format to `.docx` does not convert it. |
| `import_content_limit` or `import_text_limit` | The document's extracted content exceeds a safety limit. Reduce the document or paste a shorter source; nothing is silently cut or partially saved. |
| `import_structuring_input_limit` or `model_output_limit` | The model input or answer exceeded its limit. Nothing is truncated into a successful import. Shorten the source or ask the operator about the route; a failed import adds no facts. |
