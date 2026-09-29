# Zabaan AI

A voice agent for the complaint line of **Roshan Trust Bank**, a fictional bank. Callers speak Urdu, English, or both in one sentence. Zabaan takes the complaint, reads it back, and files a ticket only after the caller confirms it.

Built for the AssemblyAI x lablab.ai Voice Agent Hackathon.

**Live demo:** https://zabaan-ai.onrender.com
- `/`: the voice call (needs the demo passcode)
- `/mock`: the text demo, open to everyone, no passcode, no credits spent
- `/tickets`: the tickets dashboard (needs the demo passcode)

## The core rule

**A complaint the caller never confirmed cannot reach the bank's queue.**

- `create_ticket` writes a **draft** only. It never files anything.
- `confirm_ticket` is the **only** path to a filed ticket.
- All validation lives in [`tickets.py`](tickets.py), in code, not in the prompt, so the model cannot talk its way past it. The browser only forwards tool calls to the Python server.

`confirm_ticket` files the draft only if all three checks pass:

1. The confirmed amount equals the drafted amount. Amount is optional (a blocked card has none); no amount must be confirmed as no amount.
2. The caller's reply after the read-back contains no negation ("no", "nahi", "ghalat", नहीं, غلط, ...).
3. The same reply contains an affirmation ("yes", "haan", "ji", "theek hai", हाँ, جی, ...).

Words are matched whole, never as substrings ("Khan" does not match "han"), in Roman, Devanagari and Urdu script. "Thank you" and "shukriya" are not confirmation. If a check fails, the ticket stays a draft, the rejection is logged, and the agent asks again in different words.

Other rules enforced in `tickets.py`:
- Category must come from a fixed list: failed_transaction, failed_pos_transaction, blocked_card, card_block_request, card_replacement, app_issue, statement_request, balance_discrepancy, missing_transfer, cheque_book, salary_dispute, double_charge, late_fee, loan_delay, other. Anything else is asked, not guessed.
- Some categories need details before a draft is saved (see the use cases below). A missing or invalid detail saves nothing, and the tool result names the one detail to ask for again.
- Anything resembling a PIN, OTP, password or full card number is stripped before storing. The agent never asks for them. The one exception is the cheque book's "last 6 digits of the account number", which is checked as exactly 6 digits in its own field; the same digits are stripped everywhere else.
- Email and mailing address are masked on the `/tickets` dashboard and in `GET /api/tickets` (`m***@example.com`, `House …`), both in their own fields and inside the recognizer text. Display only: the stored ticket keeps the raw values.

## What it does

1. Greets: "Assalamu Alaikum, this is Roshan Trust Bank. How may I assist you today?"
2. Asks one question at a time for what is missing: the problem, the amount if money is involved, when it happened, and urgency. It never re-asks what the caller already said.
3. Drafts the ticket, reads back the issue and amount in the caller's own units (lakh, crore), and asks "Is that correct?".
4. Files on a yes, redrafts on a correction, asks again if the reply was unclear.
5. Gives the reference number, offers more help, says goodbye and hangs up.

## Use cases

Every case follows the same path: draft, read-back, the caller's yes, then filing. The agent never assumes the case from vague input and never re-asks what the caller already said.

| Case | Category | What the agent collects | What it says |
| --- | --- | --- | --- |
| Failed card payment at a shop: amount deducted, no slip printed, merchant says no money arrived | `failed_pos_transaction` | amount, date, merchant or location (only what is missing) | Before the read-back: "This amount is usually reversed automatically within 7 to 14 working days, so you need not worry. I am also filing a complaint so we can track it in case the reversal does not arrive." Then the read-back, and it files only after the caller confirms. The wording comes from `tickets.py` (returned by `create_ticket`), and only this category gets it. |
| Lost or stolen card | `card_block_request` | full name, email the account was opened with, lost or stolen | Says it has **raised a request** to block the card, never that the card is blocked. |
| App or website problem | `app_issue` | app crash, biometric login failure or website error; the phone model (never a browser, and no device question at all for a website error); short description | First suggests basic steps (update the app, restart the phone, check the internet, re-enrol biometrics). If the problem continues or the caller already tried everything, it drafts and, before the read-back, says: "The app might be undergoing maintenance on some features. I am filing a complaint for you in case it still does not work later today or tomorrow." With `APP_MAINTENANCE=1` it says the definite note instead: the app is under scheduled maintenance. Both come from `tickets.py`. |
| Replacement card | `card_replacement` | full name, email, mailing address, debit or credit, reason | Reads the address back and asks "Is that correct?" before drafting. Says a replacement card request has been raised for mailing. |
| Cheque book reorder | `cheque_book` | full name, last 6 digits of the account number, 25 or 50 leaves | Asks only for the last 6 digits, never the full account number, PIN or OTP. |

