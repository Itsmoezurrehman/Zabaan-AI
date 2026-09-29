# Browser

The only way Zabaan answers: a Python server that serves the voice page, the text demo and the tickets dashboard. Standard library only.

## Run

```powershell
python deployment/browser/server.py                          # voice, needs ASSEMBLYAI_API_KEY in .env
$env:ZABAAN_MOCK = "1"; python deployment/browser/server.py  # text demo, no key needed
```

It prints the addresses to open: http://localhost:3000 unless that port is taken.

## How a call is configured

Nothing is published and no agent id is used. At startup the server reads `agents/zabaan.jsonc`, always that file, and turns it into the session fields AssemblyAI accepts inline: system prompt, greeting, input languages and keyterms, output voice, and the tools. It stops with an error if a tool has an `http` block or the file has a key a session cannot take, so nothing secret can reach the page.

The page receives that config and sends it whole in the first `session.update` of every call. Agents stored with `POST /v1/agents` proved region-local (a browser could be routed to a region where the stored agent did not exist), so sending the config with each call is the reliable path.

`AGENT` and `AGENT_ID` are ignored; the startup log says so if either is set.

## Routes

| Route | What it does |
| --- | --- |
| `GET /` | The voice page. In mock mode, the text demo. |
| `GET /token` | A 60-second session token from AssemblyAI. The API key never leaves the server. Needs the `X-Demo-Passcode` header when `DEMO_PASSCODE` is set. Off in mock mode. |
| `GET /agent` | The inline config, read only, for the page's Agent tab. It contains the system prompt, which is public by design. Off in mock mode. |
| `GET /mock` | The text demo, on every server, no passcode. |
| `GET /tickets` | The tickets dashboard page. It holds no data; it asks for the passcode when the APIs below answer 401. |
| `GET /api/tickets`, `GET /api/rejections` | Tickets and rejected confirmations, as JSON. Need the `X-Demo-Passcode` header when `DEMO_PASSCODE` is set. Emails and addresses are masked, in their own fields and in the recognizer text. |
| `POST /api/tickets/draft` | `create_ticket`, forwarded by the page. Saves a draft only. |
| `POST /api/tickets/confirm` | `confirm_ticket`, forwarded by the page. The only route that files a ticket. |
| `POST /api/mock/message` | One turn of the text demo. |

All filing rules are in [`tickets.py`](../../tickets.py). The page only forwards tool calls and returns the server's reply as `tool.result`.

## The page

[index.html](index.html) and [app.js](app.js) stream the microphone as 24 kHz PCM16 over `wss://agents.assemblyai.com/v1/ws`, play the reply, and drop queued audio when the caller interrupts. The side pane shows every websocket frame (Events) and the inline config (Agent).

`end_call` hangs up once the goodbye has finished playing, with a 15-second fallback. Every call ends after 3 minutes (`MAX_CALL_SECONDS` in server.py). Starting a new call clears the transcript.

## Environment

| Variable | |
| --- | --- |
| `ASSEMBLYAI_API_KEY` | Required for voice. Stays in this process. |
| `DEMO_PASSCODE` | Optional. When set, starting a voice call and reading the tickets dashboard need it; the text demo does not. Plain ASCII. |
| `ZABAAN_MOCK` | `1` runs the text demo only: no key, no `/token`, no `/agent`. |
| `APP_MAINTENANCE` | `1` simulates scheduled app maintenance: `create_ticket` for an app issue returns the definite maintenance note instead of the default "might be undergoing maintenance" one. Off unless set to `1`. |
| `PORT` | When set, the server listens on every interface on that port, as Render needs. Unset, it listens on 127.0.0.1 only, from 3000 up. |

## Hosting

`render.yaml` at the repo root defines the Render service. Render asks for `ASSEMBLYAI_API_KEY` and `DEMO_PASSCODE` in its dashboard and sets `PORT` itself. Anyone with the URL and the passcode can start calls billed to your key.
