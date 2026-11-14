# The Pass as an AI assistant plugin

ChatGPT or Claude acts as a buyer's purchasing agent: it connects to The Pass over MCP with that buyer's agent key
and orders within the buyer's signed protocol. The server is `backend/app/orchestrator/mcp_server.py` (`POST /mcp`).

```
the-pass/
  mcp.json                          ChatGPT: MCP server, API key in the X-Agent-Key header
  .mcp.json                         Claude: the same server; URL and key from environment variables
  .claude-plugin/plugin.json        Claude plugin manifest
  skills/valio-purchasing/SKILL.md  How to buy through The Pass: workflow, presenting options, honesty rules
```

## Run it for a demo

1. Start the backend (from `backend/`, with `.env` loaded for live Valio data):
   `python -m uvicorn app.main:app --port 8000`
2. Replace the public demo keys, since the URL will be reachable from the internet:
   `python scripts/new_agent_keys.py agt_hotel` and keep the printed key private.
   `/v1/admin/reset` restores the demo keys; run the script again after a reset.
3. Open a tunnel: `cloudflared tunnel --url http://localhost:8000` and note the `https://….trycloudflare.com` URL.
   The URL changes every time the tunnel starts.
4. **ChatGPT**: set `url` in `mcp.json` to `https://….trycloudflare.com/mcp`, build the plugin with the skill and
   upload it; give the agent key when connecting.
   **Claude Code**: `export THE_PASS_MCP_URL=https://….trycloudflare.com/mcp THE_PASS_AGENT_KEY=<key>`, then
   `claude --plugin-dir plugin/the-pass`.

Check the endpoint without an assistant:

```
curl -s https://….trycloudflare.com/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H "X-Agent-Key: $THE_PASS_AGENT_KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Demo agents

| Agent | Buyer | Protocol | Approval |
|---|---|---|---|
| `agt_hotel` | Hotel Helsinki Central | lactose-free | procurement manager, in The Pass |
| `agt_hospital` | Hospital | nut-free | hospital manager, in The Pass |
| `agt_bistro` | Bistro Kallio | owner approves own orders | owner, in The Pass |
| `agt_rogue` | – | no protocol: every call is refused and reported | – |

Each protocol orders automatically up to €1,000 and refuses anything over €3,000; in between, a person approves.
