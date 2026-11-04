# Activity and background tasks

Some work takes longer than a click: reading a résumé, reading a job board, generating documents, rendering a PDF, or exporting your data. BonaFides runs these as **tasks** in the background, so you can keep working or close the browser. Tasks survive a restart of BonaFides.

Open **Activity & reviews** at the bottom of the menu to see them all. The **Overview** page also shows how many are active or waiting for you.

## Task states

| State | Meaning |
|---|---|
| **Pending** | Waiting to start. |
| **Running** | In progress. |
| **Waiting for approval** | Needs a decision from you before it can continue. |
| **Retryable failure** | Failed, but can be tried again. |
| **Permanent failure** | Failed and won't be retried. |
| **Cancelled** | Stopped by you. |
| **Succeeded** | Finished. |

"Succeeded" means the task ran to the end. It doesn't mean the result is factually correct: you still check every document before approving it.

## Look at a task

1. Select **Load tasks** (or **Reload tasks**). Use **Task state filter** to show only one state.
2. Select a task to see its details: when it was created and updated, how many attempts it has had, and a step-by-step log.
3. If it produced a document, select **Open résumé result**, **Open cover letter result** or **Open document result** to go to it.

While a task is open, BonaFides checks its progress automatically. Select **Pause monitoring** to stop checking; this doesn't affect the task itself.

## Cancel or retry

- **Cancel task** asks the task to stop. Anything it already finished isn't undone.
- **Request due retry** sends a failed task back for another attempt. It's shown for failures that can be retried, and becomes available once the retry is due.

## Reviews and approvals

A task in **Waiting for approval** needs a decision from you, but you don't make it on this page. **Review prepared action** shows what the task is waiting for; the decision itself happens where the work belongs:

- Résumé imports are reviewed and approved on the **Profile → Résumé imports** tab. See [Import an existing résumé](resume-import.md).
- Replacing or deleting your data is approved in **Settings**. See [Your data and privacy](your-data.md).

Every approval covers that exact action only. If anything about it changes, it needs a new approval.

If a task shows "Delivery outcome unknown — reconciliation required", BonaFides can't tell whether an action completed, so it won't repeat it automatically. Check the outcome before doing anything else.

## How long task details are kept

The temporary inputs a task works from (such as the context prepared for an AI model) can be cleared 24 hours after the task finishes, and a daily cleanup removes them. The results, such as saved document versions, are kept until you delete them.
