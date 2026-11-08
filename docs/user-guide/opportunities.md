# Find and review jobs

The **Opportunities** page keeps the job postings you're considering, your decision on each, and what each job asks for. Nothing on this page applies for a job, contacts an employer or visits a website unless you ask it to.

The page has four sections, chosen with the buttons along the top:

- **Posting catalog**: every posting you've kept, with search and filters.
- **Add a manual posting**: paste in a job advert.
- **Manage job sources**: optionally read job boards that use Greenhouse.
- **Refresh tasks**: progress of any job-board reads.

If you switch sections with unsaved work, BonaFides keeps it and tells you which section it's in.

## Add a job posting

1. Choose **Add a manual posting**.
2. Fill in:
   - **Job title** (required).
   - **Employer** (required).
   - **Location** (optional).
   - **Complete original posting text** (required): paste the whole advert. Plain text only, up to 200,000 characters.
   - **Optional HTTPS reference URL**: the advert's web address, for your own reference.
3. Select **Save manual posting**.

BonaFides never opens the reference URL. It stores it so you can open it yourself later. Saving a posting doesn't start any AI request or application.

## Browse and search your postings

Choose **Posting catalog**. Postings are listed newest first. To narrow the list, use:

- **Search posting text**: searches the title, employer, location and full text.
- **Filter location**: matches the exact words you type. "Remote" won't find postings that only say "work from anywhere".
- **Filter source**: **All sources**, manual entries, or a particular job board.
- **Posting status**: open, closed or unknown, as reported by the job board.
- **Catalog disposition**: your decision (see below).
- **Observed since (UTC)**: postings seen on or after a date.

Select **Apply catalog filters**, or **Clear catalog filters** to show everything. If there are more results, select **Load more postings**.

To open a posting, select its **Read …** button in the list, for example **Read Site Reliability Engineer at Example Corp**.

## Decide what to pursue

Each open posting shows **Your disposition**, your private decision about it:

- **Save opportunity**: you're interested. **Saved postings are the ones you can track as applications.**
- **Dismiss opportunity**: not for you.
- **Archive opportunity**: put it away.
- **Unmark opportunity**: clear your decision.

Changed your mind straight away? Select **Undo disposition change**. These decisions only label the posting. They never create an application, delete text or merge records.

The **Original posting reference** link opens the advert's web page in your browser, if you choose to visit it.

### Possible duplicates

The same job can appear twice, for example once pasted by you and once from a job board. Select **Load duplicate review candidates** to see likely matches. For each one, choose **Mark … as distinct** or **Mark … as suspected duplicate**. BonaFides only records your decision; it never merges or deletes postings.

### Where a posting came from

Select **Load attributable observation history** to see when, and from which source, BonaFides recorded the posting.

## Review what the job requires

