"""Non-executing checks on already bounded, ownership-validated DV proposals."""

from __future__ import annotations

import warnings

from openrtl.domain.artifact_validation import DVValidationError, ManifestValidationError
from openrtl.domain.design_session import JsonObject


def validate_dv_files(files: list[JsonObject], *,
                      manifest_error: ManifestValidationError | None = None) -> None:
    """Report syntax and manifest defects together without running generated code.

    The caller must validate file types, size, paths and review scope first.
    Never retain SyntaxError text: it can include provider-authored source.
    """
    locations = []
    for index, row in enumerate(files):
        try:
            # Compilation validates control-flow syntax as well as parsing.
            # Warnings may quote source, so they must not escape into logs.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                compile(row["content"], "<generated-dv>", "exec", dont_inherit=True)
        except SyntaxError as error:
            locations.append((index, max(1, error.lineno or 1), max(1, error.offset or 1)))
        except (RecursionError, MemoryError, OverflowError):
            # A bounded source can still exceed compiler resource limits. Close
            # the received operation without retrying or exposing compiler text.
            raise ValueError("python_compile_resource_limit") from None
    if locations:
        raise DVValidationError(manifest_error, locations) from None
    if manifest_error is not None:
        raise manifest_error
