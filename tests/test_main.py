import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main


class ExpenseTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temp_dir.name) / "expenses.json"
        self.budget_path = Path(self.temp_dir.name) / "budget.json"
        self.data_path_patch = patch.object(main, "data_file", self.data_path)
        self.budget_path_patch = patch.object(main, "budget_file", self.budget_path)
        self.data_path_patch.start()
        self.budget_path_patch.start()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self.data_path_patch.stop)
        self.addCleanup(self.budget_path_patch.stop)
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()

    def write_expenses(self, expenses):
        self.data_path.write_text(json.dumps(expenses), encoding="utf-8")

    def read_expenses(self):
        return json.loads(self.data_path.read_text(encoding="utf-8"))

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

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.data_path.exists())
        self.assertIn(b"Choose a valid expense date.", response.data)

    def test_cli_records_a_date_for_new_expenses(self):
        answers = ["food", "10", "2026-03-10", "done"]
        with patch("builtins.input", side_effect=answers), redirect_stdout(io.StringIO()):
            main.run_cli()

        self.assertEqual(
            self.read_expenses(),
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
        self.assertIn(b"Spending chart", response.data)
        self.assertNotIn(b"900.00", response.data)
        self.assertTrue(self.budget_path.exists())


if __name__ == "__main__":
    unittest.main()
