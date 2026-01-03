"""Vercel entrypoint for the LifeLink Flask app.

Vercel's Python runtime executes functions from files under /api by default.
We add the project root to sys.path and then import the Flask `app` instance.
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app  # noqa: E402
