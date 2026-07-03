import os
import sys

# The script/ modules import each other with flat imports (e.g. `from fetch import ...`),
# so script/ must be on sys.path for tests to import them the same way main.py does.
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "script")
sys.path.insert(0, os.path.abspath(SCRIPT_DIR))
