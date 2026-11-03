# Personal Expense Tracker

A small Flask app for recording expenses by category, with an optional command-line mode.

## Run

```powershell
python -m pip install -r requirements.txt
python main.py
```

For command-line mode, run `python main.py --cli`.

The app stores records in a local `expenses.json` file. That file is intentionally excluded from Git. Set `FLASK_SECRET_KEY` to a stable, private value when deploying; otherwise, the app generates a temporary key at startup.
