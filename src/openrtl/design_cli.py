"""Interactive M36 alpha front door; transcript text is not retained in the ledger."""

from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sqlite3
from typing import Callable

from openrtl.adapters.design_session_store import DesignSessionStore, safe_root
from openrtl.adapters.design_imports import import_design_files
from openrtl.application.design_agent import DesignAgent, DesignPolicy, design_input_digest
from openrtl.application.design_batch import bind_delegation, run_batch
from openrtl.domain.design_session import JsonObject, MAX_CONTEXT_BYTES, STAGES, canonical, content_digest, require, object_value
from openrtl.domain.design_coaching import analysis_input_digest


def add_design_commands(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    for command in ("chat", "resume", "batch", "recover", "status", "doctor", "import", "baseline", "change", "compare", "acceptance"):
        selected = subcommands.add_parser(command, help="design-agent alpha: " + command)
        selected.add_argument("--project", type=Path, required=command != "doctor")
        if command in ("chat", "resume", "batch", "recover"):
            selected.add_argument("--upgrade-session", action="store_true")
            selected.add_argument("--allow-provider", action="store_true")
            selected.add_argument("--model")
            selected.add_argument("--credential-env", default="OPENAI_API_KEY")
            selected.add_argument("--max-calls", type=int, default=40)
            selected.add_argument("--max-repairs", type=int, default=2)
            selected.add_argument("--max-output-tokens", type=int, default=16000)
            selected.add_argument("--timeout-seconds", type=int, default=120)
            selected.add_argument("--allow-simulation", action="store_true")
            selected.add_argument("--simulation-profile", type=Path)
        if command == "batch":
            selected.add_argument("--create", action="store_true")
            selected.add_argument("--spec", type=Path)
            selected.add_argument("--delegation", type=Path, required=True)
            selected.add_argument("--approve-delegation", required=True)
        if command == "recover":
            selected.add_argument("--abandon-operation", required=True)
        if command in ("import", "baseline", "change"):
            selected.add_argument("--upgrade-session", action="store_true")
            selected.add_argument("--approve", required=command == "import")
        if command == "import":
            selected.add_argument("--create", action="store_true")
            selected.add_argument("--source-root", required=True, type=Path)
            selected.add_argument("--import-plan", required=True, type=Path)
        if command == "baseline":
            selected.add_argument("--manifest", required=True, type=Path)
        if command == "change":
            group = selected.add_mutually_exclusive_group(required=True)
            group.add_argument("--request", type=Path)
            group.add_argument("--plan", type=Path)
        if command == "compare":
            selected.add_argument("--baseline-revision", type=int, required=True)
        if command == "acceptance":
            selected.add_argument("--expected-spec", type=Path)


def _json_file(path: Path) -> object:
    selected = safe_root(path)
    require(selected.suffix == ".json" and not any(p.startswith(".") for p in selected.parts),
            "explicit_nonhidden_json_file_required")
    require(selected.is_file() and selected.stat().st_size <= MAX_CONTEXT_BYTES, "json_file_unavailable_or_large")
    return json.loads(selected.read_bytes())


def show(state: JsonObject, emit: Callable[[str], None]) -> None:
    emit("State: " + state["status"] + "; expert calls: " + str(state["calls"]))
    emit("Detail: " + state["detail"] + "; pace: " + state.get("pace", "stage"))
    if state["active"] is not None:
        emit("Interrupted operation retained; no automatic replay: " + state["active"]["id"])
    if state["spec"] is not None:
        spec = state["spec"]
        emit(spec["title"] + " — top: " + spec["top"])
        emit(spec["behavior"])
        emit("Clock/reset: " + spec["clock_reset"])
        for port in spec["ports"]:
            emit("Port: " + port["direction"] + " " + port["name"] + " [" + str(port["width"]) + " bits]")
        for row in spec["requirements"]:
            emit(row["id"] + ": " + row["text"] + " | Acceptance: " + row["acceptance"])
        for row in spec["assumptions"]:
            emit("REVIEW ASSUMPTION " + row["id"] + ": " + row["text"] + " — " + row["rationale"])
        for row in spec["questions"]:
            emit("QUESTION " + row["id"] + ": " + row["text"])
        if state["status"] == "discovery" and not spec["questions"] and spec["ports"]:
            emit("Review all requirements, ports and assumptions above, then: /approve " + content_digest(spec))
    if state["simulation"]:
        emit("Simulation: " + state["simulation"]["status"] + " (" + state["simulation"]["evidence_kind"] + ")")
    if state["review"]:
        emit("Review: " + state["review"]["summary"])
        for finding in state["review"]["findings"]:
            emit("Unresolved: " + finding)
    if state["status"] == "awaiting_acceptance":
        digest = content_digest({"input": design_input_digest(state), "simulation": state["simulation"],
                                 "review": state["review"]})
        emit("Inspect artifacts and evidence before final acceptance: /approve " + digest)
    if state["last_error"]:
        emit("Last error: " + state["last_error"])
    for row in state.get("warnings", []):
        emit("WARNING " + row["id"] + " [" + row["review"] + "]: " + row["text"] + " — " + row["rationale"])
    if state.get("acceptance_mode") == "delegated":
        emit("Accepted under broad delegation, not individual final user review.")
    for path, item in state.get("imports", {}).items():
        emit("Imported reference (not run evidence): " + path + " " + item["digest"])
    if state.get("change_plan"):
        emit("Reviewed change scope: " + json.dumps(state["change_plan"]["stage_paths"], sort_keys=True))
    if state.get("proposal"):
        proposal = state["proposal"]
        emit("UNAPPLIED PROPOSAL: " + json.dumps(proposal, indent=2, sort_keys=True))
        if proposal["plan"]["base_input_digest"] == design_input_digest(state):
            emit("Review the complete proposal, then: /approve-change " + content_digest(proposal))
        else:
            emit("Proposal is stale; request a new proposal before approval.")
    if state.get("analysis"):
        label = "Current" if state["analysis"]["input_digest"] == analysis_input_digest(state) else "Stale"
        emit(label + " analysis (not an applied repair): " + json.dumps(state["analysis"], indent=2, sort_keys=True))


def coaching_preference(agent: DesignAgent, message: str, emit: Callable[[str], None]) -> bool:
    phrase = message.casefold().strip().rstrip(".! ")
    detail = {"keep it brief": "brief", "explain in detail": "detailed", "normal detail": "normal"}
    pace = {"go step by step": "stage", "one step at a time": "stage", "continue automatically": "continuous"}
    if phrase in detail:
        agent.detail(detail[phrase])
    elif phrase in pace:
        agent.pace(pace[phrase])
    else:
        return False
    emit("Preference saved. No engineering step, approval or provider permission was granted; use /continue to proceed.")
    return True


async def conversation(agent: DesignAgent, *, read: Callable[[str], str] = input,
                       emit: Callable[[str], None] = print) -> int:
    emit("OpenRTL design-agent alpha. Raw conversation text is not saved; reviewed artifacts and events are.")
    emit("/show /spec <JSON path> /approve <digest> /ack-warning <id> /next /build /revise /detail brief|normal|detailed /quit")
    emit("/import <reviewed-import-request.json> /explain <question> /baseline <manifest.json> /change-plan <request.json>")
    emit("/propose-change <request> /propose-dv <request> /propose-optimization <request> /approve-change <digest>")
    emit("/diagnose <question> /compare <baseline-revision> /pace stage|continuous /continue")
    show(agent.store.read(), emit)
    while True:
        try:
            message = read("OpenRTL > ").strip()
        except EOFError:
            return 0
        if not message:
            continue
        if message == "/quit":
            emit("Session saved. Resume using the same project directory.")
            return 0
        command, _, argument = message.partition(" ")
        try:
            if coaching_preference(agent, message, emit):
                continue
            if command == "/show":
                show(agent.store.read(), emit)
                emit(json.dumps(agent.store.read()["files"], indent=2, sort_keys=True))
            elif command == "/spec":
                show(agent.propose(_json_file(Path(argument))), emit)
            elif command == "/import":
                request = object_value(_json_file(Path(argument)), {"source_root", "plan", "approved_digest"})
                if not isinstance(agent.store, DesignSessionStore):
                    raise ValueError("local_import_store_required")
                show(import_design_files(agent.store, Path(request["source_root"]), request["plan"], request["approved_digest"]), emit)
            elif command == "/baseline":
                plan = agent.plan_baseline(_json_file(Path(argument)))
                emit(json.dumps({"plan": plan, "digest": content_digest(plan)}, indent=2, sort_keys=True))
                emit("Quit, then use the local baseline command with --approve to adopt these exact inputs.")
            elif command == "/change-plan":
                plan = agent.plan_change(_json_file(Path(argument)))
                emit(json.dumps({"plan": plan, "digest": content_digest(plan)}, indent=2, sort_keys=True))
                emit("Save the plan object, quit, then use the local change command with --plan and --approve.")
            elif command == "/approve":
                show(agent.approve(argument.strip()), emit)
            elif command in ("/propose-change", "/propose-dv", "/propose-optimization"):
                intent = {"/propose-change": "feature", "/propose-dv": "dv", "/propose-optimization": "optimization"}[command]
                await agent.propose_improvement(argument, intent=intent)
                show(agent.store.read(), emit)
            elif command == "/approve-change":
                show(agent.approve_improvement(argument.strip()), emit)
            elif command == "/diagnose":
                result = await agent.analyze(argument or "Explain current findings and recommend discriminating checks.")
                emit(json.dumps(result, indent=2, sort_keys=True))
            elif command == "/compare":
                emit(json.dumps(agent.compare(int(argument)), indent=2, sort_keys=True))
            elif command == "/pace":
                agent.pace(argument.strip())
                emit("Pace saved; approvals and evidence gates are unchanged.")
            elif command == "/revise":
                show(agent.revise(), emit)
            elif command == "/ack-warning":
                show(agent.acknowledge_warning(argument.strip()), emit)
            elif command == "/detail":
                agent.detail(argument.strip())
                emit("Explanation detail changed; review and evidence gates are unchanged.")
            elif command in ("/next", "/build", "/continue"):
                single = command == "/next" or command == "/continue" and agent.store.read()["pace"] == "stage"
                while True:
                    state = agent.store.read()
                    stage = (STAGES[state["stage"]] if state["status"] == "building" and
                             state["stage"] < len(STAGES) else state["status"])
                    emit("Starting: " + stage)
                    state = await agent.advance()
                    emit("Recorded: " + state["status"] + "; revision " + str(state["revision"]))
                    if stage in state["summaries"]:
                        emit(state["summaries"][stage])
                    if single or state["status"] not in ("building", "needs_repair", "needs_signoff"):
                        show(state, emit)
                        break
            elif command == "/explain":
                result = await agent.explain(argument)
                emit(result["explanation"])
                for ref in result["references"]:
                    emit(ref["path"] + ":" + str(ref["line"]))
            elif command.startswith("/"):
                emit("Unknown command; no changes made.")
            elif agent.store.read()["status"] == "discovery":
                show(await agent.discuss(message), emit)
            else:
                result = await agent.explain(message)
                emit(result["explanation"])
                for ref in result["references"]:
                    emit(ref["path"] + ":" + str(ref["line"]))
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
            # Provider exceptions may contain private payloads. Only standardized state codes are shown.
            state = agent.store.read()
            emit("Request stopped: " + (state["last_error"] or "review_configuration_or_operation_state"))
            if state["active"] is not None:
                return 1


def run_design_command(arguments: argparse.Namespace) -> int:
    store: DesignSessionStore | None = None
    try:
        if arguments.command == "doctor":
            report: JsonObject = {"schema": "openrtl.design-doctor.v1", "provider_calls": False,
                                   "credential_resolution": False, "simulation_performed": False}
            for package in ("openrtl", "agentrig", "openai"):
                try:
                    report[package] = version(package)
                except PackageNotFoundError:
                    report[package] = "not_installed"
            report["notice"] = "A pinned local container profile is required for untrusted generated DV; no automatic installs. Live design qualification remains separate."
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if arguments.command == "status":
            store = DesignSessionStore(arguments.project, read_only=True)
            print(json.dumps(store.read(), indent=2, sort_keys=True))
            return 0
        if arguments.command == "acceptance":
            from openrtl.adapters.design_acceptance import acceptance_report
            store = DesignSessionStore(arguments.project, read_only=True)
            report = acceptance_report(store, _json_file(arguments.expected_spec) if arguments.expected_spec else None)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["status"] == "local_gates_satisfied" else 2
        if arguments.command == "compare":
            store = DesignSessionStore(arguments.project, read_only=True)
            print(json.dumps(DesignAgent(store).compare(arguments.baseline_revision), indent=2, sort_keys=True))
            return 0
        if arguments.command in ("import", "baseline", "change"):
            create = arguments.command == "import" and arguments.create
            readonly = arguments.command != "import" and not arguments.approve and not arguments.upgrade_session
            store = DesignSessionStore(arguments.project, create=create, read_only=readonly)
            if arguments.upgrade_session:
                store.upgrade()
            local_agent = DesignAgent(store)
            if arguments.command == "import":
                show(import_design_files(store, arguments.source_root, _json_file(arguments.import_plan), arguments.approve), print)
            elif arguments.command == "baseline":
                selected = _json_file(arguments.manifest)
                if arguments.approve:
                    show(local_agent.approve_baseline(selected, arguments.approve), print)
                else:
                    plan = local_agent.plan_baseline(selected)
                    print(json.dumps({"plan": plan, "digest": content_digest(plan)}, indent=2, sort_keys=True))
            elif arguments.request is not None:
                require(not arguments.approve, "change_request_is_not_approved_plan")
                plan = local_agent.plan_change(_json_file(arguments.request))
                print(json.dumps({"plan": plan, "digest": content_digest(plan)}, indent=2, sort_keys=True))
            else:
                require(arguments.approve is not None, "explicit_change_approval_required")
                show(local_agent.approve_change(_json_file(arguments.plan), arguments.approve), print)
            return 0
        policy = DesignPolicy(arguments.max_calls, arguments.max_repairs)
        expert = None
        simulator = None
        require(arguments.command != "recover" or not arguments.allow_provider, "recovery_never_invokes_provider")
        if arguments.allow_provider:
            require(arguments.model is not None, "explicit_model_required")
            from openrtl.adapters.design_generation import openai_design_expert
            expert = openai_design_expert(authorized=True, model=arguments.model,
                                          credential_environment=arguments.credential_env,
                                          timeout_seconds=arguments.timeout_seconds,
                                          max_output_tokens=arguments.max_output_tokens)
            print("Provider calls authorized for this invocation: OpenAI model " + arguments.model +
                  "; provider-managed retention; bounded project context will leave this computer.")
        require(arguments.allow_simulation == (arguments.simulation_profile is not None),
                "simulation_profile_and_authorization_required_together")
        if arguments.allow_simulation:
            from openrtl.adapters.design_simulation import IsolatedDesignSimulator
            simulator = IsolatedDesignSimulator(arguments.project, _json_file(arguments.simulation_profile))
        create = arguments.command == "chat" or arguments.command == "batch" and arguments.create
        store = DesignSessionStore(arguments.project, create=create)
        if arguments.upgrade_session:
            store.upgrade()
        agent = DesignAgent(store, expert, simulator, policy, recovery=simulator)
        if arguments.command == "recover":
            show(asyncio.run(agent.abandon(arguments.abandon_operation)), print)
            return 0
        if arguments.command == "batch":
            if arguments.spec is not None:
                seed = _json_file(arguments.spec)
                state = store.read()
                if state["spec"] is None:
                    agent.propose(seed)
                else:
                    expected = state["delegation"]["seed_spec"] if state.get("delegation") else state["spec"]
                    require(seed == expected, "batch_seed_cannot_replace_existing_spec")
            bind_delegation(agent, _json_file(arguments.delegation), arguments.approve_delegation)
            report = asyncio.run(run_batch(agent))
            parent = safe_root(store.root / "reports")
            parent.mkdir(mode=0o700, exist_ok=True)
            target = safe_root(parent / ("batch-" + content_digest(report)[7:] + ".json"))
            payload = canonical(report)
            if target.exists():
                require(target.is_file() and target.read_bytes() == payload, "batch_report_collision")
            else:
                with target.open("xb") as stream:
                    stream.write(payload)
            print(json.dumps({"outcome": report["outcome"], "report": str(target)}, sort_keys=True))
            return 0 if report["outcome"] == "accepted" else 2
        return asyncio.run(conversation(agent))
    except KeyboardInterrupt:
        print("Interrupted. Recorded operations are not automatically replayed; inspect status before continuing.")
        return 130
    except (ValueError, OSError, sqlite3.Error, ImportError, KeyError, TypeError):
        print("OpenRTL design command stopped; check explicit project, approval and pinned runtime configuration. Details withheld.")
        return 1
    finally:
        if store is not None:
            store.close()
