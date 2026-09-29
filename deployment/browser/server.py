#!/usr/bin/env python3
"""Talk to your agent from a browser tab.

    python deployment/browser/server.py

The API key stays in this process; the page only gets 60-second tokens.

Zabaan: /tickets is the dashboard and /mock is a text demo (a scripted
agent, mock.py, driving the same ticket rules). With ZABAAN_MOCK=1 the
server needs no API key and / is the text demo. With DEMO_PASSCODE set,
starting a voice call and reading tickets or rejections need the passcode;
the text demo stays open.
"""

import copy
import hashlib
import hmac
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from lib import (ApiError, _agents_api, aai, load_env, read_agent,  # noqa: E402
                 required)
from mock import MockCall  # noqa: E402
from tickets import (DETAIL_ASKS, confirm_ticket, draft_ticket,  # noqa: E402
                     list_public_tickets, list_rejections)

# Zabaan: the browser forwards each client-side tool call here. Only these
# fields are passed on; every rule is checked in tickets.py.
TICKET_ROUTES = {
    "/api/tickets/draft": (draft_ticket, ["category", "amount", "summary", "urgency",
                                          "time_reference", "source_utterance", "heard_as",
                                          *DETAIL_ASKS]),
    "/api/tickets/confirm": (confirm_ticket, ["ticket_id", "confirmed_amount",
                                              "affirmation_text"]),
}
MAX_BODY = 64 * 1024

# Mock mode: set in main() from ZABAAN_MOCK=1. The text demo at /mock works
# either way. One scripted conversation per browser tab, keyed by the id the
# page sends; the oldest is dropped past the cap.
MOCK = False
MOCK_CALLS: dict = {}
MAX_MOCK_CALLS = 200

# When DEMO_PASSCODE is set, these need it: /token (the only route that
# starts a billed voice call), and /api/tickets and /api/rejections, which
# list what callers said. A wrong guess costs a second (_passcode_given).
PASSCODE = ""
WRONG_PASSCODE_DELAY = 1.0
# The page ends every voice call after this long.
MAX_CALL_SECONDS = 180


def passcode_ok(given) -> bool:
    if not PASSCODE:
        return True
    given = given if isinstance(given, str) else ""
    return hmac.compare_digest(given.encode(), PASSCODE.encode())


def session_ws_url() -> str:
    """The websocket the page opens for voice sessions, read from app.js so the
    log shows what the browser really uses."""
    found = re.search(r"new URL\('(wss://[^']+)'\)", (HERE / "app.js").read_text(encoding="utf-8"))
    return found.group(1) if found else "(not found in app.js)"


def log_key_diagnostics(key_source: str) -> None:
    """Startup facts for comparing two deployments. Identifies the key by
    length and a hash prefix; the key itself is never printed."""
    key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    padded = "yes" if key != key.strip() else "no"
    print(f"Key: ASSEMBLYAI_API_KEY from {key_source}, length {len(key)}, "
          f"sha256 {digest}, leading/trailing whitespace: {padded}")
    base_note = "set" if os.environ.get("AGENTS_API_BASE") else "not set, default"
    print(f"API: agents REST {_agents_api()} (AGENTS_API_BASE {base_note}); "
          f"session tokens {_agents_api()}/token; "
          f"voice sessions {session_ws_url()} (from app.js)")
    try:
        listed = aai("/agents")
    except ApiError as err:
        print(f"Agents: listing failed: {err}")
        return
    items = (listed.get("agents", []) if isinstance(listed, dict) else listed) or []
    print(f"Agents: this key can list {len(items)}:")
    for item in items:
        deleted = f" [deleted {item['deleted_at']}]" if item.get("deleted_at") else ""
        print(f'Agents:   {item.get("id")} "{item.get("name")}"{deleted}')


# Top-level keys of an agent file that the inline session understands. The
# file keeps the POST /v1/agents shape, so voice is {"voice_id": ...} there
# and output.voice in the session.
SESSION_KEYS = {"name", "system_prompt", "greeting", "voice", "input", "output", "tools"}