Inside each posting, the **Requirements and explainable suitability** panel lets you list what the job asks for and compare it with your profile. **You need reviewed requirements before BonaFides can write [tailored documents](documents.md#tailored-résumé-and-cover-letter) for the job.**

### List the requirements

1. Select **Review requirements manually**.
2. In **Selectable normalized source**, which shows the posting text, highlight the exact phrase for one requirement, for example "5+ years of SQL experience". (With the keyboard, hold <kbd>Shift</kbd> and use the arrow keys.)
3. Select **Add selected source requirement**. BonaFides records the exact quote and where it appears.
4. For each requirement, set:
   - **Requirement wording**: starts as the quoted phrase. Shorten it to a name if you like, such as "SQL experience".
   - **Importance**: **Required**, **Preferred** or **Unclear**.
   - Tick **This is an explicit hard constraint** for absolute conditions such as a right to work in a certain country, a location or a salary, then fill in the details.
5. Repeat for every requirement.
6. Tick **I reviewed all exact source spans, importance, constraints and explicit owner facts; save a new immutable reviewed revision**.
7. Select **Save reviewed requirements**.

Every save is kept. **Inspect immutable requirement history** shows earlier versions.

### Compare them with your profile

1. Select **Review evidence coverage manually**.
2. For each requirement, choose how well your profile covers it: **supported**, **partial**, **conflicting** or **not evidenced**, and explain why in **Explanation of evidence and gaps**.
3. Tick **I reviewed every coverage row and the actual cited content; references alone do not establish semantic support**.
4. Select **Save owner-reviewed assessment**.

The saved assessment shows **Saved evidence fit score** alongside its coverage counts and **Hard eligibility**. Each supported requirement earns 1 point, partial earns ½, and not evidenced or conflicting earns 0. All requirements have equal weight, including required and preferred ones. The total is converted to a percentage and rounded down: one supported, one partial and one not evidenced gives **50/100**. An empty review has no score.

This is a summary of your reviewed evidence, not a hiring probability or independent verification of your qualifications. A high score never overrides a hard constraint that is not met or is unknown. Open **Inspect saved rows, citations and provenance** to read the explanations and exact claim/source revisions behind the result.

**"Not evidenced" doesn't mean you lack the skill.** It means your profile doesn't yet contain an entry showing it. BonaFides never adds skills to your profile from a job posting. If you do have the skill, explicitly [save an entry with genuine supporting evidence](profile.md#add-an-entry), then return here and select **Reload profile citations**. That reload does not change the saved coverage or score. Select **Review evidence coverage manually** to start a fresh review: review every row again, set its coverage, choose its claim and source citations and explain the support or gap. Confirm the review and select **Save owner-reviewed assessment** to create the updated result.

If your saved profile, reviewed requirements or the posting's source details change after you save an assessment, it's marked as a stale historical snapshot so you know to review it again. Changing only the posting's disposition does not make it stale. Its saved rows, score and reasons remain unchanged; unsaved edits and AI proposals do not replace them. Existing documents also remain unchanged until you explicitly edit or rebuild them.

### AI suggestions (optional)

If an AI model has been set up (see [Optional AI tailoring](ai-tailoring.md)), **Request extraction proposal** asks it to suggest requirements, and **Request matching proposal** asks it to suggest coverage. Suggestions are labelled as untrusted until you review them. Load one into your review with **Load extraction proposal into review buffer** or **Load matching proposal into review buffer**, check it, and save it as above. Without a model, these buttons are unavailable; doing it by hand works just as well.

## Read job boards automatically (optional)

BonaFides can read the public job list of an employer that uses the Greenhouse job-board service. This is **off until you set it up**, and it reads only the boards you configure, only when you ask.

**Before you start:** each board has its own terms of use. Reading a board is your decision, and BonaFides records that you reviewed its terms. Being technically reachable doesn't mean you're allowed to collect from it.

1. Choose **Manage job sources**, then **Add a Greenhouse board**.
2. Fill in:
   - **Board employer**: the company name.
   - **Greenhouse board token**: the short name in the board's web address, for example `examplecorp` in `boards.greenhouse.io/examplecorp`. It can't be changed after saving; a different board needs a new source.
   - **Board-specific terms URL**: the web address of the terms you reviewed.
   - **Terms revision or dated review reference**: for example the terms' date or version.
   - **Your own maximum requests per minute**: 1 to 60. It starts at 1.
3. Read the summary, then tick **I reviewed these exact board-specific terms and policy and acknowledge permission for this configured board**.
4. To allow reads, also tick **Explicitly enable this reviewed source**.
5. Select **Save board configuration**.

To read the board, select **Refresh … board**. The read runs in the background; follow it under **Refresh tasks**. New and changed postings appear in the catalog. Postings that disappear from the board are kept, not deleted. "Prospect" posts (general expressions of interest rather than real jobs) are skipped.

Each board also has **Disable … board** (stop reads for now) and **Retire … board** (stop permanently; its history is kept).
