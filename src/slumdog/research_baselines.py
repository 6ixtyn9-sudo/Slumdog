"""Milestone 6B — research baselines entry point and aliases."""

from .baseline_analyzer import (
    CANONICAL_CONFIG_SHA256,
    FROZEN_CONFIG_PATH,
    BaselineIntegrityError,
    Pass1Result,
    compute_config_sha256,
    main,
    run_baseline_analysis,
    run_pass1,
    run_pass2,
    verify_frozen_config,
)

__all__ = [
    "CANONICAL_CONFIG_SHA256",
    "FROZEN_CONFIG_PATH",
    "BaselineIntegrityError",
    "Pass1Result",
    "compute_config_sha256",
    "main",
    "run_baseline_analysis",
    "run_pass1",
    "run_pass2",
    "verify_frozen_config",
]

if __name__ == "__main__":
    import sys

    sys.exit(main())
