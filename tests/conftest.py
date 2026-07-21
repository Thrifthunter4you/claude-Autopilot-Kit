"""Test bootstrap: point TASKLOOP_HOME at a temp dir BEFORE importing
taskloop (config resolves paths at import time), and put the kit on path."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="taskloop-test-")
os.environ["TASKLOOP_HOME"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taskloop import config  # noqa: E402

config.ensure_dirs()
