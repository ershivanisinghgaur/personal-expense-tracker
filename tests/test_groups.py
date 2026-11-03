import json
import re
import tempfile
import unittest
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from unittest.mock import patch

import main


class SharedGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.group_data_path = Path(self.temp_dir.name) / "group_data.json"
        self.group_data_patch = patch.object(
            main, "group_data_file", self.group_data_path
        )
        self.group_data_patch.start()
        self.addCleanup(self.group_data_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        response = self.client.post(
            "/groups", data={"name": "Family trip", "creator": "Asha"}
        )
        self.assertEqual(response.status_code, 302)
        self.group_id = response.location.rsplit("/", 1)[-1]
        self.group = self.read_group()
        self.asha, self.rahul, self.mina = [
            member["id"]
            for member in self.group["members"]
        ] + self.add_members()

    def read_group(self):
        return json.loads(self.group_data_path.read_text(encoding="utf-8"))[0]

    def add_members(self):
        for name in ("Rahul", "Mina"):
            response = self.client.post(
                f"/groups/{self.group_id}/members", data={"name": name}
            )
            self.assertEqual(response.status_code, 302)
        return [member["id"] for member in self.read_group()["members"][1:]]

    def test_group_creation_and_members_are_saved_locally(self):
        response = self.client.get(f"/groups/{self.group_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Family trip", response.data)
        self.assertEqual(
            [member["name"] for member in self.read_group()["members"]],
            ["Asha", "Rahul", "Mina"],
        )

    def test_member_email_can_be_saved_and_used_in_expense_email(self):
        for member_id, email in (
            (self.asha, "asha@example.com"),
            (self.rahul, "rahul@example.com"),
        ):
            response = self.client.post(
                f"/groups/{self.group_id}/members/{member_id}/email",
                data={"email": email},
            )
            self.assertEqual(response.status_code, 302)

        response = self.client.post(
            f"/groups/{self.group_id}/expenses",
            data={
                "description": "Family dinner",
                "amount": "45.50",
                "date": "2026-03-10",
                "paid_by": self.asha,
                "participants": [self.asha, self.rahul],
                "split_type": "equal",
            },
        )
        self.assertEqual(response.status_code, 302)

        page = self.client.get(f"/groups/{self.group_id}")
        markup = page.data.decode()
        match = re.search(r'href="(mailto:[^"]+)"[^>]*>Share by email', markup)
        self.assertIsNotNone(match)
        mailto = urlsplit(unescape(match.group(1)))
        self.assertEqual(mailto.scheme, "mailto")
        self.assertEqual(
            unquote(mailto.path),
            "asha@example.com,rahul@example.com",
        )
        query = parse_qs(mailto.query)
        self.assertEqual(
            query["subject"], ["Family trip expense: Family dinner"]
        )
        self.assertIn("Total: ₹45.50", query["body"][0])
        self.assertIn("Asha: ₹22.75", query["body"][0])
        self.assertIn("Rahul: ₹22.75", query["body"][0])

    def test_invalid_member_email_is_rejected(self):
        response = self.client.post(
            f"/groups/{self.group_id}/members/{self.asha}/email",
            data={"email": "not-an-email"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.read_group()["members"][0]["email"], "")

    def test_equal_split_assigns_remainder_and_suggests_balances(self):
        response = self.client.post(
            f"/groups/{self.group_id}/expenses",
            data={
                "description": "Dinner",
                "amount": "100.00",
                "date": "2026-03-10",
                "paid_by": self.asha,
                "participants": [self.asha, self.rahul, self.mina],
                "split_type": "equal",
            },
        )

        self.assertEqual(response.status_code, 302)
        group = self.read_group()
        shares = group["expenses"][0]["shares"]
        self.assertEqual(shares, {self.asha: 3334, self.rahul: 3333, self.mina: 3333})
        balances = main.calculate_group_balances(group)
        self.assertEqual(balances, {self.asha: 6666, self.rahul: -3333, self.mina: -3333})
        detail = self.client.get(f"/groups/{self.group_id}")
        self.assertIn(b"Suggested settlements", detail.data)
        self.assertIn("₹33.33".encode(), detail.data)

    def test_custom_split_must_sum_to_expense_total(self):
        response = self.client.post(
            f"/groups/{self.group_id}/expenses",
            data={
                "description": "Groceries",
                "amount": "30",
                "date": "2026-03-10",
                "paid_by": self.rahul,
                "participants": [self.asha, self.rahul],
                "split_type": "custom",
                f"share_{self.asha}": "10",
                f"share_{self.rahul}": "15",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Custom shares must add up exactly", response.data)
        self.assertEqual(self.read_group()["expenses"], [])

    def test_custom_split_and_settlement_update_balances(self):
        self.client.post(
            f"/groups/{self.group_id}/expenses",
            data={
                "description": "Tickets",
                "amount": "90",
                "date": "2026-03-10",
                "paid_by": self.asha,
                "participants": [self.asha, self.rahul],
                "split_type": "custom",
                f"share_{self.asha}": "30",
                f"share_{self.rahul}": "60",
            },
        )
        group = self.read_group()
        self.assertEqual(
            group["expenses"][0]["shares"], {self.asha: 3000, self.rahul: 6000}
        )

        response = self.client.post(
            f"/groups/{self.group_id}/settlements",
            data={
                "paid_by": self.rahul,
                "paid_to": self.asha,
                "amount": "60",
                "date": "2026-03-11",
            },
        )

        self.assertEqual(response.status_code, 302)
        group = self.read_group()
        self.assertEqual(
            main.calculate_group_balances(group),
            {self.asha: 0, self.rahul: 0, self.mina: 0},
        )

    def test_settlement_cannot_exceed_amount_owed(self):
        self.client.post(
            f"/groups/{self.group_id}/expenses",
            data={
                "description": "Taxi",
                "amount": "30",
                "date": "2026-03-10",
                "paid_by": self.asha,
                "participants": [self.asha, self.rahul],
                "split_type": "equal",
            },
        )

        response = self.client.post(
            f"/groups/{self.group_id}/settlements",
            data={
                "paid_by": self.rahul,
                "paid_to": self.asha,
                "amount": "20",
                "date": "2026-03-11",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cannot exceed the amount currently owed", response.data)
        self.assertEqual(self.read_group()["settlements"], [])

    def test_deleting_shared_expense_recalculates_balances(self):
        self.client.post(
            f"/groups/{self.group_id}/expenses",
            data={
                "description": "Snacks",
                "amount": "12",
                "date": "2026-03-10",
                "paid_by": self.asha,
                "participants": [self.asha, self.rahul],
                "split_type": "equal",
            },
        )
        expense_id = self.read_group()["expenses"][0]["id"]

        response = self.client.post(
            f"/groups/{self.group_id}/expenses/{expense_id}/delete"
        )

        self.assertEqual(response.status_code, 302)
        group = self.read_group()
        self.assertEqual(group["expenses"], [])
        self.assertEqual(
            main.calculate_group_balances(group),
            {self.asha: 0, self.rahul: 0, self.mina: 0},
        )


if __name__ == "__main__":
    unittest.main()
