"""Parse, convert, export, or check a net: python -m petrinet NET.txt --help."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from . import agentnet, tina
from .dot import GraphvizError, render_svg, to_agent_dot, to_dot
from .net import PetriNetError
from .parser import ParseError, load


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="petrinet", description="Find dead transitions of a Petri net using bundled Tina.")
    ap.add_argument("net", help="net in the plain-text format (see README)")
    ap.add_argument("--agentnet", action="store_true", help="read and convert the multi-agent format")
    ap.add_argument("--emit", "--emit-tina", dest="emit", metavar="FILE", help="write the Tina .net translation to FILE ('-' for stdout) and exit")
    ap.add_argument("--dot", metavar="FILE", help="write the standard net as Graphviz DOT and exit ('-' for stdout)")
    ap.add_argument("--raw-dot", metavar="FILE", help="write raw agent nets as DOT; requires --agentnet")
    ap.add_argument("--pipeline", metavar="PREFIX", help="render agent and standard nets, export Tina .net, then print dead transitions")
    ap.add_argument("--raw-render", metavar="PREFIX", help="parse and draw only the agent model as DOT and SVG; requires --agentnet")
    ap.add_argument("--render", metavar="PREFIX", help="export Tina .net, DOT, and SVG files using PREFIX; also raw-agent diagrams with --agentnet")
    ap.add_argument("--graphviz", default="dot", metavar="PATH", help="Graphviz dot executable for --render (default: dot)")
    ap.add_argument("--tina", metavar="PATH", help="override the automatically selected Tina executable")
    ap.add_argument("--timeout", type=float, default=None, help="maximum seconds for the Tina analysis")
    args = ap.parse_args(argv)

    if args.pipeline is not None:
        if args.render is not None or args.raw_render is not None:
            ap.error("--pipeline cannot be combined with --render or --raw-render")
        args.render = args.pipeline
    if args.raw_render is not None:
        if not args.agentnet:
            ap.error("--raw-render requires --agentnet")
        if args.render is not None or any(d is not None for d in (args.emit, args.dot, args.raw_dot)):
            ap.error("--raw-render cannot be combined with other export options")

    render_targets = []
    if args.render is not None:
        if any(d is not None for d in (args.emit, args.dot, args.raw_dot)):
            ap.error("--render cannot be combined with --emit, --emit-tina, --dot, or --raw-dot")
        args.emit = f"{args.render}.net"
        args.dot = f"{args.render}.dot"
        render_targets.append((args.dot, f"{args.render}.svg"))
        if args.agentnet:
            args.raw_dot = f"{args.render}-agent.dot"
            render_targets.append((args.raw_dot, f"{args.render}-agent.svg"))
    if args.raw_render is not None:
        args.raw_dot = f"{args.raw_render}-agent.dot"
        render_targets.append((args.raw_dot, f"{args.raw_render}-agent.svg"))
    destinations = [d for d in (args.emit, args.dot, args.raw_dot) if d is not None]
    destinations.extend(svg for _, svg in render_targets)
    if args.raw_dot is not None and not args.agentnet:
        ap.error("--raw-dot requires --agentnet")
    if destinations.count("-") > 1:
        ap.error("at most one export can use stdout ('-')")

    try:
        paths = [os.path.normcase(str(Path(d).resolve())) for d in destinations if d != "-"]
        if len(set(paths)) != len(paths):
            ap.error("export files must have distinct destinations")
        result = None
        if args.agentnet:
            source = agentnet.load(args.net)
            # Raw-only export remains useful even when the source is not well-formed.
            if args.emit is not None or args.dot is not None or not destinations:
                result = agentnet.convert(source)
                net = result.net
        else:
            net = load(args.net)

        outputs = []
        if args.emit is not None:
            outputs.append((args.emit, tina.to_tina(net)))
        if args.dot is not None:
            labels = None if result is None else {t: o.display_name for t, o in result.transition_origins.items()}
            outputs.append((args.dot, to_dot(net, transition_labels=labels)))
        if args.raw_dot is not None:
            outputs.append((args.raw_dot, to_agent_dot(source)))
        dot_outputs = dict(outputs)
        for dot_destination, svg_destination in render_targets:
            outputs.append((svg_destination, render_svg(dot_outputs[dot_destination], graphviz_bin=args.graphviz)))
        if args.render is not None or args.raw_render is not None:
            # Render everything successfully before creating or overwriting output files.
            Path(args.render or args.raw_render).parent.mkdir(parents=True, exist_ok=True)
        for destination, text in outputs:
            if destination == "-":
                sys.stdout.write(text)
            else:
                with open(destination, "w", encoding="utf-8") as f:
                    f.write(text)
        if outputs and args.pipeline is None:
            return 0

        dead = tina.dead_transitions(net, args.tina, args.timeout)
    except (OSError, ParseError, agentnet.ParseError, agentnet.ConversionError, PetriNetError,
            tina.TinaError, GraphvizError) as e:
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
