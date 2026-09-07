#!/usr/bin/env python
"""Build/check/explain the current-source critical path index."""
import argparse
from pathlib import Path
from architecture_compiler import compile_index, render, check_outputs, write_outputs, encoded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['build', 'check', 'explain'])
    parser.add_argument('symbol', nargs='?')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    index = compile_index(root)
    outputs = render(index)
    directory = root / 'architecture/generated'
    if not directory.resolve().is_relative_to(root):
        parser.exit(1, 'ARCHITECTURE_OUTPUT_PATH_ESCAPE\n')
    if any((directory / name).is_symlink() for name in outputs):
        parser.exit(1, 'ARCHITECTURE_OUTPUT_SYMLINK\n')
    if args.command == 'build':
        write_outputs(directory, outputs)
    elif args.command == 'check':
        stale = check_outputs(directory, outputs)
        if stale:
            parser.exit(1, 'ARCHITECTURE_GENERATED_DRIFT: ' + ', '.join(stale) + '\n')
    else:
        matches = [e for e in index['observed']['edges'] if args.symbol and args.symbol in e['source']]
        if not matches:
            parser.exit(1, 'ARCHITECTURE_SYMBOL_NOT_FOUND\n')
        print(encoded(matches))
    print(index['source_input_digest'])


if __name__ == '__main__':
    main()
