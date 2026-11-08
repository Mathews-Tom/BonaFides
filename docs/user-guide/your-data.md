# Your data and privacy

## Where your data is kept

Everything you enter in BonaFides stays on the computer it's installed on:

- Your profile, sources, postings, applications, notes, documents and PDFs are kept in Docker storage that belongs to your installation.
- The installation folder (`~/bonafides` unless you chose another) holds its settings and the `secrets` folder.

BonaFides has no online account, no cloud copy and no tracking. The developers can't see your data, and can't recover it for you.

## What can leave your computer

By default, nothing. Three things send data out, and each one is your choice:

| What | What is sent | When |
|---|---|---|
| **Job-board reads** | A request for one employer's public job list. None of your personal data is included. | Only for boards you've configured, reviewed and enabled, and only when you select **Refresh … board** for that employer. See [Read job boards automatically](opportunities.md#read-job-boards-automatically-optional). |
| **AI tailoring** | The posting, its requirements and the relevant profile entries, as shown in the preview. Your contact details, notes and attachments are never included. | Only if a model has been set up, only to that model, and only after you've reviewed the preview and confirmed each task. The model has to run on your own computer or private network; BonaFides refuses public internet addresses. See [Optional AI tailoring](ai-tailoring.md). |
| **Files you download** | Exported PDFs and data exports. | When you save them. After that, they're your responsibility. |

Installing and updating BonaFides also downloads software from the internet. That doesn't involve your data.

## The secrets folder

The `secrets` folder inside your installation folder holds the passwords BonaFides uses internally, the one-time setup code, and the keys that protect encrypted parts of your data. BonaFides creates them during installation and doesn't change them on its own afterwards.

- **Keep it private.** Don't email, upload or share it, and don't include it in screenshots or support requests.
- **Don't delete or edit it.** Lost keys can't be recreated, and data they protect can't be recovered without them.
- **Keep a safe copy.** The installer reminds you to "escrow the keyring". It means: store a copy of the `secrets/keyring` file somewhere safe and separate from this computer, such as an encrypted USB drive, where only you can get at it.

## Export your data

You can download all your saved data as a single ZIP file. Use it as a personal copy of your records.

1. Open **Settings** and expand **Privacy, backup and portable data**, then **Owner-data portability**.
2. Select **Create owner-data export**.
3. When the export appears under **Recent owner-data exports**, select **Download owner data**. (Select **Refresh exports and maintenance status** if it doesn't appear.)

The export contains your profile and evidence history, postings, applications, notes, document versions and the files they refer to. It doesn't include unsaved text, your password or the installation's keys.

Keep in mind:

- **The ZIP isn't encrypted.** Anyone who has it can read your data. Store it somewhere private.
- Each download link expires after 24 hours. You can have at most two unexpired exports, and only one being prepared at a time. While that limit is reached, **Create owner-data export** fails with `portable_export_limit`; wait for an export to expire, or for the one in progress to finish.
- It's a copy of your data, not a full backup of the installation (see [Installation backups](#installation-backups)).

## Delete data

You can permanently delete one application or everything in your workspace. Deletion can't be undone, so BonaFides makes you check exactly what will happen first.

1. **Confirm it's you.** In **Settings**, expand **Confirm owner identity**, enter your password and select **Confirm owner identity**. This lasts five minutes. See [Confirm owner identity](account-and-settings.md#confirm-owner-identity).
2. Expand **Privacy, backup and portable data**. Under **Replace or permanently delete saved data**, find **Prepare permanent deletion**.
3. Choose a **Deletion scope**:
   - **One saved application**: select **Load saved applications** and choose it under **Saved application**. Archived applications are included.
   - **All saved workspace data**: your whole profile, postings, applications and documents.
4. Select **Prepare deletion preview**. This doesn't delete anything yet.
5. Read the preview: what will be deleted, how many items, and which copies will remain.
6. Under **Approve this exact destructive plan**, type the phrase shown after "Type exactly:" into **Typed destructive confirmation**, and tick **I reviewed the exact scope, counts, credentials, remaining copies and server approval; I authorize this one destructive operation.**
7. Select **Approve exact plan and queue execution**.

The deletion then runs in the background. When it runs, you're signed out; sign in again. Select **Reload previews and dispatch status** to see when it's finished.

If you change your mind before approving, select **Cancel preview and clear retained bytes**. Cancelling can't undo a deletion that has already run.

Deleting removes your data from BonaFides. Exports and PDFs you downloaded, and any installation backups, are separate and aren't touched.

## Bring an export back in

The same **Replace or permanently delete saved data** section can replace everything in your workspace with the contents of an export, under **Prepare workspace replacement**. The steps are the same as for deletion: confirm your identity, choose the **Owner-data ZIP**, select **Upload ZIP and prepare replacement preview**, check the preview, then type the confirmation and approve.

Replacement has two safety requirements:

- A **verified installation backup** must exist first, so the installation can be recovered if the replacement goes wrong. Select **Check replacement readiness** to see whether one exists.
- If the export came from a **different installation**, open **Settings** in the **original installation**. Under **Owner account**, select **Copy owner account ID** beside **Owner account ID**. In the destination installation, paste it into **Expected original owner UUID (optional)** before selecting **Upload ZIP and prepare replacement preview**. Use the original account's ID, not the destination's ID or your login name. If clipboard access fails, select the ID and copy it manually. BonaFides doesn't take this from the file itself, because a file can't vouch for itself.

Finding the account ID needs no technical setup. Preparing the verified installation backup still does; see the developer guide's [owner-data portability and erasure](../development/README.md#owner-data-portability-and-erasure) section. Reaching the replacement preview does not replace your saved data; that needs your separate, exact approval.

## Installation backups

BonaFides can also make **encrypted backups of the whole installation** on a daily schedule, check that they can be restored, and restore them into a fresh installation. Setting this up means preparing encryption keys and backup locations, and running commands; it isn't available from the browser yet. The full procedure is in the developer guide under [Encrypted installation backup and recovery](../development/README.md#encrypted-installation-backup-and-recovery).

Until you have that set up, export your data regularly and keep a safe copy of your `secrets/keyring` file.

## How long things are kept

| What | Kept for |
|---|---|
| Saved profile, document and application versions | Until you delete them. Earlier versions are kept too. |
| Temporary task inputs, such as the context prepared for an AI model | Cleared by a daily cleanup once 24 hours have passed since the task finished. |
| Rendered PDFs | Until you delete them, together with their document. |
| Data exports | 24 hours, at most two at a time. |
| Your theme choice | One year, in your browser. |
