"""CLI — import the Freelance Stack CSV into Supabase.

Usage :
    python scripts/import_csv.py data/deals.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_manager import import_csv_to_supabase  # noqa: E402


def main() -> int:
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Import partner CSV -> Supabase")
    p.add_argument("csv_path", help="Chemin vers le CSV Freelance Stack")
    args = p.parse_args()

    n = import_csv_to_supabase(args.csv_path)
    print(f"OK — {n} deals importés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
