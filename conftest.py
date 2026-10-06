"""
Root-level conftest.py for Smart Query Router.

Inserts backend/ into sys.path so that rom app.xxx import statements
in backend/app/ and backend/tests/ resolve correctly when pytest is run
from the project root (matching pyproject.toml testpaths = ['backend/tests']).
"""
import sys
import os

# Add the backend/ directory to sys.path so import app resolves correctly
backend_dir = os.path.join(os.path.dirname(__file__), "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
