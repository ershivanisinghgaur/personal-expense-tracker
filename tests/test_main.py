import io
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main


class ExpenseTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temp_dir.name) / "expenses.json"
        self.budget_path = Path(self.temp_dir.name) / "budget.json"
        self.group_data_path = Path(self.temp_dir.name) / "groups.json"
        self.user_data_path = Path(self.temp_dir.name) / "user-data"
        self.account_db_path = Path(self.temp_dir.name) / "accounts.sqlite3"
        self.data_path_patch = patch.object(main, "data_file", self.data_path)
        self.budget_path_patch = patch.object(main, "budget_file", self.budget_path)
        self.group_data_path_patch = patch.object(
            main, "group_data_file", self.group_data_path
        )
        self.data_path_patch.start()
        self.budget_path_patch.start()
        self.group_data_path_patch.start()
        self.user_data_path_patch = patch.object(main, "user_data_dir", self.user_data_path)
        self.account_db_path_patch = patch.object(main, "account_db_file", self.account_db_path)
        self.user_data_path_patch.start()
        self.account_db_path_patch.start()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self.data_path_patch.stop)
        self.addCleanup(self.budget_path_patch.stop)
        self.addCleanup(self.group_data_path_patch.stop)
        self.addCleanup(self.user_data_path_patch.stop)
        self.addCleanup(self.account_db_path_patch.stop)
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        response = self.client.post(
            "/register",
            data={"username": "tester", "password": "correct-horse", "confirm_password": "correct-horse"},
        )
        self.recovery_code = self.extract_recovery_code(response)
        with self.client.session_transaction() as session:
            self.user_id = session["user_id"]

    def extract_recovery_code(self, response):
        match = re.search(rb'id="recovery-code">([A-F0-9-]+)</(?:code|p)>', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    @property
    def account_dir(self):
        account_dir = self.user_data_path / str(self.user_id)
        account_dir.mkdir(parents=True, exist_ok=True)
        return account_dir

    def write_expenses(self, expenses):
        (self.account_dir / self.data_path.name).write_text(
            json.dumps(expenses), encoding="utf-8"
        )

    def read_expenses(self):
        return json.loads(
            (self.account_dir / self.data_path.name).read_text(encoding="utf-8")
        )

    def test_login_is_required_and_logout_clears_the_session(self):
        anonymous = main.app.test_client()
        response = anonymous.get("/history")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/login")
        self.assertEqual(anonymous.get("/login").status_code, 200)

        self.client.post("/logout")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/login")

    def test_existing_account_database_is_upgraded_for_recovery_codes(self):
        legacy_db_path = Path(self.temp_dir.name) / "legacy-accounts.sqlite3"
        with closing(sqlite3.connect(legacy_db_path)) as connection:
            connection.execute(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL)"
            )
            connection.commit()
        with patch.object(main, "account_db_file", legacy_db_path):
            main.initialize_account_database()
        with closing(sqlite3.connect(legacy_db_path)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        self.assertIn("recovery_hash", columns)

    def test_login_hashes_password_and_keeps_account_data_separate(self):
        self.write_expenses([{"category": "food", "amount": 42, "date": "2026-03-10"}])
        self.client.post("/groups", data={"name": "Private group", "creator": "Tester"})

        second_client = main.app.test_client()
        response = second_client.post(
            "/register",
            data={
                "username": "another",
                "password": "another-password",
                "confirm_password": "another-password",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Save your recovery code", response.data)
        history = second_client.get("/history")
        self.assertEqual(history.status_code, 200)
        self.assertNotIn(b"42.00", history.data)
        self.assertNotIn(b"Private group", second_client.get("/groups").data)
        with second_client.session_transaction() as session:
            self.assertNotEqual(session["user_id"], self.user_id)

        with closing(sqlite3.connect(self.account_db_path)) as connection:
            password_hash = connection.execute(
                "SELECT password_hash FROM users WHERE username = 'tester'"
            ).fetchone()[0]
        self.assertNotEqual(password_hash, "correct-horse")

        login_client = main.app.test_client()
        response = login_client.post(
            "/login", data={"username": "tester", "password": "correct-horse"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"42.00", login_client.get("/history").data)

    def test_recovery_code_resets_password_and_cannot_be_reused(self):
        reset_client = main.app.test_client()
        response = reset_client.post(
            "/forgot-password",
            data={
                "username": "tester",
                "recovery_code": self.recovery_code,
                "password": "new-password-123",
                "confirm_password": "new-password-123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Back to sign in", response.data)
        rotated_code = self.extract_recovery_code(response)
        self.assertNotEqual(rotated_code, self.recovery_code)

        response = reset_client.post(
            "/forgot-password",
            data={
                "username": "tester",
                "recovery_code": self.recovery_code,
                "password": "another-password-123",
                "confirm_password": "another-password-123",
            },
        )
        self.assertIn(b"Username or recovery code is incorrect.", response.data)

        login_client = main.app.test_client()
        response = login_client.post(
            "/login", data={"username": "tester", "password": "new-password-123"}
        )
        self.assertEqual(response.status_code, 302)

    def test_signed_in_user_can_rotate_recovery_code(self):
        response = self.client.get("/recovery-code")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/recovery-code", data={"current_password": "correct-horse"}
        )
        self.assertEqual(response.status_code, 200)
        rotated_code = self.extract_recovery_code(response)
        self.assertNotEqual(rotated_code, self.recovery_code)

    def test_first_account_receives_legacy_json_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_expenses = root / "expenses.json"
            old_budget = root / "budget.json"
            old_groups = root / "group_data.json"
            old_expenses.write_text('[{"category": "food", "amount": 9}]', encoding="utf-8")
            old_budget.write_text('{"monthly_budget": 100}', encoding="utf-8")
            old_groups.write_text('[]', encoding="utf-8")
            with (
                patch.object(main, "data_file", old_expenses),
                patch.object(main, "budget_file", old_budget),
                patch.object(main, "group_data_file", old_groups),
                patch.object(main, "account_db_file", root / "accounts.sqlite3"),
                patch.object(main, "user_data_dir", root / "user-data"),
            ):
                client = main.app.test_client()
                response = client.post(
                    "/register",
                    data={
                        "username": "first",
                        "password": "first-password",
                        "confirm_password": "first-password",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue((root / "user-data" / "1" / "expenses.json").exists())
                self.assertTrue((root / "user-data" / "1" / "budget.json").exists())

    def test_add_expense_saves_category_amount_and_date(self):
        response = self.client.post(
            "/add",
            data={"category": "Travel", "amount": "24.50", "date": "2026-03-10"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.read_expenses(),
            [{"category": "travel", "amount": 24.5, "date": "2026-03-10"}],
        )

    def test_legacy_expenses_are_displayed_with_a_date(self):
        self.write_expenses([{"category": "food", "amount": 12.0}])

        response = self.client.get("/history")

        self.assertEqual(response.status_code, 200)
        self.assertIn(date.today().isoformat().encode(), response.data)

    def test_invalid_expense_date_is_rejected(self):
        response = self.client.post(
            "/add",
            data={"category": "food", "amount": "10", "date": "not-a-date"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/#quick-add")
        self.assertFalse((self.account_dir / self.data_path.name).exists())
        dashboard_response = self.client.get("/")
        self.assertIn(b"Choose a valid expense date.", dashboard_response.data)

    def test_add_expense_page_redirects_to_dashboard_quick_add(self):
        response = self.client.get("/add")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/#quick-add")

    def test_cli_records_a_date_for_new_expenses(self):
        answers = ["food", "10", "2026-03-10", "done"]
        with patch("builtins.input", side_effect=answers), redirect_stdout(io.StringIO()):
            main.run_cli()

        self.assertEqual(
            json.loads(self.data_path.read_text(encoding="utf-8")),
            [{"category": "food", "amount": 10.0, "date": "2026-03-10"}],
        )

    def test_history_filters_by_category_and_month(self):
        self.write_expenses(
            [
                {"category": "food", "amount": 10, "date": "2026-03-02"},
                {"category": "travel", "amount": 20, "date": "2026-03-03"},
                {"category": "food", "amount": 30, "date": "2026-02-04"},
            ]
        )

        response = self.client.get("/history?category=food&month=2026-03")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"2026-03-02", response.data)
        self.assertNotIn(b"2026-03-03", response.data)
        self.assertNotIn(b"2026-02-04", response.data)
        self.assertIn(b"10.00", response.data)
        self.assertIn("₹60.00".encode(), response.data)
        self.assertIn(b"Total logged spend", response.data)
        self.assertIn(b"Expenses shown", response.data)

    def test_expenses_page_shows_filtered_summary_in_dashboard_layout(self):
        self.write_expenses(
            [
                {"category": "food", "amount": 10, "date": "2026-03-02"},
                {"category": "travel", "amount": 20, "date": "2026-03-03"},
            ]
        )

        response = self.client.get("/history?category=food")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="dashboard-sidebar"', response.data)
        self.assertIn(b'aria-current="page" href="/history"', response.data)
        self.assertIn(b"Expenses shown", response.data)
        self.assertIn(b"Filtered total", response.data)
        self.assertIn(b"1 expense", response.data)
        self.assertIn(b"2026-03-02", response.data)
        self.assertNotIn(b"2026-03-03", response.data)

    def test_edit_expense_updates_all_fields(self):
        self.write_expenses(
            [{"category": "food", "amount": 10, "date": "2026-03-02"}]
        )
        edit_page = self.client.get("/edit/0")
        self.assertEqual(edit_page.status_code, 200)
        self.assertIn(b'value="2026-03-02"', edit_page.data)

        response = self.client.post(
            "/edit/0?category=food&month=2026-03",
            data={"category": "shopping", "amount": "22.75", "date": "2026-03-05"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            "/history?category=food&month=2026-03",
        )
        self.assertEqual(
            self.read_expenses(),
            [{"category": "shopping", "amount": 22.75, "date": "2026-03-05"}],
        )

    def test_delete_expense_removes_record_and_preserves_filters(self):
        self.write_expenses(
            [{"category": "food", "amount": 10, "date": "2026-03-02"}]
        )

        response = self.client.post("/delete/0?category=food&month=2026-03")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/history?category=food&month=2026-03")
        self.assertEqual(self.read_expenses(), [])

    def test_monthly_budget_shows_remaining_or_overspending(self):
        current_month = date.today().strftime("%Y-%m")
        self.write_expenses(
            [
                {
                    "category": "food",
                    "amount": 125,
                    "date": f"{current_month}-02",
                },
                {"category": "travel", "amount": 900, "date": "2025-01-02"},
            ]
        )
        self.client.post("/budget", data={"monthly_budget": "100"})

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Over budget by 25.00", response.data)
        self.assertIn(b"By category", response.data)
        self.assertNotIn(b"900.00", response.data)
        self.assertTrue((self.account_dir / self.budget_path.name).exists())

    def test_dashboard_quick_add_uses_existing_expense_fields_and_live_totals(self):
        current_month = date.today().strftime("%Y-%m")
        self.write_expenses(
            [
                {"category": "food", "amount": 50, "date": f"{current_month}-02"},
                {"category": "travel", "amount": 25, "date": f"{current_month}-03"},
                {"category": "shopping", "amount": 900, "date": "2025-01-02"},
            ]
        )
        self.client.post("/budget", data={"monthly_budget": "100"})

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"75.0%", response.data)
        self.assertIn(b"Food", response.data)
        self.assertIn(b"Travel", response.data)
        self.assertNotIn(b"900.00", response.data)
        self.assertIn(b'action="/add"', response.data)
        self.assertIn(b'id="quick-add"', response.data)
        self.assertIn(b'name="category"', response.data)
        for category in main.categories:
            self.assertIn(f'<option value="{category}">'.encode(), response.data)
        self.assertIn(b'name="amount"', response.data)
        self.assertIn(b'name="date"', response.data)

    def test_dashboard_quick_add_saves_expense(self):
        response = self.client.post(
            "/add",
            data={
                "category": "food",
                "amount": "24.50",
                "date": date.today().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.read_expenses(), [
            {"category": "food", "amount": 24.5, "date": date.today().isoformat()}
        ])

    def test_shared_group_pages_use_spendwise_navigation_and_live_group_data(self):
        create_response = self.client.post(
            "/groups",
            data={"name": "Weekend trip", "creator": "Alex"},
        )

        self.assertEqual(create_response.status_code, 302)
        detail_response = self.client.get(create_response.headers["Location"])
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn(b'class="dashboard-sidebar"', detail_response.data)
        self.assertIn(b'aria-current="page"', detail_response.data)
        self.assertIn(b"Weekend trip", detail_response.data)
        self.assertIn(b"Add a shared expense", detail_response.data)

        groups_response = self.client.get("/groups")
        self.assertEqual(groups_response.status_code, 200)
        self.assertIn(b"Your groups", groups_response.data)
        self.assertIn(b"Weekend trip", groups_response.data)
        self.assertIn(b"1 member", groups_response.data)


if __name__ == "__main__":
    unittest.main()
