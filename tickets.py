"""Zabaan AI ticket rules. Every filing rule (see README.md) lives here, in code, so
the model cannot talk its way past them.

    draft_ticket(...)    saves a DRAFT. Never files anything.
    confirm_ticket(...)  the only thing that files a ticket.

Standard library only. Tickets are stored in tickets.json next to this file.
Every function returns a dict: {"ok": True, ...} or {"ok": False, "error": ...}.
The error text is read by the agent, so it says what to do next.
"""

import json
import math
import os
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

TICKETS_FILE = Path(__file__).resolve().parent / "tickets.json"

CATEGORIES = [
    "failed_transaction", "failed_pos_transaction", "blocked_card",
    "card_block_request", "card_replacement", "app_issue", "statement_request",
    "balance_discrepancy", "missing_transfer", "cheque_book",
    "salary_dispute", "double_charge", "late_fee", "loan_delay", "other",
]
URGENCIES = ["low", "medium", "high"]
# Returned by create_ticket for failed_pos_transaction, and no other
# category, so the agent says it before the read-back. The fictional bank's
# stated policy, not a guarantee.
POS_REVERSAL_NOTE = ("This amount is usually reversed automatically within 7 to 14 "
                     "working days, so you need not worry. I am also filing a complaint "
                     "so we can track it in case the reversal does not arrive.")
# Returned by create_ticket for every app_issue, said before the read-back.
# The definite one only when APP_MAINTENANCE=1. The maintenance is
# simulated: nothing checks a real app.
MAINTENANCE_NOTE = ("The mobile app and website are under scheduled maintenance "
                    "right now, so some features may not work until it is over.")
POSSIBLE_MAINTENANCE_NOTE = ("The app might be undergoing maintenance on some features. "
                             "I am filing a complaint for you in case it still does not "
                             "work later today or tomorrow.")

# Categories that need more than a summary, and the fields each one needs.
# Every other category takes no details.
DETAIL_FIELDS = {
    "card_block_request": ["full_name", "email", "loss_type"],
    "app_issue": ["issue_type", "device"],
    "card_replacement": ["full_name", "email", "mailing_address", "card_type", "reason"],
    "cheque_book": ["full_name", "account_last6", "cheque_leaves"],
}
CHOICES = {
    "loss_type": ["lost", "stolen"],
    "issue_type": ["app_crash", "biometric_login_failure", "website_error"],
    "card_type": ["debit", "credit"],
}
CHEQUE_LEAVES = [25, 50]


def needed_fields(category, details) -> list:
    """The details this complaint needs. A website error needs no phone
    model; every other app issue does."""
    fields = DETAIL_FIELDS.get(category, [])
    issue = details.get("issue_type")
    if isinstance(issue, str) and issue.strip().lower() == "website_error":
        fields = [field for field in fields if field != "device"]
    return fields
# What to ask for when a detail is missing or not valid. Read by the agent.
DETAIL_ASKS = {
    "full_name": "their full name",
    "email": "the email address the account was opened with, and to say it slowly",
    "loss_type": "whether the card was lost or stolen",
    "issue_type": "whether the app crashes, biometric login fails, or the website shows an error",
    "device": "which phone model they are using (never ask for a browser)",
    "mailing_address": "the mailing address for the new card",
    "card_type": "whether it is a debit card or a credit card",
    "reason": "why they need a replacement card",
    "account_last6": "only the last 6 digits of their account number, never the full number",
    "cheque_leaves": "whether they want 25 or 50 leaves",
}
MAX_AMOUNT = 10_000_000
MAX_TEXT = 1000
# heard_as holds every caller line of a complaint, so it gets more room.
MAX_HEARD = 4000

# The accepted words are checked here only. They are never spoken to the
# caller, so no message below tells the agent to recite them.
# "thank you" and "shukriya" are left out on purpose: politeness is not
# confirmation.
AFFIRMATIONS = [
    # Roman
    "yes", "yeah", "yep", "correct", "right", "haan", "han", "ji", "jee", "g",
    "theek hai", "theek", "sahi hai", "sahi", "bilkul",
    "ok", "okay", "ok ji", "okay ji", "ji haan", "sure",
    # Devanagari: spoken Urdu comes back as Hindi
    "हाँ", "हां", "हा", "जी", "ठीक", "ठीक है", "सही", "सही है", "बिल्कुल",
    "ओके", "ओके जी",
    # Urdu script
    "ہاں", "جی", "ٹھیک", "ٹھیک ہے", "صحیح", "بالکل", "اوکے",
]

