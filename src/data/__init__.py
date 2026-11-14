"""Data loading and validation layer (Rol 1 — pozo)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.data.loaders import (
        CaseBundle,
        CaseNotFoundError,
        ProcessingConfig,
        StudyCaseConfig,
        StudyCasesConfig,
        clear_loader_cache,
        list_available_cases,
        load_all_available_bundles,
        load_case_bundle,
        load_study_cases_config,
        resolve_case_dir,
    )
    from src.data.validation import (
        CASE_ARTIFACT_FILENAMES,
        MAX_PROCESSED_BYTES,
        ArtifactValidationError,
        TreeValidationReport,
        ValidationResult,
        compute_processed_bytes,
        discover_case_dirs,
        validate_case_bundle,
        validate_case_directory,
        validate_processed_tree,
    )

__all__ = [
    "ArtifactValidationError",
    "CASE_ARTIFACT_FILENAMES",
    "CaseBundle",
    "CaseNotFoundError",
    "MAX_PROCESSED_BYTES",
    "ProcessingConfig",
    "StudyCaseConfig",
    "StudyCasesConfig",
    "TreeValidationReport",
    "ValidationResult",
    "clear_loader_cache",
    "compute_processed_bytes",
    "discover_case_dirs",
    "list_available_cases",
    "load_all_available_bundles",
    "load_case_bundle",
    "load_study_cases_config",
    "resolve_case_dir",
    "validate_case_bundle",
    "validate_case_directory",
    "validate_processed_tree",
]

_LOADER_EXPORTS = {
    "CaseBundle",
    "CaseNotFoundError",
    "ProcessingConfig",
    "StudyCaseConfig",
    "StudyCasesConfig",
    "clear_loader_cache",
    "list_available_cases",
    "load_all_available_bundles",
    "load_case_bundle",
    "load_study_cases_config",
    "resolve_case_dir",
}

_VALIDATION_EXPORTS = {
    "ArtifactValidationError",
    "CASE_ARTIFACT_FILENAMES",
    "MAX_PROCESSED_BYTES",
    "TreeValidationReport",
    "ValidationResult",
    "compute_processed_bytes",
    "discover_case_dirs",
    "validate_case_bundle",
    "validate_case_directory",
    "validate_processed_tree",
}


def __getattr__(name: str) -> Any:
    if name in _LOADER_EXPORTS:
        from src.data import loaders

        return getattr(loaders, name)
    if name in _VALIDATION_EXPORTS:
        from src.data import validation

        return getattr(validation, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
