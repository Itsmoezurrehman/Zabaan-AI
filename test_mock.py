"""Tests for mock mode's scripted agent in mock.py.

    python -m unittest -v
"""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import mock
import tickets


class MockTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.real_file = tickets.TICKETS_FILE
        tickets.TICKETS_FILE = Path(self.folder.name) / "tickets.json"

    def tearDown(self):
        tickets.TICKETS_FILE = self.real_file
        self.folder.cleanup()

    def test_blocked_card_then_ji_files(self):
        call = mock.MockCall()
        first = call.reply("mera card block ho gaya")
        self.assertIn("Is that correct?", first["reply"])
        second = call.reply("ji")
        self.assertTrue(second["result"]["ok"])
        [ticket] = tickets.list_tickets()
        self.assertEqual(ticket["category"], "blocked_card")
        self.assertIsNone(ticket["amount"])
        self.assertEqual(ticket["status"], "confirmed")
        self.assertIn(ticket["ticket_id"], second["reply"])

    def test_failed_transaction_20_lakh_then_nahi_rejected(self):
        call = mock.MockCall()
        first = call.reply("transaction fail 20 lakh")
        self.assertIn("20 lakh", first["reply"])
        second = call.reply("nahi")
        self.assertFalse(second["result"]["ok"])
        [ticket] = tickets.list_tickets()
        self.assertEqual(ticket["category"], "failed_transaction")
        self.assertEqual(ticket["amount"], 2_000_000)
        self.assertEqual(ticket["status"], "draft")
        self.assertEqual(tickets.list_rejections()[0]["reason"], "negation")

    def test_unclear_reply_asks_again_then_files(self):
        call = mock.MockCall()
        call.reply("salary nahi aayi 50 hazaar")
        self.assertIn("Is that correct?", call.reply("hmm")["reply"])
        self.assertTrue(call.reply("haan ji")["result"]["ok"])

    def test_no_keyword_asks_instead_of_guessing(self):
        call = mock.MockCall()
        call.reply("mujhe ek masla hai")
        self.assertEqual(tickets.list_tickets(), [])

    def talk(self, *lines):
        call = mock.MockCall()
        replies = [call.reply(line) for line in lines]
        return call, replies

    def test_pos_note_said_before_read_back(self):
        _, replies = self.talk("card machine se 3500 kat gaye, slip nahi aayi", "ji")
        first = replies[0]["reply"]
        # The note, then the read-back and the question, all before filing.
        self.assertTrue(first.startswith(tickets.POS_REVERSAL_NOTE), first)
        self.assertIn("failed POS transaction of 3500 rupees. Is that correct?", first)
        self.assertNotIn("working days", replies[-1]["reply"])
        [ticket] = tickets.list_tickets()
        self.assertEqual(ticket["category"], "failed_pos_transaction")
        self.assertEqual(ticket["status"], "confirmed")

    def test_general_failed_transaction_has_no_note(self):
        _, replies = self.talk("ATM transaction fail 5000", "haan")
        self.assertTrue(replies[-1]["result"]["ok"])
        self.assertNotIn("working days", replies[-1]["reply"])

    def test_stolen_card_block_request(self):
        _, replies = self.talk("mera card chori ho gaya", "Ali Raza",
                               "ali at the rate example dot com", "ji haan")
        # "chori" already said it was stolen, so that is never asked.
        self.assertEqual([r["reply"] for r in replies[:2]],
                         [mock.QUESTIONS["full_name"], mock.QUESTIONS["email"]])
        self.assertIn("Is that correct?", replies[2]["reply"])
        self.assertIn("raised a request to block your card", replies[3]["reply"])
        self.assertNotIn("is blocked", replies[3]["reply"])
        [ticket] = tickets.list_tickets()
        self.assertEqual(ticket["category"], "card_block_request")
        self.assertEqual(ticket["details"], {"full_name": "Ali Raza",
                                             "email": "ali@example.com", "loss_type": "stolen"})

    def test_bad_email_is_asked_again(self):
        call, replies = self.talk("I lost my card", "Ali Raza", "ali at example")
        self.assertIn(mock.QUESTIONS["email"], replies[-1]["reply"])
        self.assertEqual(replies[-1]["result"]["field"], "email")
        self.assertEqual(tickets.list_tickets(), [])
        call.reply("ali@example.com")
        self.assertEqual(len(tickets.list_tickets()), 1)

    def test_app_issue_steps_then_phone_model_then_might_note(self):
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop("APP_MAINTENANCE", None)
            _, replies = self.talk("my app is not working", "it crashes", "still not working",
                                   "Samsung A52", "yes")
        said = [r["reply"] for r in replies]
        self.assertEqual(said[0], mock.QUESTIONS["issue_type"])
        self.assertEqual(said[1], mock.STEPS["other"])
        self.assertIn("updating the app and restarting your phone", said[1])
        self.assertEqual(said[2], "Which phone model are you using?")
        self.assertTrue(said[3].startswith(tickets.POSSIBLE_MAINTENANCE_NOTE), said[3])
        self.assertIn("You reported an app issue", said[3])
        self.assertTrue(replies[4]["result"]["ok"])
        self.assertEqual(tickets.list_tickets()[0]["details"],
                         {"issue_type": "app_crash", "device": "Samsung A52"})

    def test_app_issue_tried_everything_skips_steps(self):
        _, replies = self.talk("the app keeps crashing, I already tried everything",
                               "Samsung", "yes")
        self.assertEqual(replies[0]["reply"], mock.QUESTIONS["device"])
        self.assertIn("might be undergoing maintenance", replies[1]["reply"])
        self.assertTrue(replies[2]["result"]["ok"])

    def test_app_issue_tried_everything_after_steps(self):
        _, replies = self.talk("biometric login is failing", "I already tried everything",
                               "iPhone 13", "yes")
        self.assertEqual(replies[0]["reply"], mock.STEPS["other"])
        self.assertEqual(replies[1]["reply"], mock.QUESTIONS["device"])
        self.assertTrue(replies[3]["result"]["ok"])
        self.assertEqual(tickets.list_tickets()[0]["details"]["issue_type"],
                         "biometric_login_failure")

    def test_app_issue_with_maintenance(self):
        with unittest.mock.patch.dict(os.environ, {"APP_MAINTENANCE": "1"}):
            _, replies = self.talk("mobile app crash ho rahi hai", "tried that",
                                   "Samsung A52", "ji")
        self.assertTrue(replies[2]["reply"].startswith(tickets.MAINTENANCE_NOTE))
        self.assertNotIn("might", replies[2]["reply"])
        self.assertTrue(replies[3]["result"]["ok"])

    def test_website_error_never_asks_for_a_device(self):
        _, replies = self.talk("website pe error aa raha hai", "abhi bhi error hai", "yes")
        said = [r["reply"] for r in replies]
        self.assertEqual(said[0], mock.STEPS["website_error"])
        self.assertIn("You reported an app issue, issue website error. Is that correct?", said[1])
        for line in said:
            self.assertNotIn("browser are you", line)
            self.assertNotIn("phone model", line)
        self.assertTrue(replies[2]["result"]["ok"])
        self.assertEqual(tickets.list_tickets()[0]["details"], {"issue_type": "website_error"})

    def test_app_fixed_by_steps_files_nothing(self):
        _, replies = self.talk("app crash ho rahi hai", "ok it works now")
        self.assertIn("Glad it works now", replies[1]["reply"])
        self.assertEqual(tickets.list_tickets(), [])

    def test_replacement_reason_from_first_message_not_asked(self):
        _, replies = self.talk("my card is damaged, please send a new one", "Ali Raza",
                               "ali@example.com", "Flat 3, Lahore", "yes", "credit", "yes")
        said = [r["reply"] for r in replies]
        self.assertNotIn(mock.QUESTIONS["reason"], said)
        self.assertIn("reason damaged. Is that correct?", said[5])
        self.assertIn("replacement card request has been raised", said[6])
        self.assertEqual(tickets.list_tickets()[0]["details"]["reason"], "damaged")

    def test_card_replacement_reads_address_back_first(self):
        _, replies = self.talk("debit card replacement chahiye", "Ali Raza",
                               "ali@example.com", "House 12, Street 5, Karachi")
        # "debit" was already said; the address is read back before any draft.
        self.assertNotIn(mock.QUESTIONS["card_type"], [r["reply"] for r in replies])
        self.assertIn("House 12, Street 5, Karachi", replies[-1]["reply"])
        self.assertIn("Is that correct?", replies[-1]["reply"])
        self.assertEqual(tickets.list_tickets(), [])

    def test_card_replacement_wrong_address_asked_again_then_files(self):
        call, _ = self.talk("I need a replacement card", "Ali Raza", "ali@example.com",
                            "House 12 Karachi")
        self.assertIn(mock.QUESTIONS["mailing_address"], call.reply("nahi")["reply"])
        call.reply("House 14, Karachi")
        self.assertEqual(call.reply("ji")["reply"], mock.QUESTIONS["card_type"])
        call.reply("credit")
        self.assertIn("Is that correct?", call.reply("chip damaged")["reply"])
        done = call.reply("haan")
        self.assertIn("replacement card request has been raised", done["reply"])
        [ticket] = tickets.list_tickets()
        self.assertEqual(ticket["details"]["mailing_address"], "House 14, Karachi")
        self.assertEqual(ticket["details"]["card_type"], "credit")

    def test_cheque_book_last6(self):
        call, replies = self.talk("cheque book chahiye", "Ali Raza", "12345")
        self.assertEqual(replies[1]["reply"], mock.QUESTIONS["account_last6"])
        self.assertEqual(replies[2]["reply"], mock.QUESTIONS["cheque_leaves"])
        # 12345 is only five digits: tickets.py rejects it once all is collected.
        again = call.reply("50")
        self.assertEqual(again["result"]["field"], "account_last6")
        call.reply("012345")
        self.assertTrue(call.reply("ji")["result"]["ok"])
        [ticket] = tickets.list_tickets()
        self.assertEqual(ticket["details"], {"full_name": "Ali Raza",
                                             "account_last6": "012345", "cheque_leaves": 50})
        self.assertIsNone(ticket["amount"])

    def test_parse_amount(self):
        cases = {
            "20 lakh": 2_000_000, "5 hazaar": 5_000, "2.5 lakh": 250_000,
            "1 crore": 10_000_000, "20,000 rupees": 20_000, "500": 500,
        }
        for text, expected in cases.items():
            self.assertEqual(mock.parse_amount(text)[0], expected, text)
        self.assertEqual(mock.parse_amount("card block"), (None, None))

    def test_guess_category(self):
        self.assertEqual(mock.guess_category("mera card block ho gaya"), "blocked_card")
        self.assertEqual(mock.guess_category("transaction fail"), "failed_transaction")
        self.assertEqual(mock.guess_category("paise do baar kat gaye"), "double_charge")
        self.assertEqual(mock.guess_category("dukaan pe card machine ne slip nahi di"),
                         "failed_pos_transaction")
        self.assertEqual(mock.guess_category("mera card kho gaya"), "card_block_request")
        self.assertEqual(mock.guess_category("I need a new card"), "card_replacement")
        self.assertEqual(mock.guess_category("website error aa raha hai"), "app_issue")
        self.assertEqual(mock.guess_category("cash deposit nahi hua"), None)
        self.assertEqual(mock.guess_category("my app is not working"), "app_issue")
        self.assertEqual(mock.guess_category("mobile banking app keeps closing"), "app_issue")
        self.assertEqual(mock.guess_category("my card expired"), "card_replacement")
        self.assertEqual(mock.guess_category("my card is damaged"), "card_replacement")
        # "app" only as a whole word.
        self.assertEqual(mock.guess_category("what happened to my salary"), "salary_dispute")
        self.assertIsNone(mock.guess_category("I want to apply"))
        self.assertIsNone(mock.guess_category("hello"))


if __name__ == "__main__":
    unittest.main()