# Any of these in the reply blocks the confirm, even next to a yes: "haan,
# lekin amount ghalat hai" is a correction, not a confirmation. This fails
# in the safe direction: "haan, koi masla nahi" is rejected and the agent
# simply asks again.
NEGATIONS = [
    # Roman
    "no", "not", "nahi", "nahin", "na", "galat", "ghalat",
    # Devanagari
    "नहीं", "नही", "गलत", "ग़लत",
    # Urdu script
    "نہیں", "نہ", "غلط",
]

# Arabic letters that look like Urdu ones. Used for matching only; stored
# text is never changed.
ARABIC_TO_URDU = str.maketrans({
    "ي": "ی",  # ي -> ی
    "ى": "ی",  # ى -> ی
    "ك": "ک",  # ك -> ک
    "ه": "ہ",  # ه -> ہ
})

# One lock for every read-modify-write of tickets.json. The server answers
# requests on several threads.
_lock = threading.Lock()


# --- affirmation check ------------------------------------------------------


def tokens(text: str) -> list:
    """Words, for matching. Regex \\b is not used: Devanagari vowel signs
    break it. Punctuation is found by Unicode category, so । ۔ ، split words
    just like . and , do. Vowel signs (category M) never split."""
    text = unicodedata.normalize("NFC", text).lower().translate(ARABIC_TO_URDU)
    words, word = [], []
    for ch in text:
        category = unicodedata.category(ch)
        if ch.isspace() or category[0] in "ZP":
            if word:
                words.append("".join(word))
                word = []
        elif category == "Cf":
            continue  # invisible joiners, which would stop an exact match
        else:
            word.append(ch)
    if word:
        words.append("".join(word))
    return words


_AFFIRMATION_TOKENS = [tokens(phrase) for phrase in AFFIRMATIONS]
_NEGATION_TOKENS = [tokens(word) for word in NEGATIONS]


def _contains(text, phrases) -> bool:
    """True if any phrase appears in the text as whole, consecutive words."""
    if not isinstance(text, str):
        return False
    words = tokens(text)
    for phrase in phrases:
        size = len(phrase)
        for i in range(len(words) - size + 1):
            if words[i:i + size] == phrase:
                return True
    return False


def is_affirmation(text) -> bool:
    return _contains(text, _AFFIRMATION_TOKENS)


def is_negation(text) -> bool:
    return _contains(text, _NEGATION_TOKENS)


# --- stripping secrets ------------------------------------------------------

REMOVED = "[removed]"

# A card number: 12 to 19 digits, possibly grouped with spaces or dashes.
_CARD = re.compile(r"(?<!\d)\d(?:[ -]?\d){11,18}(?!\d)")
# A PIN or OTP: a standalone run of 4 to 8 digits.
_SHORT_CODE = re.compile(r"(?<!\d)\d{4,8}(?!\d)")
# A password: the word that follows "password", in English, Urdu or Hindi.
_PASSWORD = re.compile(
    r"(password|passcode|pass word|پاس ورڈ|پاسورڈ|पासवर्ड)(\s*(?:is|hai|ہے|है|:|=)?\s*)(\S+)",
    re.IGNORECASE,
)


def redact(text: str, amount=None) -> str:
    """Strip anything that looks like a PIN, OTP, password or card number.
    A digit run equal to the ticket's own amount is kept."""
    text = _PASSWORD.sub(lambda m: m.group(1) + m.group(2) + REMOVED, text)
    text = _CARD.sub(REMOVED, text)

    def short(match):
        if amount is not None and int(match.group(0)) == amount:
            return match.group(0)
        return REMOVED

    return _SHORT_CODE.sub(short, text)


# --- storage ----------------------------------------------------------------


MAX_REJECTIONS = 500


def _rejections_file() -> Path:
    # Beside tickets.json, so pointing TICKETS_FILE elsewhere moves both.
    return TICKETS_FILE.with_name("rejections.json")


