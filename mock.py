"""Mock mode's scripted agent. It exists only to exercise the rules in
tickets.py without a voice session or an API key:

    first message  -> category, amount, and any details already said
    detail answers -> one question per missing detail (tickets.needed_fields);
                      an app issue gets basic steps first, unless already tried
    then           -> tickets.draft_ticket, any note it returns, then read back
                      and "Is that correct?"
    next message   -> tickets.confirm_ticket, the same check a voice call uses

It holds no rules of its own: tickets.py decides what is valid, and a
rejected detail is simply asked again. Standard library only.
"""

import re

import tickets

# First match wins, so the more specific phrases come first. Each keyword is
# a regular expression; most are plain text, found anywhere in the message.
CATEGORY_KEYWORDS = [
    ("double_charge", ["double", "twice", "do baar"]),
    ("late_fee", ["late fee", "fee", "jurmana"]),
    ("failed_pos_transaction", ["slip", "card machine", "pos machine", "merchant",
                                "shopkeeper", "dukaan", "dukandar"]),
    ("card_replacement", ["replace", "replacement", "new card", "naya card", "new one",
                          "damaged", "expired", "card kharab", "card toot"]),
    ("card_block_request", ["lost", "stolen", "chori", "kho gaya", "gum ho"]),
    ("blocked_card", ["block"]),
    # "app" as a whole word only: "happen" and "apply" must not match.
    ("app_issue", [r"\bapps?\b", "website", "biometric", "fingerprint", "login", "log in"]),
    ("failed_transaction", ["fail", "transaction"]),
    ("missing_transfer", ["transfer", "nahi pahuncha", "nahi mila"]),
    ("salary_dispute", ["salary", "tankhwah"]),
    ("loan_delay", ["loan"]),
    ("statement_request", ["statement"]),
    ("cheque_book", ["cheque", "chequebook", "check book", "checkbook"]),
    ("balance_discrepancy", ["balance"]),
]

UNITS = {
    "hazaar": 1_000, "hazar": 1_000, "hajar": 1_000, "thousand": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "laakh": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "karor": 10_000_000,
}
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(" + "|".join(UNITS) + r")?\b", re.IGNORECASE)

URGENT_WORDS = ["urgent", "jaldi", "foran", "emergency"]

QUESTIONS = {
    "full_name": "May I have your full name?",
    "email": "What is the email address the account was opened with?",
    "loss_type": "Was the card lost or stolen?",
    "issue_type": "Is the app crashing, is biometric login failing, or is the website showing an error?",
    "device": "Which phone model are you using?",
    "mailing_address": "What is the mailing address for the new card?",
    "card_type": "Is it a debit card or a credit card?",
    "reason": "Why do you need a replacement card?",
    "account_last6": "What are the last 6 digits of your account number?",
    "cheque_leaves": "Would you like 25 or 50 leaves?",
}
LABELS = {
    "full_name": "name", "email": "email", "loss_type": "card", "issue_type": "issue",
    "device": "device", "mailing_address": "address", "card_type": "card type",
    "reason": "reason", "account_last6": "account ending", "cheque_leaves": "leaves",
}
# Keywords that turn a caller's words into one of tickets.CHOICES.
CHOICE_WORDS = {
    "loss_type": [("stolen", ["stolen", "chori"]), ("lost", ["lost", "kho", "gum"])],
    "issue_type": [("biometric_login_failure", ["biometric", "fingerprint", "face"]),
                   ("website_error", ["website", "browser", "site"]),
                   ("app_crash", ["crash", "band ho", "close", "closing"])],
    "card_type": [("debit", ["debit"]), ("credit", ["credit"])],
    # Not a fixed choice: only used to take a reason already given in the
    # first message, so it is not asked again. An answer is kept as typed.
    "reason": [("damaged", ["damaged", "broken", "kharab", "toot"]),
               ("expired", ["expired", "expire"])],
}

# App issues: basic steps come first, unless the caller already tried them.
STEPS = {
    "website_error": "Please try reloading the page and checking your internet "
                     "connection. If it still does not work, tell me and I will take "
                     "the complaint.",
    "other": "Please try updating the app and restarting your phone. If it still does "
             "not work, tell me and I will take the complaint.",
}
TRIED_WORDS = ["tried everything", "already tried", "tried all", "tried that",
               "sab try", "sab kuch try", "try kar chuka", "try kar chuki", "try kar liya"]
FIXED_WORDS = ["works now", "working now", "fixed", "theek ho gaya", "theek ho gayi",
               "chal gaya", "chal gayi"]


def guess_category(text: str):
    lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(re.search(keyword, lower) for keyword in keywords):
            return category
    return None


def parse_amount(text: str):
    """(amount, words the caller used), or (None, None) if there is no number.
    "20 lakh" -> (2000000, "20 lakh"); "5 hazaar" -> (5000, "5 hazaar")."""
    match = _AMOUNT.search(text)
    if not match:
        return None, None
    number = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "").lower()
    amount = number * UNITS.get(unit, 1)
    amount = int(amount) if amount == int(amount) else round(amount, 2)
    return amount, match.group(0).strip()


def parse_choice(field: str, text: str):
    """The choice the words point to, or None."""
    lower = text.lower()
    for value, words in CHOICE_WORDS.get(field, []):
        if any(word in lower for word in words):
            return value
    return None


