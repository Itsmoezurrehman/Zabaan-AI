# agents/

[zabaan.jsonc](zabaan.jsonc) is the whole Zabaan AI voice agent: its name, greeting, system prompt, voice, language settings and the three client-side tools (`create_ticket`, `confirm_ticket`, `end_call`).

The file keeps the shape of the AssemblyAI create-agent request body, with comments. Nothing is published from it: at startup, `deployment/browser/server.py` turns it into a session configuration, and the page sends that in the first `session.update` of every call. The server only ever loads this file.

The tools have no `http` block, so AssemblyAI hands each call to the browser, which forwards it to the Python server. The prompt asks the model to use them properly, but the filing rules are enforced in [`tickets.py`](../tickets.py), not here, so no wording in the prompt can get an unconfirmed complaint filed.

`test_agent_file.py` guards the key lines of the prompt and the tool definitions.

Documentation: [Session configuration](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/session-configuration) · [Client-side tools](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/tools/client-side-tools) · [Voices](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/voices) · [Languages](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/supported-languages) · [Prompting guide](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/prompting-guide)
