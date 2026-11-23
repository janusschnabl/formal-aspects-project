"""Soundness aggregates existing results over original source activities."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from petrinet import agentnet, tina
from tests.test_dot_cli import run_cli


SOME_DEAD_VARIANTS = """agent: A
places: p q r s
transitions: move
arcs: (p,move,*) (move,s,*) (q,move) (move,r)
initial: p=2 q=1
unrestricted final: r=1
"""


def activity(name, label, *members):
    return {"name": name, "label": label,
            "members": [{"agent": a, "transition": t} for a, t in members]}


class SoundnessTests(unittest.TestCase):
    def pipeline(self, text, agent_model=True):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"):
            source, prefix = Path(tmp) / "source.txt", Path(tmp) / "out"
            source.write_text(text, encoding="utf-8")
            args = [str(source), "--pipeline", str(prefix), "--timeout", "15"]
            if agent_model:
                args.append("--agentnet")
            status, stdout, stderr = run_cli(args)
            self.assertEqual((status, stderr), (0, ""))
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["status"], "complete")
            return report, stdout, Path(f"{prefix}-pruned.net").read_text()

    def test_dead_weight_choices_do_not_make_a_source_activity_dead(self):
        with mock.patch("petrinet.tina.subprocess.run", wraps=subprocess.run) as run:
            report, stdout, pruned = self.pipeline(SOME_DEAD_VARIANTS)
        self.assertEqual(run.call_count, 3)  # No extra reachability or post-pruning check.
        self.assertEqual(report["dead_transitions"], ["move__k_A_3"])
        self.assertEqual(report["dead_activities"], [])
        self.assertIs(report["soundness"], True)
        self.assertIn("  Dead converted transitions: 1\n    move__k_A_3", stdout)
        self.assertIn("  [PASS] No dead source activities", stdout)
        self.assertIn("Soundness: yes", stdout)
        self.assertIn("move__k_A_1", pruned)
        self.assertNotIn("move__k_A_3", pruned)

    def test_an_entirely_dead_variable_activity_still_fails_after_all_variants_are_pruned(self):
        report, stdout, pruned = self.pipeline(
            "agent: A\nplaces: ready done unused\ntransitions: finish dead\n"
            "arcs: (ready,finish) (finish,done) (unused,dead,*) (dead,unused,*)\n"
            "initial: ready=1\nfinal: done=1\n")
        self.assertTrue(report["completion"]["option_to_complete"])
        self.assertEqual(report["agent_deadlocks"], {"A": False})
        self.assertEqual(report["dead_activities"], [activity("dead", None, ("A", "dead"))])
        self.assertIs(report["soundness"], False)
        self.assertEqual(report["removed_transitions"], ["dead__k_A_1"])
        self.assertNotIn("dead__k_A_1", pruned)
        self.assertIn("  [FAIL] No dead source activities\n    Dead source activity: dead (A.dead)", stdout)
        self.assertIn("  [PASS] Global option to complete", stdout)
        self.assertIn("  [PASS] No per-type deadlocks", stdout)

    def test_activity_with_zero_generated_variants_is_not_lost(self):
        text = "agent: A\nplaces: p q\ntransitions: move\narcs: (p,move,*) (move,q,*)\nfinal: p=0\n"
        source = agentnet.parse(text)
        result = agentnet.convert(source)
        self.assertEqual(result.transition_origins, {})
        self.assertEqual(set(result.source_activities), set(source.global_transitions()))
        self.assertEqual(set(result.dead_activities([])), set(source.global_transitions()))
        report, _, _ = self.pipeline(text)
        self.assertTrue(report["completion"]["option_to_complete"])
        self.assertEqual(report["agent_deadlocks"], {"A": False})
        self.assertEqual(report["dead_transitions"], [])
        self.assertEqual(report["dead_activities"], [activity("move", None, ("A", "move"))])
        self.assertIs(report["soundness"], False)

    def test_dead_fixed_activities_are_also_reported(self):
        report, _, _ = self.pipeline(
            "agent: A\nplaces: ready done unused\ntransitions: finish dead\n"
            "arcs: (ready,finish) (finish,done) (unused,dead) (dead,unused)\n"
            "initial: ready=1\nfinal: done=1\n")
        self.assertTrue(report["completion"]["option_to_complete"])
        self.assertEqual(report["dead_activities"], [activity("dead", None, ("A", "dead"))])
        self.assertIs(report["soundness"], False)

    def test_synchronized_activity_is_one_group_with_all_participants(self):
        report, _, _ = self.pipeline(
            "agent: Customer\nplaces: ready done unused\ntransitions: finish buy\n"
            "labels: buy=serve\narcs: (ready,finish) (finish,done) (unused,buy) (buy,done)\n"
            "agent: Clerk\nplaces: closed open\ntransitions: work sell\nlabels: sell=serve\n"
            "arcs: (closed,work) (work,closed)\nread: (open,sell)\n"
            "initial: ready=1 closed=1\nunrestricted final: done=1\n")
        self.assertTrue(report["completion"]["option_to_complete"])
        self.assertFalse(any(report["agent_deadlocks"].values()))
        self.assertEqual(report["dead_activities"], [
            activity("serve", "serve", ("Clerk", "sell"), ("Customer", "buy")),
        ])
        self.assertIs(report["soundness"], False)

    def test_activity_identity_does_not_merge_a_label_and_silent_transition_with_the_same_name(self):
        report, stdout, _ = self.pipeline(
            "agent: A\nplaces: ready done unused\ntransitions: move go\nlabels: move=go\n"
            "arcs: (ready,move) (move,done) (unused,go) (go,unused)\n"
            "initial: ready=1\nfinal: done=1\n")
        self.assertEqual(report["dead_transitions"], ["go__2"])
        self.assertEqual(report["dead_activities"], [activity("go", None, ("A", "go"))])
        self.assertIs(report["soundness"], False)
        self.assertIn("go (A.go)", stdout)

    def test_soundness_requires_option_to_complete_not_inevitable_completion(self):
        report, _, _ = self.pipeline(
            "agent: A\nplaces: ready done\ntransitions: finish loop\n"
            "arcs: (ready,finish) (finish,done) (ready,loop) (loop,ready)\n"
            "initial: ready=1\nfinal: done=1\n")
        self.assertFalse(report["completion"]["all_traces_complete"])
        self.assertTrue(report["completion"]["option_to_complete"])
        self.assertEqual(report["dead_activities"], [])
        self.assertIs(report["soundness"], True)

    def test_an_initial_completion_option_is_not_enough(self):
        report, _, _ = self.pipeline(
            "agent: A\nplaces: ready done stuck\ntransitions: finish fail\n"
            "arcs: (ready,finish) (finish,done) (ready,fail) (fail,stuck)\n"
            "initial: ready=1\nfinal: done=1\n")
        self.assertTrue(report["agent_completion"]["A"]["can_complete"])
        self.assertFalse(report["completion"]["option_to_complete"])
        self.assertEqual(report["dead_activities"], [])
        self.assertIs(report["soundness"], False)

    def test_agent_deadlocks_fail_soundness_even_when_global_completion_is_possible(self):
        report, stdout, _ = self.pipeline(
            "agent: A\nplaces: ready done\ntransitions: finish\narcs: (ready,finish) (finish,done)\n"
            "agent: Resource\nplaces: idle\ninitial: ready=1 idle=1\nunrestricted final: done=1\n")
        self.assertTrue(report["completion"]["option_to_complete"])
        self.assertEqual(report["dead_activities"], [])
        self.assertEqual(report["agent_deadlocks"], {"A": False, "Resource": True})
        self.assertIs(report["soundness"], False)
        self.assertIn("  [PASS] Global option to complete", stdout)
        self.assertIn("  [FAIL] No per-type deadlocks\n    Deadlocked type: Resource", stdout)
        self.assertNotIn("Deadlocked type: A\n", stdout)
        self.assertIn("  [PASS] No dead source activities", stdout)

    def test_soundness_lists_all_failed_requirements_and_affected_types_and_activities(self):
        report, stdout, _ = self.pipeline(
            "agent: A\nplaces: ready done stuck unused\ntransitions: finish fail dead\n"
            "arcs: (ready,finish) (finish,done) (ready,fail) (fail,stuck) (unused,dead) (dead,unused)\n"
            "agent: Resource\nplaces: idle\ninitial: ready=1 idle=1\nunrestricted final: done=1\n")
        self.assertIs(report["soundness"], False)
        soundness = stdout.split("\nSoundness\n---------\n", 1)[1]
        self.assertEqual(soundness, "  Soundness: no\n"
                         "  [FAIL] Global option to complete\n"
                         "    A reachable marking has no path to a global final.\n"
                         "  [FAIL] No per-type deadlocks\n"
                         "    Deadlocked type: A\n"
                         "    Deadlocked type: Resource\n"
                         "  [FAIL] No dead source activities\n"
                         "    Dead source activity: dead (A.dead)\n")

    def test_local_completion_options_do_not_replace_the_global_final(self):
        report, _, _ = self.pipeline(
            "agent: A\nplaces: a_done a_cancelled\n"
            "agent: B\nplaces: b_done b_cancelled\n"
            "initial: a_done=1 b_done=1\n"
            "final: a_done=1 b_cancelled=1\nfinal: a_cancelled=1 b_done=1\n")
        self.assertTrue(all(local["option_to_complete"] for local in report["agent_completion"].values()))
        self.assertFalse(report["completion"]["option_to_complete"])
        self.assertFalse(any(report["agent_deadlocks"].values()))
        self.assertEqual(report["dead_activities"], [])
        self.assertIs(report["soundness"], False)

    def test_without_global_finals_soundness_is_unspecified_but_dead_activities_are_reported(self):
        report, stdout, _ = self.pipeline(
            "agent: A\nplaces: p q\ntransitions: loop dead\n"
            "arcs: (p,loop) (loop,p) (q,dead) (dead,q)\ninitial: p=1\n")
        self.assertIsNone(report["soundness"])
        self.assertEqual(report["dead_activities"], [activity("dead", None, ("A", "dead"))])
        self.assertIn("Soundness not checked: no global final declarations.", stdout)
        self.assertIn("  [SKIP] Global option to complete: no global final declarations.", stdout)
        self.assertIn("  [FAIL] No dead source activities\n    Dead source activity: dead (A.dead)", stdout)

    def test_plain_nets_do_not_claim_agent_soundness(self):
        report, stdout, _ = self.pipeline("places: p\nfinal:\n", agent_model=False)
        self.assertIsNone(report["soundness"])
        self.assertIsNone(report["dead_activities"])
        self.assertNotIn("Soundness:", stdout)
        self.assertNotIn("\nSoundness\n", stdout)
        self.assertNotIn("Agent verification", stdout)

    def test_failed_rerun_clears_the_previous_soundness_verdict(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"):
            source, prefix = Path(tmp) / "source.txt", Path(tmp) / "out"
            source.write_text(SOME_DEAD_VARIANTS, encoding="utf-8")
            args = [str(source), "--agentnet", "--pipeline", str(prefix), "--timeout", "15"]
            self.assertEqual(run_cli(args)[0], 0)
            self.assertTrue(json.loads(Path(f"{prefix}-results.json").read_text())["soundness"])
            pruned = Path(f"{prefix}-pruned.net").read_bytes()
            with mock.patch("petrinet.__main__.tina.verify_properties", side_effect=tina.TinaError("Muse failed")):
                self.assertEqual(run_cli(args), (2, "", "error: Muse failed\n"))
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertIsNone(report["soundness"])
            self.assertIsNone(report["removed_transitions"])
            self.assertEqual(report["dead_activities"], [])
            self.assertEqual(Path(f"{prefix}-pruned.net").read_bytes(), pruned)


if __name__ == "__main__":
    unittest.main()
