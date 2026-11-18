"""Parse, convert, export, or check a net: python -m petrinet NET.txt --help."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import agentnet, tina
from .dot import GraphvizError, render_svg, to_agent_dot, to_dot
from .net import PetriNet, PetriNetError
from .parser import ParseError, load


def _prune_pipeline(net: PetriNet, args, labels, agent_final_clauses=None):
    """Analyze after the original exports exist, then save the pruned net."""
    report_path = Path(f"{args.pipeline}-results.json")
    report = {
        "status": "checking", "input": args.net,
        "before": {"places": len(net.places), "transitions": len(net.transitions), "arcs": len(net.arcs)},
        "dead_transitions": None, "removed_transitions": None,
        "completion": None,
        "agent_completion": None,
        "deadlocks": None,
        "properties": {},
    }

    def write_report():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    write_report()
    try:
        dead = tina.dead_transitions(net, args.tina, args.timeout)
        report["dead_transitions"] = [t.name for t in sorted(dead)]
        verification = tina.verify_properties(net, args.tina, args.timeout,
                                              agent_final_clauses=agent_final_clauses)
        report.update(verification)
        pruned = net.without_transitions(dead)
        report["after"] = {"places": len(pruned.places), "transitions": len(pruned.transitions), "arcs": len(pruned.arcs)}
        pruned_dot = to_dot(pruned, transition_labels=labels)
        # Finish rendering before replacing any previous pruned artifacts.
        pruned_svg = render_svg(pruned_dot, graphviz_bin=args.graphviz)
        files = {
            f"{args.pipeline}-pruned.net": tina.to_tina(pruned),
            f"{args.pipeline}-pruned.dot": pruned_dot,
            f"{args.pipeline}-pruned.svg": pruned_svg,
        }
        for destination, content in files.items():
            Path(destination).write_text(content, encoding="utf-8")
        report["status"] = "complete"
        report["removed_transitions"] = report["dead_transitions"]
        report["pruned_files"] = list(files)
        write_report()
        return dead, report
    except (OSError, PetriNetError, tina.TinaError, GraphvizError) as e:
        report["status"] = "failed"
        report["error"] = str(e)
        write_report()
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="petrinet", description="Find dead transitions of a Petri net using bundled Tina.")
    ap.add_argument("net", help="net in the plain-text format (see README)")
    ap.add_argument("--agentnet", action="store_true", help="read and convert the multi-agent format")
    ap.add_argument("--emit", "--emit-tina", dest="emit", metavar="FILE", help="write the Tina .net translation to FILE ('-' for stdout) and exit")
    ap.add_argument("--dot", metavar="FILE", help="write the standard net as Graphviz DOT and exit ('-' for stdout)")
    ap.add_argument("--raw-dot", metavar="FILE", help="write raw agent nets as DOT; requires --agentnet")
    ap.add_argument("--pipeline", metavar="PREFIX", help="draw original nets, check dead transitions, deadlocks and CTL properties, then report removals and draw the pruned net")
    ap.add_argument("--raw-render", metavar="PREFIX", help="parse and draw only the agent model as DOT and SVG; requires --agentnet")
    ap.add_argument("--render", metavar="PREFIX", help="export Tina .net, DOT, and SVG files using PREFIX; also raw-agent diagrams with --agentnet")
    ap.add_argument("--graphviz", default="dot", metavar="PATH", help="Graphviz dot executable for --render (default: dot)")
    ap.add_argument("--tina", metavar="PATH", help="override the automatically selected Tina executable")
    ap.add_argument("--timeout", type=float, default=None, help="maximum seconds for each dead-transition or combined property check")
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

    pipeline_check_started = False
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

        pipeline_check_started = args.pipeline is not None
        if args.pipeline is not None:
            dead, verification = _prune_pipeline(
                net, args, labels, None if result is None else result.agent_final_clauses,
            )
        else:
            dead = tina.dead_transitions(net, args.tina, args.timeout)
    except (OSError, ParseError, agentnet.ParseError, agentnet.ConversionError, PetriNetError,
            tina.TinaError, GraphvizError) as e:
        if args.pipeline is not None and not pipeline_check_started:
            # A failed rerun must not leave the previous run's success report
            # claiming that the old pruned artifacts belong to this input.
            report_path = Path(f"{args.pipeline}-results.json")
            if report_path.is_file():
                try:
                    report_path.write_text(json.dumps({
                        "status": "failed", "input": args.net, "error": str(e),
                        "dead_transitions": None, "removed_transitions": None,
                    }, indent=2) + "\n", encoding="utf-8")
                except OSError:
                    pass  # Preserve the original error if the report is unwritable.
        print(f"error: {e}", file=sys.stderr)
        return 2

    if dead:
        print("Dead transitions:")
        for t in dead:
            print(f"  {t.name}")
    else:
        print("No dead transitions.")
    if args.pipeline is not None:
        deadlocks = verification["deadlocks"]
        print("Reachable deadlock: " + ("yes" if deadlocks["reachable"] else "no"))
        print("Reachable non-final deadlock: " + ("yes" if deadlocks["non_final_reachable"] else "no"))
        completion = verification["completion"]
        if completion is None:
            print("Completion not checked: no final declarations.")
        else:
            print("Option to complete from every reachable marking: " + ("yes" if completion["option_to_complete"] else "no"))
            print("All traces eventually reach a final marking: " + ("yes" if completion["all_traces_complete"] else "no"))
        for name, local in (verification["agent_completion"] or {}).items():
            if local is None:
                print(f"Agent {name}: completion not checked: no explicit final declarations.")
            else:
                print(f"Agent {name}: can eventually complete: " + ("yes" if local["can_complete"] else "no"))
                print(f"Agent {name}: always completes: " + ("yes" if local["all_traces_complete"] else "no"))
        for name, holds in verification["properties"].items():
            print(f"CTL {name}: " + ("yes" if holds else "no"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
