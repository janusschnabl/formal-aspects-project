"""Extract and replay evidence for built-in checks on Tina's original graph.

Muse supplies the bad-state sets. pathto supplies finite paths; ktzio supplies
unambiguous edge labels and markings, also used to construct AF counterexamples.
No new reachability search or general CTL counterexample engine is used here.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .net import Marking, PetriNet


class WitnessError(ValueError):
    pass


@dataclass(frozen=True)
class _Request:
    section: str
    property: str
    agent: str | None
    kind: str
    predicate: str
    reason: str


def _requests(groups, answers):
    answers = iter(answers)
    results = {group: {key: next(answers) for key in properties}
               for group, properties in groups.items()}
    requests = []

    def completion(group, final, agent=None):
        values = results[group]
        section = "completion" if agent is None else "agent_completion"
        description = "a global final" if agent is None else f"a local final for {agent}"
        if values.get("can_complete") is False:
            requests.append(_Request(section, "can_complete", agent, "unreachable", "",
                                     f"No execution from the initial marking reaches {description}."))
        if values["option_to_complete"] is False:
            requests.append(_Request(section, "option_to_complete", agent, "path", f"- (EF ({final}))",
                                     f"The reached marking has no path to {description}."))
        if values["all_traces_complete"] is False:
            requests.append(_Request(section, "all_traces_complete", agent, "avoid", f"EG (- ({final}))",
                                     f"This execution avoids {description} forever."))

    if groups["completion"]:
        completion("completion", "IsFinal")
    if results["deadlocks"]["non_final_reachable"]:
        requests.append(_Request("deadlocks", "non_final_reachable", None, "deadlock",
                                 "IsDeadlock /\\ (- IsFinal)",
                                 "The reached marking is a non-final deadlock."))
    for group, properties in groups.items():
        if group.startswith("agent:"):
            completion(group, properties["initially_final"], group.split(":", 1)[1])
        elif group.startswith("agent_deadlock:") and results[group]["reachable"]:
            # The existing query is EF Bad; ask Muse for Bad itself.
            predicate = properties["reachable"].removeprefix("EF ")
            agent = group.split(":", 1)[1]
            requests.append(_Request("agent_deadlocks", "reachable", agent, "path", predicate,
                                     f"{agent} is unfinished and no continuation can enable its participation."))
    return requests


def _state_set(text, limited):
    match = re.fullmatch(r"\[\s*(\d+(?:\s*,\s*\d+)*)?(\s*,\s*\.\.\.)?\s*\]", text)
    if not match or (match[2] and not limited):
        raise WitnessError("could not read Muse's witness state set")
    states = [] if match[1] is None else [int(s.strip()) for s in match[1].split(",")]
    if not states or len(states) != len(set(states)) or (limited and len(states) != 1):
        raise WitnessError("Muse did not return the expected witness states")
    return states


def _marking(marking):
    return {p.name: marking[p] for p in sorted(marking.marked_places())}


def _read_graph(path, net, identifiers):
    """Read ktzio's KTS profile, keeping only the actual net edges."""
    try:
        text = path.read_text(encoding="utf-8")
        if identifiers:
            # ktzio 4.0.0 writes Tina identifiers verbatim, without JSON escaping
            # embedded quotes/backslashes. Normalize known whole identifiers once.
            pattern = "\"(?:" + "|".join(re.escape(n) for n in sorted(identifiers, key=len, reverse=True)) + ")\""
            text = re.sub(pattern, lambda m: json.dumps(identifiers[m[0][1:-1]]), text)
        data = json.loads(text)
        state_space = ([n["nsname"] for n in data["header"]["state-nspaces"]].index("S")
                       if net.places else None)
        event_space = ([n["nsname"] for n in data["header"]["trans-nspaces"]].index("E")
                       if net.transitions else None)
        places = {p.name: p for p in net.places}
        transitions = {t.name for t in net.transitions}
        markings, successors = {}, {}
        for state in data["states"]:
            rank = state["no"]
            if type(rank) is not int or rank < 0 or rank in markings:
                raise ValueError("invalid state rank")
            tokens = {}
            for prop in (() if state_space is None else state["props"][state_space]):
                place, count = places[prop["name"]], prop["value"]
                if type(count) is not int or count < 0 or place in tokens:
                    raise ValueError("invalid marking")
                tokens[place] = count
            markings[rank] = Marking(tokens)
            successors[rank] = []
            for edge in state["trans"]:
                events = edge["props"][event_space]
                if len(events) != 1 or events[0]["value"] != 1:
                    raise ValueError("expected one transition per edge")
                name, target = events[0]["name"], edge["target"]
                if name not in transitions or type(target) is not int:
                    raise ValueError("unknown edge")
                successors[rank].append((name, target))
            successors[rank].sort()
        if (len(markings) != data["header"]["nodes"] or markings[0] != net.initial_marking
                or any(target not in markings for edges in successors.values() for _, target in edges)):
            raise ValueError("inconsistent graph")
        return markings, successors
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        raise WitnessError(f"could not read Tina's witness graph: {e}") from e