def session_config(agent: dict) -> dict:
    """agents/<name>.jsonc as the inline `session` of session.update, per
    https://www.assemblyai.com/docs/voice-agents/voice-agent-api/session-configuration.
    Stored agents (agent_id) turned out to be region-local: the agent Render
    published was agent_not_found for a browser in Karachi. Inline config has
    no stored agent to find. The page receives this whole object, so anything
    that would carry a secret stops the server instead of reaching it."""
    unknown = set(agent) - SESSION_KEYS
    if unknown:
        sys.exit(f"Agent: {', '.join(sorted(unknown))} cannot be sent inline in "
                 "session.update. Remove it from the agent file.")
    session = {k: copy.deepcopy(agent[k]) for k in ("system_prompt", "greeting", "input")
               if k in agent}
    output = copy.deepcopy(agent.get("output") or {})
    voice = agent.get("voice")
    if voice:
        output["voice"] = voice.get("voice_id") if isinstance(voice, dict) else voice
    if output:
        session["output"] = output
    tools = []
    for tool in agent.get("tools") or []:
        if tool.get("http"):
            sys.exit(f'Agent: tool "{tool.get("name")}" is an HTTP tool. Inline sessions '
                     "take client-side tools only, and its headers would reach the browser.")
        tools.append({"type": "function", **{k: v for k, v in tool.items() if k != "http"}})
    if tools:
        session["tools"] = tools
    return session


AGENT_FILE = "zabaan"


def resolve_agent() -> dict:
    """The agent file, sent inline by the page on every call. No agent is
    published or looked up, so there is no agent id to go stale or missing.
    Always agents/zabaan.jsonc: there is no other agent to fall back to."""
    name = AGENT_FILE
    if os.environ.get("AGENT", AGENT_FILE) != AGENT_FILE:
        print(f"Agent: AGENT={os.environ['AGENT']} is set but ignored: "
              f"this server only runs agents/{AGENT_FILE}.jsonc")
    if os.environ.get("AGENT_ID"):
        print(f"Agent: AGENT_ID={os.environ['AGENT_ID']} is set but ignored: "
              "sessions are configured inline, not by agent id")
    agent = read_agent(name)
    session = session_config(agent)
    tools = ", ".join(t["name"] for t in session.get("tools", [])) or "none"
    print(f'Agent: inline config from agents/{name}.jsonc: "{agent.get("name")}", '
          f'voice {session.get("output", {}).get("voice", "default")}, '
          f'languages {session.get("input", {}).get("language_codes", "auto")}, '
          f"tools {tools}")
    return {"name": agent.get("name") or "Your agent", "session": session}


def public_agent(agent: dict) -> dict:
    """Read-only view of the agent config. The API keeps header values and llm
    keys write-only; these deletes hold even if that changes. The system prompt
    is in here, so a public deployment shows it to anyone who opens the page.
    Client-side tools come back with "http": null, so every lookup allows None."""
    copied = copy.deepcopy(agent)
    for tool in copied.get("tools") or []:
        for header in (tool.get("http") or {}).get("headers") or []:
            header["value"] = "<hidden>"
    llms = copied.get("llm") or []
    for llm in [llms] if isinstance(llms, dict) else llms:
        llm.pop("api_key", None)
    return copied


