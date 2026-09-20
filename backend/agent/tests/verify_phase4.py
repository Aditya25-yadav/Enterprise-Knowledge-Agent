"""
Phase 4 Verification Script (Milestone 1 — Autonomous Agent & Semantic Tool).

Executable directly from inside tests/ or from project root.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from scripts.verify_phase4 import main

if __name__ == "__main__":
    main()
