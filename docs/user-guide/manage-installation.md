# Manage your installation

This page covers looking after BonaFides once it's installed: starting and stopping, updating, running a second copy and uninstalling. Each task is a command typed into a terminal (see [Opening a terminal](getting-started.md#opening-a-terminal)).

## Where things are

| Where | What it holds |
|---|---|
| The `BonaFides` folder you downloaded | The source code and the `install.sh`, `start.sh`, `stop.sh` and `uninstall.sh` commands. Only needed for installing and updating. |
| `~/bonafides` (the installation folder) | This installation's settings, its `secrets` folder, and its own copies of `start.sh`, `stop.sh` and `uninstall.sh`. |
| Docker storage | Your data: profile, postings, applications, documents and files. It belongs to this installation and stays put when you stop, update or normally uninstall BonaFides. |

## Start and stop

From the downloaded `BonaFides` folder:

```sh
sh start.sh
sh stop.sh
```

Or from anywhere, using the installation's own copies:

```sh
sh "$HOME/bonafides/start.sh"
sh "$HOME/bonafides/stop.sh"
```

Stopping keeps all your data, settings and keys. `sh start.sh --open-browser` also opens BonaFides in your browser.

## Update to a newer version

Updating replaces the BonaFides software and keeps your data, account and password.

1. **Export your data first**, as a safety copy: see [Export your data](your-data.md#export-your-data).
2. In a terminal, go to the downloaded folder and run these commands one at a time:

   ```sh
   cd BonaFides
   sh stop.sh
   git pull
   sh install.sh --build
   sh start.sh
   ```

   `git pull` downloads the newest version of the code, and `--build` tells the installer to build it. Without `--build`, the installer keeps using the version you already had.

If `cd BonaFides` says there's no such folder, use the path to wherever you downloaded it.

Any changes the new version needs to make to stored data happen automatically while BonaFides starts.

## Run a second, separate installation

You can run more than one BonaFides, for example one for real use and one for trying things out. Each needs its own folder, port and name:

```sh
sh install.sh --dir "$HOME/bonafides-test" --port 8001 --project bonafides-test
sh start.sh --dir "$HOME/bonafides-test"
```

This one is at **http://localhost:8001**, with its own account and data. Add `--dir "$HOME/bonafides-test"` to `start.sh`, `stop.sh` and `uninstall.sh` whenever you mean it, or run the copies inside its folder (`sh "$HOME/bonafides-test/stop.sh"`). The two installations never share data.

A project name must be 3 to 48 lowercase letters, digits or hyphens, starting with a letter. An installation's port and name can't be changed after it's created.

## Uninstall

### Remove BonaFides but keep your data

```sh
sh uninstall.sh
```

You're asked to confirm. This removes the running parts of BonaFides but **keeps your data, settings and keys**. To use BonaFides again later with the same account and data, run `sh install.sh`, then `sh start.sh`.

### Remove BonaFides and permanently delete your data

> **This can't be undone.** Export anything you want to keep first.

```sh
sh uninstall.sh --purge
```

After the normal confirmation, you're asked to type the installation's name (`bonafides` unless you chose another) to confirm deleting the data, settings and keys.

### What uninstalling never removes

Neither kind of uninstall removes Docker itself, the downloaded `BonaFides` folder, files you exported or downloaded, or the software images Docker built. To remove those:

- Delete the downloaded `BonaFides` folder as you would any folder.
- In Docker Desktop, open **Images** and delete the ones whose names start with `bonafides`.
- Uninstall Docker Desktop if you no longer need it.

## Your account

Password changes and forgotten passwords are covered in [Account and settings](account-and-settings.md#forgotten-your-password).

## When something goes wrong

See [Troubleshooting](troubleshooting.md).