AGENT = None
PAGE = ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value) -> None:
        self._send(200, json.dumps(value, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _passcode_given(self) -> bool:
        """True if no passcode is set or the X-Demo-Passcode header matches.
        Otherwise waits a second, sends 401, and returns False."""
        if passcode_ok(self.headers.get("X-Demo-Passcode")):
            return True
        time.sleep(WRONG_PASSCODE_DELAY)
        self._send(401, b'{"error":"passcode required"}', "application/json")
        return False

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/tickets":
            self._send(200, (HERE / "tickets.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/tickets":
            if self._passcode_given():
                # Masked too, as a second layer behind the passcode.
                self._json(list_public_tickets())
            return
        if path == "/api/rejections":
            if self._passcode_given():
                self._json(list_rejections())
            return
        if path == "/mock":
            self._send(200, (HERE / "mock.html").read_bytes(), "text/html; charset=utf-8")
            return
        if MOCK and path in ("/token", "/agent"):
            # No API key in mock mode, so nothing that would use one.
            self._send(404, b'{"error":"not available in mock mode"}', "application/json")
            return
        if path == "/token":
            if not self._passcode_given():
                return
            try:
                token = aai("/token?product=voice_agent&expires_in_seconds=60")
                self._send(200, json.dumps(token).encode(), "application/json")
            except ApiError as err:
                print(err)
                self._send(502, b'{"error":"token request failed"}', "application/json")
            return
        if path == "/agent":
            self._json(public_agent(AGENT["session"]))
            return
        if path == "/app.js":
            self._send(200, (HERE / "app.js").read_bytes(), "text/javascript")
            return
        self._send(200, PAGE.encode(), "text/html")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        route = TICKET_ROUTES.get(path)
        is_mock = path == "/api/mock/message"
        if not route and not is_mock:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._send(413, b'{"error":"body too large"}', "application/json")
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._send(400, b'{"error":"expected a JSON object"}', "application/json")
            return
        if is_mock:
            self._json(mock_reply(body.get("session"), body.get("text")))
            return
        handler, fields = route
        self._json(handler(**{field: body.get(field) for field in fields}))

    def log_message(self, *args) -> None:  # quiet; errors are printed above
        pass


def mock_reply(session, text) -> dict:
    """One turn of a mock conversation. Only the reply goes back to the page."""
    if not isinstance(session, str) or not 0 < len(session) <= 100:
        return {"error": "missing session id"}
    call = MOCK_CALLS.get(session)
    if call is None:
        if len(MOCK_CALLS) >= MAX_MOCK_CALLS:
            MOCK_CALLS.pop(next(iter(MOCK_CALLS)))
        call = MOCK_CALLS[session] = MockCall()
    return {"reply": call.reply(text if isinstance(text, str) else "")["reply"]}


def bind_host() -> str:
    """This computer only, unless PORT is set. Hosting platforms like Render
    set PORT and need every interface; locally, the ticket endpoints have no
    password, so nobody else on the network should reach them."""
    return "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"


def main() -> None:
    global AGENT, PAGE, MOCK, PASSCODE
    # Hosts like Render read stdout through a pipe, which Python buffers; line
    # buffering makes the startup log (agent id included) appear at once.
    sys.stdout.reconfigure(line_buffering=True)
    # load_env never overrides the process environment, so a key already set
    # here came from the host (Render's dashboard), not from a .env file.
    key_source = ("process environment" if "ASSEMBLYAI_API_KEY" in os.environ
                  else ".env file")
    load_env()
    MOCK = os.environ.get("ZABAAN_MOCK") == "1"
    PASSCODE = os.environ.get("DEMO_PASSCODE", "")
    if os.environ.get("APP_MAINTENANCE") == "1":
        print("APP_MAINTENANCE=1: app issue drafts say the app is under maintenance (simulated).")

    if MOCK:
        # No API key, no agent, no AssemblyAI calls: the page is a text box.
        print("Mock mode: no API key used, no voice session.")
        if PASSCODE:
            print("The tickets dashboard needs the passcode; the text demo does not.")
        PAGE = (HERE / "mock.html").read_text(encoding="utf-8")
    else:
        required("ASSEMBLYAI_API_KEY", "get one at https://www.assemblyai.com/dashboard/api-keys")
        log_key_diagnostics(key_source)
        AGENT = resolve_agent()
        # The page learns whether to ask for a passcode, never the passcode.
        demo = {"passcode": bool(PASSCODE), "maxSeconds": MAX_CALL_SECONDS}
        PAGE = ((HERE / "index.html").read_text()
                .replace("{{AGENT_NAME}}", AGENT["name"])
                .replace("{{AGENT_JSON}}", json.dumps(AGENT).replace("<", "\\u003c"))
                .replace("{{DEMO_JSON}}", json.dumps(demo)))
        print("Voice calls and the tickets dashboard need the passcode." if PASSCODE
              else "No DEMO_PASSCODE: anyone who can open this page can start billed "
                   "calls and read the tickets dashboard.")

    # PORT when set, otherwise 3000 and up until one is free.
    fixed = os.environ.get("PORT")
    port = int(fixed) if fixed else 3000
    while True:
        try:
            server = ThreadingHTTPServer((bind_host(), port), Handler)
            break
        except OSError:
            if fixed or port >= 3010:
                raise
            port += 1

    print(f"Talk to it: http://localhost:{port}")
    print(f"Tickets:    http://localhost:{port}/tickets")
    print(f"Text demo:  http://localhost:{port}/mock")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
