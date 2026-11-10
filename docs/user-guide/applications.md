# Track your applications

The **Applications** page is your record of the jobs you're pursuing: where each one stands, who you've spoken to, what's coming up and what you've sent. It's a notebook, not a messenger: **BonaFides never sends anything.** You apply through the employer's own website or email; marking an application as "Applied" is a note for you, not proof of delivery.

## Start tracking an application

You can only track a posting you've **saved**. See [Decide what to pursue](opportunities.md#decide-what-to-pursue).

1. Open **Applications** and expand **Track a saved posting manually**.
2. Select **Load saved postings for tracking**.
3. Choose the job under **Saved posting to track**.
4. Select **Track selected posting manually**.

The application starts at the **Preparing** stage. If you've tracked this posting before, BonaFides opens your latest attempt instead of creating a duplicate.

## Stages

An application moves through these stages: **Preparing**, **Applied**, **Screening**, **Interview**, **Offer**, **Accepted**, **Rejected** and **Withdrawn**. Rejected and Withdrawn close the application.

To change the stage:

1. Select the application in the **Application list**.
2. Select **Change stage or reopen same attempt**.
3. Choose the **Recorded stage**, set **Effective date and time (UTC)** to when it actually happened, and optionally add a **Stage reason**.
4. Tick **I confirm this exact manual tracking action; it sends nothing**.
5. Select **Save manual tracking change**.

Every change is kept under **Append-only stage history**. If you recorded something wrongly, select **Correct event *N***; the correction is added and the original stays visible.

**Reopening or trying again?** To reopen a closed application, change its stage again; it's the same attempt. To apply to the same job again later as a fresh pursuit, select **Start a separate repeat attempt**.

## Find an application

Use **Search application title or company**, **Filter stage** and **Archive scope** (**Active only**, **Archived only** or **Active and archived**), then select **Apply application filters**.

## Use the stage board and undo a change

Tick **Show independently paginated stage board**. Each of the eight stages loads separately; use **Load more … applications** within a column. The board uses the applied search and archive scope, but always shows all stages; **Filter stage** applies to the list. Loaded counts are not your complete totals.

Select **Change stage for …** on a board entry to open the same stage editor and exact manual confirmation described above. Keyboard users can Tab to the action and press Enter or Space; dragging never changes a saved stage.

To reverse the latest ordinary stage change, open its application and select **Undo latest stage change**, tick the exact manual confirmation and select **Save manual tracking change**. Undo appends a correction: it restores the previous stage and recalculates historical progress, without removing history. **It does not reinstate cancelled interviews or follow-ups.** It sends or recalls nothing.

Undo stays bound to that event and the version you inspected. A later change refuses it rather than overwriting newer work. Discard the tracking buffer, refresh and inspect the latest history before choosing another action; an undo buffer cannot be rebased. Older history remains correctable through **Correct event …**.

If undo would return to Rejected or Withdrawn while any work is scheduled, it is refused without changing that work. Discard the buffer and use **Change stage or reopen same attempt** to review cancellation and which follow-ups to retain. Completed and cancelled appointments do not block undo.


## Notes, contacts and files

With an application selected:

- **Private notes**: select **Add private note** and write in **Private note text**. Notes are never sent to an AI model.
- **Contacts**: select **Add contact** and fill in **Contact name**, and optionally **Contact email (optional)** and **Contact role (optional)**.
- **Inert attachments**: select **Add inert attachment** and choose an **Attachment file**: a PDF or plain-text file of up to 10 MiB, such as the exact version of the cover letter you sent. Programs and compressed archives are refused. BonaFides stores attachments without opening or previewing them; only download one if you trust where it came from.

Each change asks you to tick the confirmation and select **Save manual tracking change**.

## Follow-ups and interviews

Under **Follow-ups and local interviews**:

