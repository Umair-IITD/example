"""
scripts/setup_asana_project.py

Sprint 2.63.2: One-off, idempotent script to set up the "Support Escalation"
Asana project's board structure — sections for triage workflow. Run this once
(safe to re-run — it never creates a duplicate of a section whose name already
exists, and it never touches or deletes any existing section, including the
leftover "Follow Up Steps" section from Asana's default project template).

Prerequisites:
  ASANA_API_KEY / ASANA_PROJECT_ID / ASANA_WORKSPACE_ID set in .env
  (same credentials used by scripts/register_asana_webhook.py).

Usage:
    python scripts/setup_asana_project.py

What it does:
  1. Lists existing sections in the configured ASANA_PROJECT_ID.
  2. Creates any of the four standard sections that don't already exist by
     name: "New - Needs Triage", "In Progress", "Blocked / Needs Info", "Done".
  3. Prints the GID of "New - Needs Triage" and tells you to set it as
     ASANA_NEW_TICKET_SECTION_ID in .env — that's the only GID the app
     actually needs at runtime (new tickets are placed there at creation;
     the other three are purely for your team to drag cards between as they
     work, the app never reads or writes them).

This script does not touch any other Asana project. It only ever acts on the
project configured via ASANA_PROJECT_ID in your own .env, and it never
deletes or renames anything that already exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from asana.client import AsanaConfig, AsanaClient

# Standard triage workflow sections, in board order. Dev drags cards between
# these manually as they work — the app never reads section membership except
# to place a brand-new ticket into the first one.
STANDARD_SECTIONS = [
    "New - Needs Triage",
    "In Progress",
    "Blocked / Needs Info",
    "Done",
]


def main() -> int:
    config = AsanaConfig.from_env()
    if not config.has_credentials:
        print(
            "ASANA_API_KEY and ASANA_PROJECT_ID must be set in .env before running this script."
        )
        return 1

    client = AsanaClient(config)

    print(f"Checking existing sections in project={config.project_gid}...")
    try:
        existing = client.get_project_sections()
    except Exception as exc:
        print(f"FAILED to list sections: {exc}")
        return 1

    existing_names = {s.get("name") for s in existing}
    print(f"Found {len(existing)} existing section(s): {sorted(n for n in existing_names if n)}")

    gids: dict[str, str] = {s.get("name"): s.get("gid") for s in existing}

    for name in STANDARD_SECTIONS:
        if name in existing_names:
            print(f"  [skip]   '{name}' already exists (gid={gids[name]})")
            continue
        try:
            created = client.create_section(name)
        except Exception as exc:
            print(f"  [FAILED] '{name}': {exc}")
            return 1
        gids[name] = created.get("gid", "")
        print(f"  [created] '{name}' (gid={gids[name]})")

    new_section_gid = gids.get("New - Needs Triage", "")
    print()
    print("Done. Add this line to your .env:")
    print(f"  ASANA_NEW_TICKET_SECTION_ID={new_section_gid}")
    print()
    print(
        "Note: the pre-existing 'Follow Up Steps' section (leftover from Asana's "
        "default project template) was left untouched — delete or rename it "
        "yourself in the Asana UI if you don't want it, this script won't touch it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
