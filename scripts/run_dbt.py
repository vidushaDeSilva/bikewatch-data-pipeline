import os
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "dbt_bikewatch"


def main():
    load_dotenv(ROOT / ".env")

    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: python3 scripts/run_dbt.py <dbt command>"
        )

    os.execvp(
        "dbt",
        [
            "dbt",
            *sys.argv[1:],
            "--project-dir",
            str(PROJECT),
            "--profiles-dir",
            str(PROJECT),
        ],
    )


if __name__ == "__main__":
    main()