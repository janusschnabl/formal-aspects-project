"""Independent firing rules and real command-line artifact verification.

The reference uses raw source fields, not converter/model firing helpers.
Small, explicit fixtures allow exhaustive checking of every conserved marking.
"""

from collections import deque
from itertools import product
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

from petrinet import Marking, Place
from petrinet import agentnet, tina
from tests.test_conversion import INDEXED, PAIRS


ROOT = Path(__file__).resolve().parent.parent
MIXED = """agent: A
 places: p q r s
 transitions: move reset dead
 labels: move=sync
 arcs: (p, move, *) (move, s, *) (q, move) (move, r)
       (s, reset, *) (reset, p, *) (r, reset) (reset, q)
       (r, dead, 4) (dead, q, 4)
agent: B
 places: open away
 transitions: serve pause resume
 labels: serve=sync
 read: (open, serve)
 arcs: (open, pause) (pause, away) (away, resume) (resume, open)
initial: p=2 q=1 open=1
final: p=2 q=1 open=1
"""
READ_OUTPUT = """agent: A
 places: p q
 transitions: t
 arcs: (q, t) (t, p)
 read: (p, t)
initial: p=1 q=1
final: p=2
"""
WEIGHTED = """agent: A
 places: p q
 transitions: batch back
 arcs: (p, batch, 2) (batch, q, 2) (q, back) (back, p)
initial: p=2
final: q=2
"""
THREE_WAY = PAIRS.replace("initial: p=3 r=2", """agent: C
 places: gate asleep
 transitions: watch wake
 labels: watch=go
 read: (gate, watch)
 arcs: (asleep, wake) (wake, gate)
initial: p=3 r=2 asleep=1""")


def reference_steps(source, tokens):
    """Literal source rule: group labels, choose current-token weights, fire atomically."""
    groups = {}
    for a, local in source.agents.items():
        for t in local.transitions:
            label = local.labels.get(t)
            key = ("label", label) if label is not None else ("silent", a, t)
            groups.setdefault(key, []).append((a, t))
    for members in groups.values():
        members = tuple(sorted(members))
        variable = []
        for a, t in members:
            for (p, target), weight in source.agents[a].arcs.items():
                if target == t and weight == "*":
                    variable.append(((a, t), range(1, tokens[p] + 1)))
        for weights in product(*(domain for _, domain in variable)):
            choices = dict(zip((key for key, _ in variable), weights))
            after = dict(tokens)
            enabled = True
            for a, t in members:
                local = source.agents[a]
                for p, target in local.read_arcs:
                    if target == t and tokens[p] == 0:
                        enabled = False
                for (src, dst), weight in local.arcs.items():
                    if src != t and dst != t:
                        continue
                    amount = choices[(a, t)] if weight == "*" else weight
                    if dst == t:
                        enabled &= tokens[src] >= amount
                        after[src] -= amount
                    else:
                        after[dst] += amount
            if enabled:
                yield members, tuple(sorted(choices.items())), after


def conserved_markings(source):
    local_markings = []
    for local in source.agents.values():
        population = sum(source.initial.get(p, 0) for p in local.places)
        local_markings.append([
            dict(zip(local.places, counts))
            for counts in product(range(population + 1), repeat=len(local.places))
            if sum(counts) == population
        ])
    for parts in product(*local_markings):
        yield {p: count for part in parts for p, count in part.items()}


def reference_graph(source):
    names = sorted(source.places)
    initial = tuple(source.initial.get(p, 0) for p in names)
    seen, pending, edges, fired = {initial}, deque([initial]), set(), set()
    while pending:
        before = pending.popleft()
        for members, choices, tokens in reference_steps(source, dict(zip(names, before))):
            after = tuple(tokens[p] for p in names)
            edges.add((before, members, choices, after))
            fired.add((members, choices))
            if after not in seen:
                seen.add(after)
                pending.append(after)
    return seen, edges, fired


class ExhaustiveConstructionTests(unittest.TestCase):
    def test_every_conserved_marking_matches_independent_source_rule(self):
        cases = [("pairs", PAIRS), ("indices", INDEXED), ("read-output", READ_OUTPUT),
                 ("weighted", WEIGHTED), ("three-way", THREE_WAY), ("empty", ""),
                 ("shop", (ROOT / "examples/agents/shop.txt").read_text())]
        for population, clerk in product(range(4), range(2)):
            cases.append((f"mixed-p{population}-clerk{clerk}",
                          MIXED.replace("p=2 q=1 open=1", f"p={population} q=1 open={clerk}")))
        for name, text in cases:
            source = agentnet.parse(text)
            result = agentnet.convert(source)
            names = sorted(source.places)
            for tokens in conserved_markings(source):
                with self.subTest(case=name, marking=tokens):
                    expected = {(members, choices, tuple(after[p] for p in names))
                                for members, choices, after in reference_steps(source, tokens)}
                    marking = Marking({Place(p): n for p, n in tokens.items()})
                    actual = set()
                    for t in result.net.enabled_transitions(marking):
                        origin = result.transition_origins[t]
                        after = result.net.fire(marking, t)
                        actual.add((tuple(sorted(origin.global_transition.members)), origin.multiplicities,
                                    tuple(after[Place(p)] for p in names)))
                    self.assertEqual(actual, expected)
                    self.assertEqual(result.net.is_final(marking),
                                     any(all(tokens[p] == final.get(p, 0) for p in names) for final in source.finals))


