# Account and settings

## Sign in and sign out

Go to the exact address printed by `sh start.sh`: **http://localhost:8000** (or your chosen port) by default, or your configured HTTPS origin. Enter your **Login name** and **Password**, then select **Sign in**. Each installation has exactly one account; there's no way to add another user.

**Sign out** is in the top bar. It's unavailable while you have unsaved changes on some pages or while a task you started is still running. Save or discard your changes first.

## Light or dark theme

Use the theme switch in the top bar: **System theme** follows your computer's setting; **Light theme** and **Dark theme** are remembered in this browser for a year. The theme only changes the screen, never your documents or PDFs.

## The Settings page

| Section | What it does |
|---|---|
| **Your workspace, your authority** | A summary of what BonaFides will and won't do. |
| **Owner account** | Find your **Owner account ID** and select **Copy owner account ID** before moving an export to another installation. |
| **Operations** | Inspect version/schema/database health and optionally check official release notices. See [Check health and release notices](manage-installation.md#check-health-and-release-notices). |
| **Privacy, backup and portable data** | Export your data, move it to another installation, or delete it. See [Your data and privacy](your-data.md). |
| **Advanced diagnostic tools** | Tools for developers. You don't need them. |
| **Confirm owner identity** | Re-enter your password before a sensitive action (see below). |
| **Change password** | Change your password. |

Your **Owner account ID** is different from your login name and password. Use the ID shown in the original installation when bringing its export into another installation; see [Bring an export back in](your-data.md#bring-an-export-back-in). If the browser refuses clipboard access, select the ID and copy it manually.

## Change your password

1. In **Settings**, expand **Change password**.
2. Enter your **Current password** and a **New password** of 15 to 128 characters.
3. Select **Change password**.

You stay signed in in this browser. Other browser sessions are signed out.

## Forgotten your password

You can set a new password from the computer BonaFides runs on. Open a terminal and run:

```sh
BONAFIDES_IMAGE="$(cat "$HOME/bonafides/.installer-image")" \
  docker compose --project-directory "$HOME/bonafides" \
  --env-file "$HOME/bonafides/.env" -f "$HOME/bonafides/compose.yaml" \
  exec api bonafides recover-password
```

Type your new password when asked for `New password:`, then again at `Confirm password:`. Nothing appears on screen as you type. When it's done you'll see "Owner password recovered; existing sessions and approvals invalidated." Sign in with the new password; your login name is unchanged.

If you installed BonaFides somewhere other than `~/bonafides`, replace `$HOME/bonafides` with your folder each time it appears. BonaFides must be running.

## Confirm owner identity

Replacing or deleting data needs a recent password check, so nobody can do it from a browser you left signed in. In **Settings**, expand **Confirm owner identity**, enter your password in **Password to confirm owner identity**, and select **Confirm owner identity**. The confirmation lasts five minutes. It doesn't approve anything by itself; it lets you prepare a fresh preview, which you then approve separately.

## Unsaved changes

BonaFides keeps your unsaved text while you move between pages. A dot next to a page in the menu means it has unsaved changes, and a banner at the top, "Unsaved changes are retained.", links back to each one.

Unsaved text lives only in the open browser tab. **Closing or reloading the tab loses it**, so save as you go.

### Why is a button greyed out?

To stop two edits from overwriting each other, BonaFides locks some actions while another one is unfinished. For example, you can't build a document while you have unsaved profile changes, and you can't edit profile entries while a résumé import review has unsaved choices. Save or discard the unfinished change (the dot and banner show where it is), and the button becomes available again.

## If the connection drops or your sign-in expires

If BonaFides stops responding, or your sign-in expires, an **Installation connection** panel appears and saving is paused. **Your unsaved text stays on screen.**

- If BonaFides was stopped, start it again (`sh start.sh`), then select **Check connection and compare saved workspace**.
- If your sign-in expired, enter your password in **Password to reconnect**, choose **Sign in and retain editors**, then select **Check connection and compare saved workspace**.
- After the comparison, select **Resume writes and keep all editor text** to carry on where you were. If the saved workspace changed in the meantime, that button isn't offered; copy any text you need before going further.
- Choose **Discard all editors and load this saved workspace** only if you're sure you don't need your unsaved text. You first have to tick **I have preserved the text I need and explicitly discard all unsaved workspace editors.**

If you see "A newer revision exists. Your text remains here…", something was saved in another tab. Use the page's compare button to see the saved version before saving again.