def _read(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def _write(path: Path, items: list) -> None:
    # Write a temporary file, then swap it in, so a crash never leaves half a file.
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _load() -> list:
    return _read(TICKETS_FILE)


def _save(tickets: list) -> None:
    _write(TICKETS_FILE, tickets)


def list_tickets() -> list:
    with _lock:
        return _load()


def list_rejections() -> list:
    with _lock:
        return _read(_rejections_file())


# --- masking, for the dashboard and GET /api/tickets --------------------------

ADDRESS_SHOWN = 6  # characters of the address left visible


def mask_email(email: str) -> str:
    """moez@example.com -> m***@example.com"""
    if "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)
    return local[:1] + "***@" + domain


def mask_address(address: str) -> str:
    """The first few characters only. A short address shows at most half."""
    return address[:min(ADDRESS_SHOWN, len(address) // 2)] + "…"


# An email inside free text, written or spoken: "ali.raza@example.com",
# "ali dot raza at the rate example dot com". A match is masked only if it
# normalises to a valid email.
_WORD = r"[^\s@.,;:!?()\[\]]+"
_EMAIL_IN_TEXT = re.compile(
    rf"{_WORD}(?:(?:\s*[._-]\s*|\s+(?:dot|underscore|dash|hyphen)\s+){_WORD})*"
    r"\s*(?:@|\bat\s+the\s+rate(?:\s+of)?\b|\(at\)|\[at\]|\bat\b)\s*"
    rf"(?:{_WORD}(?:(?:\s*\.\s*|\s+dot\s+){_WORD})+|{_WORD}\s*dotcom\b)",
    re.IGNORECASE)
# The start of an address: an address word, then a number (or digits
# already stripped). Everything from there to the end of the line is masked.
_ADDRESS_START = re.compile(
    r"\b(?:house|h\s*no|flat|apartment|apt|plot|street|road|block|sector|phase|"
    r"lane|gali|mohalla)\b\.?\s*(?:no\.?|number|#)?\s*(?:\d|\[removed\])",
    re.IGNORECASE)


def _mask_emails_in(text: str) -> str:
    def masked(match):
        email = normalize_email(match.group(0))
        return mask_email(email) if valid_email(email) else match.group(0)
    return _EMAIL_IN_TEXT.sub(masked, text)


def _mask_address_in(line: str, address_words: set) -> str:
    match = _ADDRESS_START.search(line)
    if match:
        return line[:match.start()] + mask_address(line[match.start():])
    # A line that repeats most of the ticket's own address, however phrased.
    words = set(tokens(line))
    if len(address_words) >= 2 and len(address_words & words) * 2 >= len(address_words):
        return mask_address(line)
    return line


def mask_heard_as(text: str, address: str = "") -> str:
    """Recognizer text for display: emails and addresses masked the same way
    as the stored fields. Display only; the stored heard_as is never changed.
    Best effort: an address with no number that is not the ticket's own
    stored address is not caught."""
    address_words = {w for w in tokens(address or "") if len(w) >= 2}
    return "\n".join(_mask_address_in(_mask_emails_in(line), address_words)
                     for line in text.split("\n"))


def public_ticket(ticket: dict) -> dict:
    details = dict(ticket.get("details") or {})
    address = details.get("mailing_address") or ""
    if details.get("email"):
        details["email"] = mask_email(details["email"])
    if address:
        details["mailing_address"] = mask_address(address)
    heard_as = mask_heard_as(ticket.get("heard_as") or "", address)
    return {**ticket, "details": details, "heard_as": heard_as}


def list_public_tickets() -> list:
    """Tickets as anyone may see them: email and address masked."""
    return [public_ticket(ticket) for ticket in list_tickets()]


# Reason codes for a rejected confirm, as shown on the dashboard.
REJECTION_REASONS = {
    "no_such_ticket": "no such ticket",
    "already_confirmed": "already confirmed",
    "amount_mismatch": "amount mismatch",
    "negation": "negation found",
    "no_affirmation": "no affirmation",
}


def _log_rejection(reason, ticket_id, confirmed_amount, reply) -> None:
    """Record a rejected confirm. Call with _lock held."""
    path = _rejections_file()
    rejections = _read(path)
    rejections.append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticket_id": ticket_id if isinstance(ticket_id, str) else None,
        "reason": reason,
        "confirmed_amount": _amount(confirmed_amount),
        # What the caller said, with anything secret-looking stripped.
        "reply": redact(_text(reply)),
    })
    _write(path, rejections[-MAX_REJECTIONS:])


# --- validation helpers -----------------------------------------------------


def _amount(value):
    """A number from 0 to MAX_AMOUNT, or None if it is not one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0 <= number <= MAX_AMOUNT:
        return None
    number = round(number, 2)
    return int(number) if number == int(number) else number


def _text(value, limit=MAX_TEXT) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _error(message: str, reason: str = "", field: str = "") -> dict:
    result = {"ok": False, "error": message}
    if reason:
        result["reason"] = reason
    if field:
        result["field"] = field  # which detail to ask for again
    return result


# Spoken email, as a recognizer writes it: "moez at the rate gmail dot com".
_SPOKEN_EMAIL = [
    (r"\bat\s+the\s+rate(?:\s+of)?\b|\(at\)|\[at\]", "@"),
    (r"\bdot\s*com\b", ".com"),
    (r"\bdot\b|\(dot\)|\[dot\]", "."),
    (r"\bunderscore\b", "_"),
    (r"\b(?:dash|hyphen)\b", "-"),
]


def normalize_email(value) -> str:
    """Spoken forms to symbols, lowercase, no spaces. Not validated."""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).strip().lower()
    for pattern, symbol in _SPOKEN_EMAIL:
        text = re.sub(pattern, symbol, text)
    if "@" not in text:
        # A bare "at" only when nothing else gave the @.
        text = re.sub(r"\bat\b", "@", text)
    return re.sub(r"\s+", "", text)


def valid_email(email: str) -> bool:
    """The shape only: one @, something before it, and a dot inside the
    domain with text on both sides. No check that the address exists."""
    if email.count("@") != 1:
        return False
    local, domain = email.split("@")
    return bool(local) and "." in domain and all(domain.split("."))


def _account_last6(value):
    """Exactly six digits, as a string so a leading zero survives."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[\s-]", "", value)
    return digits if re.fullmatch(r"[0-9]{6}", digits) else None


