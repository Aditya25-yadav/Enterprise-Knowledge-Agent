"""
Phase 3 Verification Script (BM25 Keyword Search & Lexical Index).

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

# Forward to scripts/verify_phase3.py main
from scripts.verify_phase3 import (
    test_section_1_tokenizer,
    test_section_2_exact_identifier_search,
    test_section_3_rbac_security_isolation,
    test_section_4_disk_persistence,
    test_section_5_dual_indexing_pipeline,
    print_banner,
)

if __name__ == "__main__":
    test_section_1_tokenizer()
    test_section_2_exact_identifier_search()
    test_section_3_rbac_security_isolation()
    test_section_4_disk_persistence()
    test_section_5_dual_indexing_pipeline()
    print_banner("ALL PHASE 3 BM25 KEYWORD SEARCH VERIFICATIONS PASSED! 🎉")
