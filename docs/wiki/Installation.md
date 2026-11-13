# Installation

## Supported platform

The current desktop release is developed and validated on **Arch Linux**.

The recommended installation method is the `.whl` file from GitHub Releases. Installing from a Git clone is also supported.

## Recommended: install the release wheel

### 1. Install system packages

```bash
sudo pacman -S --needed python python-pip libglvnd libxkbcommon libxkbcommon-x11 libxcb fontconfig
```

### 2. Download the wheel

Open the [GitHub Releases page](https://github.com/newnetmp3/Bi-Weekly-Bills/releases), choose the latest release, and download the file ending in:

```text
-py3-none-any.whl
```

For v1.0.0:

```text
bi_weekly_bills-1.0.0-py3-none-any.whl
```

It will normally be saved in `~/Downloads`.

### 3. Create the app environment

```bash
mkdir -p ~/.local/opt/bi-weekly-bills
python -m venv ~/.local/opt/bi-weekly-bills
~/.local/opt/bi-weekly-bills/bin/python -m pip install --upgrade pip
```

### 4. Install Bi-Weekly Bills

For v1.0.0:

```bash
~/.local/opt/bi-weekly-bills/bin/python -m pip install ~/Downloads/bi_weekly_bills-1.0.0-py3-none-any.whl
```

Use the filename of the release you actually downloaded if it is newer.

### 5. Run it

```bash
~/.local/opt/bi-weekly-bills/bin/biweekly-bills-app
```

The first launch installs or refreshes the current user's desktop launcher and icon. After that, **Bi-Weekly Bills** should be available from the desktop application menu.

## Alternate: install from a Git clone

If you prefer to install directly from the repository:

```bash
sudo pacman -S --needed git python python-pip libglvnd libxkbcommon libxkbcommon-x11 libxcb fontconfig

git clone https://github.com/newnetmp3/Bi-Weekly-Bills.git
cd Bi-Weekly-Bills

python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/biweekly-bills-app
```

Normal users do not need any Git workflow beyond the initial `git clone`. There is no need to commit, push, create branches, or otherwise manage Git to use the application.

## Updating (keeps your bank connection)

Updating never requires signing in to your bank again. Your saved bank connection, Plaid API keys, sync state, and bills database all live in your home folder, not in the app's install folder.

Close Bi-Weekly Bills, then run:

```bash
curl -fsSLo /tmp/update-bi-weekly-bills.sh https://raw.githubusercontent.com/newnetmp3/Bi-Weekly-Bills/main/scripts/update-bi-weekly-bills.sh
```

```bash
bash /tmp/update-bi-weekly-bills.sh
```

The script:

1. notes which bank connection you have now
2. makes a verified backup of your bills database and a private snapshot of `~/.config/bi-weekly-bills`
3. installs the latest release into `~/.local/opt/bi-weekly-bills`, rebuilding that environment automatically if an Arch Python upgrade broke it
4. confirms, without contacting your bank, that the same connection is still in place

Options:

- `--wheel ~/Downloads/bi_weekly_bills-X.Y.Z-py3-none-any.whl` installs a wheel you already downloaded
- `--source ~/Bi-Weekly-Bills` installs from a Git checkout
- `--venv DIR` uses a different app environment (for a Git-clone install, use `--venv ~/Bi-Weekly-Bills/.venv`)

If the final check reports a problem, **do not choose "Connect bank"**. Your previous settings are in the snapshot the script printed; run `~/.local/opt/bi-weekly-bills/bin/biweekly-bills doctor` and see [[Troubleshooting]].

You can run the same check yourself at any time:

```bash
~/.local/opt/bi-weekly-bills/bin/biweekly-bills verify-update
```

### Updating by hand

Installing a newer wheel into the same environment also keeps your connection:

```bash
~/.local/opt/bi-weekly-bills/bin/python -m pip install --upgrade ~/Downloads/bi_weekly_bills-X.Y.Z-py3-none-any.whl
```

Version 1.1 and newer automatically move connection state that 1.0 kept inside the install folder into `~/.config/bi-weekly-bills/state` the first time they start.

## Desktop launcher

The application automatically installs or refreshes its launcher and icon on first run.

You can refresh them manually if needed:

```bash
~/.local/opt/bi-weekly-bills/bin/biweekly-bills install-desktop
```

If you used the Git-clone installation instead, run:

```bash
.venv/bin/biweekly-bills install-desktop
```

Next: [[First-Run-and-Bank-Connection]].