def _cheque_leaves(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value if isinstance(value, int) and value in CHEQUE_LEAVES else None


def _detail(field, value, number):
    """The value to store, or None if it is missing or not valid."""
    if field in CHOICES:
        choice = value.strip().lower() if isinstance(value, str) else None
        return choice if choice in CHOICES[field] else None
    if field == "account_last6":
        # Its own field, checked here and never stripped. Everywhere else,
        # six digits are stripped like any PIN or OTP.
        return _account_last6(value)
    if field == "cheque_leaves":
        return _cheque_leaves(value)
    if field == "email":
        email = normalize_email(value)
        return redact(email, number) if valid_email(email) else None
    return redact(_text(value), number) or None


# --- the two tools ----------------------------------------------------------


def draft_ticket(category=None, amount=None, summary=None, urgency=None,
                 time_reference=None, source_utterance=None, heard_as=None,
                 **details) -> dict:
    """Save a DRAFT. Nothing is filed until confirm_ticket accepts it.
    details: the fields in DETAIL_FIELDS. Only the ones the category needs
    are kept; the rest are ignored."""
    unknown = set(details) - set(DETAIL_ASKS)
    if unknown:
        raise TypeError(f"draft_ticket got unexpected fields: {sorted(unknown)}")
    if category not in CATEGORIES:
        return _error("Nothing was saved. The category must be one of: "
                      + ", ".join(CATEGORIES)
                      + ". Ask the caller what the complaint is about. Do not guess.")
    # Amount is optional: a blocked card or a pending loan has none.
    number = None if amount is None else _amount(amount)
    if amount is not None and number is None:
        return _error("Nothing was saved. The amount must be a number from 0 to "
                      "10,000,000 rupees. Ask the caller for the amount, or leave "
                      "it out if no money is involved.")
    if urgency not in URGENCIES:
        return _error("Nothing was saved. Urgency must be low, medium or high.")
    summary = _text(summary)
    if not summary:
        return _error("Nothing was saved. Give a short English summary of the complaint.")
    kept = {}
    for field in needed_fields(category, details):
        value = _detail(field, details.get(field), number)
        if value is None:
            return _error(f"Nothing was saved. Ask the caller for {DETAIL_ASKS[field]}.",
                          field=field)
        kept[field] = value

    ticket = {
        "ticket_id": "ZB-" + uuid.uuid4().hex[:6].upper(),
        "status": "draft",
        "category": category,
        "amount": number,
        "time_reference": redact(_text(time_reference), number),
        "urgency": urgency,
        "summary": redact(summary, number),
        # From the browser: every caller line the recognizer heard for this
        # complaint, one per line, in whatever script it came.
        "heard_as": redact(_text(heard_as, MAX_HEARD), number),
        # The model's claim about what the caller said.
        "source_utterance": redact(_text(source_utterance), number),
        # Recorded as given, never verified. Email and address are masked
        # wherever tickets are shown (list_public_tickets).
        "details": kept,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _lock:
        tickets = _load()
        tickets.append(ticket)
        _save(tickets)

    result = {
        "ok": True,
        "ticket_id": ticket["ticket_id"],
        "status": "draft",
        "category": category,
        "amount": number,
        "details": kept,
    }
    # Said to the caller before the read-back, never after filing.
    first = ""
    if category == "failed_pos_transaction":
        result["caller_note"] = POS_REVERSAL_NOTE
        first = "First say the caller_note. "
    if category == "app_issue":
        result["maintenance_note"] = (MAINTENANCE_NOTE
                                      if os.environ.get("APP_MAINTENANCE") == "1"
                                      else POSSIBLE_MAINTENANCE_NOTE)
        first = "First say the maintenance_note. "
    result["message"] = ("Draft saved. Nothing is filed yet. " + first
                         + "Briefly read back the issue and the exact amount if there "
                         "is one, then ask: Is that correct? Call confirm_ticket only "
                         "after the caller answers.")
    return result


def confirm_ticket(ticket_id=None, confirmed_amount=None, affirmation_text=None) -> dict:
    """File a draft. Only if the amount matches and the caller said yes."""
    with _lock:
        def reject(reason, message):
            _log_rejection(reason, ticket_id, confirmed_amount, affirmation_text)
            return _error(message, reason)

        tickets = _load()
        ticket = next((t for t in tickets if t.get("ticket_id") == ticket_id), None)
        if ticket is None:
            return reject("no_such_ticket",
                          f"There is no ticket {ticket_id}. Nothing was filed. "
                          "Create the draft first with create_ticket.")
        if ticket["status"] != "draft":
            return reject("already_confirmed",
                          f"Ticket {ticket_id} is already filed. Do not file it again.")
        # No amount on the draft means no amount on the confirm: null equals null.
        confirmed = None if confirmed_amount is None else _amount(confirmed_amount)
        invalid = confirmed_amount is not None and confirmed is None
        if invalid or confirmed != ticket["amount"]:
            return reject("amount_mismatch",
                          "The amount does not match the draft. Nothing was filed. "
                          "Read the amount back again and ask: Is that correct?")
        if is_negation(affirmation_text):
            return reject("negation",
                          "The caller's reply included a no or a correction. Nothing "
                          "was filed. Ask what needs to change, or ask again in "
                          "different words.")
        if not is_affirmation(affirmation_text):
            return reject("no_affirmation",
                          "The caller's reply was unclear. Nothing was filed. "
                          "Ask again in different words.")
        ticket["status"] = "confirmed"
        _save(tickets)

    message = FILED_MESSAGES.get(ticket["category"],
                                 "Ticket {id} is filed. Tell the caller their ticket number.")
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "status": "confirmed",
        "message": message.format(id=ticket_id),
    }


# Requests are raised, not carried out: the card is not blocked or mailed by
# filing a ticket, so the agent must not say it is.
FILED_MESSAGES = {
    "card_block_request": "Request {id} is filed. Tell the caller you have raised a "
                          "request to block their card, and give the reference number. "
                          "Do not say the card is blocked.",
    "card_replacement": "Request {id} is filed. Tell the caller a replacement card "
                        "request has been raised for mailing, and give the reference number.",
}
