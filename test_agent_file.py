"""Checks that agents/zabaan.jsonc agrees with tickets.py.

    python -m unittest -v
"""

import unittest

import tickets
from lib import read_agent


class AgentFileTest(unittest.TestCase):
    def setUp(self):
        self.agent = read_agent("zabaan")
        self.tools = {tool["name"]: tool for tool in self.agent["tools"]}

    def test_all_tools_are_client_side(self):
        self.assertEqual(set(self.tools), {"create_ticket", "confirm_ticket", "end_call"})
        for tool in self.tools.values():
            self.assertNotIn("http", tool)

    def test_end_call_only_after_goodbye(self):
        self.assertEqual(self.tools["end_call"]["parameters"]["properties"], {})
        self.assertIn("Only call end_call after saying the goodbye line, "
                      "never mid-conversation.", self.agent["system_prompt"])

    def test_categories_match_tickets_py(self):
        enum = self.tools["create_ticket"]["parameters"]["properties"]["category"]["enum"]
        self.assertEqual(enum, tickets.CATEGORIES)

    def test_urgencies_match_tickets_py(self):
        enum = self.tools["create_ticket"]["parameters"]["properties"]["urgency"]["enum"]
        self.assertEqual(enum, tickets.URGENCIES)

    def test_browser_fields_are_not_model_parameters(self):
        for tool in self.tools.values():
            properties = tool["parameters"]["properties"]
            self.assertNotIn("heard_as", properties)
            self.assertNotIn("affirmation_text", properties)

    def test_language_codes(self):
        self.assertEqual(self.agent["input"]["language_codes"], ["en", "hi"])

    def test_prompt_never_teaches_confirm_words(self):
        prompt = self.agent["system_prompt"]
        self.assertIn("Never tell the caller which words to use to confirm.", prompt)
        self.assertNotIn("haan, theek hai", prompt)
        self.assertIn('"Is that correct?"', prompt)

    def test_key_prompt_lines(self):
        prompt = self.agent["system_prompt"]
        for line in [
            '"Thank you for calling Roshan Trust Bank. Goodbye and Allah Hafiz."',
            "Do not ask for anything the caller has already told you. If they "
            "said how long it has been pending, use that as the time reference.",
            "When reading back an amount, use the same units the caller used. "
            "If they said lakh or crore, say lakh or crore.",
        ]:
            self.assertIn(line, prompt)
        self.assertNotIn("Goodbye, Allah Hafiz", prompt)

    def test_pos_note_lines(self):
        prompt = self.agent["system_prompt"]
        self.assertIn("card machine printed no slip", prompt)
        self.assertIn("use failed_pos_transaction, even if the caller does not call it that",
                      prompt)
        self.assertIn("Collect only what is still missing", prompt)
        self.assertIn("never as a promise", prompt)
        # The reversal wording lives only in tickets.py.
        self.assertNotIn("working days", prompt)
        self.assertNotIn("card_machine_payment", prompt)
        self.assertNotIn("card_machine_payment",
                         self.tools["create_ticket"]["parameters"]["properties"])

    def test_detail_fields_match_tickets_py(self):
        props = self.tools["create_ticket"]["parameters"]["properties"]
        for field in tickets.DETAIL_ASKS:
            self.assertIn(field, props)
            # Only the category decides which details are needed.
            self.assertNotIn(field, self.tools["create_ticket"]["parameters"]["required"])
        for field, choices in tickets.CHOICES.items():
            self.assertEqual(props[field]["enum"], choices)
        self.assertEqual(props["cheque_leaves"]["enum"], tickets.CHEQUE_LEAVES)
        # A string, so a leading zero survives.
        self.assertEqual(props["account_last6"]["type"], "string")

    def test_new_case_lines(self):
        prompt = self.agent["system_prompt"]
        for line in [
            "Do not assume what the issue is. If the input is vague, ask what "
            "happened; do not decide the type yourself.",
            "say you have raised a request to block the card. Never say the card is blocked.",
            "a replacement card request has been raised",
            "Read the mailing address back and ask \"Is that correct?\" before you "
            "call create_ticket.",
            "Ask only for the last 6 digits. Never ask for the full account number.",
            "Never ask for card numbers, PINs, passwords, or OTPs.",
        ]:
            self.assertIn(line, prompt)
        # Wording that lives only in tickets.py.
        self.assertNotIn("scheduled maintenance", prompt)
        self.assertNotIn("might be undergoing maintenance", prompt)

    def test_notes_are_said_before_the_read_back(self):
        prompt = self.agent["system_prompt"]
        self.assertIn("If it returns a caller_note or a maintenance_note, say that note "
                      "first, as written, never as a promise. Then briefly read back", prompt)
        # confirm_ticket no longer returns a note.
        self.assertNotIn("confirm_ticket returned a caller_note", prompt)

    def test_app_issue_lines(self):
        prompt = self.agent["system_prompt"]
        for line in [
            "For an app problem, also ask which phone model they use. Never ask for a "
            "browser. For a website error, ask no device question.",
            "updating the app, restarting the phone",
            "or the caller says they already tried everything, suggest nothing more and "
            "call create_ticket.",
        ]:
            self.assertIn(line, prompt)
        self.assertNotIn("phone or browser", prompt)
        device = self.tools["create_ticket"]["parameters"]["properties"]["device"]
        self.assertIn("Never a browser", device["description"])

    def test_amount_is_optional(self):
        self.assertNotIn("amount", self.tools["create_ticket"]["parameters"]["required"])
        self.assertNotIn("confirmed_amount", self.tools["confirm_ticket"]["parameters"]["required"])

    def test_bank_name(self):
        self.assertIn("Roshan Trust Bank", self.agent["greeting"])
        self.assertNotIn("Demo Bank", self.agent["system_prompt"])


if __name__ == "__main__":
    unittest.main()