def tool_paths():
    executable = Path(tina.resolve_executable()).resolve()
    suffix = ".exe" if sys.platform == "win32" else ""
    dot = Path(shutil.which("dot") or executable.with_name("dot" + suffix))
    ndrio = executable.with_name("ndrio" + suffix)
    for path in [dot, ndrio]:
        if not path.is_file():
            raise RuntimeError(f"Required integration-test tool missing: {path}")
        if sys.platform == "linux" and not os.access(path, os.X_OK):
            path.chmod(path.stat().st_mode | 0o100)
    return executable, dot, ndrio


def read_export_with_ndrio(path, binary):
    """Let upstream Tina parse our file; independently read its PNML representation."""
    pnml = path.with_suffix(".pnml")
    proc = subprocess.run([str(binary), str(path), str(pnml)], capture_output=True, text=True, timeout=20)
    if proc.returncode:
        raise AssertionError(proc.stderr)
    root = ET.parse(pnml).getroot()
    ns = {"n": "http://www.pnml.org/version-2009/grammar/pnml"}
    nodes, places, transitions, initial = {}, set(), set(), {}
    for kind, target in [("place", places), ("transition", transitions)]:
        for node in root.findall(f".//n:{kind}", ns):
            name = node.findtext("n:name/n:text", namespaces=ns)
            nodes[node.attrib["id"]] = name
            target.add(name)
            if kind == "place":
                initial[name] = int(node.findtext("n:initialMarking/n:text", default="0", namespaces=ns))
    arcs = {(nodes[a.attrib["source"]], nodes[a.attrib["target"]],
             int(a.findtext("n:inscription/n:text", default="1", namespaces=ns)))
            for a in root.findall(".//n:arc", ns)}
    return places, transitions, initial, arcs


def exported_graph(model):
    """Explore upstream-parsed arcs directly, without PetriNet firing methods."""
    places, transitions, initial, arcs = model
    names = sorted(places)
    start = tuple(initial[p] for p in names)
    seen, pending, edges = {start}, deque([start]), set()
    while pending:
        before = pending.popleft()
        tokens = dict(zip(names, before))
        for t in transitions:
            pre = [(p, w) for p, target, w in arcs if target == t]
            if not all(tokens[p] >= w for p, w in pre):
                continue
            after = dict(tokens)
            for p, w in pre:
                after[p] -= w
            for src, p, w in arcs:
                if src == t:
                    after[p] += w
            target = tuple(after[p] for p in names)
            edges.add((before, t, target))
            if target not in seen:
                seen.add(target)
                pending.append(target)
        if len(seen) > 1000:
            raise AssertionError("Unexpected state growth in small bounded fixture")
    return seen, edges


