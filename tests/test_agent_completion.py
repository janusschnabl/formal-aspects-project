"""Infer local finals from explicit declarations; evaluate on the global graph."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from petrinet import Marking, Place, agentnet, tina
from tests.test_dot_cli import run_cli


class AgentFinalInferenceTests(unittest.TestCase):
    def test_exact_finals_preserve_explicit_zeros_and_skip_omitted_types(self):
        source = agentnet.parse("agent: A\nplaces: ready done\n"
                                "agent: B\nplaces: pending finished\n"
                                "agent: Resource\nplaces: idle\n"
                                "final: done=1\nfinal: pending=0\n")
        result = agentnet.convert(source)
        self.assertEqual(source.finals, [{"done": 1}, {"pending": 0}])
        self.assertEqual(result.agent_final_clauses, {
            "A": ({Place("ready"): 0, Place("done"): 1},),
            "B": ({Place("pending"): 0, Place("finished"): 0},),
            "Resource": (),
        })
        self.assertEqual(result.net.final_markings, frozenset([Marking({Place("done"): 1}), Marking()]))
        source.finals[0]["done"] = 2
        self.assertEqual(result.agent_final_clauses["A"][0][Place("done")], 1)

    def test_partial_finals_keep_only_explicit_constraints_and_ignore_empty_clauses(self):
        source = agentnet.parse("agent: A\nplaces: ready[1] done[1]\n"
                                "agent: B\nplaces: idle\n"
                                "unrestricted final: ready[1]=0\nunrestricted final:\n")
        result = agentnet.convert(source)
        self.assertEqual(result.agent_final_clauses, {"A": ({Place("ready[1]"): 0},), "B": ()})

    def test_empty_global_finals_do_not_create_local_finals(self):
        for declaration in ["", "final:\n", "unrestricted final:\n"]:
            with self.subTest(declaration=declaration):
                source = agentnet.parse("agent: A\nplaces: p\n" + declaration)
                self.assertEqual(agentnet.convert(source).agent_final_clauses, {"A": ()})


class AgentCompletionTests(unittest.TestCase):
    def verify(self, text):
        result = agentnet.convert(agentnet.parse(text))
        return tina.verify_properties(result.net, timeout=15, agent_final_clauses=result.agent_final_clauses)

    def test_initial_and_preserved_completion_options_and_inevitability(self):
        cases = [
            ("finishes", "transitions: finish\narcs: (ready,finish) (finish,done)\n", True, True, True),
            ("loop-with-exit", "transitions: finish loop\n"
             "arcs: (ready,finish) (finish,done) (ready,loop) (loop,ready)\n", True, True, False),
            # A successful path exists initially, even though a bad branch loses it.
            ("bad-branch", "transitions: finish fail\n"
             "arcs: (ready,finish) (finish,done) (ready,fail) (fail,stuck)\n", True, False, False),
            # A non-final loop can also lose completion without a terminal state.
            ("trapped-cycle", "transitions: finish fail loop\n"
             "arcs: (ready,finish) (finish,done) (ready,fail) (fail,stuck) (stuck,loop) (loop,stuck)\n",
             True, False, False),
            ("unreachable-final", "", False, False, False),
        ]
        for name, behavior, possible, option, inevitable in cases:
            with self.subTest(case=name):
                result = self.verify("agent: A\nplaces: ready done stuck\n" + behavior
                                     + "initial: ready=1\nfinal: done=1\n")
                self.assertEqual(result["agent_completion"], {
                    "A": {"can_complete": possible, "option_to_complete": option, "all_traces_complete": inevitable},
                })
                if name == "bad-branch":
                    self.assertFalse(result["completion"]["option_to_complete"])

    def test_completion_uses_global_interleavings_and_reports_unconstrained_type_as_unspecified(self):
        result = self.verify("agent: A\nplaces: ready done\ntransitions: finish\n"
                             "arcs: (ready,finish) (finish,done)\n"
                             "agent: Resource\nplaces: idle\ntransitions: loop\narcs: (idle,loop) (loop,idle)\n"
                             "initial: ready=1 idle=1\nunrestricted final: done=1\n")
        # The resource can loop forever, starving A, despite A's acyclic local net.
        self.assertEqual(result["agent_completion"], {
            "A": {"can_complete": True, "option_to_complete": True, "all_traces_complete": False}, "Resource": None,
        })

    def test_local_completion_can_hold_when_global_exact_final_is_unreachable(self):
        result = self.verify("agent: A\nplaces: ready done\ntransitions: finish\n"
                             "arcs: (ready,finish) (finish,done)\n"
                             "agent: Resource\nplaces: idle\ninitial: ready=1 idle=1\nfinal: done=1\n")
        self.assertEqual(result["completion"], {"option_to_complete": False, "all_traces_complete": False})
        self.assertEqual(result["agent_completion"], {
            "A": {"can_complete": True, "option_to_complete": True, "all_traces_complete": True}, "Resource": None,
        })

    def test_exact_and_partial_local_finals_have_distinct_zero_semantics(self):
        for declaration, holds in [("final: done=1", False), ("unrestricted final: done=1", True)]:
            with self.subTest(declaration=declaration):
                result = self.verify("agent: A\nplaces: ready done\ninitial: ready=1 done=1\n" + declaration)
                self.assertEqual(result["agent_completion"]["A"], {
                    "can_complete": holds, "option_to_complete": holds, "all_traces_complete": holds,
                })

    def test_alternative_local_finals_do_not_mix_global_alternatives(self):
        result = self.verify("agent: A\nplaces: a_done a_cancelled\n"
                             "agent: B\nplaces: b_done b_cancelled\n"
                             "initial: a_done=1 b_done=1\n"
                             "final: a_done=1 b_cancelled=1\nfinal: a_cancelled=1 b_done=1\n")
        self.assertEqual(result["completion"], {"option_to_complete": False, "all_traces_complete": False})
        self.assertEqual(result["agent_completion"], {
            name: {"can_complete": True, "option_to_complete": True, "all_traces_complete": True} for name in ["A", "B"]
        })

    def test_explicit_zero_local_final_and_completion_at_time_zero(self):
        result = self.verify("agent: A\nplaces: p\nagent: B\nplaces: q\nfinal: p=0\n")
        self.assertEqual(result["agent_completion"], {
            "A": {"can_complete": True, "option_to_complete": True, "all_traces_complete": True}, "B": None,
        })
        result = self.verify("agent: A\nplaces: done left\ntransitions: leave\n"
                             "arcs: (done,leave) (leave,left)\ninitial: done=1\nfinal: done=1\n")
        self.assertEqual(result["agent_completion"]["A"], {
            "can_complete": True, "option_to_complete": False, "all_traces_complete": True,
        })

    def test_synchronization_partners_determine_local_completion(self):
        for initial, holds in [("waiting=1 closed=1", False), ("waiting=1 open=1", True)]:
            with self.subTest(initial=initial):
                result = self.verify("agent: Customer\nplaces: waiting served\ntransitions: buy\n"
                                     "labels: buy=serve\narcs: (waiting,buy) (buy,served)\n"
                                     "agent: Clerk\nplaces: open closed\ntransitions: sell\n"
                                     "labels: sell=serve\nread: (open,sell)\n"
                                     f"initial: {initial}\nunrestricted final: served=1\n")
                self.assertEqual(result["agent_completion"], {
                    "Customer": {"can_complete": holds, "option_to_complete": holds, "all_traces_complete": holds}, "Clerk": None,
                })

    def test_pipeline_reports_indexed_local_completion_in_one_property_session(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"):
            source, prefix = Path(tmp) / "source.txt", Path(tmp) / "out"
            source.write_text("agent: Customer\nplaces: waiting[1] done[1]\ntransitions: finish\n"
                              "arcs: (waiting[1],finish) (finish,done[1])\n"
                              "agent: Resource\nplaces: idle\ninitial: waiting[1]=1 idle=1\n"
                              "unrestricted final: waiting[1]=0\n", encoding="utf-8")
            with mock.patch("petrinet.tina.subprocess.run", wraps=subprocess.run) as run:
                status, stdout, stderr = run_cli([str(source), "--agentnet", "--pipeline", str(prefix), "--timeout", "15"])
            self.assertEqual((status, stderr), (0, ""))
            # One dead-transition invocation, then one property graph and Muse session.
            self.assertEqual(run.call_count, 3)
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["agent_completion"], {
                "Customer": {"can_complete": True, "option_to_complete": True, "all_traces_complete": True}, "Resource": None,
            })
            self.assertIn("Agent Customer: can complete from initial marking: yes", stdout)
            self.assertIn("Agent Customer: option to complete from every reachable marking: yes", stdout)
            self.assertIn("Agent Customer: all traces reach a local final (initial marking counts): yes", stdout)
            self.assertIn("Agent Resource: completion not checked: no explicit final declarations.", stdout)

    def test_airport_can_finish_initially_but_every_type_can_lose_completion(self):
        source = Path(__file__).resolve().parent.parent / "examples" / "agents" / "airport.txt"
        result = self.verify(source.read_text(encoding="utf-8"))
        self.assertFalse(result["completion"]["option_to_complete"])
        self.assertEqual(result["agent_completion"], {
            "Aircraft": {"can_complete": True, "option_to_complete": False, "all_traces_complete": False},
            "Crew": {"can_complete": True, "option_to_complete": False, "all_traces_complete": True},
            "Passenger": {"can_complete": True, "option_to_complete": False, "all_traces_complete": False},
        })


if __name__ == "__main__":
    unittest.main()
