from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from xml.etree import ElementTree

from petrinet import Marking, PetriNet, PetriNetError, Place, Transition
from petrinet import agentnet
from petrinet.__main__ import main
from petrinet.dot import GraphvizError, render_svg, to_agent_dot, to_dot
from tests.test_conversion import EXAMPLES, INDEXED, PAIRS


EXAMPLE_PIPELINE_OUTPUT = """Global verification
-------------------
  Dead converted transitions: 0
  Reachable deadlock: yes
  Reachable non-final deadlock: no
  Option to complete from every reachable marking: yes
  All traces eventually reach a final marking: yes

Agent verification
------------------
  The initial marking may already satisfy an agent type's local final condition.
  Janus
    Can complete from initial marking: yes
    Option to complete from every reachable marking: yes
    All traces reach a local final: yes
    Reachable deadlock: no

  Thomas
    Can complete from initial marking: yes
    Option to complete from every reachable marking: yes
    All traces reach a local final: yes
    Reachable deadlock: no

Soundness
---------
  Soundness: yes
  [PASS] Global option to complete
  [PASS] No per-type deadlocks
  [PASS] No dead source activities
"""


def run_cli(args):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            status = main(args)
        except SystemExit as e:
            status = e.code
    return status, stdout.getvalue(), stderr.getvalue()


class DotTests(unittest.TestCase):
    def test_standard_diagram_preserves_names_weights_and_acceptance(self):
        result = agentnet.convert(agentnet.parse(INDEXED))
        labels = {t: origin.display_name for t, origin in result.transition_origins.items()}
        text = to_dot(result.net, transition_labels=labels)
        self.assertIn('shape="circle"', text)
        self.assertIn('shape="box"', text)
        self.assertIn('label="p[-1][north]\\n2"', text)
        self.assertIn('label="p[0][north]"', text)
        self.assertIn('label="2"', text)
        self.assertIn('label="go[-1][north]\\nA.move[-1][north]=2"', text)
        self.assertIn('Final markings (unlisted places = 0):\\n{p[0][north]=2}\\n{}', text)
        self.assertEqual(text, to_dot(result.net, transition_labels=labels))

    def test_raw_diagram_preserves_agents_locals_reads_and_stars(self):
        source = agentnet.load(os.path.join(EXAMPLES, "shop.txt"))
        raw = to_agent_dot(source)
        self.assertIn("subgraph cluster_a0", raw)
        self.assertIn("subgraph cluster_a1", raw)
        self.assertIn('label="Customer"', raw)
        self.assertIn('label="Clerk"', raw)
        self.assertIn('label="buy[1]\\nlabel: serve[1]"', raw)
        self.assertIn('label="sell[1]\\nlabel: serve[1]"', raw)
        self.assertIn('style="dashed", label="read"', raw)
        self.assertIn('label="*"', raw)
        self.assertIn('label="open[1]\\n1"', raw)
        self.assertIn("Same non-silent label = synchronized firing", raw)
        self.assertIn("home=3", raw)
        self.assertNotIn("__k_", raw)
        self.assertEqual(raw, to_agent_dot(source))

    def test_raw_indexed_names_are_identical(self):
        source = agentnet.parse(INDEXED)
        raw = to_agent_dot(source)
        for name in source.places:
            self.assertIn(f'label="{name}', raw)
        self.assertIn("move[-1][north]", raw)

    def test_empty_and_isolated_nodes_are_kept(self):
        self.assertTrue(to_dot(PetriNet(set(), set(), set())).startswith("digraph net {"))
        net = PetriNet({Place("alone")}, {Transition("idle")}, set())
        self.assertIn('label="alone"', to_dot(net))
        self.assertIn('label="idle"', to_dot(net))
        source = agentnet.parse("agent: A\n places: p\n transitions: t")
        self.assertIn('label="t\\nsilent"', to_agent_dot(source))

    def test_quotes_backslashes_and_newlines_are_escaped(self):
        p = Place('p"\\\nq')
        text = to_dot(PetriNet({p}, set(), set()))
        self.assertIn('label="p\\"\\\\\\nq"', text)

    def test_diagram_can_show_another_marking_without_changing_the_net(self):
        p = Place("p")
        net = PetriNet({p}, set(), set())
        self.assertIn('label="p\\n3"', to_dot(net, marking=Marking({p: 3})))
        self.assertEqual(net.initial_marking[p], 0)
        with self.assertRaises(PetriNetError):
            to_dot(net, marking=Marking({Place("outside"): 1}))


