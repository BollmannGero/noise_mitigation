#!/usr/bin/env python3
"""Print the CSM0/CSM1 ROOT files belonging to one input file."""

import sys

from create_analysis import resolve_root_files


def main():
    if len(sys.argv) != 2:
        print("Usage: python select_root_pair.py <path_to_root_file>")
        print("Example: python select_root_pair.py ./data/CSM0_run123.root")
        sys.exit(1)

    input_path = sys.argv[1]

    try:
        files = resolve_root_files(input_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Found files:")
    for idx, file_path in enumerate(files, start=1):
        print(f"  {idx}. {file_path}")

    print(f"\nUsing {len(files)} file(s) for analysis.")


if __name__ == "__main__":
    main()
