#!/bin/sh
# Build plugin/passtar.zip for ChatGPT: the package in plugin/passtar with the MCP URL filled in.
#
#   plugin/build.sh https://xxxx.trycloudflare.com
#
# With THE_PASS_AGENT_KEY set, the key goes into mcp.json as an X-Agent-Key header, for clients that do not ask
# for a key when connecting. The zip then holds the key: keep it to yourself (plugin/*.zip is gitignored).
set -eu
[ $# -eq 1 ] || { echo "usage: $0 https://<public host>" >&2; exit 2; }
url="${1%/}/mcp"
case "$url" in */mcp/mcp) url="${url%/mcp}" ;; esac
here="$(cd "$(dirname "$0")" && pwd)"
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT

cp -R "$here/passtar/." "$build/"
rm -rf "$build/.mcp.json" "$build/.claude-plugin"
URL="$url" python3 - "$build/mcp.json" <<'EOF'
import json, os, sys
path = sys.argv[1]
config = json.load(open(path))
server = config["mcpServers"]["passtar"]
server["url"] = os.environ["URL"]
key = os.environ.get("THE_PASS_AGENT_KEY")
if key:
    server["headers"] = {"X-Agent-Key": key}
json.dump(config, open(path, "w"), indent=2)
print(f"url: {server['url']}  key: {'included' if key else 'not included'}")
EOF
rm -f "$here/passtar.zip"
(cd "$build" && zip -qr "$here/passtar.zip" . -x '*.DS_Store')
echo "built $here/passtar.zip"
