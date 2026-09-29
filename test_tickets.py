"""Tests for the ticket rules in tickets.py.

    python -m unittest -v
"""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import tickets

# Details that pass for every category that needs them.
VALID_DETAILS = {
    "card_block_request": {"full_name": "Ali Raza", "email": "ali.raza@example.com",
                           "loss_type": "stolen"},
    "app_issue": {"issue_type": "app_crash", "device": "Samsung Galaxy A52"},
    "card_replacement": {"full_name": "Ali Raza", "email": "ali.raza@example.com",
                         "mailing_address": "House 12, Street 5, Karachi",
                         "card_type": "debit", "reason": "The chip is damaged."},
    "cheque_book": {"full_name": "Ali Raza", "account_last6": "012345", "cheque_leaves": 25},
}


class TicketTest(unittest.TestCase):
    def setUp(self):
        # Every test gets its own empty tickets.json.
        self.folder = tempfile.TemporaryDirectory()
        self.real_file = tickets.TICKETS_FILE
        tickets.TICKETS_FILE = Path(self.folder.name) / "tickets.json"

    def tearDown(self):
        tickets.TICKETS_FILE = self.real_file
        self.folder.cleanup()

    def draft(self, **changes):
        fields = {
            "category": "failed_transaction",
            "amount": 5000,
            "summary": "ATM did not give cash but the account was debited.",
            "urgency": "high",
            "time_reference": "yesterday evening",
            "source_utterance": "ATM se paise nahi nikle lekin account se kat gaye",
            "heard_as": "एटीएम से पैसे नहीं निकले लेकिन अकाउंट से कट गए",
        }
        fields.update(changes)
        return tickets.draft_ticket(**fields)

    def confirm_with(self, text, amount=5000):
        ticket_id = self.draft()["ticket_id"]
        return tickets.confirm_ticket(ticket_id, amount, text)

    # --- draft rules ---

    def test_bad_category_rejected(self):
        result = self.draft(category="atm_problem")
        self.assertFalse(result["ok"])
        self.assertEqual(tickets.list_tickets(), [])

    def test_negative_amount_rejected(self):
        self.assertFalse(self.draft(amount=-1)["ok"])

    def test_amount_above_limit_rejected(self):
        self.assertFalse(self.draft(amount=10_000_001)["ok"])

    def test_draft_is_saved_as_draft(self):
        result = self.draft()
        self.assertTrue(result["ok"])
        [saved] = tickets.list_tickets()
        self.assertEqual(saved["status"], "draft")

    # --- confirm rules ---

    def test_confirm_missing_ticket_rejected(self):
        result = tickets.confirm_ticket("ZB-NOPE00", 5000, "haan ji")
        self.assertFalse(result["ok"])

    def test_confirm_twice_rejected(self):
        ticket_id = self.draft()["ticket_id"]
        self.assertTrue(tickets.confirm_ticket(ticket_id, 5000, "haan ji")["ok"])
        self.assertFalse(tickets.confirm_ticket(ticket_id, 5000, "haan ji")["ok"])

    def test_amount_mismatch_rejected(self):
        result = self.confirm_with("haan ji", amount=500)
        self.assertFalse(result["ok"])
        self.assertEqual(tickets.list_tickets()[0]["status"], "draft")

    def test_no_affirmation_rejected(self):
        result = self.confirm_with("nahi, amount ghalat hai")
        self.assertFalse(result["ok"])
        self.assertEqual(tickets.list_tickets()[0]["status"], "draft")

    def test_confirmed_ticket_is_saved_as_confirmed(self):
        self.assertTrue(self.confirm_with("ji bilkul")["ok"])
        self.assertEqual(tickets.list_tickets()[0]["status"], "confirmed")

    # --- caller_note: only failed_pos_transaction, from the draft ---

    def confirm_draft(self, **changes):
        draft = self.draft(**changes)
        return tickets.confirm_ticket(ticket_id=draft["ticket_id"],
                                      confirmed_amount=draft["amount"],
                                      affirmation_text="ji haan")

    def test_failed_pos_draft_gets_caller_note_before_read_back(self):
        draft = self.draft(category="failed_pos_transaction")
        self.assertTrue(draft["ok"])
        self.assertEqual(draft["caller_note"], tickets.POS_REVERSAL_NOTE)
        self.assertEqual(draft["caller_note"], (
            "This amount is usually reversed automatically within 7 to 14 working days, "
            "so you need not worry. I am also filing a complaint so we can track it in "
            "case the reversal does not arrive."))
        # Said first, then the normal read-back and question.
        self.assertIn("First say the caller_note. Briefly read back", draft["message"])
        self.assertIn("Is that correct?", draft["message"])
        self.assertEqual(tickets.list_tickets()[0]["status"], "draft")

    def test_confirm_never_returns_caller_note(self):
        for reply in ("ji haan", "nahi"):
            draft = self.draft(category="failed_pos_transaction")
            result = tickets.confirm_ticket(draft["ticket_id"], draft["amount"], reply)
            self.assertNotIn("caller_note", result, reply)
        self.assertNotIn("caller_note", self.confirm_draft(category="failed_pos_transaction"))

    def test_general_failed_transaction_gets_no_note(self):
        # The default draft is an ATM failure.
        draft = self.draft()
        self.assertTrue(draft["ok"])
        self.assertNotIn("caller_note", draft)
        self.assertNotIn("First say", draft["message"])

    def test_no_other_category_gets_the_note(self):
        for category in tickets.CATEGORIES:
            if category == "failed_pos_transaction":
                continue
            draft = self.draft(category=category, **VALID_DETAILS.get(category, {}))
            self.assertTrue(draft["ok"], category)
            self.assertNotIn("caller_note", draft, category)

    def test_card_machine_flag_is_gone(self):
        with self.assertRaises(TypeError):
            self.draft(card_machine_payment=True)

    # --- categories with details ---

    def detail_draft(self, category, **changes):
        fields = dict(VALID_DETAILS[category])
        fields.update(changes)
        return self.draft(category=category, amount=None, **fields)

    def test_every_detail_category_drafts_with_valid_details(self):
        for category in tickets.DETAIL_FIELDS:
            result = self.detail_draft(category)
            self.assertTrue(result["ok"], (category, result))
            self.assertEqual(set(result["details"]), set(tickets.DETAIL_FIELDS[category]))

    def test_each_missing_detail_is_named(self):
        for category, fields in tickets.DETAIL_FIELDS.items():
            for field in fields:
                result = self.detail_draft(category, **{field: None})
                self.assertFalse(result["ok"], (category, field))
                self.assertEqual(result["field"], field)
        self.assertEqual(tickets.list_tickets(), [])

    def test_bad_choices_rejected(self):
        self.assertEqual(self.detail_draft("card_block_request", loss_type="misplaced")["field"],
                         "loss_type")
        self.assertEqual(self.detail_draft("app_issue", issue_type="slow")["field"], "issue_type")
        self.assertEqual(self.detail_draft("card_replacement", card_type="prepaid")["field"],
                         "card_type")

    def test_choices_ignore_case(self):
        result = self.detail_draft("card_block_request", loss_type=" Stolen ")
        self.assertEqual(result["details"]["loss_type"], "stolen")

    def test_details_of_other_categories_are_not_stored(self):
        result = self.detail_draft("card_block_request", device="iPhone", cheque_leaves=25)
        self.assertNotIn("device", tickets.list_tickets()[0]["details"])
        self.assertNotIn("cheque_leaves", result["details"])
        self.assertEqual(self.draft()["details"], {})

    def test_block_and_replacement_are_requests_not_actions(self):
        for category in ("card_block_request", "card_replacement"):
            draft = self.detail_draft(category)
            result = tickets.confirm_ticket(draft["ticket_id"], None, "ji haan")
            self.assertTrue(result["ok"])
            self.assertIn("request", result["message"])
        draft = self.detail_draft("card_block_request")
        result = tickets.confirm_ticket(draft["ticket_id"], None, "ji")
        self.assertIn("Do not say the card is blocked", result["message"])

    # --- email ---

    def test_spoken_email_normalised(self):
        cases = {
            "moez at the rate gmail dot com": "moez@gmail.com",
            "Moez At The Rate Of Gmail Dot Com": "moez@gmail.com",
            "ali underscore khan at yahoo dot co dot uk": "ali_khan@yahoo.co.uk",
            "sara dash ahmed at outlook dotcom": "sara-ahmed@outlook.com",
            "moez at gmail dot com": "moez@gmail.com",
            "moez@gmail.com": "moez@gmail.com",
            "fatima@example.com": "fatima@example.com",  # "at" inside a word stays
        }
        for spoken, expected in cases.items():
            self.assertEqual(tickets.normalize_email(spoken), expected, spoken)

    def test_email_shape(self):
        for good in ["a@b.co", "moez.khan@mail.example.pk"]:
            self.assertTrue(tickets.valid_email(good), good)
        for bad in ["moez", "moez@gmail", "moez@@gmail.com", "a@b@c.com", "@gmail.com",
                    "moez@gmail.", "moez@.com", ""]:
            self.assertFalse(tickets.valid_email(bad), bad)

    def test_spoken_email_stored_normalised(self):
        result = self.detail_draft("card_block_request", email="moez at the rate gmail dot com")
        self.assertEqual(result["details"]["email"], "moez@gmail.com")

    def test_bad_email_rejected(self):
        result = self.detail_draft("card_replacement", email="moez at gmail")
        self.assertFalse(result["ok"])
        self.assertEqual(result["field"], "email")

    # --- last 6 digits ---

    def test_account_last6_exactly_six_digits(self):
        for good, stored in [("123456", "123456"), ("012345", "012345"),
                             ("12 34 56", "123456"), (654321, "654321")]:
            result = self.detail_draft("cheque_book", account_last6=good)
            self.assertTrue(result["ok"], good)
            self.assertEqual(result["details"]["account_last6"], stored)
        for bad in ["12345", "1234567", "12345a", "१२३४५६",
                    True, 12345, "", "0123456789012"]:
            result = self.detail_draft("cheque_book", account_last6=bad)
            self.assertEqual(result.get("field"), "account_last6", bad)

    def test_six_digits_still_stripped_everywhere_else(self):
        self.detail_draft("cheque_book", account_last6="482913",
                          summary="Caller wants a cheque book for account ending 482913.",
                          heard_as="last digits 482913", full_name="Ali 482913")
        [saved] = tickets.list_tickets()
        self.assertEqual(saved["details"]["account_last6"], "482913")
        for text in (saved["summary"], saved["heard_as"], saved["details"]["full_name"]):
            self.assertNotIn("482913", text)

    def test_cheque_leaves_25_or_50(self):
        for good in [25, 50, "50", 25.0]:
            self.assertTrue(self.detail_draft("cheque_book", cheque_leaves=good)["ok"], good)
        for bad in [10, 100, "fifty", True, 25.5]:
            self.assertEqual(self.detail_draft("cheque_book", cheque_leaves=bad).get("field"),
                             "cheque_leaves", bad)

    # --- masking ---

    def test_masking(self):
        self.assertEqual(tickets.mask_email("moez@gmail.com"), "m***@gmail.com")
        self.assertEqual(tickets.mask_address("House 12, Street 5, Karachi"), "House …")
        self.assertEqual(tickets.mask_address("Lahore"), "Lah…")

    def test_public_tickets_mask_email_and_address(self):
        self.detail_draft("card_replacement")
        [public] = tickets.list_public_tickets()
        self.assertEqual(public["details"]["email"], "a***@example.com")
        self.assertEqual(public["details"]["mailing_address"], "House …")
        self.assertEqual(public["details"]["full_name"], "Ali Raza")
        # The stored ticket keeps the real values.
        self.assertEqual(tickets.list_tickets()[0]["details"]["email"], "ali.raza@example.com")

    def test_heard_as_emails_masked(self):
        cases = {
            "mera email ali dot raza at the rate example dot com hai":
                "mera email a***@example.com hai",
            "it is moez@gmail.com.": "it is m***@gmail.com.",
            "sara underscore khan at yahoo dot co dot uk": "s***@yahoo.co.uk",
            "moez at gmail dot com": "m***@gmail.com",
            # Not an email: left alone.
            "I was at the branch yesterday": "I was at the branch yesterday",
            "mera card block ho gaya": "mera card block ho gaya",
        }
        for said, shown in cases.items():
            self.assertEqual(tickets.mask_heard_as(said), shown, said)

    def test_heard_as_addresses_masked(self):
        self.assertEqual(tickets.mask_heard_as("my address is House 12, Street 5, Karachi"),
                         "my address is House …")
        self.assertEqual(tickets.mask_heard_as("street number 7 Lahore"), "street…")
        # No address word with a number, but it is the ticket's own address.
        self.assertEqual(tickets.mask_heard_as("Gulshan-e-Iqbal, Karachi",
                                               "Gulshan-e-Iqbal Karachi"), "Gulsha…")
        # Other lines are untouched, line breaks kept.
        self.assertEqual(tickets.mask_heard_as("ji\nHouse 4 Karachi\nhaan"),
                         "ji\nHouse …\nhaan")

    def test_heard_as_masked_for_display_only(self):
        heard = "email ali at the rate example dot com\nHouse 12, Street 5, Karachi"
        self.detail_draft("card_replacement", heard_as=heard)
        [public] = tickets.list_public_tickets()
        self.assertNotIn("raza", public["heard_as"])
        self.assertNotIn("Street 5", public["heard_as"])
        self.assertEqual(tickets.list_tickets()[0]["heard_as"], heard)

    def test_public_tickets_handle_old_tickets_without_details(self):
        self.draft()
        tickets._save([{k: v for k, v in t.items() if k != "details"}
                       for t in tickets.list_tickets()])
        self.assertEqual(tickets.list_public_tickets()[0]["details"], {})

    # --- maintenance (simulated) ---

    def test_maintenance_note_definite_only_when_flag_set(self):
        with unittest.mock.patch.dict(os.environ, {"APP_MAINTENANCE": "1"}):
            on = self.detail_draft("app_issue")
            other = self.draft()
        with unittest.mock.patch.dict(os.environ, {"APP_MAINTENANCE": "0"}):
            zero = self.detail_draft("app_issue")
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop("APP_MAINTENANCE", None)
            off = self.detail_draft("app_issue")
        self.assertEqual(on["maintenance_note"], tickets.MAINTENANCE_NOTE)
        self.assertIn("scheduled maintenance", on["maintenance_note"])
        self.assertNotIn("maintenance_note", other)
        # Off: the "tried everything" reply, with "might".
        for result in (zero, off):
            self.assertEqual(result["maintenance_note"], (
                "The app might be undergoing maintenance on some features. I am filing "
                "a complaint for you in case it still does not work later today or "
                "tomorrow."))
            self.assertIn("First say the maintenance_note.", result["message"])

    # --- app issue: phone model, never a browser ---

    def test_website_error_needs_no_device(self):
        result = self.detail_draft("app_issue", issue_type="website_error", device=None)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["details"], {"issue_type": "website_error"})
        # A browser sent anyway is not stored.
        result = self.detail_draft("app_issue", issue_type="website_error", device="Chrome")
        self.assertNotIn("device", result["details"])

    def test_app_problem_needs_phone_model(self):
        for issue in ("app_crash", "biometric_login_failure"):
            result = self.detail_draft("app_issue", issue_type=issue, device=None)
            self.assertEqual(result["field"], "device", issue)
            self.assertIn("phone model", result["error"])
            self.assertIn("never ask for a browser", result["error"])

    def test_needed_fields(self):
        self.assertEqual(tickets.needed_fields("app_issue", {"issue_type": "Website_Error"}),
                         ["issue_type"])
        self.assertEqual(tickets.needed_fields("app_issue", {}), ["issue_type", "device"])
        self.assertEqual(tickets.needed_fields("statement_request", {}), [])

    # --- affirmations in three scripts ---

    def test_roman_affirmation_accepted(self):
        self.assertTrue(self.confirm_with("ji bilkul")["ok"])

    def test_devanagari_affirmation_accepted(self):
        self.assertTrue(self.confirm_with("हाँ जी")["ok"])

    def test_urdu_script_affirmation_accepted(self):
        self.assertTrue(self.confirm_with("ہاں جی")["ok"])

    def test_common_replies_accepted(self):
        self.assertTrue(tickets.is_affirmation("Haan, theek hai."))
        self.assertTrue(tickets.is_affirmation("Yes, that's correct."))

    def test_ok_ji_accepted(self):
        self.assertTrue(self.confirm_with("ok ji")["ok"])

    def test_han_theek_hai_accepted(self):
        self.assertTrue(self.confirm_with("han theek hai")["ok"])

    def test_ok_in_three_scripts(self):
        for text in ["Okay.", "ओके जी", "اوکے"]:
            self.assertTrue(tickets.is_affirmation(text), text)

    def test_thank_you_alone_rejected(self):
        # Politeness is not confirmation.
        self.assertFalse(self.confirm_with("thank you")["ok"])
        self.assertFalse(tickets.is_affirmation("shukriya"))
        self.assertEqual(tickets.list_tickets()[0]["status"], "draft")

    # --- negations ---

    def test_no_thats_not_correct_rejected(self):
        self.assertFalse(self.confirm_with("no, that's not correct")["ok"])
        self.assertEqual(tickets.list_tickets()[0]["status"], "draft")

    def test_haan_lekin_ghalat_rejected(self):
        self.assertFalse(self.confirm_with("haan, lekin amount ghalat hai")["ok"])

    def test_negation_in_other_scripts_rejected(self):
        self.assertFalse(self.confirm_with("हाँ, लेकिन अमाउंट गलत है")["ok"])
        self.assertFalse(tickets.confirm_ticket(self.draft()["ticket_id"], 5000,
                                                "جی نہیں")["ok"])

    def test_known_limit_fails_safe(self):
        # Documented in README.md (honest limits): real yeses with a negation word are
        # rejected, and the agent asks again. Safe direction of failure.
        self.assertFalse(self.confirm_with("haan, koi masla nahi")["ok"])
        self.assertFalse(tickets.confirm_ticket(self.draft()["ticket_id"], 5000,
                                                "theek hai na?")["ok"])

    def test_nukta_spelling_either_way(self):
        # ग़ can be one code point (U+095A) or ग plus nukta; NFC makes them equal.
        self.assertTrue(tickets.is_negation("ग़लत"))
        self.assertTrue(tickets.is_negation("ग़लत"))

    def test_negation_is_whole_word(self):
        # "not" inside "note", "na" inside "naam": no match.
        self.assertFalse(tickets.is_negation("haan, note kar lein, mera naam Ali"))

    # --- optional amount ---

    def test_loan_delay_without_amount_drafts_and_confirms(self):
        result = self.draft(category="loan_delay", amount=None,
                            summary="Loan application pending for three weeks.")
        self.assertTrue(result["ok"])
        self.assertIsNone(tickets.list_tickets()[0]["amount"])
        confirmed = tickets.confirm_ticket(result["ticket_id"], None, "ji haan")
        self.assertTrue(confirmed["ok"])
        self.assertEqual(tickets.list_tickets()[0]["status"], "confirmed")

    def test_amount_on_confirm_when_draft_has_none_rejected(self):
        ticket_id = self.draft(category="blocked_card", amount=None)["ticket_id"]
        self.assertFalse(tickets.confirm_ticket(ticket_id, 0, "yes")["ok"])

    def test_no_amount_on_confirm_when_draft_has_one_rejected(self):
        ticket_id = self.draft(amount=5000)["ticket_id"]
        self.assertFalse(tickets.confirm_ticket(ticket_id, None, "yes")["ok"])

    def test_tool_messages_never_teach_confirm_words(self):
        # The accepted words are checked in code only, never spoken.
        draft = self.draft()
        rejected = tickets.confirm_ticket(draft["ticket_id"], 5000, "hmm")
        for message in (draft["message"], rejected["error"]):
            self.assertNotIn("haan", message.lower())
            self.assertNotIn("theek", message.lower())

    def test_arabic_heh_counts(self):
        text = "هاں"  # هاں, with Arabic heh U+0647
        self.assertTrue(tickets.is_affirmation(text))

    def test_khan_is_not_han(self):
        self.assertFalse(tickets.is_affirmation("Mera naam Khan hai"))

    def test_alright_is_not_right(self):
        self.assertFalse(tickets.is_affirmation("alright"))

    # --- rejection log ---

    def test_every_rejection_reason_is_logged(self):
        ticket_id = self.draft()["ticket_id"]
        tickets.confirm_ticket(ticket_id, 5000, "hmm")            # no affirmation
        tickets.confirm_ticket(ticket_id, 5000, "nahi")           # negation
        tickets.confirm_ticket(ticket_id, 500, "haan ji")         # amount mismatch
        tickets.confirm_ticket(ticket_id, 5000, "haan ji")        # filed
        tickets.confirm_ticket(ticket_id, 5000, "haan ji")        # already confirmed
        tickets.confirm_ticket("ZB-NOPE00", 5000, "haan ji")      # no such ticket
        reasons = [r["reason"] for r in tickets.list_rejections()]
        self.assertEqual(reasons, ["no_affirmation", "negation", "amount_mismatch",
                                   "already_confirmed", "no_such_ticket"])
        self.assertEqual(set(reasons), set(tickets.REJECTION_REASONS))

    def test_rejection_result_carries_reason(self):
        result = self.confirm_with("nahi")
        self.assertEqual(result["reason"], "negation")

    def test_successful_confirm_is_not_logged(self):
        self.confirm_with("ji bilkul")
        self.assertEqual(tickets.list_rejections(), [])

    def test_logged_reply_is_redacted(self):
        self.confirm_with("mera otp 482913 hai")
        self.assertNotIn("482913", tickets.list_rejections()[0]["reply"])

    # --- stripping secrets ---

    def test_otp_stripped_from_summary(self):
        self.draft(summary="Caller read out OTP 482913 before we could stop them.")
        saved = tickets.list_tickets()[0]
        self.assertNotIn("482913", saved["summary"])
        self.assertIn(tickets.REMOVED, saved["summary"])

    def test_card_number_stripped(self):
        self.draft(heard_as="mera card 4111 1111 1111 1111 block hai")
        self.assertNotIn("4111", tickets.list_tickets()[0]["heard_as"])

    def test_long_heard_as_kept_whole_with_line_breaks(self):
        lines = ["मेरा लोन एप्लीकेशन पेंडिंग है 60 दिन से।"] * 30 + ["कल"]
        heard = "\n".join(lines)  # about 1,200 characters
        self.draft(heard_as=heard)
        self.assertEqual(tickets.list_tickets()[0]["heard_as"], heard)

    def test_amount_itself_is_kept(self):
        self.draft(amount=25000, summary="Charged 25000 twice for one purchase.")
        self.assertIn("25000", tickets.list_tickets()[0]["summary"])


if __name__ == "__main__":
    unittest.main()
