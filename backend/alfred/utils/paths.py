"""
Path-jail utility.

Every file operation in ALFRED resolves the realpath of the target and
asserts it lives inside the allowed root before proceeding.  This single
function is the enforcement point for C2 rule 2 (filesystem sandboxing).
"""

from __future__ import annotations

import os
from pathlib import Path


class PathJailError(Exception):
    """Raised when a resolved path escapes the allowed root."""


def assert_within(root: str | Path, target: str | Path) -> Path:
    """
    Resolve *target* to an absolute real path and assert it is inside *root*.

    Returns the resolved Path on success; raises PathJailError otherwise.

    Why realpath and not just startswith?  Symlinks and ``..`` components can
    bypass a naive prefix check.  os.path.realpath follows every symlink so the
    comparison is always against the canonical filesystem path.
    """
    resolved_root = Path(os.path.realpath(root))
    resolved_target = Path(os.path.realpath(target))

    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        raise PathJailError(
            f"Access denied: '{resolved_target}' is outside the allowed root "
            f"'{resolved_root}'. Only paths inside the experiment folder may be "
            f"read or written during a run."
        )

    return resolved_target


def safe_mkdir(root: str | Path, relative: str | Path) -> Path:
    """
    Create *relative* directory inside *root* after jail-checking.
    Returns the created Path.
    """
    target = Path(root) / relative
    checked = assert_within(root, target)
    checked.mkdir(parents=True, exist_ok=True)
    return checked