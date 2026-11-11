import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

from flask import Flask, flash, has_request_context, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

data_file = Path(__file__).with_name("expenses.json")
budget_file = Path(__file__).with_name("budget.json")
group_data_file = Path(__file__).with_name("group_data.json")
account_db_file = Path(__file__).with_name("accounts.sqlite3")
user_data_dir = Path(__file__).with_name("user_data")
categories = ("food", "travel", "shopping","movie","donation", "other")
currency_symbol = "₹"
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)


def format_inr(paise):
    return f"{currency_symbol}{paise // 100:,.0f}.{paise % 100:02d}"


def format_paise_input(paise):
    return f"{paise // 100}.{paise % 100:02d}"


def format_rupees(amount):
    return f"{currency_symbol}{amount:,.2f}"


app.jinja_env.filters["inr"] = format_inr
app.jinja_env.filters["paise_input"] = format_paise_input
app.jinja_env.filters["rupees"] = format_rupees


def initialize_account_database():
    account_db_file.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(account_db_file)) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                recovery_hash TEXT
            )"""
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "recovery_hash" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN recovery_hash TEXT")
        connection.commit()


def user_data_file(path):
    user_id = session.get("user_id") if has_request_context() else None
    if user_id is None:
        return path
    account_dir = user_data_dir / str(user_id)
    account_dir.mkdir(parents=True, exist_ok=True)
    return account_dir / path.name


def migrate_legacy_data(user_id):
    account_dir = user_data_dir / str(user_id)
    account_dir.mkdir(parents=True, exist_ok=True)
    for path in (data_file, budget_file, group_data_file):
        destination = account_dir / path.name
        if path.exists() and not destination.exists():
            shutil.copy2(path, destination)


def normalize_recovery_code(value):
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def create_recovery_code():
    raw_code = secrets.token_hex(16).upper()
    return "-".join(raw_code[index:index + 4] for index in range(0, len(raw_code), 4))


@app.before_request
def require_account():
    initialize_account_database()
    if request.endpoint in ("login", "register", "forgot_password", "static"):
        return
    user_id = session.get("user_id")
    if user_id is None:
        return redirect(url_for("login"))
    with closing(sqlite3.connect(account_db_file)) as connection:
        account = connection.execute(
            "SELECT username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if account is None:
        session.clear()
        return redirect(url_for("login"))
    session["username"] = account[0]


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth.html", mode="register")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirmation = request.form.get("confirm_password", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        flash("Choose a username with 3 to 32 letters, numbers, dots, dashes, or underscores.", "error")
        return render_template("auth.html", mode="register", username=username)
    if len(password) < 8 or len(password) > 1024:
        flash("Choose a password with at least 8 characters.", "error")
        return render_template("auth.html", mode="register", username=username)
    if password != confirmation:
        flash("The passwords do not match.", "error")
        return render_template("auth.html", mode="register", username=username)

    recovery_code = create_recovery_code()
    with closing(sqlite3.connect(account_db_file)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        first_account = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, recovery_hash) VALUES (?, ?, ?)",
                (
                    username,
                    generate_password_hash(password),
                    generate_password_hash(normalize_recovery_code(recovery_code)),
                ),
            )
            user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            flash("That username is already in use.", "error")
            return render_template("auth.html", mode="register", username=username)
        connection.commit()

    if first_account:
        migrate_legacy_data(user_id)
    session.clear()
    session["user_id"] = user_id
    session["username"] = username
    return render_template(
        "auth.html", mode="recovery-code", recovery_code=recovery_code, logged_in=True
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth.html", mode="login")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    with closing(sqlite3.connect(account_db_file)) as connection:
        account = connection.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if account is None or not check_password_hash(account[2], password):
        flash("Username or password is incorrect.", "error")
        return render_template("auth.html", mode="login", username=username)

    session.clear()
    session["user_id"] = account[0]
    session["username"] = account[1]
    return redirect(url_for("dashboard"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("auth.html", mode="forgot-password")

    username = request.form.get("username", "").strip()
    recovery_code = normalize_recovery_code(
        request.form.get("recovery_code", "").strip()
    )
    password = request.form.get("password", "")
    confirmation = request.form.get("confirm_password", "")
    if len(password) < 8 or len(password) > 1024:
        flash("Choose a new password with at least 8 characters.", "error")
        return render_template("auth.html", mode="forgot-password", username=username)
    if password != confirmation:
        flash("The passwords do not match.", "error")
        return render_template("auth.html", mode="forgot-password", username=username)

    with closing(sqlite3.connect(account_db_file)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = connection.execute(
            "SELECT id, recovery_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if (
            account is None
            or not account[1]
            or not recovery_code
            or not check_password_hash(account[1], recovery_code)
        ):
            flash("Username or recovery code is incorrect.", "error")
            return render_template(
                "auth.html", mode="forgot-password", username=username
            )

        new_recovery_code = create_recovery_code()
        connection.execute(
            "UPDATE users SET password_hash = ?, recovery_hash = ? WHERE id = ?",
            (
                generate_password_hash(password),
                generate_password_hash(normalize_recovery_code(new_recovery_code)),
                account[0],
            ),
        )
        connection.commit()

    return render_template(
        "auth.html", mode="recovery-code", recovery_code=new_recovery_code
    )


@app.route("/recovery-code", methods=["GET", "POST"])
def recovery_code_settings():
    user_id = session["user_id"]
    username = session["username"]
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        with closing(sqlite3.connect(account_db_file)) as connection:
            account = connection.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if account is None or not check_password_hash(account[0], current_password):
                flash("Your current password is incorrect.", "error")
                return render_template("recovery_code.html", username=username)

            recovery_code = create_recovery_code()
            connection.execute(
                "UPDATE users SET recovery_hash = ? WHERE id = ?",
                (generate_password_hash(normalize_recovery_code(recovery_code)), user_id),
            )
            connection.commit()
        return render_template(
            "recovery_code.html", username=username, recovery_code=recovery_code
        )

    return render_template("recovery_code.html", username=username)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


def load_expenses():
    path = user_data_file(data_file)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        expenses = json.load(file)

    today = date.today().isoformat()
    for expense in expenses:
        expense.setdefault("date", today)
    return expenses


def save_expenses(expenses):
    with user_data_file(data_file).open("w", encoding="utf-8") as file:
        json.dump(expenses, file, indent=4)


def load_monthly_budget():
    path = user_data_file(budget_file)
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)["monthly_budget"]


def save_monthly_budget(amount):
    with user_data_file(budget_file).open("w", encoding="utf-8") as file:
        json.dump({"monthly_budget": amount}, file, indent=4)


def load_groups():
    path = user_data_file(group_data_file)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        groups = json.load(file)
    return groups


def save_groups(groups):
    with user_data_file(group_data_file).open("w", encoding="utf-8") as file:
        json.dump(groups, file, indent=4, ensure_ascii=False)


def find_group(group_id):
    return next(
        (group for group in load_groups() if group["id"] == group_id),
        None,
    )


def parse_paise(value, allow_zero=False):
    try:
        amount = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None

    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        return None
    if amount < 0 or (amount == 0 and not allow_zero):
        return None
    return int(amount * 100)


def calculate_group_balances(group):
    balances = {member["id"]: 0 for member in group["members"]}
    for expense in group["expenses"]:
        balances[expense["paid_by"]] += expense["amount"]
        for member_id, amount in expense["shares"].items():
            balances[member_id] -= amount
    for settlement in group["settlements"]:
        balances[settlement["paid_by"]] += settlement["amount"]
        balances[settlement["paid_to"]] -= settlement["amount"]
    return balances


def suggest_settlements(group, balances):
    members = {member["id"]: member["name"] for member in group["members"]}
    debtors = [
        [member_id, -balance]
        for member_id, balance in balances.items()
        if balance < 0
    ]
    creditors = [
        [member_id, balance]
        for member_id, balance in balances.items()
        if balance > 0
    ]
    suggestions = []
    debtor_index = creditor_index = 0

    while debtor_index < len(debtors) and creditor_index < len(creditors):
        debtor_id, owed = debtors[debtor_index]
        creditor_id, due = creditors[creditor_index]
        amount = min(owed, due)
        suggestions.append(
            {
                "from_id": debtor_id,
                "from_name": members[debtor_id],
                "to_id": creditor_id,
                "to_name": members[creditor_id],
                "amount": amount,
            }
        )
        debtors[debtor_index][1] -= amount
        creditors[creditor_index][1] -= amount
        if debtors[debtor_index][1] == 0:
            debtor_index += 1
        if creditors[creditor_index][1] == 0:
            creditor_index += 1
    return suggestions


def render_expense_form(title, heading, submit_label, action, values, index=None):
    return render_template(
        "edit_expense.html",
        title=title,
        heading=heading,
        submit_label=submit_label,
        form_action=action,
        categories=categories,
        values=values,
        index=index,
    )


def validate_expense_form():
    category = request.form.get("category", "").strip().lower()
    amount_text = request.form.get("amount", "").strip()
    expense_date = request.form.get("date", "").strip()

    if category not in categories:
        flash("Please choose a valid category.", "error")
        return None

    try:
        amount = float(amount_text)
    except ValueError:
        flash("Enter a valid amount, such as 150 or 150.50.", "error")
        return None

    if not math.isfinite(amount) or amount <= 0:
        flash("The amount must be a number greater than zero.", "error")
        return None

    try:
        parsed_date = date.fromisoformat(expense_date)
    except ValueError:
        flash("Choose a valid expense date.", "error")
        return None

    if parsed_date.isoformat() != expense_date:
        flash("Choose a valid expense date.", "error")
        return None

    return {"category": category, "amount": amount, "date": expense_date}


def history_redirect():
    filters = {
        key: request.args[key]
        for key in ("category", "month")
        if request.args.get(key)
    }
    return redirect(url_for("history", **filters))


def edit_expense_url(index):
    filters = {
        key: request.args[key]
        for key in ("category", "month")
        if request.args.get(key)
    }
    return url_for("edit_expense", index=index, **filters)


def render_group_detail(group):
    balances = calculate_group_balances(group)
    member_names = {member["id"]: member["name"] for member in group["members"]}
    expenses = sorted(
        group["expenses"],
        key=lambda expense: (expense["date"], expense["id"]),
        reverse=True,
    )
    settlements = sorted(
        group["settlements"],
        key=lambda settlement: (settlement["date"], settlement["id"]),
        reverse=True,
    )
    return render_template(
        "group_detail.html",
        group=group,
        members=group["members"],
        member_names=member_names,
        gmail_compose_links={
            expense["id"]: expense_gmail_compose_link(group, expense, member_names)
            for expense in expenses
        },
        balances=balances,
        suggestions=suggest_settlements(group, balances),
        expenses=expenses,
        settlements=settlements,
        today=date.today().isoformat(),
        current_month_label=date.today().strftime("%B %Y"),
    )


def expense_gmail_compose_link(group, expense, member_names):
    members = {member["id"]: member for member in group["members"]}
    recipients = [
        members[member_id].get("email", "").strip()
        for member_id in expense["shares"]
        if members[member_id].get("email", "").strip()
    ]
    split_details = "\n".join(
        f"- {member_names[member_id]}: {format_inr(amount)}"
        for member_id, amount in expense["shares"].items()
    )
    body = "\n".join(
        [
            f"Group: {group['name']}",
            f"Expense: {expense['description']}",
            f"Date: {expense['date']}",
            f"Total: {format_inr(expense['amount'])}",
            f"Paid by: {member_names[expense['paid_by']]}",
            "",
            "Split:",
            split_details,
        ]
    )
    query = urlencode(
        {
            "view": "cm",
            "fs": "1",
            "to": ",".join(recipients),
            "su": f"{group['name']} expense: {expense['description']}",
            "body": body,
        },
        quote_via=quote,
    )
    return f"https://mail.google.com/mail/?{query}"


def valid_iso_date(value):
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed_date.isoformat() == value


def valid_email(value):
    return re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None


@app.route("/")
def dashboard():
    today = date.today()
    current_month = today.strftime("%Y-%m")
    expenses = [
        (index, expense)
        for index, expense in enumerate(load_expenses())
        if expense["date"].startswith(current_month)
    ]
    totals_by_category = {category: 0 for category in categories}
    for _, expense in expenses:
        category = expense["category"]
        if category in totals_by_category:
            totals_by_category[category] += expense["amount"]
    total = sum(expense["amount"] for _, expense in expenses)
    monthly_budget = load_monthly_budget()
    recent_expenses = sorted(
        expenses, key=lambda entry: (entry[1]["date"], entry[0]), reverse=True
    )[:6]
    budget_utilization = total / monthly_budget * 100 if monthly_budget else 0
    top_category = max(totals_by_category, key=totals_by_category.get) if total else None
    return render_template(
        "dashboard.html",
        total=total,
        expense_count=len(expenses),
        totals_by_category=totals_by_category,
        chart_max=max(totals_by_category.values(), default=0),
        recent_expenses=recent_expenses,
        current_month=current_month,
        current_month_label=today.strftime("%B %Y"),
        today=today.isoformat(),
        categories=categories,
        daily_average=total / today.day,
        budget_utilization=budget_utilization,
        budget_utilization_bar=min(budget_utilization, 100),
        top_category=top_category,
        monthly_budget=monthly_budget,
        remaining_budget=monthly_budget - total,
    )


@app.route("/add", methods=["GET", "POST"])
def add_expense():
    if request.method == "GET":
        return redirect(url_for("dashboard") + "#quick-add")

    expense = validate_expense_form()
    if expense is None:
        return redirect(url_for("dashboard") + "#quick-add")

    expenses = load_expenses()
    expenses.append(expense)
    save_expenses(expenses)
    flash("Expense added successfully.", "success")
    return redirect(url_for("history"))


@app.route("/edit/<int:index>", methods=["GET", "POST"])
def edit_expense(index):
    expenses = load_expenses()
    if index < 0 or index >= len(expenses):
        flash("That expense could not be found.", "error")
        return history_redirect()

    if request.method == "POST":
        expense = validate_expense_form()
        if expense is None:
            return render_expense_form(
                "Edit expense | Expense Tracker",
                "Edit expense",
                "Save changes",
                edit_expense_url(index),
                request.form,
                index,
            )

        expenses[index].update(expense)
        save_expenses(expenses)
        flash("Expense updated.", "success")
        return history_redirect()

    return render_expense_form(
        "Edit expense | Expense Tracker",
        "Edit expense",
        "Save changes",
        edit_expense_url(index),
        expenses[index],
        index,
    )


@app.route("/budget", methods=["POST"])
def update_budget():
    amount_text = request.form.get("monthly_budget", "").strip()
    try:
        monthly_budget = float(amount_text)
    except ValueError:
        flash("Enter a valid monthly budget.", "error")
        return redirect(url_for("dashboard"))

    if not math.isfinite(monthly_budget) or monthly_budget < 0:
        flash("The monthly budget must be zero or greater.", "error")
        return redirect(url_for("dashboard"))

    save_monthly_budget(monthly_budget)
    flash("Monthly budget updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/history")
def history():
    selected_category = request.args.get("category", "").strip().lower()
    selected_month = request.args.get("month", "").strip()
    if selected_category and selected_category not in categories:
        flash("Please choose a valid category filter.", "error")
        return redirect(url_for("history"))
    if selected_month:
        try:
            parsed_month = date.fromisoformat(f"{selected_month}-01")
        except ValueError:
            flash("Choose a valid month filter.", "error")
            return redirect(url_for("history"))
        if parsed_month.strftime("%Y-%m") != selected_month:
            flash("Choose a valid month filter.", "error")
            return redirect(url_for("history"))

    all_expenses = load_expenses()
    expenses = [
        (index, expense)
        for index, expense in enumerate(all_expenses)
        if (not selected_category or expense["category"] == selected_category)
        and (not selected_month or expense["date"].startswith(selected_month))
    ]
    return render_template(
        "history.html",
        expenses=sorted(
            expenses, key=lambda entry: (entry[1]["date"], entry[0]), reverse=True
        ),
        total=sum(expense["amount"] for _, expense in expenses),
        expense_count=len(expenses),
        total_logged_spend=sum(expense["amount"] for expense in all_expenses),
        total_logged_count=len(all_expenses),
        categories=categories,
        selected_category=selected_category,
        selected_month=selected_month,
        current_month_label=date.today().strftime("%B %Y"),
    )


@app.route("/delete/<int:index>", methods=["POST"])
def delete_expense(index):
    expenses = load_expenses()
    if index < 0 or index >= len(expenses):
        flash("That expense could not be found.", "error")
        return redirect(url_for("history"))

    del expenses[index]
    save_expenses(expenses)
    flash("Expense deleted.", "success")
    return history_redirect()


@app.route("/groups", methods=["GET", "POST"])
def groups():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        creator = request.form.get("creator", "").strip()
        creator_email = request.form.get("creator_email", "").strip()
        if not name or len(name) > 80 or not creator or len(creator) > 60:
            flash("Enter a group name (up to 80 characters) and your name (up to 60).", "error")
            return render_template(
                "groups.html",
                groups=load_groups(),
                current_month_label=date.today().strftime("%B %Y"),
            )
        if creator_email and (len(creator_email) > 254 or not valid_email(creator_email)):
            flash("Enter a valid email address, or leave it blank.", "error")
            return render_template(
                "groups.html",
                groups=load_groups(),
                current_month_label=date.today().strftime("%B %Y"),
            )
        group = {
            "id": uuid4().hex,
            "name": name,
            "currency": currency_symbol,
            "members": [{"id": uuid4().hex, "name": creator, "email": creator_email}],
            "expenses": [],
            "settlements": [],
        }
        all_groups = load_groups()
        all_groups.append(group)
        save_groups(all_groups)
        flash("Group created. Add your friends and family as members.", "success")
        return redirect(url_for("group_detail", group_id=group["id"]))

    return render_template(
        "groups.html",
        groups=load_groups(),
        current_month_label=date.today().strftime("%B %Y"),
    )


@app.route("/groups/<group_id>")
def group_detail(group_id):
    group = find_group(group_id)
    if group is None:
        flash("That group could not be found.", "error")
        return redirect(url_for("groups"))
    return render_group_detail(group)


@app.route("/groups/<group_id>/members", methods=["POST"])
def add_group_member(group_id):
    all_groups = load_groups()
    group = next((item for item in all_groups if item["id"] == group_id), None)
    if group is None:
        flash("That group could not be found.", "error")
        return redirect(url_for("groups"))

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    if not name or len(name) > 60:
        flash("Enter a member name up to 60 characters.", "error")
    elif email and (len(email) > 254 or not valid_email(email)):
        flash("Enter a valid email address, or leave it blank.", "error")
    elif any(member["name"].casefold() == name.casefold() for member in group["members"]):
        flash("That person is already in this group.", "error")
    else:
        group["members"].append({"id": uuid4().hex, "name": name, "email": email})
        save_groups(all_groups)
        flash(f"{name} added to the group.", "success")
    return redirect(url_for("group_detail", group_id=group_id))


@app.route("/groups/<group_id>/members/<member_id>/email", methods=["POST"])
def update_group_member_email(group_id, member_id):
    all_groups = load_groups()
    group = next((item for item in all_groups if item["id"] == group_id), None)
    if group is None:
        flash("That group could not be found.", "error")
        return redirect(url_for("groups"))

    member = next((item for item in group["members"] if item["id"] == member_id), None)
    if member is None:
        flash("That group member could not be found.", "error")
        return redirect(url_for("group_detail", group_id=group_id))

    email = request.form.get("email", "").strip()
    if email and (len(email) > 254 or not valid_email(email)):
        flash("Enter a valid email address, or leave it blank.", "error")
    else:
        member["email"] = email
        save_groups(all_groups)
        flash(f"Email address updated for {member['name']}.", "success")
    return redirect(url_for("group_detail", group_id=group_id))


@app.route("/groups/<group_id>/expenses", methods=["POST"])
def add_group_expense(group_id):
    all_groups = load_groups()
    group = next((item for item in all_groups if item["id"] == group_id), None)
    if group is None:
        flash("That group could not be found.", "error")
        return redirect(url_for("groups"))

    description = request.form.get("description", "").strip()
    amount = parse_paise(request.form.get("amount", ""))
    expense_date = request.form.get("date", "").strip()
    member_ids = {member["id"] for member in group["members"]}
    paid_by = request.form.get("paid_by", "")
    participants = request.form.getlist("participants")
    split_type = request.form.get("split_type", "")

    if not description or len(description) > 120:
        flash("Enter an expense description up to 120 characters.", "error")
    elif amount is None:
        flash("Enter an amount greater than zero with up to two decimal places.", "error")
    elif not valid_iso_date(expense_date):
        flash("Choose a valid expense date.", "error")
    elif paid_by not in member_ids:
        flash("Choose a valid person who paid.", "error")
    elif not participants or len(set(participants)) != len(participants) or not set(participants) <= member_ids:
        flash("Choose at least one valid participant for the split.", "error")
    elif split_type not in ("equal", "custom"):
        flash("Choose an equal or custom split.", "error")
    else:
        if split_type == "equal":
            base_share, remainder = divmod(amount, len(participants))
            shares = {
                member_id: base_share + (position < remainder)
                for position, member_id in enumerate(participants)
            }
        else:
            shares = {
                member_id: parse_paise(
                    request.form.get(f"share_{member_id}", "")
                )
                for member_id in participants
            }
            if any(share is None for share in shares.values()):
                flash("Enter a positive share for every selected participant.", "error")
                return render_group_detail(group)
            if sum(shares.values()) != amount:
                flash("Custom shares must add up exactly to the expense total.", "error")
                return render_group_detail(group)

        group["expenses"].append(
            {
                "id": uuid4().hex,
                "description": description,
                "amount": amount,
                "date": expense_date,
                "paid_by": paid_by,
                "split_type": split_type,
                "shares": shares,
            }
        )
        save_groups(all_groups)
        flash("Shared expense added.", "success")
        return redirect(url_for("group_detail", group_id=group_id))

    return render_group_detail(group)


@app.route("/groups/<group_id>/expenses/<expense_id>/delete", methods=["POST"])
def delete_group_expense(group_id, expense_id):
    all_groups = load_groups()
    group = next((item for item in all_groups if item["id"] == group_id), None)
    if group is None:
        flash("That group could not be found.", "error")
        return redirect(url_for("groups"))
    expense = next(
        (item for item in group["expenses"] if item["id"] == expense_id),
        None,
    )
    if expense is None:
        flash("That shared expense could not be found.", "error")
    else:
        group["expenses"].remove(expense)
        save_groups(all_groups)
        flash("Shared expense deleted.", "success")
    return redirect(url_for("group_detail", group_id=group_id))


@app.route("/groups/<group_id>/settlements", methods=["POST"])
def add_group_settlement(group_id):
    all_groups = load_groups()
    group = next((item for item in all_groups if item["id"] == group_id), None)
    if group is None:
        flash("That group could not be found.", "error")
        return redirect(url_for("groups"))

    paid_by = request.form.get("paid_by", "")
    paid_to = request.form.get("paid_to", "")
    amount = parse_paise(request.form.get("amount", ""))
    settlement_date = request.form.get("date", "").strip()
    balances = calculate_group_balances(group)

    if paid_by not in balances or paid_to not in balances or paid_by == paid_to:
        flash("Choose two different members for the settlement.", "error")
    elif amount is None:
        flash("Enter a settlement amount greater than zero.", "error")
    elif not valid_iso_date(settlement_date):
        flash("Choose a valid settlement date.", "error")
    elif balances[paid_by] >= 0 or balances[paid_to] <= 0:
        flash("A settlement must go from someone who owes to someone who is owed.", "error")
    elif amount > min(-balances[paid_by], balances[paid_to]):
        flash("The settlement cannot exceed the amount currently owed between these members.", "error")
    else:
        group["settlements"].append(
            {
                "id": uuid4().hex,
                "paid_by": paid_by,
                "paid_to": paid_to,
                "amount": amount,
                "date": settlement_date,
            }
        )
        save_groups(all_groups)
        flash("Settlement recorded.", "success")
        return redirect(url_for("group_detail", group_id=group_id))

    return render_group_detail(group)


def run_cli():
    expenses = load_expenses()
    print("Welcome to your Personal Expense Tracker!")
    while True:
        category = input(
            "\nEnter a category (Food, Travel, Shopping, Other) or 'done': "
        ).strip().lower()
        if category == "done":
            break
        if category not in categories:
            print("Please choose a valid category.")
            continue
        try:
            amount = float(input("How much did you spend? "))
        except ValueError:
            print("Please enter a number, such as 150 or 150.50.")
            continue
        if not math.isfinite(amount) or amount <= 0:
            print("The amount must be greater than zero.")
            continue
        expense_date = input(
            f"When did you spend? (YYYY-MM-DD, default {date.today().isoformat()}): "
        ).strip()
        if not expense_date:
            expense_date = date.today().isoformat()
        try:
            parsed_date = date.fromisoformat(expense_date)
        except ValueError:
            print("Please enter a valid date in YYYY-MM-DD format.")
            continue
        if parsed_date.isoformat() != expense_date:
            print("Please enter a valid date in YYYY-MM-DD format.")
            continue
        expenses.append(
            {"category": category, "amount": amount, "date": expense_date}
        )
        save_expenses(expenses)
        print("Expense added!")

    print("\nYour expenses:")
    for expense in expenses:
        print(expense["date"], "-", expense["category"], "-", expense["amount"])
    print(f"Total spent: {sum(expense['amount'] for expense in expenses):.2f}")


if __name__ == "__main__":
    if "--cli" in sys.argv:
        run_cli()
    else:
        app.run()