def _avoiding_run(allowed, successors, check_timeout):
    """Follow edges within EG not-final until a real deadlock or repeated state."""
    if 0 not in allowed:
        raise WitnessError("the initial state is missing from the AF counterexample set")
    visited, edges, state = {}, [], 0
    while state not in visited:
        check_timeout()
        visited[state] = len(edges)
        choices = [edge for edge in successors[state] if edge[1] in allowed]
        if not choices:
            if successors[state]:
                raise WitnessError("Muse's AF counterexample set has no continuing edge")
            return edges, [], "deadlock"
        edge = choices[0]
        edges.append(edge)
        state = edge[1]
    split = visited[state]
    return edges[:split], edges[split:], "cycle"


def _replay(net, graph_markings, edges, start, check_timeout):
    transitions = {t.name: t for t in net.transitions}
    marking, steps = start, []
    for name, target in edges:
        check_timeout()
        transition = transitions[name]
        if not net.is_enabled(marking, transition):
            raise WitnessError(f"witness transition {name!r} is not enabled")
        marking = net.fire(marking, transition)
        if marking != graph_markings[target]:
            raise WitnessError("witness replay disagrees with Tina's marking")
        steps.append({"transition": name, "marking": _marking(marking)})
    return steps, marking


def collect(net, groups, answers, *, graph, formulas, prelude, run, tool, identifier, check_timeout):
    """Collect evidence while the property graph and shared time budget exist."""
    requests = _requests(groups, answers)
    sets = {}
    for limited in (True, False):
        predicates = list(dict.fromkeys(r.predicate for r in requests
                                       if r.kind != "unreachable" and (r.kind != "avoid") == limited))
        if not predicates:
            continue
        formulas.write_text(prelude + ";\n".join(predicates) + ";\n", encoding="utf-8")
        output = run([str(tool("muse")), "-q", "-s", "-limit", "1" if limited else "0",
                      str(graph), str(formulas)]).splitlines()
        if len(output) != len(predicates):
            raise WitnessError("could not read Muse's witness results")
        sets.update((predicate, _state_set(line, limited)) for predicate, line in zip(predicates, output))

    markings, successors = {}, {}
    if sets:
        export = graph.with_suffix(".ktj")
        run([str(tool("ktzio")), "-ktj", str(graph), str(export)])
        check_timeout()
        identifiers = {identifier(node.name): node.name for node in (*net.places, *net.transitions)}
        markings, successors = _read_graph(export, net, identifiers)
        check_timeout()
        if any(state not in markings for states in sets.values() for state in states):
            raise WitnessError("Muse returned an unknown witness state")

    paths, evidence = {}, []
    for request in requests:
        check_timeout()
        prefix, cycle, kind = [], [], request.kind
        if kind == "avoid":
            prefix, cycle, kind = _avoiding_run(set(sets[request.predicate]), successors, check_timeout)
        elif kind != "unreachable":
            target = sets[request.predicate][0]
            if target not in paths:
                output = run([str(tool("pathto")), str(target), "-s", str(graph)]).strip()
                if not output and target == 0:
                    output = "0"  # pathto prints nothing for the empty initial-state path.
                if not re.fullmatch(r"\d+(?:\s+\d+)*", output):
                    raise WitnessError("could not read Tina's witness path")
                states = [int(s) for s in output.split()]
                if states[0] != 0 or states[-1] != target:
                    raise WitnessError("Tina's witness path has incorrect endpoints")
                path = []
                for before, after in zip(states, states[1:]):
                    check_timeout()
                    choices = [edge for edge in successors.get(before, ()) if edge[1] == after]
                    if not choices:
                        raise WitnessError("Tina's witness path contains an unknown edge")
                    path.append(choices[0])
                paths[target] = path
            prefix = paths[target]

        prefix_steps, marking = _replay(net, markings, prefix, net.initial_marking, check_timeout)
        cycle_steps, after_cycle = _replay(net, markings, cycle, marking, check_timeout)
        if cycle and after_cycle != marking:
            raise WitnessError("witness cycle does not return to its starting marking")
        if kind == "deadlock" and net.enabled_transitions(marking):
            raise WitnessError("witness endpoint is not a deadlock")
        trace = {"section": request.section, "property": request.property, "kind": kind,
                 "reason": request.reason, "initial_marking": _marking(net.initial_marking),
                 "prefix": prefix_steps, "marking": _marking(marking), "cycle": cycle_steps}
        if request.agent is not None:
            trace["agent"] = request.agent
        evidence.append(trace)
        check_timeout()
    return evidence