Other failures (ATM, online) stay `failed_transaction`, with no reversal note. `blocked_card` is for a card the bank has blocked, not a request to block one.

Spoken emails are normalised before checking: "moez at the rate gmail dot com" becomes `moez@gmail.com`. The check is the shape only: one @, and a dot in the domain.

Each ticket stores both what the model understood (category, amount, summary) and `heard_as`, exactly what speech recognition produced, so a Roman-Urdu or mixed-language complaint stays auditable. The `/tickets` dashboard shows drafts and filed tickets side by side, plus every rejected confirmation with its reason.

## Language

Spoken Urdu works via Hindi-English auto-detection: the session limits detection to English and Hindi (`language_codes: ["en", "hi"]`). Urdu is not on AssemblyAI's supported language list, so spoken Urdu comes back as Hindi, in Devanagari script. The confirmation checks understand Roman, Devanagari and Urdu script.

## Run it on Windows (PowerShell)

Python 3.9 or later. Standard library only: nothing to `pip install`.

```powershell
git clone <this-repo-url>
cd <repo-folder>
Copy-Item .env.example .env
```

### Mock mode: no API key needed

A scripted text agent drives the same tools and the same rules, so you can test the filing logic without spending credits.

```powershell
$env:ZABAAN_MOCK = "1"; python deployment/browser/server.py
```

