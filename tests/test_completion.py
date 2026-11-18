"""Small, explicit completion examples checked with the real Tina/Muse tools."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from petrinet import agentnet, parse, tina
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
                    "ctl: until_all = AU (S.waiting = 1) IsFinal\n")
        completion, properties = tina.verify_properties(net, timeout=15)
        self.assertEqual(completion, {"option_to_complete": True, "all_traces_complete": False})
        self.assertEqual(properties, {"possible_next": True, "certain_next": False, "can_avoid": True,
                                     "stays_bounded": True, "until_some": True, "until_all": False})
        # A final deadlock is totalized with an implicit temporal self-loop.
        terminal = parse("places: p\nfinal:\nctl: next_final = AX IsFinal\nctl: some_next_final = EX IsFinal\n")
        self.assertEqual(tina.verify_properties(terminal, timeout=15)[1],
                         {"next_final": True, "some_next_final": True})

    def test_named_properties_without_finals_and_invalid_formula(self):
        net = parse("places: p\nctl: safe = AG (S.p = 0)\nctl: no_final = EF IsFinal\n")
        self.assertEqual(tina.verify_properties(net, timeout=15), (None, {"safe": True, "no_final": False}))
        invalid = parse("places: p\nctl: bad = AG UnknownPlace\n")
        with self.assertRaisesRegex(tina.TinaError, "Muse CTL results"):
            tina.verify_properties(invalid, timeout=15)


if __name__ == "__main__":
    unittest.main()
