# PasStar as an AI assistant plugin

ChatGPT or Claude acts as a buyer's purchasing agent: it connects to PasStar over MCP with that buyer's agent key
and orders within the buyer's signed protocol. The server is `backend/app/orchestrator/mcp_server.py` (`POST /mcp`).

```
passtar/
  plugin.json                       Agent Plugins manifest (required at the package root)
  mcp.json                          MCP server (Agent Plugins schema: type and url only; the key is given when connecting)
  .mcp.json                         Claude: the same server; URL and key from environment variables
  .claude-plugin/plugin.json        Claude plugin manifest
  skills/valio-purchasing/SKILL.md  How to buy through PasStar: workflow, presenting options, honesty rules
```

## Run it for a demo

1. Start the backend (from `backend/`, with `.env` loaded for live Valio data):
   `python -m uvicorn app.main:app --port 8000 --reload`
2. Get a private key for the agent, since the URL will be reachable from the internet: on the screens, Agents →
   Connect an agent → Issue the key (as the protocol's approver), or `python scripts/new_agent_keys.py agt_hotel`.
   Reset demo restores the public demo keys; issue a new key after a reset.
3. Open a tunnel: `cloudflared tunnel --url http://localhost:8000` and note the `https://….trycloudflare.com` URL.
   The URL changes every time the tunnel starts.
4. **ChatGPT**: build the package with the URL and the key, then upload `plugin/passtar.zip` in the ChatGPT desktop
   app (remove the previous version first):
   ```bash
   read -s "THE_PASS_AGENT_KEY?Agent key: "; export THE_PASS_AGENT_KEY
   plugin/build.sh https://….trycloudflare.com
   ```
   `build.sh` fills in `mcp.json` and puts the key in an `X-Agent-Key` header. The zip holds the key, so keep it to
   yourself; `plugin/*.zip` is git-ignored.
   **Claude Code**: `claude mcp add --transport http passtar https://….trycloudflare.com/mcp --header "X-Agent-Key: <key>"`,
   or `THE_PASS_MCP_URL=https://….trycloudflare.com/mcp THE_PASS_AGENT_KEY=<key> claude --plugin-dir plugin/passtar`.

Check the endpoint without an assistant:

```
curl -s https://….trycloudflare.com/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H "X-Agent-Key: $THE_PASS_AGENT_KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Demo agents

| Agent | Buyer | Protocol | Approval |
|---|---|---|---|
| `agt_hotel` | Hotel Helsinki Central | lactose-free | procurement manager, in PasStar |
| `agt_hospital` | Hospital | nut-free | hospital manager, in PasStar |
| `agt_bistro` | Bistro Kallio | signed in step 0 after Reset demo; the owner approves her own orders | owner, in PasStar |
| `agt_rogue` | – | no protocol: every call is refused and reported | – |

Each protocol orders automatically up to €1,000 and refuses anything over €3,000; in between, a person approves.