Open the address it prints (http://localhost:3000 unless that port is taken). The text demo is at `/`, the dashboard at `/tickets`. The text demo is also at `/mock` on every server, mock mode or not.

The text demo handles every use case above. Try "card machine se 3500 kat gaye, slip nahi aayi", "mera card chori ho gaya", "mobile app crash ho rahi hai", "I need a replacement card", or "cheque book chahiye", then answer its questions. It asks one detail at a time, suggests the basic app steps first, and says the same notes before its read-back as the voice agent.

### Voice mode

Put your key from https://www.assemblyai.com/dashboard/api-keys in `.env`:

```
ASSEMBLYAI_API_KEY=your_key_here
```

Then:

```powershell
Remove-Item Env:ZABAAN_MOCK -ErrorAction SilentlyContinue
python deployment/browser/server.py
```

Open the printed address and start the call. The server listens on this computer only (127.0.0.1) unless `PORT` is set.

### Tests

```powershell
python -m unittest -v
```

No API key needed.

## How a call works

The browser sends the whole agent config (`agents/zabaan.jsonc`) in the first `session.update`; no stored agent id is used. `create_ticket`, `confirm_ticket` and `end_call` are client-side tools: the browser receives each tool call and POSTs it to the Python server (`/api/tickets/draft`, `/api/tickets/confirm`), then sends the reply back as the tool result. `end_call` hangs up only after the goodbye has finished playing.

## Deploying (Render)

[`render.yaml`](render.yaml) defines one free web service. Render asks for two values in its dashboard; neither is ever in the repo:

| Variable | What it does |
| --- | --- |
| `ASSEMBLYAI_API_KEY` | Stays on the server. Never sent to the page. |
| `DEMO_PASSCODE` | Needed to start a voice call (the only thing that spends credits) and to see the tickets dashboard: `GET /api/tickets` and `GET /api/rejections` answer 401 without it, and `/tickets` asks for it. The text demo at `/mock` stays open and still gives each caller their own reference number. Unset (as when running locally), everything is open. Use plain ASCII. |

Optional: set `APP_MAINTENANCE=1` under Environment to simulate scheduled app maintenance. Off by default.

Every voice call ends after 3 minutes.

## Honest limits

- **The confirm check matches words, not meaning.** "haan, lekin amount ghalat hai" is correctly rejected, but so are genuine yeses containing a negation word, like "haan, koi masla nahi" or "theek hai na?". The agent then asks again. That is the intended direction of failure: a real complaint waits one more turn rather than an unconfirmed one being filed.
- **Urdu is not officially supported** by AssemblyAI. It works through Hindi detection, so transcripts are in Devanagari. Short one-word replies can occasionally be misheard as another language; the confirm is then rejected and the agent asks again.
- **Stripping PINs and OTPs protects our ticket store only.** Speech has already reached AssemblyAI and the LLM.
- **The 7 to 14 working-day reversal figure is the fictional bank's stated policy, not a guarantee.** The agent says "usually". Whether a complaint is a failed card payment at a shop is the model's choice of category; if it misjudges, a caller may hear the note when it does not apply, or miss it.
- **Caller identity is recorded, not verified.** Name and email are stored as given. Nothing checks them against an account.
- **Name plus email would be weak verification in real life.** Both are easy for someone else to know. A real bank would need proper identity checks before acting on a block or replacement request.
- **Block and replacement are requests, not actions.** Filing a ticket does not block a card or mail one, and the agent is told never to say the card is blocked.
- **App maintenance status is simulated.** `APP_MAINTENANCE=1` makes the agent say the app is under maintenance; nothing checks a real app. Without it, every app issue gets the "might be undergoing maintenance" line, which is a guess, not a status.
- **A 6-digit OTP could be mistaken for account digits.** If a caller reads out an OTP when asked for the last 6 digits of their account, it is stored as account digits. Six-digit runs are stripped everywhere else.
- **Digit stripping also hits emails and addresses.** A run of 4 to 8 digits (a postal code, a house number, digits in an email) is stripped from every field except the account digits, so it is stored as `[removed]`.
- **The dashboard passcode is one shared secret.** Everyone who has it sees every ticket, and anyone can still add tickets (the draft and confirm APIs, used by the voice page and the text demo, stay open). On the live demo, the passcode is the first layer and masking is the second.
- **Masking in the recognizer text is best effort.** On the dashboard and in `GET /api/tickets`, emails (written, or spoken as "at the rate … dot com") and addresses in `heard_as` are masked like the stored fields. The stored file keeps the raw text. Addresses are found by an address word followed by a number ("House 12", "Street 5") or by matching the ticket's own stored address, so an address with no number, said on a ticket that has no address field, is not caught. Emails spelled letter by letter, or in Urdu or Hindi words, may be partly or not masked. The summary and the rejected-confirmations panel are not masked.
- **Spoken-email normalisation is English only** ("at the rate", "dot", "underscore", "dash"). An email spelled out in Urdu or Hindi words will fail the shape check, and the agent asks again.
- **Hanging up relies on the model** saying goodbye before calling `end_call`.
- **The 3-minute cap is enforced in the page**, not by AssemblyAI. The passcode is what keeps strangers from starting calls.
- **Demo data is not permanent.** On Render's free plan, tickets reset on restart, redeploy, or wake from sleep. Anyone can add test tickets through `/mock`.
- **Browser only.** No phone line.
- **Only fake data.** The bank is fictional. **Use fake names, emails and addresses on the live demo:** everyone with the passcode sees the same dashboard, and masking of what callers say is best effort.

## Credits

Built on AssemblyAI's voice-agent-starter-python (https://github.com/AssemblyAI/voice-agent-starter-python). The browser page, the audio handling and the server skeleton come from it. The starter's example agents, publishing scripts and Twilio phone setup were removed: this project is browser-only. Server details are in [`deployment/browser/`](deployment/browser/).

## License

MIT for the Zabaan AI team's own code. The starter code remains AssemblyAI's; see [LICENSE](LICENSE).
