# Getting started

This page takes you from nothing to a signed-in BonaFides workspace. You'll type a few commands into a terminal; each one is shown in full, so you can copy and paste it.

## What you need

- **A Mac or a Linux computer.** BonaFides is developed and tested mainly on Macs with Apple silicon (M1 or later). Windows isn't supported.
- **Docker.** BonaFides runs inside Docker, a free tool that keeps it in its own sealed-off space on your computer. On a Mac, install [Docker Desktop](https://www.docker.com/products/docker-desktop/). On Linux, install [Docker Engine](https://docs.docker.com/engine/install/) with the Compose and Buildx plugins.
- **Git**, which downloads BonaFides and tells the installer which version you have.
- **A web browser.** BonaFides is tested with Chromium-based browsers such as Google Chrome and Microsoft Edge.

You don't need Python, Node or any other programming tools.

### Opening a terminal

- **Mac:** press <kbd>Cmd</kbd>+<kbd>Space</kbd>, type `Terminal` and press <kbd>Return</kbd>.
- **Linux:** open your distribution's Terminal app.

To run a command, paste it into the terminal and press <kbd>Return</kbd>.

## Step 1: Start Docker

Open Docker Desktop (or start the Docker service on Linux) and wait until it says it's running. Docker has to be running every time you install or start BonaFides.

## Step 2: Check that Git is installed

```sh
git --version
```

If this prints a version number, you're ready. On a Mac, if a window asks to install the "command line developer tools", accept it, wait for it to finish, then run the command again.

## Step 3: Download BonaFides

```sh
git clone https://github.com/Mathews-Tom/BonaFides.git
cd BonaFides
```

This creates a `BonaFides` folder in your current folder (your home folder, if you just opened the terminal) and moves you into it. Run the remaining commands from inside that folder.

## Step 4: Install

```sh
sh install.sh
```

The first installation builds BonaFides on your computer. Docker downloads the building blocks it needs, so this can take a while. When it finishes, you'll see:

```text
Installation prepared; the application is not started.
```

Installing only prepares BonaFides; it doesn't start it. It creates a folder called `bonafides` in your home folder (`~/bonafides`) for its settings and private keys, and sets it up to be reached at `http://localhost:8000`.

> **Keep the `~/bonafides/secrets` folder private.** It holds the keys that protect your data. Don't share it, upload it or delete it. See [Your data and privacy](your-data.md#the-secrets-folder).

If port 8000 is already used by another program, installation stops and tells you so. Choose another port, for example 8001:

```sh
sh install.sh --port 8001
```

## Step 5: Start BonaFides

```sh
sh start.sh
```

Starting can take a few minutes. When BonaFides is ready, you'll see something like:

```text
BonaFides is ready. Sign in: http://localhost:8000
Secrets (keep private; escrow the keyring separately): /Users/you/bonafides/secrets
First-time owner setup (only while no owner exists):
  Read the setup token in your private terminal: cat '/Users/you/bonafides/secrets/bootstrap_token'
  The setup token is valid for 15 minutes. Choose your own login and password when signing up.
  ...
```

Add `--open-browser` (`sh start.sh --open-browser`) if you'd like BonaFides to open the page in your browser for you.

## Step 6: Create your account

Each installation has exactly one account, and it's yours. Creating it needs a one-time setup code that only someone with access to your computer can read.

1. Show the setup code by running the command that `start.sh` printed. For the default installation, it's:

   ```sh
   cat "$HOME/bonafides/secrets/bootstrap_token"
   ```

   Treat this code like a password: don't paste it into chats, screenshots or support requests.
2. Open the address `start.sh` printed in your browser: **http://localhost:8000** for the default installation, or `http://localhost:8001` if you installed with `--port 8001`. Use `localhost` exactly as written, not `127.0.0.1` or your computer's name.
3. On **Create your account**, fill in:
   - **One-time setup code**: the code from step 1.
   - **Login name**: any name you'll remember.
   - **Password**: between 15 and 128 characters. A short sentence you'll remember works well.
4. Select **Create account**.

The setup code expires **15 minutes** after BonaFides starts. If it expires before you create your account, run `sh start.sh` again to get a fresh window. Nothing is lost or reinstalled.

Once your account exists, the setup code can't create another one. From then on, sign in with your login name and password.

## Step 7: Find your way around

You land on **Overview**. The [quick tour](README.md#a-quick-tour) explains each page in the menu on the left.

A good order for your first session:

1. [Build your profile](profile.md), or [import a résumé you already have](resume-import.md).
2. [Create a base résumé](documents.md#create-a-base-résumé). This needs no AI.
3. [Add a job posting](opportunities.md#add-a-job-posting) you're interested in.
4. [Start tracking](applications.md#start-tracking-an-application) the application.

## Optional: explore with demo data

To see how BonaFides works before entering your own details, you can load a small made-up profile (two facts about a fictional person, with their sources). This only works **after** you've created your account and **before** you've added anything to your profile.

```sh
BONAFIDES_IMAGE="$(cat "$HOME/bonafides/.installer-image")" \
  docker compose --project-directory "$HOME/bonafides" \
  --env-file "$HOME/bonafides/.env" -f "$HOME/bonafides/compose.yaml" \
  exec api bonafides demo load
```

If you installed BonaFides somewhere other than `~/bonafides`, replace `$HOME/bonafides` with your folder each time it appears. Running the command again does nothing. It refuses, without changing anything, if your profile already holds your own data or if you've edited the demo data.

When you've finished exploring, clear the demo data with **Settings → Privacy, backup and portable data → Prepare permanent deletion → All saved workspace data** (see [Your data and privacy](your-data.md#delete-data)), then start your own profile.

## Day to day

BonaFides keeps running in the background until you stop it, quit Docker or shut down your computer. If the page doesn't load after a restart, open Docker and run `sh start.sh` again.

```sh
sh stop.sh     # stop BonaFides; all your data stays
sh start.sh    # start it again
```

You don't need the downloaded `BonaFides` folder for this. The installation keeps its own copies of these commands:

```sh
sh "$HOME/bonafides/start.sh"
sh "$HOME/bonafides/stop.sh"
```

[Manage your installation](manage-installation.md) covers updating, running a second copy and uninstalling.
