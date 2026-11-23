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


def _print_pipeline_report(report, *, agent_model: bool, verbose: bool = False):
    """Group completed results and explain each failed soundness requirement."""
    def section(title):
        print(title)
        print("-" * len(title))

    def check(label, holds, indent="  "):
        print(f"{indent}{label}: " + ("yes" if holds else "no"))

    section("Global verification")
    dead = report["dead_transitions"]
    label = "Dead converted transitions" if agent_model else "Dead transitions"
    print(f"  {label}: {len(dead)}")
    for name in dead:
        print(f"    {name}")
    deadlocks = report["deadlocks"]
    check("Reachable deadlock", deadlocks["reachable"])
    check("Reachable non-final deadlock", deadlocks["non_final_reachable"])
    completion = report["completion"]
    if completion is None:
        print("  Completion not checked: no final declarations.")
    else:
        check("Option to complete from every reachable marking", completion["option_to_complete"])
        check("All traces eventually reach a final marking", completion["all_traces_complete"])

    if agent_model:
        print()
        section("Agent verification")
        local_completion = report["agent_completion"] or {}
        local_deadlocks = report["agent_deadlocks"] or {}
        names = sorted(local_completion.keys() | local_deadlocks.keys())
        if not names:
            print("  No agent types.")
        for index, name in enumerate(names):
            if index:
                print()
            print(f"  {name}")
            local = local_completion.get(name)
            if local is None:
                print("    Completion not checked: no explicit final declarations.")
            else:
                check("Initial marking is locally final", local["initially_final"], "    ")
                check("Can complete from initial marking", local["can_complete"], "    ")
                check("Option to complete from every reachable marking", local["option_to_complete"], "    ")
                check("All traces reach a local final", local["all_traces_complete"], "    ")
            if name in local_deadlocks:
                check("Reachable deadlock", local_deadlocks[name], "    ")

    if report["properties"]:
        print()
        section("CTL properties")
        for name, holds in report["properties"].items():
            check(name, holds)

    if report.get("traces") and not verbose:
        print()
        print(f"Counterexamples: {len(report['traces'])} (use --verbose to show; full traces are saved in JSON).")

    if report.get("traces") and verbose:
        print()
        section("Counterexamples")

        def steps_text(steps):
            names = []
            for step in steps:
                name = step.get("activity", step["transition"])
                if step.get("weights"):
                    name += " (" + ", ".join(f"{key}={value}" for key, value in step["weights"].items()) + ")"
                names.append(name)
            return " -> ".join(names) or "(initial marking)"

        for trace in report["traces"]:
            scope = trace["section"] + (f".{trace['agent']}" if "agent" in trace else "")
            print(f"  {scope}.{trace['property']}")
            if trace["kind"] != "unreachable":
                print(f"    Run: {steps_text(trace['prefix'])}")
                if trace["cycle"]:
                    print(f"    Repeat forever: {steps_text(trace['cycle'])}")
                elif trace["kind"] == "deadlock":
                    print("    Ends in a deadlock.")
            marking = ", ".join(f"{p}={n}" for p, n in trace["marking"].items()) or "(all places empty)"
            print(f"    Marking: {marking}")
            print(f"    {trace['reason']}")

    if agent_model:
        print()
        section("Soundness")
        if report["soundness"] is None:
            print("  Soundness not checked: no global final declarations.")
        else:
            check("Soundness", report["soundness"])

        def requirement(label, holds):
            print(f"  [{'PASS' if holds else 'FAIL'}] {label}")

        if completion is None:
            print("  [SKIP] Global option to complete: no global final declarations.")
        else:
            requirement("Global option to complete", completion["option_to_complete"])
            if not completion["option_to_complete"]:
                print("    A reachable marking has no path to a global final.")
        blocked = sorted(name for name, reachable in local_deadlocks.items() if reachable)
        requirement("No per-type deadlocks", not blocked)
        for name in blocked:
            print(f"    Deadlocked type: {name}")
        requirement("No dead source activities", not report["dead_activities"])
        for activity in report["dead_activities"]:
            members = ", ".join(f"{m['agent']}.{m['transition']}" for m in activity["members"])
            print(f"    Dead source activity: {activity['name']} ({members})")


