# Installation

## Supported platform

The current desktop workflow is developed and validated on **Arch Linux**. The app uses PySide6 and includes Wayland-compatible launcher/icon integration.

## Download and install

```bash
sudo pacman -S --needed python python-pip

git clone https://github.com/newnetmp3/Bi-Weekly-Bills.git
cd Bi-Weekly-Bills

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install .
```

The `git clone` command is only used to download Bi-Weekly Bills. Normal users do not need to commit, push, pull, create branches, or otherwise manage Git.

## Launch

```bash
biweekly-bills-app
```

The application installs or refreshes its current-user Wayland-compatible desktop launcher and icon automatically.

Manual refresh:

```bash
biweekly-bills install-desktop
```

Typical launcher assets:

```text
~/.local/share/applications/biweekly-bills.desktop
~/.local/share/icons/hicolor/scalable/apps/biweekly-bills.svg
```

The Qt/Wayland desktop ID is `biweekly-bills`, matching the installed desktop file and icon name.

## Release artifacts

Versioned wheel and source archives are available from GitHub Releases. The release workflow validates the project in an Arch Linux container before publishing.

Next: [[First-Run-and-Bank-Connection]].
