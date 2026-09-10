"""Optional AgentRig backend composition, outside the RTL engineering workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

from openrtl.domain.design_session import JsonObject, require

if TYPE_CHECKING:
    from agentrig.capabilities.local_backend import LocalBackendCatalog


def backend_catalog() -> LocalBackendCatalog:
    # AgentRig 0.3.0 remains the immutable public bootstrap dependency. New
    # contracts are an optional source-candidate feature until SDK publication.
    try:
        from agentrig.capabilities.local_backend import LocalBackendCatalog
        from agentrig.integrations.local_backends import ExistingRootlessDockerBackend, LimaBackend
    except ImportError:
        raise ValueError("runtime_backend_sdk_candidate_required") from None
    return LocalBackendCatalog([ExistingRootlessDockerBackend(), LimaBackend()])


def backends(catalog: LocalBackendCatalog | None = None) -> JsonObject:
    selected = backend_catalog() if catalog is None else catalog
    return {"schema": "openrtl.local-backends.v1", "backends": [
        {"id": item.backend_id, "version": item.version,
         "capabilities": sorted(item.capabilities), "actions": sorted(item.actions)}
        for item in selected.descriptors], "runtime_contact": False,
        "execution_authorized": False, "m42b": "pending-runtime-qualification",
        "m46": "pending", "m47": "pending"}


def review_backend(backend_id: str, action: str, configuration_json: str, operation_id: str,
                   catalog: LocalBackendCatalog | None = None) -> JsonObject:
    """Pure review. Output is never written as runtime selection or authority."""
    selected = backend_catalog() if catalog is None else catalog
    from agentrig.capabilities.local_backend import BackendAction, BackendFailure, BackendRequest
    from agentrig.core._json import thaw_json_value
    require(len(configuration_json.encode("utf-8")) <= 65536, "runtime_backend_configuration_invalid")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            require(key not in value, "runtime_backend_configuration_invalid")
            value[key] = item
        return value

    try:
        config = json.loads(configuration_json, object_pairs_hook=unique_object)
        require(isinstance(config, dict), "runtime_backend_configuration_invalid")
        backend = selected.resolve(backend_id)
        request = BackendRequest(action=BackendAction(action), operation_id=operation_id,
                                 configuration=config)
        plan = backend.plan(request)
        return {"schema": "openrtl.local-backend-review.v1", "plan_digest": plan.digest,
            "plan": cast(JsonObject, thaw_json_value(plan.to_json())),
            "execution_authorized": False, "runtime_contact": False, "installation": False,
            "selection_changed": False, "ready_for_simulation": False,
            "notice": "Review only. Proposed pins and capabilities are not observed runtime evidence.",
            "m42b": "pending-runtime-qualification", "m46": "pending", "m47": "pending"}
    except BackendFailure as error:
        raise ValueError("runtime_" + error.code) from None
    except (ValueError, TypeError, RecursionError):
        raise ValueError("runtime_backend_configuration_invalid") from None


def backend_operation_status(state: Path) -> JsonObject:
    """Local diagnostic only; no lock, state creation, runtime contact or grant."""
    from openrtl.onboarding import _open_state
    directory = state.absolute() / 'backend-operations'
    try:
        descriptor = _open_state(directory, create=False)
    except FileNotFoundError:
        return {'schema': 'openrtl.backend-operation-status.v1', 'status': 'unconfigured',
                'runtime_contact': False, 'execution_authorized': False, 'ready_for_simulation': False}
    os.close(descriptor)
    try:
        from agentrig.integrations.backend_journal import LocalBackendJournal
    except ImportError:
        raise ValueError('runtime_backend_sdk_candidate_required') from None
    from agentrig.capabilities.local_backend import BackendFailure
    try:
        record = LocalBackendJournal(directory).inspect()
    except BackendFailure:
        raise ValueError('runtime_backend_journal_invalid') from None
    return {'schema': 'openrtl.backend-operation-status.v1',
        'status': record.status if record else 'empty',
        'operation_id': record.operation_id if record else None,
        'backend_id': record.backend_id if record else None,
        'action': record.action.value if record else None,
        'observation': record.observation if record else None,
        'runtime_contact': False, 'execution_authorized': False, 'ready_for_simulation': False,
        'notice': 'Local journal snapshot only. Reconciliation needs a fresh authorized runtime inspection.',
        'm42b': 'pending-runtime-qualification', 'm46': 'pending', 'm47': 'pending'}


def audit_backend_bundle(root: Path, manifest_json: str) -> JsonObject:
    """Audit explicitly selected public artifact bytes without configuring a runtime."""
    require(len(manifest_json.encode()) <= 65536, 'runtime_backend_artifacts_invalid')
    try:
        from agentrig.capabilities.backend_artifacts import BackendArtifact, BackendArtifactManifest
        from agentrig.integrations.backend_artifacts import audit_backend_artifacts
    except ImportError:
        raise ValueError('runtime_backend_sdk_candidate_required') from None
    from agentrig.capabilities.local_backend import BackendFailure
    from agentrig.core import CancellationSource, Deadline, DeadlineExceeded, RunContext, RunId
    from agentrig.core.clock import SystemClock
    from agentrig.core.identity import Uuid4IdGenerator
    from openrtl.onboarding import _open_state

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            require(key not in result, 'runtime_backend_artifacts_invalid')
            result[key] = value
        return result

    try:
        value = json.loads(manifest_json, object_pairs_hook=unique)
        require(isinstance(value, dict) and set(value) == {'schema', 'artifacts'} and
                value['schema'] == 'agentrig.backend-artifacts.v1' and isinstance(value['artifacts'], list),
                'runtime_backend_artifacts_invalid')
        artifacts = []
        for item in value['artifacts']:
            require(isinstance(item, dict) and set(item) == {'id', 'path', 'sha256', 'size_bytes', 'executable'},
                    'runtime_backend_artifacts_invalid')
            artifacts.append(BackendArtifact(artifact_id=item['id'], path=item['path'], sha256=item['sha256'],
                             size_bytes=item['size_bytes'], executable=item['executable']))
        manifest = BackendArtifactManifest(artifacts=tuple(artifacts))
        directory = root.absolute()
        descriptor = _open_state(directory, create=False)
        os.close(descriptor)
        clock = SystemClock()
        context = RunContext.create_root(clock=clock, id_generator=Uuid4IdGenerator(RunId),
                                         cancellation=CancellationSource().token,
                                         deadline=Deadline.after(60, clock))
        result = audit_backend_artifacts(directory, manifest, context)
    except (BackendFailure, DeadlineExceeded, ValueError, TypeError, KeyError, RecursionError, OSError):
        raise ValueError('runtime_backend_artifacts_invalid') from None
    return {'schema': 'openrtl.backend-artifact-audit.v1', 'manifest_digest': result.manifest_digest,
        'file_count': result.file_count, 'total_bytes': result.total_bytes, 'declared_bytes_verified': True,
        'dependency_closure_qualified': False, 'runtime_contact': False, 'execution_authorized': False,
        'ready_for_simulation': False, 'installation': False, 'selection_changed': False,
        'notice': 'Local bytes match the declared manifest. Guest dependencies, transport and runtime readiness remain unverified.',
        'm42b': 'pending-runtime-qualification', 'm46': 'pending', 'm47': 'pending'}
