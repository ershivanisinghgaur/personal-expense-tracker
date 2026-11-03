import json
import math
import os
import secrets
import sys
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

data_file = Path(__file__).with_name("expenses.json")
categories = ("food", "travel", "shopping", "other")
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)


def load_expenses():
    if not data_file.exists():
        return []
    with data_file.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_expenses(expenses):
    with data_file.open("w", encoding="utf-8") as file:
        json.dump(expenses, file, indent=4)


@app.route("/")
def dashboard():
    expenses = load_expenses()
    totals_by_category = {category: 0 for category in categories}
    for expense in expenses:
        category = expense["category"]
        if category in totals_by_category:
            totals_by_category[category] += expense["amount"]
    total = sum(expense["amount"] for expense in expenses)
    return render_template(
        "dashboard.html",
        total=total,
        expense_count=len(expenses),
        totals_by_category=totals_by_category,
        recent_expenses=list(reversed(expenses[-5:])),
    )


@app.route("/add", methods=["GET", "POST"])
def add_expense():
    if request.method == "POST":
        category = request.form.get("category", "").strip().lower()
        amount_text = request.form.get("amount", "").strip()
        if category not in categories:
            flash("Please choose a valid category.", "error")
            return render_template("add_expense.html", categories=categories)

        try:
            amount = float(amount_text)
        except ValueError:
            flash("Enter a valid amount, such as 150 or 150.50.", "error")
            return render_template("add_expense.html", categories=categories)

        if not math.isfinite(amount) or amount <= 0:
            flash("The amount must be a number greater than zero.", "error")
            return render_template("add_expense.html", categories=categories)

        expenses = load_expenses()
        expenses.append({"category": category, "amount": amount})
        save_expenses(expenses)
        flash("Expense added successfully.", "success")
        return redirect(url_for("history"))

    return render_template("add_expense.html", categories=categories)


@app.route("/history")
def history():
    expenses = load_expenses()
    return render_template(
        "history.html",
        expenses=[
            (index, expenses[index])
            for index in reversed(range(len(expenses)))
        ],
        total=sum(expense["amount"] for expense in expenses),
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
    return redirect(url_for("history"))


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
        expenses.append({"category": category, "amount": amount})
        save_expenses(expenses)
        print("Expense added!")

    print("\nYour expenses:")
    for expense in expenses:
        print(expense["category"], "-", expense["amount"])
    print(f"Total spent: {sum(expense['amount'] for expense in expenses):.2f}")


if __name__ == "__main__":
    if "--cli" in sys.argv:
        run_cli()
    else:
        app.run()
