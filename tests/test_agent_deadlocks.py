"""Per-type permanent blocking, checked with real Tina/Muse on the global graph."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from petrinet import Transition, agentnet, tina
from tests.test_dot_cli import run_cli


BLOCKED = """agent: Customer
places: waiting done
transitions: buy
labels: buy=serve
arcs: (waiting,buy) (buy,done)
agent: Clerk
places: closed open
transitions: work sell
labels: sell=serve
arcs: (closed,work) (work,closed)
read: (open,sell)
initial: waiting=1 closed=1
unrestricted final: done=1
"""


class AgentDeadlockTests(unittest.TestCase):
    def verify(self, text):
        result = agentnet.convert(agentnet.parse(text))
        return tina.verify_properties(result.net, timeout=15,
                                      agent_final_clauses=result.agent_final_clauses,
                                      agent_transitions=result.agent_transitions)

    def test_participation_includes_weight_variants_read_members_and_empty_types(self):
        source = agentnet.parse("agent: Customer\nplaces: waiting done\ntransitions: buy\n"
                                "labels: buy=serve\narcs: (waiting,buy,*) (buy,done,*)\n"
                                "agent: Clerk\nplaces: open\ntransitions: sell\nlabels: sell=serve\n"
                                "read: (open,sell)\nagent: Passive\nplaces: parked\n"
                                "initial: waiting=2 open=1 parked=1\n")
        result = agentnet.convert(source)
        variants = frozenset([Transition("serve__k_Customer_1"), Transition("serve__k_Customer_2")])
        self.assertEqual(result.agent_transitions, {"Clerk": variants, "Customer": variants, "Passive": frozenset()})
        returned = result.agent_transitions
        returned["Customer"] = frozenset()
        self.assertEqual(result.agent_transitions["Customer"], variants)

    def test_one_type_can_deadlock_while_the_system_keeps_running(self):
        result = self.verify(BLOCKED)
        self.assertEqual(result["deadlocks"], {"reachable": False, "non_final_reachable": False})
        self.assertEqual(result["agent_deadlocks"], {"Customer": True, "Clerk": False})

    def test_temporary_waiting_can_be_resolved_by_another_type(self):
        source = BLOCKED.replace("(work,closed)", "(work,open)")
        source = source.replace("unrestricted final: done=1", "unrestricted final: done=1 open=1")
        self.assertEqual(self.verify(source)["agent_deadlocks"], {"Customer": False, "Clerk": False})

    def test_scheduler_starvation_does_not_imply_permanent_blocking(self):
        result = self.verify("agent: A\nplaces: ready done\ntransitions: finish\n"
                             "arcs: (ready,finish) (finish,done)\n"
                             "agent: Resource\nplaces: idle\ntransitions: loop\narcs: (idle,loop) (loop,idle)\n"
                             "initial: ready=1 idle=1\nunrestricted final: done=1\n")
        self.assertFalse(result["agent_completion"]["A"]["all_traces_complete"])
        self.assertEqual(result["agent_deadlocks"], {"A": False, "Resource": False})

    def test_missing_local_finals_give_no_completion_exemption(self):
        result = self.verify("agent: A\nplaces: ready done\ntransitions: finish\n"
                             "arcs: (ready,finish) (finish,done)\n"
                             "agent: Resource\nplaces: idle\ninitial: ready=1 idle=1\nunrestricted final: done=1\n")
        self.assertEqual(result["deadlocks"], {"reachable": True, "non_final_reachable": False})
        self.assertEqual(result["agent_deadlocks"], {"A": False, "Resource": True})
        self.assertIsNone(result["agent_completion"]["Resource"])

    def test_terminal_selfloops_are_not_agent_actions_and_explicit_zero_finals_exempt(self):
        result = self.verify("agent: A\nplaces: p\nagent: B\nplaces: q\nfinal: p=0\n")
        self.assertEqual(result["agent_deadlocks"], {"A": False, "B": True})
        self.assertEqual(self.verify("agent: A\nplaces: p\n")["agent_deadlocks"], {"A": True})

    def test_completion_at_start_does_not_exempt_later_unfinished_blocking(self):
        result = self.verify("agent: A\nplaces: done left\ntransitions: leave\n"
                             "arcs: (done,leave) (leave,left)\ninitial: done=1\nfinal: done=1\n")
        self.assertTrue(result["agent_completion"]["A"]["all_traces_complete"])
        self.assertEqual(result["agent_deadlocks"], {"A": True})

    def test_read_only_participants_can_act_without_changing_their_places(self):
        result = self.verify("agent: A\nplaces: p[1] q[1]\ntransitions: move back\n"
                             "labels: move=go[1]\narcs: (p[1],move,*) (move,q[1],*) (q[1],back) (back,p[1])\n"
                             "agent: B\nplaces: gate\ntransitions: watch\nlabels: watch=go[1]\n"
                             "read: (gate,watch)\ninitial: p[1]=2 gate=1\nunrestricted final: q[1]=2\n")
        self.assertEqual(result["agent_deadlocks"], {"A": False, "B": False})

    def test_deadlock_can_be_reached_after_an_initially_enabled_choice(self):
        result = self.verify("agent: A\nplaces: ready done stuck\ntransitions: finish fail\n"
                             "arcs: (ready,finish) (finish,done) (ready,fail) (fail,stuck)\n"
                             "agent: B\nplaces: idle\ntransitions: loop\narcs: (idle,loop) (loop,idle)\n"
                             "initial: ready=1 idle=1\nunrestricted final: done=1\n")
        self.assertTrue(result["agent_completion"]["A"]["can_complete"])
        self.assertFalse(result["deadlocks"]["reachable"])
        self.assertEqual(result["agent_deadlocks"], {"A": True, "B": False})

    def test_place_names_in_final_predicates_are_not_replaced_by_template_substitution(self):
        result = self.verify("agent: A\nplaces: can_act final\ntransitions: finish\n"
                             "arcs: (can_act,finish) (finish,final)\ninitial: can_act=1\nfinal: final=1\n")
        self.assertEqual(result["agent_deadlocks"], {"A": False})

    def test_pipeline_checks_original_participation_before_pruning_in_one_session(self):
        source_text = BLOCKED.replace("(waiting,buy) (buy,done)", "(waiting,buy,*) (buy,done,*)")
        source_text = source_text.replace("waiting=1", "waiting=2").replace("done=1", "done=2")
        with tempfile.TemporaryDirectory() as tmp, mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"):
            source, prefix = Path(tmp) / "source.txt", Path(tmp) / "out"
            source.write_text(source_text, encoding="utf-8")
            with mock.patch("petrinet.tina.subprocess.run", wraps=subprocess.run) as run:
                status, stdout, stderr = run_cli([str(source), "--agentnet", "--pipeline", str(prefix), "--timeout", "15"])
            self.assertEqual((status, stderr), (0, ""))
            self.assertEqual(run.call_count, 3)
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["agent_deadlocks"], {"Customer": True, "Clerk": False})
            self.assertEqual(report["removed_transitions"], ["serve__k_Customer_1", "serve__k_Customer_2"])
            self.assertIn("Agent Customer: reachable deadlock: yes", stdout)
            self.assertIn("Agent Clerk: reachable deadlock: no", stdout)
            self.assertNotIn("serve__k_Customer_", Path(f"{prefix}-pruned.net").read_text())


if __name__ == "__main__":
    unittest.main()