def _prune_pipeline(net: PetriNet, args, labels, conversion: agentnet.ConversionResult | None = None,
                    place_groups=None):
    """Analyze after the original exports exist, then save the pruned net."""
    report_path = Path(f"{args.pipeline}-results.json")
    report = {
        "status": "checking", "input": args.net,
        "before": {"places": len(net.places), "transitions": len(net.transitions), "arcs": len(net.arcs)},
        "dead_transitions": None, "removed_transitions": None,
        "dead_activities": None, "soundness": None,
        "completion": None,
        "agent_completion": None,
        "agent_deadlocks": None,
        "deadlocks": None,
        "properties": {},
        "traces": None,
    }

    def write_report():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    write_report()
    try:
        dead = tina.dead_transitions(net, args.tina, args.timeout)
        report["dead_transitions"] = [t.name for t in sorted(dead)]
        if conversion is not None:
            report["dead_activities"] = [
                {"name": activity.name, "label": activity.label,
                 "members": [{"agent": a, "transition": t} for a, t in sorted(activity.members)]}
                for activity in conversion.dead_activities(dead)
            ]
        verification = tina.verify_properties(net, args.tina, args.timeout,
                                              agent_final_clauses=None if conversion is None else conversion.agent_final_clauses,
                                              agent_transitions=None if conversion is None else conversion.agent_transitions,
                                              include_traces=True)
        report.update(verification)
        if conversion is not None:
            origins = {t.name: origin for t, origin in conversion.transition_origins.items()}
            for trace in report.get("traces") or []:
                for step in trace["prefix"] + trace["cycle"]:
                    origin = origins[step["transition"]]
                    step["activity"] = origin.global_transition.name
                    step["weights"] = {f"{a}.{t}": k for (a, t), k in origin.multiplicities}
        if conversion is not None and verification["completion"] is not None:
            report["soundness"] = (
                verification["completion"]["option_to_complete"]
                and not any(verification["agent_deadlocks"].values())
                and not report["dead_activities"]
            )
        pruned = net.without_transitions(dead)
        report["after"] = {"places": len(pruned.places), "transitions": len(pruned.transitions), "arcs": len(pruned.arcs)}
        pruned_dot = to_dot(pruned, transition_labels=labels, place_groups=place_groups)
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
    ap.add_argument("--pipeline", metavar="PREFIX", help="draw original nets, check dead transitions, deadlocks, agent soundness and CTL properties, then draw the pruned net")
    ap.add_argument("-v", "--verbose", action="store_true", help="show full counterexample runs in pipeline output")
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

        # Color converted-net places by the agent that owns them.
        groups = None if result is None else {
            result.place_map[p]: agent.name for agent in source.agents.values() for p in agent.places
        }
        outputs = []
        if args.emit is not None:
            outputs.append((args.emit, tina.to_tina(net)))
        if args.dot is not None:
            labels = None if result is None else {t: o.display_name for t, o in result.transition_origins.items()}
            outputs.append((args.dot, to_dot(net, transition_labels=labels, place_groups=groups)))
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
            dead, verification = _prune_pipeline(net, args, labels, result, groups)
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
                        "dead_activities": None, "soundness": None,
                        "traces": None,
                    }, indent=2) + "\n", encoding="utf-8")
                except OSError:
                    pass  # Preserve the original error if the report is unwritable.
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.pipeline is not None:
        _print_pipeline_report(verification, agent_model=args.agentnet, verbose=args.verbose)
    elif dead:
        print("Dead converted transitions:" if args.agentnet else "Dead transitions:")
        for t in dead:
            print(f"  {t.name}")
    else:
        print("No dead transitions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
