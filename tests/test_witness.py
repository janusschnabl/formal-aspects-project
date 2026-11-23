"""Real-tool counterexamples checked against independently fired source graphs."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from petrinet import InputArc, Marking, OutputArc, PetriNet, Place, Transition, agentnet, parse, tina
from tests.ctl_reference import agent_deadlock_states, satisfying_states
from tests.test_dot_cli import run_cli
from tests.test_pipeline_semantics import reference_agent_finals, reference_graph, reference_is_final


ROOT = Path(__file__).resolve().parent.parent


class WitnessTests(unittest.TestCase):
    def verify(self, source):
        conversion = agentnet.convert(source)
        report = tina.verify_properties(conversion.net, timeout=30, include_traces=True,
                                        agent_final_clauses=conversion.agent_final_clauses,
                                        agent_transitions=conversion.agent_transitions)
        return conversion, report

    def assert_evidence(self, source, conversion, report):
        """Check edges, endpoint predicates, and infinite runs without production firing."""
        states, source_edges, _ = reference_graph(source)
        names = sorted(source.places)
        state_of = lambda tokens: tuple(tokens.get(p, 0) for p in names)
        initial = state_of(source.initial)
        edges = {(before, after) for before, _, _, after in source_edges}
        global_finals = {state for state in states if reference_is_final(source, dict(zip(names, state)))}
        origins = {t.name: origin for t, origin in conversion.transition_origins.items()}
        for trace in report["traces"]:
            with self.subTest(section=trace["section"], agent=trace.get("agent"), property=trace["property"]):
                self.assertEqual(state_of(trace["initial_marking"]), initial)
                current = initial
                visited = [current]
                for step in trace["prefix"]:
                    after = state_of(step["marking"])
                    origin = origins[step["transition"]]
                    self.assertIn((current, tuple(sorted(origin.global_transition.members)),
                                   origin.multiplicities, after), source_edges)
                    current = after
                    visited.append(current)
                self.assertEqual(current, state_of(trace["marking"]))
                endpoint = current
                for step in trace["cycle"]:
                    after = state_of(step["marking"])
                    origin = origins[step["transition"]]
                    self.assertIn((current, tuple(sorted(origin.global_transition.members)),
                                   origin.multiplicities, after), source_edges)
                    current = after
                    visited.append(current)
                if trace["kind"] == "cycle":
                    self.assertTrue(trace["cycle"])
                    self.assertEqual(current, endpoint)
                else:
                    self.assertEqual(trace["cycle"], [])
                if trace["kind"] == "deadlock":
                    self.assertFalse(any(before == endpoint for before, _ in edges))

                finals = global_finals
                if "agent" in trace:
                    finals = reference_agent_finals(source, source.agents[trace["agent"]], states) or set()
                can_finish = satisfying_states(states, edges, {"final": finals}, ("EF", "final"))
                if trace["property"] == "option_to_complete":
                    self.assertNotIn(endpoint, can_finish)
                elif trace["property"] == "all_traces_complete":
                    self.assertFalse(set(visited) & finals)
                    self.assertIn(trace["kind"], {"cycle", "deadlock"})
                elif trace["property"] == "can_complete":
                    self.assertEqual(trace["kind"], "unreachable")
                    self.assertNotIn(initial, can_finish)
                elif trace["section"] == "agent_deadlocks":
                    can_act = {before for before, members, _, _ in source_edges
                               if any(a == trace["agent"] for a, _ in members)}
                    self.assertIn(endpoint, agent_deadlock_states(states, edges, can_act, finals))
                else:
                    self.assertEqual((trace["section"], trace["property"]), ("deadlocks", "non_final_reachable"))
                    self.assertNotIn(endpoint, global_finals)

    def test_all_three_scenarios_return_independently_validated_counterexamples(self):
        for name in ("airport", "swimming_club", "swimming_club_faulty"):
            with self.subTest(model=name):
                source = agentnet.load(ROOT / "examples/agents" / f"{name}.txt")
                conversion, report = self.verify(source)
                self.assert_evidence(source, conversion, report)
                keys = {(t["section"], t.get("agent"), t["property"]) for t in report["traces"]}
                for key, value in report["completion"].items():
                    self.assertEqual(("completion", None, key) in keys, not value)
                for agent, completion in report["agent_completion"].items():
                    for key in ("can_complete", "option_to_complete", "all_traces_complete"):
                        self.assertEqual(("agent_completion", agent, key) in keys, not completion[key])
                for agent, blocked in report["agent_deadlocks"].items():
                    self.assertEqual(("agent_deadlocks", agent, "reachable") in keys, blocked)
                if name == "swimming_club_faulty":
                    trace = next(t for t in report["traces"] if t["section"] == "completion"
                                 and t["property"] == "option_to_complete")
                    self.assertEqual([s["transition"] for s in trace["prefix"]], ["request_work", "quit"])
                    self.assertEqual(trace["marking"]["i_left"], 1)

    def test_zero_step_deadlocks_and_unreachable_local_finals(self):
        source = agentnet.parse("agent: A\nplaces: p q\ninitial: p=1\nfinal: q=1\n")
        conversion, report = self.verify(source)
        self.assert_evidence(source, conversion, report)
        self.assertTrue(any(t["kind"] == "unreachable" for t in report["traces"]))
        self.assertTrue(all(t["prefix"] == [] and t["cycle"] == [] for t in report["traces"]))
        empty = tina.verify_properties(parse(""), timeout=15, include_traces=True)
        self.assertEqual(empty["traces"][0]["marking"], {})
        self.assertEqual(empty["traces"][0]["kind"], "deadlock")

    def test_a_loop_with_an_exit_refutes_AF_but_preserves_completion_option(self):
        source = agentnet.parse("agent: A\nplaces: p q\ntransitions: loop finish\n"
                                "arcs: (p,loop) (loop,p) (p,finish) (finish,q)\ninitial: p=1\nfinal: q=1\n")
        conversion, report = self.verify(source)
        self.assert_evidence(source, conversion, report)
        self.assertTrue(report["completion"]["option_to_complete"])
        for trace in report["traces"]:
            self.assertEqual(trace["property"], "all_traces_complete")
            self.assertEqual(trace["prefix"], [])
            self.assertEqual([s["transition"] for s in trace["cycle"]], ["loop"])

    def test_reaching_a_final_before_looping_is_not_an_AF_counterexample(self):
        source = agentnet.parse("agent: A\nplaces: p q r\ntransitions: finish leave loop\n"
                                "arcs: (p,finish) (finish,q) (q,leave) (leave,r) (r,loop) (loop,r)\n"
                                "initial: p=1\nfinal: q=1\n")
        conversion, report = self.verify(source)
        self.assert_evidence(source, conversion, report)
        self.assertTrue(report["completion"]["all_traces_complete"])
        self.assertTrue(report["traces"])
        self.assertFalse(any(t["property"] == "all_traces_complete" for t in report["traces"]))

    def test_unambiguous_labels_even_when_pathto_text_names_would_be_ambiguous(self):
        for name in ("go[1]", "123", "a b", "a{b}\\c", 'a"b', "a\\\\b"):
            with self.subTest(name=name):
                p, done, stuck = (Place(n) for n in ("p[1]", "done", "stuck"))
                fail, finish = Transition(name), Transition("finish")
                net = PetriNet({p, done, stuck}, {fail, finish},
                               {InputArc(p, fail), OutputArc(fail, stuck), InputArc(p, finish), OutputArc(finish, done)},
                               Marking({p: 1}), final_markings=[Marking({done: 1})])
                report = tina.verify_properties(net, timeout=15, include_traces=True)
                trace = next(t for t in report["traces"] if t["property"] == "option_to_complete")
                self.assertEqual(trace["prefix"], [{"transition": name, "marking": {"stuck": 1}}])

    def test_no_witness_tools_are_needed_when_built_in_properties_pass(self):
        net = parse("places: p q\ntransitions: finish\narcs: (p,finish) (finish,q)\ninitial: p=1\nfinal: q=1\n"
                    "ctl: custom_false = F:S\n")
        with mock.patch("petrinet.tina.subprocess.run", wraps=subprocess.run) as run:
            report = tina.verify_properties(net, timeout=15, include_traces=True)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(report["traces"], [])
        self.assertFalse(report["properties"]["custom_false"])

    def test_pipeline_exports_source_activities_weights_and_replayable_markings(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"):
            prefix = Path(folder) / "airport"
            args = [str(ROOT / "examples/agents/airport.txt"), "--agentnet",
                    "--pipeline", str(prefix), "--timeout", "30"]
            original_traces = None
            for flags in ([], ["--verbose"], ["-v"]):
                with self.subTest(flags=flags):
                    status, stdout, stderr = run_cli(args + flags)
                    self.assertEqual((status, stderr), (0, ""))
                    report = json.loads(Path(f"{prefix}-results.json").read_text())
                    self.assertEqual(report["status"], "complete")
                    steps = [step for trace in report["traces"] for step in trace["prefix"] + trace["cycle"]]
                    weighted = [step for step in steps if step["weights"]]
                    self.assertTrue(weighted)
                    self.assertTrue(all("__k_" not in step["activity"] for step in weighted))
                    self.assertIn("Soundness: no", stdout)
                    if flags:
                        self.assertEqual(report["traces"], original_traces)
                        self.assertIn("Counterexamples\n", stdout)
                        self.assertIn("Repeat forever:", stdout)
                        self.assertIn("Passenger.pax_open[", stdout)
                    else:
                        original_traces = report["traces"]
                        self.assertIn(f"Counterexamples: {len(original_traces)} (use --verbose", stdout)
                        self.assertNotIn("    Run:", stdout)
                        self.assertNotIn("Repeat forever:", stdout)

    def test_witness_failures_clear_previous_evidence_and_preserve_original_outputs(self):
        for failure in ("missing-pathto", "pathto-timeout", "invalid-path", "invalid-graph", "invalid-targets", "replay-mismatch"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder, \
                    mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"):
                folder = Path(folder)
                source, prefix = folder / "source.txt", folder / "out"
                source.write_text("places: p done stuck\ntransitions: finish fail\ninitial: p=1\n"
                                  "arcs: (p,finish) (finish,done) (p,fail) (fail,stuck)\nfinal: done=1\n")
                args = [str(source), "--pipeline", str(prefix), "--timeout", "15"]
                self.assertEqual(run_cli(args)[0], 0)
                original_pruned = Path(f"{prefix}-pruned.net").read_bytes()
                real_run = subprocess.run

                def run(cmd, **kwargs):
                    tool = Path(cmd[0]).stem
                    if tool == "pathto":
                        if failure == "missing-pathto":
                            raise FileNotFoundError()
                        if failure == "pathto-timeout":
                            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
                        if failure == "invalid-path":
                            return subprocess.CompletedProcess(cmd, 0, "0 999\n", "")
                    if tool == "muse" and "-s" in cmd and failure == "invalid-targets":
                        return subprocess.CompletedProcess(cmd, 0, "[]\n[]\n", "")
                    result = real_run(cmd, **kwargs)
                    if tool == "ktzio" and failure == "invalid-graph":
                        Path(cmd[-1]).write_text("{}")
                    if tool == "ktzio" and failure == "replay-mismatch":
                        path = Path(cmd[-1])
                        graph = json.loads(path.read_text())
                        for state in graph["states"]:
                            if state["no"] != 0:
                                for prop in state["props"][1]:
                                    prop["value"] += 1
                        path.write_text(json.dumps(graph))
                    return result

                with mock.patch("petrinet.tina.subprocess.run", side_effect=run):
                    status, stdout, stderr = run_cli(args)
                self.assertEqual((status, stdout), (2, ""))
                self.assertIn("error:", stderr)
                report = json.loads(Path(f"{prefix}-results.json").read_text())
                self.assertEqual(report["status"], "failed")
                self.assertIsNone(report["traces"])
                self.assertIsNone(report["completion"])
                self.assertIsNone(report["removed_transitions"])
                self.assertEqual(Path(f"{prefix}-pruned.net").read_bytes(), original_pruned)
                self.assertTrue(Path(f"{prefix}.net").is_file())

    def test_trace_extraction_shares_the_property_timeout(self):
        net = parse("places: p q\ntransitions: loop\narcs: (p,loop) (loop,p)\ninitial: p=1\nfinal: q=1\n")
        now = [0]
        real_run = subprocess.run

        def run(cmd, **kwargs):
            result = real_run(cmd, **kwargs)
            if Path(cmd[0]).stem == "muse" and "-s" in cmd:
                now[0] = 61
            return result

        with mock.patch("petrinet.tina.time.monotonic", side_effect=lambda: now[0]), \
                mock.patch("petrinet.tina.subprocess.run", side_effect=run), \
                self.assertRaisesRegex(tina.TinaError, "timed out"):
            tina.verify_properties(net, timeout=60, include_traces=True)


if __name__ == "__main__":
    unittest.main()
