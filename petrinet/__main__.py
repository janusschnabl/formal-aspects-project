"""CLI: python -m petrinet NET.txt [--emit OUT.lola] [--builtin] [--lola PATH]"""

from __future__ import annotations

import argparse
import sys

from . import explore, lola
from .parser import ParseError, load


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="petrinet", description="Find dead transitions of a Petri net using LoLA.")
    ap.add_argument("net", help="net in the plain-text format (see README)")
    ap.add_argument("--emit", metavar="FILE", help="write the LoLA translation to FILE ('-' for stdout) and exit")
    ap.add_argument("--builtin", action="store_true", help="use the built-in explorer instead of LoLA")
    ap.add_argument("--lola", default="lola", metavar="PATH", help="LoLA executable (default: lola)")
    ap.add_argument("--timeout", type=float, default=None, help="per-query LoLA timeout in seconds")
    args = ap.parse_args(argv)

    try:
        net = load(args.net)
    except (OSError, ParseError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.emit:
        text = lola.to_lola(net)
        if args.emit == "-":
            sys.stdout.write(text)
        else:
            with open(args.emit, "w", encoding="utf-8") as f:
                f.write(text)
        return 0

    try:
        dead = explore.dead_transitions(net) if args.builtin else lola.dead_transitions(net, args.lola, args.timeout)
    except (lola.LolaError, explore.StateSpaceTooLarge) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if dead:
        print("Dead transitions:")
        for t in dead:
            print(f"  {t.name}")
    else:
        print("No dead transitions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