1. Select **Add local follow-up** (for a reminder such as "chase recruiter") or **Schedule local interview**.
2. Fill in **Local action title**, **Local start date and time** and, for interviews, **Local end date and time**. Set **Original IANA timezone** to the time zone the time is in, for example `Europe/London` or `America/New_York`.
3. Confirm and save.

Later, mark each one **Complete action …** or **Cancel action …**. Nothing is added to an outside calendar.

You can't schedule anything on a Rejected or Withdrawn application. Reopen it first.

## View your local calendar

**Local application calendar** shows interviews and follow-ups from active applications without connecting an outside calendar. Reports and calendar load when you first open **Applications**, then keep their chosen view while you move between workspace pages. Completed and cancelled entries remain labelled; earlier appointments say **Passed**.

Choose **Calendar month**, **Previous month**, **Next month** or **Today**. Use **Calendar view** to switch between **Month** and **Day**, then select a day. With a day focused, Left/Right moves one day and Up/Down moves one week, including across month boundaries.

Enter an IANA zone under **Calendar IANA timezone**, then select **Apply calendar timezone**. For example, `America/New_York` changes which local day/month an appointment appears in, not its saved instant or original scheduling intent. Select **Open … at …** to open its application.

Use **Load more month appointments** when offered, including before concluding a selected day is empty. **Refresh calendar** retries an unavailable view; old rows remain explicitly stale, not a successful empty result. No calendar action sends invitations.


## What needs your attention

Expand **Attention: due actions and upcoming interviews** and select **Refresh attention list** to see follow-ups that are due and interviews in the next seven days. The **Overview** page shows the same information. Archived applications aren't included.

The **What's next** list under **Pipeline insights** also brings together local due follow-ups, interviews, offers, unfinished Preparing work, quiet Applied/Screening applications and interviews needing a date or outcome update. Each application appears once, archived attempts stay out and changing the creation cohort does not hide present-day work. It shows up to 50 applications and says when more remain; use the application list for the rest.

Choose **Quiet application threshold** to change the number of quiet days. Quiet time uses the latest valid stage's effective date, not when you typed it; without stage history it uses the last application update. Reporting and quiet preferences are saved in this browser, not in your professional records.

## Historical funnel and weekly activity

Under **Pipeline insights**, choose **Creation cohort**: **All time**, **Created in last 30 days** or **Created in last 90 days**. The cohort selects when attempts were created, not when a response arrived. Archived attempts are included and repeat attempts count separately.

**Historical funnel** shows attempts that ever reached each positive stage, with raw reached/cohort counts and rates. It is not the adjacent **Current stages in this creation cohort**. Moving backwards does not erase earlier progress; corrections and undo can reduce it. Direct rejection implies Applied only and is named explicitly. No cohort means an unavailable rate, not zero percent.

Expand **52-week activity (UTC)** for separate **Created** and **Recorded Applied** counts in Monday-start UTC weeks. Created uses creation time; Recorded Applied uses the first valid positive-stage or rejection effective date. The 52-week series is independent of the creation-cohort filter. These are manual tracking records, never proof of delivery or verified employer replies. **Refresh pipeline insights** reloads corrected history; failed refreshes are unavailable/stale rather than zero activity.


## Counts over time

Expand **All-owner counts and period activity** to see how many applications you have at each stage. Enter **Period start date (UTC, inclusive)** and **Period end date (UTC, exclusive)**, then select **Refresh all-owner counts** to see what happened in that period.

## Archive an application

Select **Archive attempt** to move a finished application out of your active list. Its stage and history don't change. **Reactivate archived attempt** brings it back.

To delete an application permanently, see [Your data and privacy](your-data.md#delete-data).

## Which entries did I claim in this application?

With an application selected, select **Show claims used** under **Claims used** to list the profile entries its résumé and cover letter state. This helps you remember exactly what you told each employer, for example before an interview. On the Profile page, **Show where … is used** does the reverse: it lists every document that states one entry.