class RealPipelineAuditTests(unittest.TestCase):
    def test_real_failures_never_produce_a_successful_removal_report(self):
        _, dot, _ = tool_paths()
        cases = [
            ("unbounded", "places: p\ntransitions: source\narcs: (source, p)\n", []),
            ("overflow", "places: p\ntransitions: t\ninitial: p=2147483648\narcs: (p, t) (t, p)\n", []),
            ("missing-tool", "places: p\ntransitions: t\narcs: (p, t)\n", ["--tina", "missing-tina-executable"]),
        ]
        for name, text, extra in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "source.txt"
                source.write_text(text, encoding="utf-8")
                prefix = Path(tmp) / "model"
                proc = subprocess.run([sys.executable, "-m", "petrinet", str(source), "--pipeline", str(prefix),
                                       "--graphviz", str(dot), "--timeout", "20", *extra],
                                      cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(proc.stdout, "")
                self.assertTrue(Path(f"{prefix}.svg").is_file())
                self.assertFalse(Path(f"{prefix}-pruned.net").exists())
                report = json.loads(Path(f"{prefix}-results.json").read_text())
                self.assertEqual(report["status"], "failed")
                self.assertIsNone(report["removed_transitions"])
                self.assertTrue(report["error"])

    def test_agent_pipeline_artifacts_preserve_complete_reachable_behavior(self):
        binary, dot, ndrio = tool_paths()
        cases = [("mixed", MIXED), ("read-output", READ_OUTPUT), ("indices", INDEXED), ("pairs", PAIRS),
                 ("weighted", WEIGHTED), ("three-way", THREE_WAY), ("empty", ""),
                 ("zero-population", PAIRS.replace("p=3 r=2", "p=0 r=0")),
                 ("blocked", MIXED.replace("open=1", "open=0")),
                 ("shop", (ROOT / "examples/agents/shop.txt").read_text())]
        for name, text in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory(prefix="pipeline audit ") as tmp:
                source_path = Path(tmp) / "source.txt"
                source_path.write_text(text, encoding="utf-8")
                prefix = Path(tmp) / "output folder" / name
                source = agentnet.parse(text)
                construction = agentnet.convert(source)
                states, source_edges, fired = reference_graph(source)
                expected_dead = sorted(t.name for t, o in construction.transition_origins.items()
                                       if (tuple(sorted(o.global_transition.members)), o.multiplicities) not in fired)
                if name == "mixed":
                    self.assertEqual(expected_dead, ["dead", "reset__k_A_3", "sync__k_A_3"])
                proc = subprocess.run([sys.executable, "-m", "petrinet", str(source_path), "--agentnet",
                                       "--pipeline", str(prefix), "--graphviz", str(dot), "--timeout", "20"],
                                      cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(source_path.read_text(encoding="utf-8"), text)
                report = json.loads(Path(f"{prefix}-results.json").read_text())
                self.assertEqual(report["status"], "complete")
                self.assertEqual(report["removed_transitions"], expected_dead)
                self.assertEqual(report["dead_transitions"], expected_dead)
                expected_stdout = ("Dead transitions:\n" + "".join(f"  {t}\n" for t in expected_dead)
                                   if expected_dead else "No dead transitions.\n")
                self.assertTrue(proc.stdout.startswith(expected_stdout), proc.stdout)
                if source.finals or source.unrestricted_finals:
                    completion = report["completion"]
                    self.assertEqual(set(completion), {"option_to_complete", "all_traces_complete"})
                    self.assertTrue(all(isinstance(value, bool) for value in completion.values()))
                    self.assertIn("Option to complete from every reachable marking:", proc.stdout)
                    self.assertIn("All traces eventually reach a final marking:", proc.stdout)
                else:
                    self.assertIsNone(report["completion"])
                    self.assertIn("Completion not checked: no final declarations.", proc.stdout)
                original = read_export_with_ndrio(Path(f"{prefix}.net"), ndrio)
                pruned = read_export_with_ndrio(Path(f"{prefix}-pruned.net"), ndrio)
                self.assertEqual(original[0], set(source.places))
                self.assertEqual(original[1], {t.name for t in construction.net.transitions})
                self.assertEqual(original[2], {p: source.initial.get(p, 0) for p in source.places})
                self.assertEqual(original[3], {(a.source.name, a.target.name, a.weight) for a in construction.net.arcs})
                self.assertEqual(pruned[0], original[0])
                self.assertEqual(pruned[2], original[2])
                self.assertEqual(pruned[1], original[1] - set(expected_dead))
                self.assertEqual(pruned[3], {a for a in original[3] if a[0] not in expected_dead and a[1] not in expected_dead})
                original_states, original_edges = exported_graph(original)
                pruned_states, pruned_edges = exported_graph(pruned)
                self.assertEqual(original_states, states)
                mapped_edges = set()
                for before, t, after in original_edges:
                    origin = construction.transition_origins[next(tn for tn in construction.net.transitions if tn.name == t)]
                    mapped_edges.add((before, tuple(sorted(origin.global_transition.members)), origin.multiplicities, after))
                self.assertEqual(mapped_edges, source_edges)
                self.assertEqual((pruned_states, pruned_edges), (original_states, original_edges))
                for ending in ["", "-pruned"]:
                    parsed = original if not ending else pruned
                    self.assertEqual(report["before" if not ending else "after"],
                                     {"places": len(parsed[0]), "transitions": len(parsed[1]), "arcs": len(parsed[3])})
                    svg = ET.parse(f"{prefix}{ending}.svg").getroot()
                    nodes = [g for g in svg.iter() if g.attrib.get("class") == "node"]
                    edges = [g for g in svg.iter() if g.attrib.get("class") == "edge"]
                    self.assertEqual(len(nodes), len(parsed[0]) + len(parsed[1]))
                    self.assertEqual(len(edges), len(parsed[3]))
                    visible_text = {element.text for node in nodes for element in node.iter()
                                    if element.tag.endswith("}text")}
                    for place in parsed[0]:
                        self.assertIn(place, visible_text)
                    legend_text = {element.text for element in svg.iter() if element.tag.endswith("}text")}
                    for final in source.finals:
                        expected_final = "{" + ", ".join(f"{p}={n}" for p, n in sorted(final.items()) if n) + "}"
                        self.assertIn(expected_final, legend_text)
                raw_svg = ET.parse(f"{prefix}-agent.svg").getroot()
                raw_text = {element.text for element in raw_svg.iter() if element.tag.endswith("}text")}
                for place in source.places:
                    self.assertIn(place, raw_text)
                # Re-check saved pruned bytes with the real checker, including no-transition nets.
                checked = subprocess.run([str(binary), "-NET", "-R", "-q", f"{prefix}-pruned.net"],
                                         capture_output=True, text=True, timeout=20)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                self.assertIn("0 dead transition(s)", checked.stdout)


if __name__ == "__main__":
    unittest.main()
