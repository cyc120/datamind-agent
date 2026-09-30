"""Command-line entry point for module one."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datamind.sql_engine import ask, json_output


def main():
    parser = argparse.ArgumentParser(description="Ask a read-only question of a structured data source")
    parser.add_argument("question")
    parser.add_argument("--source", choices=["demo_retail", "chinook"])
    args = parser.parse_args()
    result = ask(args.question, args.source)
    print(json_output(result))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
