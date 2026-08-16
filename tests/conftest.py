"""
Test configuration.

config.Settings requires the Bridge credentials at import time, so placeholders
are installed before any test module imports the application. Nothing in the
test suite opens a connection, so these are never used to authenticate.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("PROTON_BRIDGE_USER", "test@proton.me")
os.environ.setdefault("PROTON_BRIDGE_PASSWORD", "test-bridge-password")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
