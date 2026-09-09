#!/usr/bin/env python3
"""The two-function test harness both suites share.

No pytest: the bundle has no runtime dependency but pyyaml, and a test suite that needs a package
the tool does not is a suite that stops being run. Extracted because the second suite copied it,
and a counter duplicated is a counter that drifts — one file reporting `0 failed` while the other's
copy has been edited is exactly the silent-green shape these suites exist to catch.
"""
from __future__ import annotations

import sys

FAILURES: list[str] = []
PASSES = 0


def check(name: str, got, want) -> None:
    """One assertion. Records rather than raises, so a suite reports every failure in one run."""
    global PASSES
    if got == want:
        PASSES += 1
    else:
        FAILURES.append(f"{name}\n      expected {want!r}\n      got      {got!r}")


def run(namespace: dict) -> int:
    """Run every `test_*` in `namespace`, in name order, and report."""
    for name, fn in sorted(namespace.items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except Exception as exc:  # a broken test is a failure, not a skip
            FAILURES.append(f"{name} raised {type(exc).__name__}: {exc}")
    print(f"{PASSES} assertions passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAIL  {f}")
    return 1 if FAILURES else 0


def main(namespace: dict) -> None:
    sys.exit(run(namespace))
