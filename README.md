# Personal Expense Tracker

A small Flask app for recording and reviewing expenses, with account sign-in and an optional command-line mode.

## Features

- Record expenses with a category, amount, and date.
- Edit or delete expenses from the history page.
- Filter history by category and month.
- Set a recurring monthly budget and see the remaining amount or overspending.
- View a category spending chart for the current month.
- Create local groups, add members by name and email, split shared expenses equally or with custom shares, view suggested balances, record repayments, and open prefilled Gmail drafts.
- Create an account and sign in to a private, account-specific tracker.

## Run

```powershell
python -m pip install -r requirements.txt
python main.py
```

For command-line mode, run `python main.py --cli`.

On first run, create an account from the sign-in page. Usernames are unique on this device and passwords are stored as hashes in `accounts.sqlite3`. A one-time recovery code is shown at account creation; save it privately because it is not stored in readable form or shown again. Use it with your username to reset a forgotten password. Each reset replaces the code. Signed-in users can generate a replacement from the dashboard. Expenses, budgets, and groups are stored in account-specific folders under `user_data/`. Existing root-level JSON files are copied into the first account; the original files are left untouched. These local files are intentionally excluded from Git. The optional `--cli` mode remains separate from web accounts and reads and writes root-level `expenses.json`. Shared groups are private to the account that created them; they do not sync or provide remote access for friends and family. The **Gmail** link opens a prefilled compose page; **Send with Gmail** sends directly through SMTP and requires `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in the environment before starting the app. Use a Google App Password (requires 2-Step Verification), not your normal account password. Older personal expenses without a date are displayed as if they were recorded today. Set `FLASK_SECRET_KEY` to a stable, private value when deploying; otherwise, the app generates a temporary key at startup.
On first run, create an account from the sign-in page. Usernames are unique on this device and passwords are stored as hashes in `accounts.sqlite3`. A one-time recovery code is shown at account creation; save it privately because it is not stored in readable form or shown again. Use it with your username to reset a forgotten password. Each reset replaces the code. Signed-in users can generate a replacement from the dashboard. Expenses, budgets, and groups are stored in account-specific folders under `user_data/`. Existing root-level JSON files are copied into the first account; the original files are left untouched. These local files are intentionally excluded from Git. The optional `--cli` mode remains separate from web accounts and reads and writes root-level `expenses.json`. Shared groups are private to the account that created them; they do not sync or provide remote access for friends and family. The **Gmail** link opens a prefilled compose page; you review and send the email from Gmail. Older personal expenses without a date are displayed as if they were recorded today. Set `FLASK_SECRET_KEY` to a stable, private value when deploying; otherwise, the app generates a temporary key at startup.
