"""Invocation-local reviews for source import, baseline adoption and export."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from openrtl.adapters.design_export import export_design, export_material, show_export
from openrtl.adapters.design_imports import import_design_files
from openrtl.adapters.design_selection import selection, show_selection
from openrtl.adapters.design_session_store import DesignSessionStore, safe_root
from openrtl.application.design_agent import DesignAgent
from openrtl.application.design_conversation import render_spec
from openrtl.application.design_import_workflow import approve_completion
from openrtl.domain.design_readiness import require_ready
from openrtl.domain.design_session import JsonObject, canonical, content_digest, require


@dataclass(frozen=True)
class LocalReview:
    kind: str
    state_digest: str
    payload: bytes
    location: str = ""
    root_identity: tuple[int, int] | None = None
    selections: tuple[str, ...] = ()


def show_manifest(manifest: JsonObject, emit: Callable[[str], None]) -> None:
    emit("Simulation top: " + manifest["top"] + "; seed: " + str(manifest["seed"]))
    for key in ("sources", "test_modules", "expected_tests"):
        emit(key.replace("_", " ") + ": " + ", ".join(manifest[key]))
    for row in manifest["requirement_tests"]:
        emit("Proposed checks for " + row["requirement_id"] + ": " + ", ".join(row["tests"]))
    emit("These links are proposed coverage, not proof of test adequacy. Fresh simulation and signoff are required.")


def review_import(agent: DesignAgent, root: Path, tokens: list[str], emit: Callable[[str], None]) -> LocalReview:
    state = agent._idle()
    require(state["status"] == "discovery", "imports_require_idle_discovery")
    plan, contents, identity = selection(root, tokens)
    require(not set(contents).intersection(state["imports"]), "selection_target_already_imported")
    emit("Selected source root: " + str(safe_root(root)))
    show_selection(plan, contents, emit)
    emit("To import exactly these files, say: approve this import")
    return LocalReview("import", content_digest(state), canonical(plan), str(safe_root(root)), identity, tuple(tokens))


def review_plan(agent: DesignAgent, kind: str, plan: JsonObject, emit: Callable[[str], None]) -> LocalReview:
    require(kind in ("baseline", "completion", "change"), "local_review_kind_invalid")
    state = agent._idle()
    require_ready(plan["specification"])
    if kind == "change":
        emit("Previous specification:")
        render_spec(state["spec"], emit)
    emit("Review " + kind + " specification:")
    render_spec(plan["specification"], emit)
    emit("Existing files to retain:")
    for path in plan["files"] if kind == "baseline" else plan["base_files"]:
        emit("  " + path)
    if kind != "baseline":
        for stage, paths in plan["stage_paths"].items():
            emit("Writable " + stage + ": " + (", ".join(paths) or "none"))
    show_manifest(plan["manifest"], emit)
    emit("Approval records this scope only; it does not invoke a provider or simulator.")
    emit("To approve this review, say: approve this " + kind)
    return LocalReview(kind, content_digest(state), canonical(plan))


def review_export(agent: DesignAgent, destination: Path, emit: Callable[[str], None], *, include_evidence: bool = True) -> LocalReview:
    require(isinstance(agent.store, DesignSessionStore), "local_store_required")
    assert isinstance(agent.store, DesignSessionStore)
    target = safe_root(destination)
    require(not target.exists() and target.parent.is_dir(), "export_destination_must_be_new")
    plan, _ = export_material(agent.store, include_evidence=include_evidence)
    show_export(plan, target, emit)
    emit("To write these source-containing files, say: export this design")
    return LocalReview("export", content_digest(agent.store.read()), canonical(plan), str(target))


def approve_local(agent: DesignAgent, review: LocalReview | None, kind: str) -> JsonObject:
    require(review is not None and review.kind == kind, "show_current_review_before_approval")
    assert review is not None
    require(content_digest(agent._idle()) == review.state_digest, "shown_review_stale")
    plan = json.loads(review.payload)
    if kind == "import":
        require(isinstance(agent.store, DesignSessionStore), "local_store_required")
        assert isinstance(agent.store, DesignSessionStore)
        observed, _, identity = selection(Path(review.location), list(review.selections))
        require(canonical(observed) == review.payload and identity == review.root_identity, "import_selection_changed")
        import_design_files(agent.store, Path(review.location), plan, content_digest(plan))
    elif kind == "baseline":
        require(agent.plan_baseline(plan["manifest"]) == plan, "shown_review_stale")
        agent.approve_baseline(plan["manifest"], content_digest(plan))
    elif kind == "completion":
        approve_completion(agent, plan)
    elif kind == "change":
        agent.approve_change(plan, content_digest(plan))
    else:
        require(kind == "export" and isinstance(agent.store, DesignSessionStore), "local_store_required")
        assert isinstance(agent.store, DesignSessionStore)
        export_design(agent.store, Path(review.location), content_digest(plan), include_evidence=plan["evidence_requested"])
    return agent.store.read()