def parse_answer(field: str, text: str):
    """A detail answer, shaped for tickets.py, which does the checking."""
    if field in tickets.CHOICES:
        return parse_choice(field, text) or text
    if field == "cheque_leaves":
        match = re.search(r"\d+", text)
        return int(match.group(0)) if match else text
    return text


class MockCall:
    """One mock conversation."""

    def __init__(self):
        self._reset()

    def _reset(self):
        self.ticket_id = None
        self.amount = None
        self.category = None
        self.said = None        # the amount in the caller's own words
        self.urgency = "medium"
        self.details = {}
        self.asking = None      # the detail the last question asked for
        self.address_check = False
        self.steps_done = False  # app issue: basic steps suggested or already tried
        self.lines = []         # every caller line for this complaint

    def reply(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"reply": "Sorry, I didn't catch that."}
        self.lines.append(text)
        if self.ticket_id:
            return self._confirm(text)
        if self.address_check:
            return self._check_address(text)
        if self.asking == "steps":
            self.asking = None
            lower = text.lower()
            if any(w in lower for w in FIXED_WORDS) and not tickets.is_negation(text):
                self._reset()
                return {"reply": "Glad it works now. Is there anything else I can help with?"}
            return self._next()
        if self.asking:
            if any(w in text.lower() for w in TRIED_WORDS):
                self.steps_done = True
            field, self.asking = self.asking, None
            self.details[field] = parse_answer(field, text)
            if field == "mailing_address":
                # Read the address back before drafting, as the prompt does.
                self.address_check = True
                return {"reply": f"I have the address as {text}. Is that correct?"}
            return self._next()
        return self._start(text)

    def _fields(self):
        # A website error needs no phone model; tickets.py decides.
        return tickets.needed_fields(self.category, self.details)

    def _start(self, text: str) -> dict:
        category = guess_category(text)
        if category is None:
            # The rule: anything outside the list is asked, not guessed.
            self.lines = []
            return {"reply": "Could you tell me what the problem is about? For "
                             "example a card, a transaction, a transfer, or a loan."}
        self.category = category
        self.urgency = "high" if any(w in text.lower() for w in URGENT_WORDS) else "medium"
        if category != "cheque_book":  # there, numbers are digits and leaves
            self.amount, self.said = parse_amount(text)
        # Never re-ask what the caller already said.
        for field in self._fields():
            choice = parse_choice(field, text)
            if choice:
                self.details[field] = choice
        self.steps_done = any(w in text.lower() for w in TRIED_WORDS)
        return self._next()

    def _check_address(self, text: str) -> dict:
        self.address_check = False
        if tickets.is_negation(text) or not tickets.is_affirmation(text):
            self.details.pop("mailing_address", None)
            self.asking = "mailing_address"
            return {"reply": "Sorry about that. " + QUESTIONS["mailing_address"]}
        return self._next()

    def _next(self) -> dict:
        """Ask for the next missing detail, or draft when there is none. For an
        app issue, suggest basic steps once the issue type is known."""
        for field in self._fields():
            if field not in self.details:
                self.asking = field
                return {"reply": QUESTIONS[field]}
            if field == "issue_type" and not self.steps_done:
                self.steps_done = True
                self.asking = "steps"
                return {"reply": STEPS.get(self.details["issue_type"], STEPS["other"])}
        return self._draft()

    def _draft(self) -> dict:
        label = self.category.replace("_", " ").replace("pos", "POS")
        result = tickets.draft_ticket(
            category=self.category, amount=self.amount, urgency=self.urgency,
            summary=f"Mock call: the caller reported a {label}.",
            source_utterance=self.lines[0], heard_as="\n".join(self.lines),
            **self.details,
        )
        if not result["ok"]:
            field = result.get("field")
            if field:
                # tickets.py rejected one detail: ask for it again.
                self.details.pop(field, None)
                self.asking = field
                return {"reply": "Sorry, that doesn't look right. " + QUESTIONS[field],
                        "result": result}
            self._reset()
            return {"reply": "Sorry, I couldn't take that down. Could you say it again?",
                    "result": result}
        self.ticket_id, self.amount = result["ticket_id"], result["amount"]
        amount_part = f" of {self.said} rupees" if self.said else ""
        detail_part = "".join(f", {LABELS[k]} {str(v).replace('_', ' ')}"
                              for k, v in result["details"].items())
        # Notes from tickets.py are said before the read-back.
        note = result.get("caller_note") or result.get("maintenance_note")
        article = "an" if label[0] in "aeiou" else "a"
        return {"reply": (note + " " if note else "")
                + f"You reported {article} {label}{amount_part}{detail_part}. Is that correct?",
                "result": result}

    def _confirm(self, text: str) -> dict:
        result = tickets.confirm_ticket(self.ticket_id, self.amount, text)
        if result["ok"]:
            category = self.category
            self._reset()
            opening = {
                "card_block_request": "I have raised a request to block your card.",
                "card_replacement": "A replacement card request has been raised for mailing.",
            }.get(category, "Your complaint is registered.")
            return {"reply": f"{opening} Your reference number is {result['ticket_id']}."
                             " Is there anything else I can help with?",
                    "result": result}
        if result.get("reason") == "negation":
            # The draft stays unfiled; start the complaint over.
            self._reset()
            return {"reply": "Sorry about that. Please tell me the problem again.",
                    "result": result}
        return {"reply": "Sorry, I didn't catch that. Is that correct?", "result": result}
