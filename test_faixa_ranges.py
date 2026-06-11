#!/usr/bin/env python
"""Test script: verify faixa etária age ranges are properly submitted.

Run with: python test_faixa_ranges.py

Opens a visible browser (not headless) with slow-motion (500ms) so you can
observe each age range being filled and the 'Adicionar' button being clicked.

Log output will show:
  Faixa 1: 0-1 → adicionada (1 total)
  Faixa 2: 2-4 → adicionada (2 total)
  ...
  ✓ Faixas etárias definidas: 6/6
"""
import sys
from pathlib import Path

import sivep_core

def log_with_timestamp(msg: str):
    """Print with ISO timestamp for easy correlation."""
    from datetime import datetime
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {msg}")

if __name__ == "__main__":
    login, senha = sivep_core.load_credentials()
    if not (login and senha):
        print("ERROR: No credentials in .env. Set SIVEP_LOGIN and SIVEP_SENHA first.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("FAIXA ETÁRIA AGE RANGE SUBMISSION TEST")
    print("=" * 70)
    print(f"\nConfiguration:")
    print(f"  Units to test: US 165 (just one for speed)")
    print(f"  Browser: VISIBLE (headless=False)")
    print(f"  Speed: SLOW (slow_mo=500ms) so you can observe button clicks")
    print(f"  Log level: DETAILED (per-range logging enabled)")
    print(f"\nWatch for:")
    print(f"  - Browser opens with SIVEP portal")
    print(f"  - Form loads with age range input fields")
    print(f"  - Each range (0-1, 2-4, 5-14, 15-49, 50-64, 65+) is filled")
    print(f"  - Adicionar button clicks for each range")
    print(f"  - Log shows 'Faixa 1:', 'Faixa 2:', ... 'Faixa 6:'")
    print(f"\n" + "=" * 70 + "\n")

    try:
        files = sivep_core.run_faixa_etaria_sync(
            units=["165"],  # Just one unit for faster test
            headless=False,  # VISIBLE browser
            slow_mo_ms=500,  # Slow motion to observe clicks
            log=log_with_timestamp,
        )
        print("\n" + "=" * 70)
        print("✅ TEST PASSED: All age ranges submitted successfully")
        print("=" * 70)
        for f in files:
            print(f"  Downloaded: {f}")
    except Exception as e:
        print("\n" + "=" * 70)
        print(f"❌ TEST FAILED: {e}")
        print("=" * 70)
        import traceback
        traceback.print_exc()
        sys.exit(1)
