"""Small, explicit completion examples checked with the real Tina/Muse tools."""

import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest
from unittest import mock

from petrinet import InputArc, Marking, OutputArc, PetriNet, Place, Transition, agentnet, parse, tina
from tests.ctl_reference import expected_verification, satisfying_states
from tests.test_dot_cli import run_cli


class CompletionTests(unittest.TestCase):
    def test_no_final_declarations_are_not_a_false_verdict(self):
        with mock.patch("petrinet.tina.subprocess.run") as run:
            self.assertIsNone(tina.check_completion(parse("places: p\n")))
            run.assert_not_called()

    def test_bundled_linux_muse_restores_execute_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "linux-amd64" / "bin"
            directory.mkdir(parents=True)
            binary, muse = directory / "tina", directory / "muse"
            binary.touch()
            muse.touch()

            def run(cmd, **kwargs):
                if cmd[0] == str(binary):
                    Path(cmd[-1]).touch()
                    return subprocess.CompletedProcess(cmd, 0, "# bounded, live\n", "")
                return subprocess.CompletedProcess(cmd, 0, "TRUE\nTRUE\n", "")

            with mock.patch("petrinet.tina._BUNDLED_ROOT", root), \
                    mock.patch("petrinet.tina.sys.platform", "linux"), \
                    mock.patch("petrinet.tina.os.access", return_value=False), \
                    mock.patch.object(Path, "chmod") as chmod, \
                    mock.patch("petrinet.tina.subprocess.run", side_effect=run):
                net = parse("places: p\nunrestricted final: p=0\n")
                self.assertEqual(tina.check_completion(net, str(binary)),
                                 {"option_to_complete": True, "all_traces_complete": True})
                chmod.assert_called_once_with(muse.stat().st_mode | 0o100)

    def test_completion_and_inevitability_have_distinct_meanings(self):
        cases = [
            # A final deadlock is successful completion; crew is unconstrained.
            ("finishes", "transitions: board\narcs: (waiting,board) (board,done)\n",
             "unrestricted final: done=1\n", True, True),
            # A scheduler can repeat loop forever instead of boarding.
            ("loop-with-exit", "transitions: board loop\narcs: (waiting,board) (board,done) (waiting,loop) (loop,waiting)\n",
             "unrestricted final: done=1\n", True, False),
            # The failure is a reachable non-final deadlock, not a missing path initially.
            ("bad-branch", "transitions: board abandon\narcs: (waiting,board) (board,done) (waiting,abandon) (abandon,stuck)\n",
             "unrestricted final: done=1\n", False, False),
            # No deadlock is necessary to lose the option to complete.
            ("trapped-cycle", "transitions: board abandon loop\narcs: (waiting,board) (board,done) (waiting,abandon) (abandon,stuck) (stuck,loop) (loop,stuck)\n",
             "unrestricted final: done=1\n", False, False),
            # Exact final omits crew, requiring zero, so it cannot be reached.
            ("exact-omitted-crew", "transitions: board\narcs: (waiting,board) (board,done)\n",
             "final: done=1\n", False, False),
            # Each clause contributes to the union, including literal markings.
            ("union", "transitions: board abandon\narcs: (waiting,board) (board,done) (waiting,abandon) (abandon,stuck)\n",
             "unrestricted final: done=1\nfinal: stuck=1 crew=1\n", True, True),
        ]
        for name, behavior, finals, option, inevitable in cases:
            with self.subTest(case=name):
                net = parse("places: waiting done stuck crew\ninitial: waiting=1 crew=1\n" + behavior + finals)
                self.assertEqual(tina.check_completion(net, timeout=15),
                                 {"option_to_complete": option, "all_traces_complete": inevitable})

    def test_zero_constraints_and_indexed_names_use_the_same_final_semantics(self):
        source = agentnet.parse("agent: A\nplaces: waiting[1] done[1]\ntransitions: board\n"
                                "arcs: (waiting[1],board) (board,done[1])\n"
                                "initial: waiting[1]=1\nunrestricted final: waiting[1]=0\n")
        self.assertEqual(tina.check_completion(agentnet.convert(source).net, timeout=15),
                         {"option_to_complete": True, "all_traces_complete": True})

    def test_empty_clauses_and_final_at_start(self):
        for text in ["final:\n", "unrestricted final:\n"]:
            with self.subTest(text=text):
                self.assertEqual(tina.check_completion(parse(text), timeout=15),
                                 {"option_to_complete": True, "all_traces_complete": True})
        # AF includes reaching a final at time zero; AG EF also checks later states.
        net = parse("places: done stuck\ntransitions: leave\ninitial: done=1\n"
                    "arcs: (done,leave) (leave,stuck)\nunrestricted final: done=1\n")
        self.assertEqual(tina.check_completion(net, timeout=15),
                         {"option_to_complete": False, "all_traces_complete": True})

    def test_incomplete_or_unreadable_analysis_is_an_error(self):
        net = parse("places: p\ntransitions: add\narcs: (add,p)\nunrestricted final: p=1\n")
        with self.assertRaises(tina.TinaError):
            tina.check_completion(net, timeout=15)
        net = parse("places: p\nunrestricted final: p=0\n")
        with self.assertRaises(tina.TinaError):
            tina.check_completion(net, tina_bin="missing-tina-executable", timeout=15)
        with mock.patch("petrinet.tina.subprocess.run") as run:
            with self.assertRaisesRegex(tina.TinaError, "timed out"):
                tina.check_completion(net, timeout=0)
            run.assert_not_called()

    def test_pipeline_reports_false_property_without_treating_it_as_a_tool_error(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"):
            source = Path(tmp) / "net.txt"
            source.write_text("places: waiting done\ntransitions: board loop\ninitial: waiting=1\n"
                              "arcs: (waiting,board) (board,done) (waiting,loop) (loop,waiting)\n"
                              "unrestricted final: done=1\n"
                              "ctl: can_finish = AG EF IsFinal\n"
                              "ctl: must_finish = AF IsFinal\n", encoding="utf-8")
            prefix = Path(tmp) / "out"
            status, stdout, stderr = run_cli([str(source), "--pipeline", str(prefix), "--timeout", "15"])
            self.assertEqual((status, stderr), (0, ""))
            self.assertIn("Option to complete from every reachable marking: yes", stdout)
            self.assertIn("All traces eventually reach a final marking: no", stdout)
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["completion"], {"option_to_complete": True, "all_traces_complete": False})
            self.assertEqual(report["deadlocks"], {"reachable": True, "non_final_reachable": False})
            self.assertEqual(report["properties"], {"can_finish": True, "must_finish": False})
            self.assertIn("CTL can_finish: yes", stdout)
            self.assertIn("CTL must_finish: no", stdout)

    def test_named_ctl_operators_and_place_comparisons(self):
        net = parse("places: waiting done\ntransitions: board loop\ninitial: waiting=1\n"
                    "arcs: (waiting,board) (board,done) (waiting,loop) (loop,waiting)\n"
                    "unrestricted final: done=1\n"
                    "ctl: possible_next = EX IsFinal\n"
                    "ctl: certain_next = AX IsFinal\n"
                    "ctl: can_avoid = EG (- IsFinal)\n"
                    "ctl: stays_bounded = AG (S.waiting <= 1)\n"
                    "ctl: until_some = EU (S.waiting = 1) IsFinal\n"
                    "ctl: until_all = AU (S.waiting = 1) IsFinal\n"
                    "ctl: option_to_complete = F:S\n")
        with mock.patch("petrinet.tina.subprocess.run", wraps=subprocess.run) as run:
            result = tina.verify_properties(net, timeout=15)
        # All default and user properties share one graph and one Muse process.
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][1:4], ["-NET", "-R", "-ktz"])
        self.assertEqual(run.call_args_list[1].args[0][1:3], ["-q", "-b"])
        self.assertEqual(result["completion"], {"option_to_complete": True, "all_traces_complete": False})
        self.assertEqual(result["properties"], {"possible_next": True, "certain_next": False, "can_avoid": True,
                                               "stays_bounded": True, "until_some": True, "until_all": False,
                                               "option_to_complete": False})
        # A final deadlock is totalized with an implicit temporal self-loop.
        terminal = parse("places: p\nfinal:\nctl: next_final = AX IsFinal\nctl: some_next_final = EX IsFinal\n")
        self.assertEqual(tina.verify_properties(terminal, timeout=15)["properties"],
                         {"next_final": True, "some_next_final": True})

    def test_named_properties_without_finals_and_invalid_formula(self):
        net = parse("places: p\nctl: safe = AG (S.p = 0)\nctl: no_final = EF IsFinal\n")
        self.assertEqual(tina.verify_properties(net, timeout=15), {
            "completion": None, "deadlocks": {"reachable": True, "non_final_reachable": True},
            "properties": {"safe": True, "no_final": False},
        })
        invalid = parse("places: p\nctl: bad = AG UnknownPlace\n")
        with self.assertRaisesRegex(tina.TinaError, "Muse CTL results"):
            tina.verify_properties(invalid, timeout=15)

    def test_deadlocks_distinguish_terminal_markings_from_cycles_and_dead_transitions(self):
        cases = [
            ("initial-deadlock", "places: p\n", True, True),
            ("empty-net", "", True, True),
            ("final-deadlock", "places: p\nfinal:\n", True, False),
            ("unrestricted-final-deadlock", "places: p\ninitial: p=1\nunrestricted final:\n", True, False),
            ("self-loop", "places: p\ntransitions: loop\ninitial: p=1\narcs: (p,loop) (loop,p)\n", False, False),
            ("transition-without-arcs", "transitions: loop\n", False, False),
            ("dead-transition-with-live-cycle", "places: p q\ntransitions: loop dead\ninitial: p=1\n"
             "arcs: (p,loop) (loop,p) (q,dead) (dead,q)\n", False, False),
            ("bad-branch", "places: p done stuck\ntransitions: finish fail\ninitial: p=1\n"
             "arcs: (p,finish) (finish,done) (p,fail) (fail,stuck)\nfinal: done=1\n", True, True),
        ]
        for name, text, reachable, non_final in cases:
            with self.subTest(case=name):
                net = parse(text + "ctl: no_deadlocks = AG (- IsDeadlock)\n")
                expected = {"reachable": reachable, "non_final_reachable": non_final}
                result = tina.verify_properties(net, timeout=15)
                self.assertEqual(result["deadlocks"], expected)
                self.assertEqual(result["properties"], {"no_deadlocks": not reachable})
                self.assertEqual(tina.check_deadlocks(net, timeout=15), expected)

    def test_nested_ctl_matches_an_independent_finite_graph_evaluator(self):
        atoms = [("final", "IsFinal"), ("start", "(S.p0 = 1)"),
                 ("middle", "(S.p1 = 1)"), ("deadlock", "IsDeadlock")]
        formulas = list(atoms)
        temporal = ["EX", "AX", "EF", "AF", "EG", "AG"]
        for operator in temporal:
            formulas.extend(((operator, key), f"{operator} ({text})") for key, text in atoms)
            formulas.extend(((operator, (inner, "final")), f"{operator} ({inner} IsFinal)")
                            for inner in temporal)
        for operator in ["EU", "AU"]:
            formulas.extend(((operator, first, second), f"{operator} ({a}) ({b})")
                            for first, a in atoms[:3] for second, b in atoms[:3])
        formulas.extend([
            (("not", ("EF", "final")), "- (EF IsFinal)"),
            (("AG", ("or", "final", ("not", "deadlock"))), "AG (IsFinal \\/ (- IsDeadlock))"),
        ])
        rng = random.Random(738)
        graphs = [set(), {(0, 2)}, {(0, 0), (0, 2)}, {(0, 1), (1, 1)}, {(0, 1), (0, 2)}]
        graphs.extend({(a, b) for a in range(4) for b in range(4) if rng.random() < 0.23}
                      for _ in range(10))
        for case, edges in enumerate(graphs):
            with self.subTest(graph=case):
                places = [Place(f"p{i}") for i in range(4)]
                transitions = {(a, b): Transition(f"t{a}_{b}") for a, b in edges}
                arcs = {arc for (a, b), t in transitions.items()
                        for arc in [InputArc(places[a], t), OutputArc(t, places[b])]}
                net = PetriNet(set(places), set(transitions.values()), arcs, Marking({places[0]: 1}),
                               unrestricted_final_markings=[{places[2]: 1}],
                               ctl_properties={f"property_{i}": text for i, (_, text) in enumerate(formulas)})
                states = {0}
                while True:
                    reachable = states | {b for a, b in edges if a in states}
                    if reachable == states:
                        break
                    states = reachable
                reachable_edges = {(a, b) for a, b in edges if a in states}
                expected = expected_verification(states, reachable_edges, 0, states & {2}, True)
                atom_sets = {"final": states & {2}, "start": {0}, "middle": states & {1}}
                expected["properties"] = {
                    f"property_{i}": 0 in satisfying_states(states, reachable_edges, atom_sets, expression)
                    for i, (expression, _) in enumerate(formulas)
                }
                self.assertEqual(tina.verify_properties(net, timeout=15), expected)

    def test_property_failures_after_dead_transition_analysis_preserve_pipeline_artifacts(self):
        cases = ["invalid-formula", "missing-muse", "muse-timeout", "unreadable-results"]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"):
                source, prefix = Path(tmp) / "source.txt", Path(tmp) / "out"
                source.write_text("places: p q\ntransitions: finish dead\ninitial: p=1\n"
                                  "arcs: (p,finish) (q,dead)\nfinal:\n", encoding="utf-8")
                args = [str(source), "--pipeline", str(prefix), "--timeout", "15"]
                self.assertEqual(run_cli(args)[0], 0)
                pruned = {suffix: Path(f"{prefix}-pruned.{suffix}").read_bytes()
                          for suffix in ["net", "dot", "svg"]}
                real_run = subprocess.run
                muse_calls = []

                def run(cmd, **kwargs):
                    if Path(cmd[0]).stem != "muse":
                        return real_run(cmd, **kwargs)
                    muse_calls.append(cmd)
                    if case == "missing-muse":
                        raise FileNotFoundError()
                    if case == "muse-timeout":
                        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
                    if case == "unreadable-results":
                        return subprocess.CompletedProcess(cmd, 0, "TRUE\n", "")
                    return real_run(cmd, **kwargs)

                if case == "invalid-formula":
                    with source.open("a", encoding="utf-8") as stream:
                        stream.write("ctl: invalid = AG UnknownPlace\n")
                with mock.patch("petrinet.tina.subprocess.run", side_effect=run):
                    status, stdout, stderr = run_cli(args)
                self.assertEqual((status, stdout), (2, ""))
                self.assertTrue(muse_calls)
                self.assertIn("error:", stderr)
                report = json.loads(Path(f"{prefix}-results.json").read_text())
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["dead_transitions"], ["dead"])
                self.assertIsNone(report["removed_transitions"])
                self.assertIsNone(report["completion"])
                self.assertIsNone(report["deadlocks"])
                self.assertNotIn("pruned_files", report)
                self.assertTrue(report["error"])
                for suffix, content in pruned.items():
                    self.assertEqual(Path(f"{prefix}-pruned.{suffix}").read_bytes(), content)
                self.assertTrue(Path(f"{prefix}.net").is_file())
                self.assertTrue(Path(f"{prefix}.svg").is_file())


if __name__ == "__main__":
    unittest.main()
