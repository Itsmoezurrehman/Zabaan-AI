"""Tests for the ticket endpoints in deployment/browser/server.py.

Runs the request handler on a local port. No API key, no AssemblyAI calls.

    python -m unittest -v
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "deployment" / "browser"))

import server  # noqa: E402
import tickets  # noqa: E402


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.real_file = tickets.TICKETS_FILE
        tickets.TICKETS_FILE = Path(self.folder.name) / "tickets.json"

    def tearDown(self):
        tickets.TICKETS_FILE = self.real_file
        self.folder.cleanup()

    def request(self, path, body=None, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.loads(err.read().decode("utf-8"))

    def test_draft_confirm_list(self):
        status, draft = self.request("/api/tickets/draft", {
            "category": "missing_transfer", "amount": 12000, "urgency": "medium",
            "summary": "Transfer to brother not received.", "heard_as": "हाँ पैसे नहीं पहुँचे",
        })
        self.assertEqual(status, 200)
        self.assertTrue(draft["ok"])

        status, confirmed = self.request("/api/tickets/confirm", {
            "ticket_id": draft["ticket_id"], "confirmed_amount": 12000,
            "affirmation_text": "Haan, theek hai.",
        })
        self.assertEqual(status, 200)
        self.assertTrue(confirmed["ok"])

        status, listed = self.request("/api/tickets")
        self.assertEqual(status, 200)
        self.assertEqual(listed[0]["status"], "confirmed")
        self.assertEqual(listed[0]["heard_as"], "हाँ पैसे नहीं पहुँचे")

    def test_pos_note_reaches_the_browser(self):
        _, draft = self.request("/api/tickets/draft", {
            "category": "failed_pos_transaction", "amount": 3500, "urgency": "medium",
            "summary": "Paid by card at a shop; no slip, merchant says no payment.",
            "card_machine_payment": True,  # the old flag is dropped, not an error
        })
        self.assertTrue(draft["ok"])
        # The note comes with the draft, before the read-back.
        self.assertIn("7 to 14 working days", draft["caller_note"])
        _, confirmed = self.request("/api/tickets/confirm", {
            "ticket_id": draft["ticket_id"], "confirmed_amount": 3500,
            "affirmation_text": "ji haan",
        })
        self.assertTrue(confirmed["ok"])
        self.assertNotIn("caller_note", confirmed)

    def test_details_reach_python_and_list_is_masked(self):
        _, draft = self.request("/api/tickets/draft", {
            "category": "card_replacement", "urgency": "medium",
            "summary": "Card chip damaged; needs a replacement.",
            "full_name": "Ali Raza", "email": "ali dot raza at the rate example dot com",
            "mailing_address": "House 12, Street 5, Karachi", "card_type": "debit",
            "reason": "Chip damaged",
        })
        self.assertTrue(draft["ok"], draft)
        self.assertEqual(draft["details"]["email"], "ali.raza@example.com")
        _, listed = self.request("/api/tickets")
        self.assertEqual(listed[0]["details"]["email"], "a***@example.com")
        self.assertEqual(listed[0]["details"]["mailing_address"], "House …")
        self.assertNotIn("ali.raza", json.dumps(listed))
        self.assertNotIn("Street 5", json.dumps(listed))

    def test_spoken_email_and_address_in_heard_as_masked_over_http(self):
        heard = ("mujhe naya card chahiye\n"
                 "mera email ali dot raza at the rate example dot com hai\n"
                 "address hai House 12, Street 5, Gulshan, Karachi\n"
                 "ji haan")
        _, draft = self.request("/api/tickets/draft", {
            "category": "card_replacement", "urgency": "medium",
            "summary": "Card chip damaged; needs a replacement.",
            "full_name": "Ali Raza", "email": "ali.raza@example.com",
            "mailing_address": "House 12, Street 5, Gulshan, Karachi",
            "card_type": "debit", "reason": "Chip damaged", "heard_as": heard,
        })
        self.assertTrue(draft["ok"], draft)
        _, listed = self.request("/api/tickets")
        shown = listed[0]["heard_as"]
        self.assertEqual(shown, "mujhe naya card chahiye\n"
                                "mera email a***@example.com hai\n"
                                "address hai House …\n"
                                "ji haan")
        for secret in ("raza", "Street 5", "Gulshan", "Karachi"):
            self.assertNotIn(secret, json.dumps(listed, ensure_ascii=False))
        # The stored file keeps the raw recognizer text.
        self.assertEqual(tickets.list_tickets()[0]["heard_as"], heard)

    def test_rule_failure_is_200_with_error(self):
        status, result = self.request("/api/tickets/draft", {"category": "guess"})
        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_extra_fields_are_ignored(self):
        status, result = self.request("/api/tickets/confirm", {
            "ticket_id": "ZB-NOPE00", "confirmed_amount": 1, "affirmation_text": "yes",
            "status": "confirmed",
        })
        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])

    def test_bad_json_is_400(self):
        status, _ = self.request("/api/tickets/draft", raw=b"not json")
        self.assertEqual(status, 400)

    def get_text(self, path):
        with urllib.request.urlopen(self.base + path) as res:
            return res.read().decode("utf-8")

    def test_dashboard_page(self):
        page = self.get_text("/tickets")
        self.assertIn("Recognizer text", page)
        self.assertIn("Rejected confirmations", page)
        self.assertIn("setInterval(refresh, 3000)", page)

    def test_rejections_endpoint(self):
        _, draft = self.request("/api/tickets/draft", {
            "category": "blocked_card", "urgency": "high", "summary": "Card blocked."})
        self.request("/api/tickets/confirm", {
            "ticket_id": draft["ticket_id"], "affirmation_text": "nahi"})
        status, rejections = self.request("/api/rejections")
        self.assertEqual(status, 200)
        self.assertEqual(rejections[0]["reason"], "negation")

    def test_text_demo_open_on_a_voice_server(self):
        # MOCK is False here: /mock still works, with no passcode.
        self.assertIn("Zabaan mock call", self.get_text("/mock"))
        with unittest.mock.patch.object(server, "MOCK_CALLS", {}):
            status, reply = self.request("/api/mock/message",
                                         {"session": "s", "text": "mera card block ho gaya"})
        self.assertEqual(status, 200)
        self.assertIn("Is that correct?", reply["reply"])

    def test_token_needs_passcode_when_set(self):
        fake_aai = unittest.mock.Mock(return_value={"token": "t"})
        with unittest.mock.patch.object(server, "PASSCODE", "open-sesame"), \
             unittest.mock.patch.object(server, "WRONG_PASSCODE_DELAY", 0), \
             unittest.mock.patch.object(server, "aai", fake_aai):
            self.assertEqual(self.request("/token")[0], 401)
            self.assertEqual(self.request("/token", headers={"X-Demo-Passcode": "guess"})[0], 401)
            fake_aai.assert_not_called()  # no billed token without the passcode
            status, body = self.request("/token", headers={"X-Demo-Passcode": "open-sesame"})
            self.assertEqual((status, body), (200, {"token": "t"}))

    def test_token_open_when_no_passcode(self):
        fake_aai = unittest.mock.Mock(return_value={"token": "t"})
        with unittest.mock.patch.object(server, "PASSCODE", ""), \
             unittest.mock.patch.object(server, "aai", fake_aai):
            self.assertEqual(self.request("/token")[0], 200)

    def add_confirmed_and_rejected(self):
        _, draft = self.request("/api/tickets/draft", {
            "category": "blocked_card", "urgency": "high", "summary": "Card blocked.",
            "heard_as": "mera card block ho gaya"})
        self.request("/api/tickets/confirm", {"ticket_id": draft["ticket_id"],
                                              "affirmation_text": "nahi"})
        return draft["ticket_id"]

    def test_dashboard_data_needs_passcode_when_set(self):
        ticket_id = self.add_confirmed_and_rejected()
        with unittest.mock.patch.object(server, "PASSCODE", "open-sesame"), \
             unittest.mock.patch.object(server, "WRONG_PASSCODE_DELAY", 0):
            for path in ("/api/tickets", "/api/rejections"):
                for headers in ({}, {"X-Demo-Passcode": "guess"}):
                    status, body = self.request(path, headers=headers)
                    self.assertEqual((status, body), (401, {"error": "passcode required"}),
                                     (path, headers))
            right = {"X-Demo-Passcode": "open-sesame"}
            status, listed = self.request("/api/tickets", headers=right)
            self.assertEqual(status, 200)
            self.assertEqual(listed[0]["ticket_id"], ticket_id)
            self.assertEqual(listed[0]["summary"], "Card blocked.")
            status, rejections = self.request("/api/rejections", headers=right)
            self.assertEqual(status, 200)
            self.assertEqual(rejections[0]["reason"], "negation")

    def test_dashboard_data_open_without_passcode(self):
        self.add_confirmed_and_rejected()
        with unittest.mock.patch.object(server, "PASSCODE", ""):
            self.assertEqual(self.request("/api/tickets")[0], 200)
            self.assertEqual(self.request("/api/rejections")[0], 200)

    def test_text_demo_open_and_gives_reference_with_passcode_set(self):
        with unittest.mock.patch.object(server, "PASSCODE", "open-sesame"), \
             unittest.mock.patch.object(server, "WRONG_PASSCODE_DELAY", 0), \
             unittest.mock.patch.object(server, "MOCK_CALLS", {}):
            self.assertIn("Zabaan mock call", self.get_text("/mock"))
            self.request("/api/mock/message", {"session": "s", "text": "mera card block ho gaya"})
            status, reply = self.request("/api/mock/message", {"session": "s", "text": "ji"})
            self.assertEqual(status, 200)
            self.assertRegex(reply["reply"], r"Your reference number is ZB-[0-9A-F]{6}\.")
            self.assertEqual(tickets.list_tickets()[0]["status"], "confirmed")
            # The dashboard page itself loads, then asks for the passcode.
            page = self.get_text("/tickets")
            self.assertIn("X-Demo-Passcode", page)
            self.assertIn("This dashboard needs the demo passcode.", page)

    def test_page_is_told_about_passcode_not_given_it(self):
        page = (Path(server.__file__).parent / "index.html").read_text(encoding="utf-8")
        self.assertIn("window.DEMO = {{DEMO_JSON}}", page)
        self.assertIn('id="passcode"', page)

    def test_mock_conversation_files_a_ticket(self):
        with unittest.mock.patch.object(server, "MOCK", True), \
             unittest.mock.patch.object(server, "MOCK_CALLS", {}):
            _, first = self.request("/api/mock/message",
                                    {"session": "tab1", "text": "mera card block ho gaya"})
            self.assertIn("Is that correct?", first["reply"])
            _, second = self.request("/api/mock/message", {"session": "tab1", "text": "ji"})
            self.assertIn("registered", second["reply"])
            status, _ = self.request("/token")
            self.assertEqual(status, 404)
        self.assertEqual(tickets.list_tickets()[0]["status"], "confirmed")

    def test_binds_locally_unless_port_is_set(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PORT", None)
            self.assertEqual(server.bind_host(), "127.0.0.1")
            os.environ["PORT"] = "10000"
            self.assertEqual(server.bind_host(), "0.0.0.0")

    def test_unknown_path_is_404(self):
        status, _ = self.request("/api/tickets/delete", {})
        self.assertEqual(status, 404)


class PublicAgentTest(unittest.TestCase):
    def test_client_side_tool_with_http_null(self):
        # The API returns "http": null for client-side tools; this used to crash.
        agent = {"tools": [{"name": "create_ticket", "http": None}]}
        self.assertEqual(server.public_agent(agent), agent)

    def test_nulls_everywhere(self):
        agent = {"tools": None, "llm": None}
        self.assertEqual(server.public_agent(agent), agent)
        server.public_agent({"tools": [{"http": {"headers": None}}]})

    def test_header_values_still_hidden(self):
        agent = {"tools": [{"http": {"headers": [{"name": "x-key", "value": "s3cret"}]}}],
                 "llm": {"api_key": "k"}}
        shown = server.public_agent(agent)
        self.assertEqual(shown["tools"][0]["http"]["headers"][0]["value"], "<hidden>")
        self.assertNotIn("api_key", shown["llm"])


class FakeAgentsAPI:
    """An in-memory AssemblyAI account: enough of /agents for resolve_agent."""

    def __init__(self, agents=(), put_returns_id=True):
        self.agents = {a["id"]: dict(a) for a in agents}
        self.calls = []
        self.put_returns_id = put_returns_id
        self.next_id = 1

    def __call__(self, path, method="GET", body=None, headers=None):
        from lib import ApiError
        self.calls.append((method, path))
        if path == "/agents" and method == "GET":
            return {"agents": [{"id": i, "name": a["name"], "deleted_at": a.get("deleted_at")}
                               for i, a in self.agents.items()]}
        if path == "/agents" and method == "POST":
            new_id = f"agent_new{self.next_id}"
            self.next_id += 1
            self.agents[new_id] = {"id": new_id, **body}
            return self.agents[new_id]
        agent_id = path.rsplit("/", 1)[1]
        agent = self.agents.get(agent_id)
        if agent is None or agent.get("deleted_at"):
            raise ApiError(f"{method} {path}", 404, '{"error":"agent_not_found"}')
        if method == "PUT":
            agent.update(body)
            return agent if self.put_returns_id else {k: v for k, v in agent.items() if k != "id"}
        return agent


class SessionConfigTest(unittest.TestCase):
    """The agent file becomes the inline session of session.update."""

    def test_zabaan_file_maps_to_documented_fields(self):
        import lib
        agent = lib.read_agent("zabaan")
        session = server.session_config(agent)
        self.assertEqual(set(session), {"system_prompt", "greeting", "input", "output", "tools"})
        self.assertNotIn("agent_id", session)  # docs: inline and agent_id exclude each other
        self.assertEqual(session["system_prompt"], agent["system_prompt"])
        self.assertEqual(session["greeting"], agent["greeting"])
        self.assertEqual(session["output"], {"voice": agent["voice"]["voice_id"]})
        self.assertEqual(session["input"]["language_codes"], ["en", "hi"])
        self.assertEqual([t["name"] for t in session["tools"]],
                         ["create_ticket", "confirm_ticket", "end_call"])
        for tool in session["tools"]:
            self.assertEqual(tool["type"], "function")
            self.assertEqual(set(tool) - {"type", "name", "description", "parameters",
                                          "execution_mode", "timeout_seconds"}, set())

    def test_http_tool_stops_rather_than_leaking_headers(self):
        agent = {"name": "x", "tools": [{"name": "search", "http": {
            "url": "https://example.com", "headers": [{"name": "k", "value": "secret"}]}}]}
        with self.assertRaises(SystemExit):
            server.session_config(agent)

    def test_client_tool_with_http_null_is_kept(self):
        session = server.session_config({"tools": [{"name": "t", "http": None,
                                                    "description": "d", "parameters": {}}]})
        self.assertEqual(session["tools"], [{"type": "function", "name": "t",
                                             "description": "d", "parameters": {}}])

    def test_unknown_key_stops(self):
        with self.assertRaises(SystemExit):
            server.session_config({"name": "x", "llm": {"model": "m"}})

    def test_resolve_agent_makes_no_api_calls_and_ignores_agent_id(self):
        import contextlib
        import io
        out = io.StringIO()
        api = unittest.mock.Mock(side_effect=AssertionError("no API call expected"))
        with unittest.mock.patch.dict(os.environ, {"AGENT": "zabaan", "AGENT_ID": "agent_old"}),              unittest.mock.patch.object(server, "aai", api), contextlib.redirect_stdout(out):
            agent = server.resolve_agent()
        self.assertEqual(agent["name"], "Zabaan AI")
        self.assertNotIn("id", agent)
        self.assertIn("ignored", out.getvalue())
        api.assert_not_called()

    def test_resolve_agent_never_loads_another_agent_file(self):
        import contextlib
        import io
        out = io.StringIO()
        with unittest.mock.patch.dict(os.environ, {"AGENT": "minimal"}), \
                contextlib.redirect_stdout(out):
            agent = server.resolve_agent()
        self.assertEqual(agent["name"], "Zabaan AI")
        self.assertIn("AGENT=minimal is set but ignored", out.getvalue())
        self.assertEqual(sorted(p.name for p in (Path(server.HERE).parents[1] / "agents")
                                .glob("*.jsonc")), ["zabaan.jsonc"])

    def test_page_sends_inline_session_not_agent_id(self):
        app = (Path(server.HERE) / "app.js").read_text(encoding="utf-8")
        self.assertIn("session: AGENT.session", app)
        self.assertNotRegex(app, r"agent_id\s*:")  # comments may mention it


class KeyDiagnosticsTest(unittest.TestCase):
    def test_logs_facts_never_the_key(self):
        import contextlib
        import hashlib
        import io
        key = "abcd1234secretvalue9999"
        api = FakeAgentsAPI([{"id": "agent_live", "name": "Zabaan AI"},
                             {"id": "agent_gone", "name": "Zabaan AI",
                              "deleted_at": "2026-09-26T10:00:00Z"}])
        out = io.StringIO()
        with unittest.mock.patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": key + " "}), \
             unittest.mock.patch.object(server, "aai", api), \
             contextlib.redirect_stdout(out):
            os.environ.pop("AGENTS_API_BASE", None)
            server.log_key_diagnostics("process environment")
        log = out.getvalue()
        self.assertNotIn(key, log)
        self.assertNotIn(key[:8], log)
        self.assertIn("from process environment", log)
        self.assertIn(f"length {len(key) + 1}", log)
        self.assertIn(hashlib.sha256((key + " ").encode()).hexdigest()[:12], log)
        self.assertIn("leading/trailing whitespace: yes", log)
        self.assertIn("https://agents.assemblyai.com/v1", log)
        self.assertIn("wss://agents.assemblyai.com/v1/ws (from app.js)", log)
        self.assertIn('agent_live "Zabaan AI"', log)
        self.assertIn('agent_gone "Zabaan AI" [deleted', log)


class DeployFilesTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent

    def test_render_blueprint(self):
        text = (self.ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("startCommand: python deployment/browser/server.py", text)
        # The server always loads agents/zabaan.jsonc, so no AGENT is set.
        self.assertNotIn("key: AGENT", text)
        # Secrets are asked for in the Render dashboard, never stored here.
        for key in ("ASSEMBLYAI_API_KEY", "DEMO_PASSCODE"):
            block = text.split(f"- key: {key}", 1)[1].split("- key:", 1)[0]
            self.assertIn("sync: false", block, key)
            self.assertNotIn("value:", block, key)

    def test_license(self):
        text = (self.ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 Zabaan AI team", text)


class MockStartupTest(unittest.TestCase):
    def test_starts_with_no_api_key(self):
        """The real server, as a separate process, with ZABAAN_MOCK=1 and an
        empty key. Only reads pages, so it never writes tickets.json."""
        import re
        import subprocess
        env = dict(os.environ, ZABAAN_MOCK="1", ASSEMBLYAI_API_KEY="", PYTHONUNBUFFERED="1")
        env.pop("PORT", None)
        script = Path(__file__).resolve().parent / "deployment" / "browser" / "server.py"
        proc = subprocess.Popen([sys.executable, str(script)], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            url = None
            for _ in range(10):
                line = proc.stdout.readline()
                found = re.search(r"Talk to it: (http://localhost:\d+)", line)
                if found:
                    url = found.group(1)
                    break
            self.assertIsNotNone(url, "server did not start in mock mode")
            with urllib.request.urlopen(url + "/") as res:
                self.assertIn("Zabaan mock call", res.read().decode("utf-8"))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
