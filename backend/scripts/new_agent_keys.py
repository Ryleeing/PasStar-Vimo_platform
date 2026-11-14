"""Give every agent a new random key before the API is reachable from the internet (e.g. through a tunnel).

The seeded keys (demo-hotel, demo-hospital, ...) are in the repository, so anyone who finds the public URL could
order with them. This replaces them in the database and prints the new keys once; only their hashes are stored.
/v1/admin/reset reseeds the demo keys, so run this again after a reset.

Run from backend/:  python scripts/new_agent_keys.py            all agents
                    python scripts/new_agent_keys.py agt_hotel  one agent
"""
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.channel.agents import store_key  # noqa: E402


def main(agent_ids: list[str]) -> None:
    db.init()
    rows = db.all("SELECT id, name FROM agents ORDER BY id")
    chosen = [r for r in rows if not agent_ids or r["id"] in agent_ids]
    if not chosen:
        sys.exit(f"No such agent. Agents: {', '.join(r['id'] for r in rows)}")
    for r in chosen:
        key = f"tp_{secrets.token_urlsafe(24)}"
        store_key(r["id"], key)
        print(f"{r['id']:<16} {key}   ({r['name']})")
    print(f"\nDatabase: {db.DB_PATH}. Keep these keys out of chat and out of git.")


if __name__ == "__main__":
    main(sys.argv[1:])
