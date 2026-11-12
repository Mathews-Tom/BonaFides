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

## Check health and release notices

Open **Settings → Operations**. **Refresh operations health** reports the application version, database schema version and database readiness. It does not test PDF rendering, a model server, mail delivery or your public TLS proxy.

Release checks start disabled. Select **Enable release checks** to allow the installation to request official GitHub release metadata. GitHub receives ordinary network metadata, including the server's public IP address; BonaFides sends no profile, credential or installation identifier. Automatic checks run when Settings first opens, at most once per UTC day; **Check for updates** starts an explicit check. Nothing is downloaded or installed.

**Dismiss update** hides the notice for that exact release, not later releases. A failed check leaves the last recognized release visible and identifies it as retained information, not a fresh result. “No published stable release” does not mean your development checkout is a released version. Disabling checks prevents future requests; it cannot recall a request already transmitted.

Update preferences belong to this installation, not to career-data exports. An isolated native recovery disables release checks before reopening access; enable them again deliberately.

## Put a server behind HTTPS

This is an operator task: provide a domain, certificates and an existing TLS proxy first. Default installations stay local to their computer. Select the exact public origin when preparing a fresh installation, configure the matching proxy template, then start:

```sh
sh install.sh --origin https://career.example
# Configure your existing proxy using the template below.
sh start.sh
```

For a container-based proxy, also pass `--proxy-network NAME` to `install.sh`. The network must already exist on the same Docker node and allow standalone containers to attach. Only the credential-free ingress joins it; API, worker, database, renderer and parser remain private. The installer saves its immutable Docker ID and refuses a replacement network with the same name. It never creates or removes the external network. Host publication remains loopback.

| Platform | Template and operator action |
|---|---|
| Ubuntu | Customize [`ubuntu.conf`](../../deploy/operations/ubuntu.conf) with your domain, certificate paths and selected loopback port. Install it as `/etc/nginx/conf.d/bonafides.conf`; run `sudo nginx -t && sudo systemctl reload nginx`. |
| Coolify | Verify the existing proxy network before selecting `--proxy-network coolify`. Add customized [`coolify.yaml`](../../deploy/operations/coolify.yaml) under **Servers → your server → Proxy → Dynamic Configurations**. Match the proxy's actual `http`/`https` entrypoints and certificate resolver. |
| Dokploy | Verify the existing proxy network before selecting `--proxy-network dokploy-network`. Put customized [`dokploy.yaml`](../../deploy/operations/dokploy.yaml) in the watched dynamic directory through **Traefik File System**. Match the actual `web`/`websecure` entrypoints and certificate resolver. |
| CapRover | On the same Docker node as the proxy, inspect `docker network inspect --format '{{.Id}} {{.Attachable}}' captain-overlay-network`; use `--proxy-network captain-overlay-network` only if attachable. Install customized [`caprover.conf`](../../deploy/operations/caprover.conf) as `/captain/data/nginx-shared/bonafides.conf` and place the operator-managed certificates alongside it. Preserve the existing base NGINX template and add `include /nginx-shared/bonafides.conf;` inside its `http` block through server settings. |

Replace every example domain, certificate path and resolver. Container-proxy templates target `bonafides-ingress:8000`; use `NAME-ingress:8000` for `--project NAME`. Keep the custom configuration persistent through platform regeneration. These adapters use an existing proxy, not another BonaFides installation or a dummy platform application. A non-attachable overlay requires a separately managed attachable proxy network; the installer never changes the platform network.

Use the printed HTTPS address exactly. Stop before reinstalling. Repeating `sh install.sh` reuses the saved origin, network, account, data and credentials. A conflicting origin or network is refused; do not edit `.env` to retarget an installation.

Platform dashboards, public DNS and certificate provisioning require operator validation. Controlled local TLS verification does not mean those consoles were activated.


## Update to a newer version

Updating replaces the BonaFides software and keeps your data, account and password.

1. **Export your data first**, as a safety copy of your records: see [Export your data](your-data.md#export-your-data). An export isn't a full backup and can't undo an update. If the [changelog](../../CHANGELOG.md) says the new version changes how data is stored, also make an [encrypted installation backup](../development/README.md#encrypted-installation-backup-and-recovery) before updating; that's currently a technical task.
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

Any changes the new version needs to make to stored data happen automatically while BonaFides starts. They can't be undone by going back to the older version.

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
