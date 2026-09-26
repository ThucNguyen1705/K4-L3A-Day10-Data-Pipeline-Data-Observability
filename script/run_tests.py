"""One-click test run: `python script/run_tests.py` (pytest + coverage gate at 80%)."""

from __future__ import annotations

import sys

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main(["--cov", "--cov-report=term", "--cov-fail-under=80", *sys.argv[1:]]))
