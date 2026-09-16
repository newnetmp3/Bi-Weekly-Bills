# Bi-Weekly Bills

Bi-Weekly Bills is a desktop app for people who use a **separate checking account just for bills**.

## The idea behind the app

This app is built around using two checking accounts:

1. **Primary Checking / Transfer Source** — your normal checking account where income is received and everyday money stays.
2. **Bills Checking** — a separate checking account used only to hold money for scheduled automatic bill payments.

Instead of leaving bill money mixed in with spending money, you move the amount needed for upcoming bills into Bills Checking. That money stays there until the scheduled automatic payments come out.

Bi-Weekly Bills helps you answer a few simple questions:

- How much money should I move into Bills Checking for the **1st** and **15th** pay periods?
- Which bills is that money meant to cover?
- Did the automatic payments actually come out?
- How much is still needed for the rest of the pay period?
- Which payments need my attention?

The app **does not move money or pay bills for you**. You make transfers and payments through your bank as usual. Bi-Weekly Bills helps you plan, track, and verify them.

## What the app does

Bi-Weekly Bills can:

- keep a master list of recurring bills
- separate bills into 1st and 15th pay-period groups
- calculate how much should be in Bills Checking
- connect to your bank and download balances and transactions
- match posted payments to the bills they belong to
- flag payments that need review instead of guessing
- show merchant details and logos when available
- track transfers into Bills Checking
- show monthly progress and remaining bills
- export monthly reports to Excel, PDF, or ODS
- create and restore verified local database backups
- import older ODS bill history into the desktop app

## What is Plaid?

**Plaid** is a third-party financial-data service that securely connects supported bank accounts to applications like Bi-Weekly Bills.

Plaid provides the app with account, balance, and transaction data. Your bank sign-in happens through Plaid rather than inside Bi-Weekly Bills.

You will need your own Plaid Client ID and Secret to connect live bank data.

## Install on Arch Linux

The easiest way to install Bi-Weekly Bills is from the **Python wheel (`.whl`)** attached to each GitHub Release. A Git clone install is also available if you prefer to install directly from the repository.

### Recommended: install the release wheel

#### 1. Install the required system packages

```bash
sudo pacman -S --needed python python-pip libglvnd libxkbcommon libxkbcommon-x11 libxcb fontconfig
```

#### 2. Download Bi-Weekly Bills

Open the **[GitHub Releases page](https://github.com/newnetmp3/Bi-Weekly-Bills/releases)**, open the latest release, and download the file ending in:

```text
-py3-none-any.whl
```

For v1.0.0, the filename is:

```text
bi_weekly_bills-1.0.0-py3-none-any.whl
```

Your browser will normally save it in `~/Downloads`.

#### 3. Create a private Python environment for the app

```bash
mkdir -p ~/.local/opt/bi-weekly-bills
python -m venv ~/.local/opt/bi-weekly-bills
~/.local/opt/bi-weekly-bills/bin/python -m pip install --upgrade pip
```

#### 4. Install the wheel

For v1.0.0:

```bash
~/.local/opt/bi-weekly-bills/bin/python -m pip install ~/Downloads/bi_weekly_bills-1.0.0-py3-none-any.whl
```

If you downloaded a newer release, use that wheel's filename instead.

#### 5. Run Bi-Weekly Bills

```bash
~/.local/opt/bi-weekly-bills/bin/biweekly-bills-app
```

On first launch, Bi-Weekly Bills installs or refreshes its desktop launcher and icon. After that, you can normally start **Bi-Weekly Bills** from your desktop environment's application menu.

#### Updating a wheel installation

Download the newer wheel from Releases and install it into the same app environment:

```bash
~/.local/opt/bi-weekly-bills/bin/python -m pip install --upgrade ~/Downloads/bi_weekly_bills-X.Y.Z-py3-none-any.whl
```

Replace `X.Y.Z` with the version you downloaded.

### Alternate: install with Git clone

If you prefer to download the repository itself:

```bash
sudo pacman -S --needed git python python-pip libglvnd libxkbcommon libxkbcommon-x11 libxcb fontconfig

git clone https://github.com/newnetmp3/Bi-Weekly-Bills.git
cd Bi-Weekly-Bills

python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/biweekly-bills-app
```

For normal use, **`git clone` is the only Git command you need**. You do not need to commit, push, create branches, or manage the repository.

## First-time setup

When you first open Bi-Weekly Bills, it takes you to **Settings**.

1. Enter your **Plaid Client ID** and **Plaid Secret**.
2. Choose **Save & continue**.
3. Complete your bank sign-in through the Plaid window that opens.
4. Let the app sync your accounts and transactions.
5. In **Accounts & bill funding**, choose the separate checking account you use for automatic bills and mark it as **Bills Checking**.
6. Choose your normal checking account and mark it as **Transfer Source**.
7. Open **Bills** and add your recurring bills.
8. For each bill, choose the account the payment actually comes from.

If a bill is paid from **Bills Checking**, the app includes it when calculating how much money needs to be transferred into that account.

## Normal day-to-day use

A typical pay-period workflow is:

1. Open **Pay Periods** and review the bills coming up.
2. Check the amount the app says should be funded into Bills Checking.
3. Make that transfer through your bank.
4. Let scheduled automatic payments post normally.
5. Sync transactions in Bi-Weekly Bills.
6. Review anything the app could not confidently match on its own.

The app deliberately leaves uncertain payments for review instead of automatically assigning them to the wrong bill.

## Your data

The main local database is normally stored at:

```text
~/.local/share/bi-weekly-bills/biweekly-bills.sqlite3
```

Private Plaid configuration and credentials are stored separately under:

```text
~/.config/bi-weekly-bills/
```

Do not upload or share personal databases, reports, account information, or Plaid credentials.

## Documentation

For more detailed instructions, use the **[Bi-Weekly Bills Wiki](https://github.com/newnetmp3/Bi-Weekly-Bills/wiki)**.

Useful links:

- **[Installation](https://github.com/newnetmp3/Bi-Weekly-Bills/wiki/Installation)**
- **[First Run and Bank Connection](https://github.com/newnetmp3/Bi-Weekly-Bills/wiki/First-Run-and-Bank-Connection)**
- **[Bills and Pay Periods](https://github.com/newnetmp3/Bi-Weekly-Bills/wiki/Bills-and-Pay-Periods)**
- **[Transactions and Reconciliation](https://github.com/newnetmp3/Bi-Weekly-Bills/wiki/Transactions-and-Reconciliation)**
- **[Troubleshooting](https://github.com/newnetmp3/Bi-Weekly-Bills/wiki/Troubleshooting)**
- **[Security and Privacy](SECURITY.md)**

Technical and migration documentation is also available in the repository under `docs/`.

## Release

The current stable release is **v1.0.0**.

Release downloads are available from the **[GitHub Releases page](https://github.com/newnetmp3/Bi-Weekly-Bills/releases)**.
