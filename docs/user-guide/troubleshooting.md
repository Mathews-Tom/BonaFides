# Troubleshooting

BonaFides explains what went wrong rather than guessing. Most refused commands stop before changing anything, so you can fix the cause and run them again. The exception is a start that fails while services are already starting (`installation is not healthy` or `services did not become healthy`): run `sh stop.sh` before trying again, as the table below says.

## Installing

Messages from `sh install.sh` start with `Setup refused:` or `install refused:`.

| Message contains | What to do |
|---|---|
| `install Docker Desktop (macOS) or Docker Engine…` | Docker isn't installed. Install it (see [What you need](getting-started.md#what-you-need)), start it, and try again. |
| `start your Docker runtime…` | Docker is installed but not running. Open Docker Desktop, wait until it's running, and try again. |
| `Docker Compose v2 is required` or `Docker Buildx is required` | Your Docker is missing a component. Update Docker Desktop, or on Linux install the Compose and Buildx plugins. |
| `Git is required for a source build` | Install Git (see [Step 2](getting-started.md#step-2-check-that-git-is-installed)). |
| `run this entrypoint from a complete source checkout` | Run the command from inside the downloaded `BonaFides` folder (`cd BonaFides`). |
| `port 8000 is occupied` | Another program is using that port. Choose another: `sh install.sh --port 8001`. |
| `target is running; use its stop.sh before installing` | Run `sh stop.sh` first. |
| `existing installation requires its saved port` or `…its saved project` | You can't change an existing installation's port or name. Leave out `--port` and `--project`, or set up a [second installation](manage-installation.md#run-a-second-separate-installation) instead. |
| `project … belongs to another installation` | Another installation already uses that name. Choose a different `--project`. |
| `unsupported Docker server platform` | Your computer's processor type isn't supported. |

## Starting

Messages from `sh start.sh` start with `start refused:`.

| Message contains | What to do |
|---|---|
| `Docker is not available` | Open Docker Desktop, wait until it's running, and try again. |
| `no completed managed installation found` | BonaFides isn't installed in that folder. Run `sh install.sh` first, or add `--dir` with the right folder. |
| `installation is partially running` | Run `sh stop.sh`, then `sh start.sh`. |
| `installation is not healthy` or `services did not become healthy` | Run `sh stop.sh`, then `sh start.sh`. If it happens again, see [Getting help](#getting-help). |
| `saved application image is not loaded` | The software image was removed from Docker. Run `sh install.sh --build` from the downloaded folder, then `sh start.sh`. Your data is unaffected. |

## In the browser

**The page doesn't load.** Check that BonaFides is running (`sh start.sh` tells you if it is) and that you're using the exact address it printed. Default installations use `http://localhost:8000`; `127.0.0.1` or a computer name is not interchangeable. An explicitly configured HTTPS installation must use its saved origin through its operator-managed TLS proxy. Default loopback installations cannot be reached from another computer or phone.

**"Installation unavailable".** BonaFides isn't reachable. Start it, then select **Check installation again**.

**The setup code is rejected.** Codes expire 15 minutes after BonaFides starts and work only once. Run `sh start.sh` again to renew the window, then copy the whole code from `cat "$HOME/bonafides/secrets/bootstrap_token"`. Once your account exists, sign in with your password instead.

**You forgot your password.** See [Forgotten your password](account-and-settings.md#forgotten-your-password).

**You keep being signed out, or sign-in doesn't stick.** Use a Chromium-based browser such as Google Chrome or Microsoft Edge and the exact saved origin: `localhost` for a default installation, or the configured HTTPS address.

## Using the workspace

**A button is greyed out.** Another page usually has unsaved changes or an unfinished action. Look for the dot in the menu and the "Unsaved changes are retained." banner. See [Why is a button greyed out?](account-and-settings.md#why-is-a-button-greyed-out)

**I can't add a profile entry.** Every entry needs a source. Add one under **Profile → Sources** first. See [Add a source](profile.md#add-a-source).

**Saving or importing `SQL` is refused, but my source says `PostgreSQL`.** `SQL` inside a longer word is not a whole phrase. The refusal names the entry and leaves the saved profile unchanged. Use wording actually supported by the source, or supply a genuine source containing the complete statement. In the entry editor, choose a supporting **Evidence revision**, use **Direct source (owner supplied)** or **Owner attestation (unverified)**, and copy a genuine **Source excerpt** containing the whole statement. **Context only** cannot support it. Select **Save entry revision**; for an import, correct the reviewed entry before approving again.

**An older entry says "Needs attention: this statement is not present as a whole phrase in a pinned supporting source and excerpt."** The original saved entry is unchanged. Use **Edit … entry *N*** to correct its statement or source link, then **Save entry revision**. Editing a source alone creates a new source revision and does not change the entry's pin; choose the intended **Evidence revision** explicitly. Archiving or restoring history does not silently repair unsupported entries. The separate warning **Needs attention: this statement is still free text. Edit the entry to restructure it as a claim.** concerns older Work experience or Projects entries, not all single-statement entries.


**My document can't be approved or exported.** It has unresolved issues, listed under "Export blocked". See [Check every statement](documents.md#check-every-statement).

**An entry is missing from my base résumé.** Check **Entries omitted from this saved revision** for the exact entry statement and **Claim revision** identifier. Unsupported older single-statement entries may be omitted while supported entries remain. Fix and save the entry's pinned support on **Profile**, select the updated **Saved profile revision** on **Documents**, then choose **Rebuild base from selected profile**. The saved omission warning persists until an explicit rebuild creates a different revision; source edits do not change saved document words, pins or omission lists. Historical previews show their own omissions, and restoring one retains its historical pins and omissions. Older saved documents may have no omission list.

**The base build is refused and names entries.** None of the selected input is usable, so no empty résumé is created and any existing document is unchanged. Save a supported entry revision and explicitly select its updated **Saved profile revision** before rebuilding. Tailored generation also names unsupported entries but refuses the request instead of silently omitting them.

**My job isn't listed when I try to track it.** Only saved postings can be tracked. Open the posting under **Opportunities** and select **Save opportunity**.

**Tailored documents aren't available.** You need an AI model set up, reviewed requirements for the posting, and a tracked application. See [Before you start](documents.md#before-you-start).

**The PDF won't render because of unsupported text.** The document contains characters the built-in fonts can't print, such as emoji. Remove them, save, approve and try again.

**"A newer revision exists".** Something was saved in another tab. Your text is kept; compare with the saved version before saving again.

**"The server could not be reached".** BonaFides stopped or restarted. Your text is kept on screen. Start BonaFides if needed, then use the **Installation connection** panel to reconnect. See [If the connection drops](account-and-settings.md#if-the-connection-drops-or-your-sign-in-expires).

## Getting help

If a problem persists, the recent log of the BonaFides service can help someone diagnose it:

```sh
BONAFIDES_IMAGE="$(cat "$HOME/bonafides/.installer-image")" \
  docker compose --project-directory "$HOME/bonafides" \
  --env-file "$HOME/bonafides/.env" -f "$HOME/bonafides/compose.yaml" \
  logs --tail 100 migrate api worker
```

If `sh start.sh` says `services did not become healthy` and this log contains `pre_baseline_database`, your data was created by a development version from before release 0.1.0 and can't be upgraded directly. Follow the steps in the [changelog](../../CHANGELOG.md) under 0.1.0.

Report problems on the project's [GitHub issues page](https://github.com/Mathews-Tom/BonaFides/issues). Before you share anything, check that it contains no personal details, and **never share the setup code, your password or anything from the `secrets` folder**. To report a security problem, follow the [security policy](../../SECURITY.md) instead of opening a public issue.
