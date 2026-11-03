# Personal Expense Tracker

A small Flask app for recording and reviewing expenses, with an optional command-line mode.

## Features

- Record expenses with a category, amount, and date.
- Edit or delete expenses from the history page.
- Filter history by category and month.
- Set a recurring monthly budget and see the remaining amount or overspending.
- View a category spending chart for the current month.
- Create local groups, add members by name and email, split shared expenses equally or with custom shares, view suggested balances, record repayments, and open a prefilled email to share expense details.

## Run

```powershell
python -m pip install -r requirements.txt
python main.py
```

For command-line mode, run `python main.py --cli`.

The app stores records in local `expenses.json`, `budget.json`, and `group_data.json` files. These files are intentionally excluded from Git. Shared groups are local to this device; they do not sync or provide remote access for friends and family. Email sharing opens the device’s default email application with the expense summary and saved participant email addresses; you review and send the message yourself. Older personal expenses without a date are displayed as if they were recorded today. Set `FLASK_SECRET_KEY` to a stable, private value when deploying; otherwise, the app generates a temporary key at startup.
