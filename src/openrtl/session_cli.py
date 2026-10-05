"""Local session discovery, diagnostics and explicit portable backup/restore."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from openrtl.adapters.design_portable import portable_input, portable_material, restore_portable, write_portable
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_sessions import discover, inspect_session, support_bundle
from openrtl.application.design_diagnostics import diagnostic
from openrtl.domain.design_session import content_digest


def add_session_commands(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser("sessions", help="local session listing, backup, restore and diagnostics")
    parser.add_argument("--state-dir", type=Path)
    actions = parser.add_subparsers(dest="session_action", required=True)
    listing = actions.add_parser("list")
    listing.add_argument("--root", type=Path, help="explicit directory of projects; defaults to selected product state's projects")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=20)
    for name in ("inspect", "export", "restore", "support"):
        action = actions.add_parser(name)
        action.add_argument("--source" if name == "restore" else "--project", type=Path, required=True)
        if name == "inspect": action.add_argument("--limit", type=int, default=50)
        else:
            action.add_argument("--destination", type=Path, required=True)
            action.add_argument("--approve", help="digest from the exact preview; absent means read-only preview")


def run_sessions(arguments: argparse.Namespace) -> int:
    store = None
    try:
        action = arguments.session_action
        if action == "list":
            from openrtl.onboarding import default_state_dir
            root = arguments.root or ((arguments.state_dir or default_state_dir()) / "projects")
            print(json.dumps(discover(root, offset=arguments.offset, limit=arguments.limit), indent=2))
            return 0
        if action == "restore":
            plan, _, _ = portable_input(arguments.source)
            if arguments.approve:
                restore_portable(arguments.source, arguments.destination, arguments.approve)
        else:
            store = DesignSessionStore(arguments.project, read_only=True)
            if action == "inspect":
                print(json.dumps(inspect_session(store, limit=arguments.limit), indent=2))
                return 0
            if action == "support": plan = support_bundle(store, arguments.destination, arguments.approve)
            else:
                plan, _ = portable_material(store)
                if arguments.approve: write_portable(store, arguments.destination, arguments.approve)
        print(json.dumps(plan, indent=2, sort_keys=True))
        if arguments.approve:
            print("Local " + action + " completed. No provider/runtime permission or cleanup authority transferred; nothing uploaded.")
        else:
            print("Preview only: " + str(arguments.destination))
            print("Includes source, specification, engineering history and run evidence." if action != "support" else
                  "Contains standardized diagnostics and hashes; excludes source, narrative text and provider/model labels.")
            print("To approve exactly this preview, repeat with --approve " + content_digest(plan))
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, UnicodeError) as error:
        print(json.dumps(diagnostic(error), sort_keys=True))
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Preserve incomplete output; choose a new destination to retry. No work was replayed.")
        return 130
    finally:
        if store is not None: store.close()