class CliTests(unittest.TestCase):
    def test_pipeline_reuses_one_parse_and_conversion_for_exports_and_check(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"), \
                mock.patch("petrinet.__main__.agentnet.load", wraps=agentnet.load) as load, \
                mock.patch("petrinet.__main__.agentnet.convert", wraps=agentnet.convert) as convert, \
                mock.patch("petrinet.__main__.tina.dead_transitions", return_value=[]) as check, \
                mock.patch("petrinet.__main__.tina.verify_properties", return_value={
                    "completion": {"option_to_complete": True, "all_traces_complete": True},
                    "deadlocks": {"reachable": True, "non_final_reachable": False}, "properties": {},
                    "agent_completion": {name: {"can_complete": True, "option_to_complete": True, "all_traces_complete": True}
                                         for name in ["Janus", "Thomas"]},
                    "agent_deadlocks": {"Janus": False, "Thomas": False},
                }) as verification:
            prefix = Path(tmp) / "new folder" / "example"
            def verify_original_before_check(net, *args):
                self.assertTrue(Path(f"{prefix}.net").exists())
                self.assertTrue(Path(f"{prefix}.svg").exists())
                self.assertFalse(Path(f"{prefix}-pruned.net").exists())
                return []
            check.side_effect = verify_original_before_check
            self.assertEqual(run_cli([os.path.join(EXAMPLES, "example.txt"), "--agentnet",
                                      "--pipeline", str(prefix), "--timeout", "10"]),
                             (0, EXAMPLE_PIPELINE_OUTPUT, ""))
            load.assert_called_once()
            convert.assert_called_once()
            check.assert_called_once()
            verification.assert_called_once_with(check.call_args.args[0], None, 10,
                agent_final_clauses={
                    "Janus": ({Place("hjem1"): 0, Place("uni1"): 1},),
                    "Thomas": ({Place("hjem2"): 0, Place("uni2"): 1},),
                }, agent_transitions={
                    "Janus": frozenset([Transition("dukOp")]),
                    "Thomas": frozenset([Transition("dukOp")]),
                })
            from petrinet.tina import to_tina
            self.assertEqual(Path(f"{prefix}.net").read_text(encoding="utf-8"), to_tina(check.call_args.args[0]))
            # Without removals, pruning must retain agent colors and labels.
            self.assertEqual(Path(f"{prefix}-pruned.dot").read_text(encoding="utf-8"),
                             Path(f"{prefix}.dot").read_text(encoding="utf-8"))
            self.assertEqual(check.call_args.args[1:], (None, 10))
            self.assertEqual(len(list(prefix.parent.iterdir())), 9)
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["removed_transitions"], [])
            self.assertEqual(report["before"], report["after"])
            self.assertTrue(report["soundness"])
            self.assertEqual(report["dead_activities"], [])
            for section, verdicts in verification.return_value.items():
                self.assertEqual(report[section], verdicts)

    def test_failed_check_overwrites_previous_success_report(self):
        from petrinet.tina import TinaError
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"), \
                mock.patch("petrinet.__main__.tina.dead_transitions", side_effect=[[], TinaError("timed out")]), \
                mock.patch("petrinet.__main__.tina.verify_properties", return_value={
                    "completion": {"option_to_complete": True, "all_traces_complete": True},
                    "deadlocks": {"reachable": True, "non_final_reachable": False}, "properties": {},
                    "agent_completion": {}, "agent_deadlocks": {},
                }):
            prefix = Path(tmp) / "example"
            args = [os.path.join(EXAMPLES, "example.txt"), "--agentnet", "--pipeline", str(prefix)]
            self.assertEqual(run_cli(args)[0], 0)
            self.assertEqual(run_cli(args), (2, "", "error: timed out\n"))
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertIsNone(report["removed_transitions"])
            self.assertEqual(report["error"], "timed out")

    def test_parse_failure_cannot_leave_previous_report_claiming_success(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"), \
                mock.patch("petrinet.__main__.tina.dead_transitions", return_value=[]):
            source = Path(tmp) / "source.txt"
            source.write_text("places: p\ntransitions: t\narcs: (p, t)\n", encoding="utf-8")
            prefix = Path(tmp) / "output" / "example"
            args = [str(source), "--pipeline", str(prefix)]
            self.assertEqual(run_cli(args)[0], 0)
            source.write_text("not a valid net", encoding="utf-8")
            self.assertEqual(run_cli(args)[0], 2)
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertIsNone(report["removed_transitions"])
            self.assertIsNone(report["soundness"])
            self.assertIsNone(report["dead_activities"])

    def test_pruned_render_failure_leaves_original_and_records_failure(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.__main__.render_svg", side_effect=["<svg/>", GraphvizError("pruned render failed")]), \
                mock.patch("petrinet.__main__.tina.dead_transitions", return_value=[Transition("t3"), Transition("t4")]):
            prefix = Path(tmp) / "example"
            source = os.path.join(os.path.dirname(__file__), "nets", "two_dead.txt")
            self.assertEqual(run_cli([source, "--pipeline", str(prefix)])[0], 2)
            self.assertTrue(Path(f"{prefix}.svg").exists())
            self.assertFalse(Path(f"{prefix}-pruned.net").exists())
            report = json.loads(Path(f"{prefix}-results.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertIsNone(report["removed_transitions"])

    def test_raw_render_skips_conversion_and_analysis(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.__main__.render_svg", return_value="<svg/>"), \
                mock.patch("petrinet.__main__.agentnet.convert") as convert, \
                mock.patch("petrinet.__main__.tina.dead_transitions") as check:
            source = Path(tmp) / "unbalanced.txt"
            source.write_text("agent: A\n places: p q\n transitions: t\n arcs: (p, t, 2) (t, q)\n", encoding="utf-8")
            prefix = Path(tmp) / "out" / "example"
            self.assertEqual(run_cli([str(source), "--agentnet", "--raw-render", str(prefix)]), (0, "", ""))
            convert.assert_not_called()
            check.assert_not_called()
            self.assertEqual(sorted(p.name for p in prefix.parent.iterdir()), ["example-agent.dot", "example-agent.svg"])

    def test_render_mode_conflicts_are_rejected(self):
        for flags in [["--raw-render", "example"], ["--agentnet", "--raw-render", "example", "--emit", "-"],
                      ["--pipeline", "example", "--render", "other"],
                      ["--pipeline", "example", "--emit", "-"],
                      ["--pipeline", "example", "--raw-render", "other"]]:
            with self.subTest(flags=flags):
                self.assertEqual(run_cli(["unused.txt", *flags])[0], 2)

    def test_pipeline_render_failure_skips_check_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.__main__.render_svg", side_effect=GraphvizError("render failed")), \
                mock.patch("petrinet.__main__.tina.dead_transitions") as check:
            prefix = Path(tmp) / "out" / "example"
            self.assertEqual(run_cli([os.path.join(EXAMPLES, "example.txt"), "--agentnet", "--pipeline", str(prefix)]),
                             (2, "", "error: render failed\n"))
            self.assertFalse(prefix.parent.exists())
            check.assert_not_called()

    def test_render_pipeline_writes_all_five_files_with_one_conversion(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.dot.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "<svg/>", "")) as render, \
                mock.patch("petrinet.__main__.agentnet.convert", wraps=agentnet.convert) as convert, \
                mock.patch("petrinet.__main__.tina.dead_transitions") as check:
            prefix = Path(tmp) / "new folder" / "example"
            status, out, err = run_cli([
                os.path.join(EXAMPLES, "example.txt"), "--agentnet", "--render", str(prefix),
                "--graphviz", "path with spaces/dot",
            ])
            self.assertEqual((status, out, err), (0, "", ""))
            convert.assert_called_once()
            check.assert_not_called()
            self.assertEqual(render.call_count, 2)
            self.assertEqual(render.call_args.args[0], ["path with spaces/dot", "-Tsvg"])
            self.assertNotIn("shell", render.call_args.kwargs)
            self.assertIn("label: dukOp", render.call_args.kwargs["input"])
            self.assertEqual(Path(f"{prefix}.svg").read_text(encoding="utf-8"), "<svg/>")
            self.assertEqual(Path(f"{prefix}-agent.svg").read_text(encoding="utf-8"), "<svg/>")
            self.assertIn("tr {dukOp}", Path(f"{prefix}.net").read_text(encoding="utf-8"))
            self.assertIn("digraph net", Path(f"{prefix}.dot").read_text(encoding="utf-8"))
            self.assertIn("cluster_a0", Path(f"{prefix}-agent.dot").read_text(encoding="utf-8"))

    def test_render_pipeline_also_handles_standard_nets(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.dot.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "<svg/>", "")) as render:
            prefix = Path(tmp) / "standard"
            path = os.path.join(os.path.dirname(__file__), "nets", "two_dead.txt")
            self.assertEqual(run_cli([path, "--render", str(prefix)]), (0, "", ""))
            render.assert_called_once()
            self.assertEqual(len(list(Path(tmp).iterdir())), 3)

    def test_render_failure_does_not_overwrite_existing_exports(self):
        path = os.path.join(EXAMPLES, "example.txt")
        for error in [FileNotFoundError(), subprocess.TimeoutExpired("dot", 60)]:
            with self.subTest(error=type(error)), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch("petrinet.dot.subprocess.run", side_effect=error):
                prefix = Path(tmp) / "example"
                output = Path(f"{prefix}.net")
                output.write_text("keep existing", encoding="utf-8")
                status, out, err = run_cli([path, "--agentnet", "--render", str(prefix)])
                self.assertEqual((status, out), (2, ""))
                self.assertIn("Graphviz", err)
                self.assertEqual(output.read_text(encoding="utf-8"), "keep existing")
                self.assertEqual(len(list(Path(tmp).iterdir())), 1)

    def test_render_cannot_be_combined_with_individual_exports(self):
        for flag in ["--emit", "--dot", "--raw-dot"]:
            with self.subTest(flag=flag):
                status, out, err = run_cli(["unused.txt", "--agentnet", "--render", "example", flag, "-"])
                self.assertEqual((status, out), (2, ""))
                self.assertIn("cannot be combined", err)

    def test_combined_exports_parse_and_convert_once_without_analysis(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.__main__.agentnet.load", wraps=agentnet.load) as load, \
                mock.patch("petrinet.__main__.agentnet.convert", wraps=agentnet.convert) as convert, \
                mock.patch("petrinet.__main__.tina.dead_transitions") as check:
            tina_file, dot_file, raw_file = [Path(tmp) / name for name in ("shop.net", "shop.dot", "shop-agent.dot")]
            status, out, err = run_cli([
                os.path.join(EXAMPLES, "shop.txt"), "--agentnet", "--emit", str(tina_file),
                "--dot", str(dot_file), "--raw-dot", str(raw_file),
            ])
            self.assertEqual((status, out, err), (0, "", ""))
            load.assert_called_once()
            convert.assert_called_once()
            check.assert_not_called()
            self.assertEqual(tina_file.read_text(encoding="utf-8").count("tr "), 12)
            self.assertIn('label="open[1]\\n1"', dot_file.read_text(encoding="utf-8"))
            self.assertIn("Customer.enter=3", dot_file.read_text(encoding="utf-8"))
            self.assertIn('label="*"', raw_file.read_text(encoding="utf-8"))

    def test_raw_only_does_not_convert_or_require_well_formedness(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("petrinet.__main__.agentnet.convert") as convert:
            path = Path(tmp) / "unbalanced.txt"
            path.write_text("agent: A\n places: p q\n transitions: t\n arcs: (p, t, 2) (t, q)\n", encoding="utf-8")
            status, out, err = run_cli([str(path), "--agentnet", "--raw-dot", "-"])
            self.assertEqual((status, err), (0, ""))
            self.assertIn('label="2"', out)
            convert.assert_not_called()

    def test_stdout_exports(self):
        for flag in ["--emit", "--dot", "--raw-dot"]:
            with self.subTest(flag=flag):
                status, out, err = run_cli([os.path.join(EXAMPLES, "shop.txt"), "--agentnet", flag, "-"])
                self.assertEqual((status, err), (0, ""))
                self.assertTrue(out.startswith("pl " if flag == "--emit" else "digraph net {"))

    def test_argument_errors(self):
        for flags, message in [
            (["--raw-dot", "-"], "requires --agentnet"),
            (["--emit", "-", "--dot", "-"], "at most one"),
            (["--emit", "same.dot", "--dot", "./same.dot"], "distinct destinations"),
        ]:
            with self.subTest(flags=flags):
                status, out, err = run_cli(["unused.txt", *flags])
                self.assertEqual((status, out), (2, ""))
                self.assertIn(message, err)

    def test_input_conversion_and_output_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.txt"
            cases = [
                ("agent: A\n places: p[1,a]", ["--dot", "-"], "line 2"),
                ("agent: A\n places: p q\n transitions: t\n arcs: (p, t, 2) (t, q)",
                 ["--dot", "-"], "does not preserve agents"),
                (PAIRS, ["--dot", str(Path(tmp) / "missing" / "net.dot")], "error:"),
            ]
            for text, flags, message in cases:
                with self.subTest(message=message):
                    path.write_text(text, encoding="utf-8")
                    status, out, err = run_cli([str(path), "--agentnet", *flags])
                    self.assertEqual((status, out), (2, ""))
                    self.assertIn(message, err)
            self.assertEqual(run_cli([str(Path(tmp) / "absent.txt"), "--agentnet", "--dot", "-"])[0], 2)

    def test_empty_conversion_can_be_drawn_and_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.txt"
            path.write_text("", encoding="utf-8")
            status, out, err = run_cli([str(path), "--agentnet", "--dot", "-"])
            self.assertEqual((status, err), (0, ""))
            self.assertIn("digraph net {", out)
            self.assertEqual(run_cli([str(path), "--agentnet", "--emit", "-"]), (0, "\n", ""))

    def test_standard_net_export(self):
        path = os.path.join(os.path.dirname(__file__), "nets", "two_dead.txt")
        status, out, err = run_cli([path, "--dot", "-"])
        self.assertEqual((status, err), (0, ""))
        self.assertIn('label="p1\\n1"', out)

    def test_converted_net_uses_tina_by_default(self):
        path = os.path.join(EXAMPLES, "shop.txt")
        with mock.patch("petrinet.__main__.tina.dead_transitions", return_value=[]) as check:
            self.assertEqual(run_cli([path, "--agentnet"]), (0, "No dead transitions.\n", ""))
            self.assertEqual(len(check.call_args.args[0].transitions), 12)


@unittest.skipUnless(shutil.which("dot"), "Graphviz dot not on PATH")
class RealGraphvizTests(unittest.TestCase):
    def test_full_pipeline_draws_both_models_and_checks_with_real_tina(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "output folder" / "example"
            self.assertEqual(run_cli([os.path.join(EXAMPLES, "example.txt"), "--agentnet",
                                      "--pipeline", str(prefix), "--timeout", "15"]),
                             (0, EXAMPLE_PIPELINE_OUTPUT, ""))
            for suffix in [".svg", "-agent.svg"]:
                self.assertEqual(ElementTree.parse(f"{prefix}{suffix}").getroot().tag,
                                 "{http://www.w3.org/2000/svg}svg")
            standard = os.path.join(os.path.dirname(__file__), "nets", "two_dead.txt")
            self.assertEqual(run_cli([standard, "--pipeline", str(Path(tmp) / "two_dead")]),
                             (0, "Global verification\n-------------------\n"
                              "  Dead transitions: 2\n    t3\n    t4\n"
                              "  Reachable deadlock: no\n  Reachable non-final deadlock: no\n"
                              "  Completion not checked: no final declarations.\n", ""))
            report = json.loads((Path(tmp) / "two_dead-results.json").read_text())
            self.assertEqual(report["removed_transitions"], ["t3", "t4"])
            self.assertEqual(report["before"]["transitions"], 4)
            self.assertEqual(report["after"]["transitions"], 2)
            self.assertIsNone(report["completion"])
            pruned_text = (Path(tmp) / "two_dead-pruned.net").read_text()
            self.assertIn("tr {t1}", pruned_text)
            self.assertNotIn("tr {t3}", pruned_text)
            self.assertNotIn("tr {t4}", pruned_text)
            self.assertEqual(ElementTree.parse(Path(tmp) / "two_dead-pruned.svg").getroot().tag,
                             "{http://www.w3.org/2000/svg}svg")
            all_dead_source = Path(EXAMPLES).parent / "simple.txt"
            self.assertEqual(run_cli([str(all_dead_source), "--pipeline", str(Path(tmp) / "all_dead")])[0], 0)
            report = json.loads((Path(tmp) / "all_dead-results.json").read_text())
            self.assertEqual(report["after"]["transitions"], 0)
            self.assertEqual(report["after"]["arcs"], 0)
            self.assertNotIn("tr ", (Path(tmp) / "all_dead-pruned.net").read_text())

    def test_cli_pipeline_creates_both_svgs(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "folder with spaces" / "example"
            status, out, err = run_cli([
                os.path.join(EXAMPLES, "example.txt"), "--agentnet", "--render", str(prefix),
            ])
            self.assertEqual((status, out, err), (0, "", ""))
            self.assertEqual(len(list(prefix.parent.iterdir())), 5)
            for svg in [Path(f"{prefix}.svg"), Path(f"{prefix}-agent.svg")]:
                self.assertEqual(ElementTree.parse(svg).getroot().tag, "{http://www.w3.org/2000/svg}svg")

    def test_converted_and_raw_diagrams_render_as_svg(self):
        source = agentnet.load(os.path.join(EXAMPLES, "shop.txt"))
        result = agentnet.convert(source)
        for text in [to_agent_dot(source), to_dot(result.net),
                     to_dot(PetriNet({Place('p"\\\nq')}, set(), set()))]:
            with self.subTest(text=text[:80]):
                proc = subprocess.run(["dot", "-Tsvg"], input=text, capture_output=True, text=True, timeout=20)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(ElementTree.fromstring(proc.stdout).tag, "{http://www.w3.org/2000/svg}svg")


class GraphvizDriverTests(unittest.TestCase):
    def test_graphviz_errors_include_its_diagnostics(self):
        with mock.patch("petrinet.dot.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "syntax error")):
            with self.assertRaisesRegex(GraphvizError, "syntax error"):
                render_svg("invalid DOT")


if __name__ == "__main__":
    unittest.main()
