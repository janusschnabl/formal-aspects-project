"""Check Tina interoperability and reject incomplete analysis results."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from petrinet import InputArc, Marking, OutputArc, PetriNet, Place, Transition
from petrinet import agentnet, explore, tina
from petrinet.parser import load, parse
from tests.test_conversion import INDEXED, PAIRS
from tests.test_dot_cli import run_cli


ROOT = Path(__file__).resolve().parent.parent
try:
    TINA = tina.resolve_executable()
except tina.TinaError:
    TINA = None


class TinaDiscoveryTests(unittest.TestCase):
    def test_explicit_path_overrides_environment_and_bundled_binary(self):
        with mock.patch.dict(os.environ, {"TINA_BIN": "environment-tina"}):
            self.assertEqual(tina.resolve_executable("explicit-tina"), "explicit-tina")
            self.assertEqual(tina.resolve_executable(), "environment-tina")

    def test_platform_selection_prefers_bundled_binary_over_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory, filename in [("windows-amd64", "tina.exe"), ("linux-amd64", "tina")]:
                path = root / directory / "bin" / filename
                path.parent.mkdir(parents=True)
                path.touch()
            for system, machine, relative in [
                ("win32", "AMD64", "windows-amd64/bin/tina.exe"),
                ("linux", "x86_64", "linux-amd64/bin/tina"),
            ]:
                with self.subTest(system=system), mock.patch.dict(os.environ, {"TINA_BIN": ""}), \
                        mock.patch("petrinet.tina._BUNDLED_ROOT", root), \
                        mock.patch("petrinet.tina.sys.platform", system), \
                        mock.patch("petrinet.tina.platform.machine", return_value=machine), \
                        mock.patch("petrinet.tina.os.access", return_value=True), \
                        mock.patch("petrinet.tina.shutil.which", return_value="path-tina") as which:
                    self.assertEqual(tina.resolve_executable(), str(root / relative))
                    which.assert_not_called()

    def test_other_architectures_use_path_or_report_missing_binary(self):
        with mock.patch.dict(os.environ, {"TINA_BIN": ""}), \
                mock.patch("petrinet.tina.sys.platform", "linux"), \
                mock.patch("petrinet.tina.platform.machine", return_value="aarch64"), \
                mock.patch("petrinet.tina.shutil.which", return_value="installed-tina") as which:
            self.assertEqual(tina.resolve_executable(), "installed-tina")
            which.return_value = None
            with self.assertRaisesRegex(tina.TinaError, "linux/aarch64"):
                tina.resolve_executable()

    def test_linux_execute_permission_is_restored_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "linux-amd64/bin/tina"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with mock.patch.dict(os.environ, {"TINA_BIN": ""}), \
                    mock.patch("petrinet.tina._BUNDLED_ROOT", root), \
                    mock.patch("petrinet.tina.sys.platform", "linux"), \
                    mock.patch("petrinet.tina.platform.machine", return_value="x86_64"), \
                    mock.patch("petrinet.tina.os.access", return_value=False), \
                    mock.patch.object(Path, "chmod") as chmod:
                self.assertEqual(tina.resolve_executable(), str(executable))
                chmod.assert_called_once_with(executable.stat().st_mode | 0o100)
                chmod.side_effect = PermissionError("read-only checkout")
                with self.assertRaisesRegex(tina.TinaError, "cannot make bundled Tina executable"):
                    tina.resolve_executable()


class TinaResultTests(unittest.TestCase):
    def test_dead_names_and_zero_dead_result(self):
        net = PetriNet(set(), {Transition("t[1]"), Transition("live")}, set())
        report = "bounded\n1 dead transition(s), 0 live transition(s)\ndead transition(s): {t[1]}\nANALYSIS COMPLETED ---\n"
        self.assertEqual(tina._parse_result(report.splitlines(), net), [Transition("t[1]")])
        report = "bounded\n0 dead transition(s), 0 live transition(s)\nANALYSIS COMPLETED ---\n"
        self.assertEqual(tina._parse_result(report.splitlines(), net), [])

    def test_incomplete_or_unreadable_results_are_not_verdicts(self):
        net = PetriNet(set(), {Transition("t")}, set())
        reports = [
            "net unbounded\nenumeration not completed\nANALYSIS COMPLETED ---",
            "partial enumeration\nenumeration not completed\nANALYSIS COMPLETED ---",
            "bounded\n0 dead transition(s), 0 live transition(s)",
            "bounded\nANALYSIS COMPLETED ---",
            "bounded\n1 dead transition(s), 0 live transition(s)\nANALYSIS COMPLETED ---",
            "bounded\n1 dead transition(s), 0 live transition(s)\ndead transition(s): unknown\nANALYSIS COMPLETED ---",
        ]
        for report in reports:
            with self.subTest(report=report), self.assertRaises(tina.TinaError):
                tina._parse_result(report.splitlines(), net)

    def test_process_errors_are_not_verdicts(self):
        net = parse("places: p\ntransitions: t\narcs: (p, t)")
        for error in [FileNotFoundError(), subprocess.TimeoutExpired("tina", 1)]:
            with self.subTest(error=error), mock.patch("petrinet.tina.subprocess.run", side_effect=error):
                with self.assertRaises(tina.TinaError):
                    tina.dead_transitions(net, timeout=1)
        for code in [0, 1]:
            with self.subTest(code=code), mock.patch(
                "petrinet.tina.subprocess.run", return_value=subprocess.CompletedProcess([], code, "", "failed")
            ):
                with self.assertRaises(tina.TinaError):
                    tina.dead_transitions(net)

    def test_control_characters_are_rejected(self):
        for name in ["p\ntr injected", "p\x00", "p\r"]:
            with self.subTest(name=name), self.assertRaises(tina.TinaError):
                tina.to_tina(PetriNet({Place(name)}, set(), set()))


class TinaCliTests(unittest.TestCase):
    def test_default_checker_uses_tina_without_backend_flags(self):
        with mock.patch("petrinet.__main__.tina.dead_transitions", return_value=[]) as check:
            self.assertEqual(run_cli([str(ROOT / "examples/two_dead.txt"), "--timeout", "10"]),
                             (0, "No dead transitions.\n", ""))
            self.assertEqual(check.call_args.args[1:], (None, 10))

    def test_selecting_tina_passes_executable_and_timeout(self):
        with mock.patch("petrinet.__main__.tina.dead_transitions", return_value=[Transition("t3")]) as check:
            status, out, err = run_cli([
                str(ROOT / "examples/two_dead.txt"), "--tina", "C:/tools/tina.exe", "--timeout", "5"
            ])
        self.assertEqual((status, out, err), (0, "Dead transitions:\n  t3\n", ""))
        self.assertEqual(check.call_args.args[1:], ("C:/tools/tina.exe", 5))

    def test_tina_export_converts_agent_input_and_skips_analysis(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("petrinet.__main__.tina.dead_transitions") as check:
            output = Path(tmp) / "example.net"
            self.assertEqual(run_cli([
                str(ROOT / "examples/agents/example.txt"), "--agentnet", "--emit-tina", str(output)
            ]), (0, "", ""))
            self.assertIn("tr {dukOp} {hjem1}*1 {hjem2}*1 -> {uni1}*1 {uni2}*1", output.read_text(encoding="utf-8"))
            check.assert_not_called()

    def test_default_tina_errors_are_reported_with_exit_two(self):
        with mock.patch("petrinet.__main__.tina.dead_transitions", side_effect=tina.TinaError("incomplete analysis")):
            self.assertEqual(run_cli([str(ROOT / "examples/two_dead.txt")]),
                             (2, "", "error: incomplete analysis\n"))

    def test_removed_backend_flags_and_conflicting_exports_are_rejected(self):
        for flags in [
            ["--builtin"], ["--lola", "unused"],
            ["--emit-tina", "-", "--dot", "-"],
            ["--emit-tina", "same.net", "--dot", "./same.net"],
            ["--render", "example", "--emit-tina", "example.net"],
        ]:
            with self.subTest(flags=flags):
                status, out, err = run_cli(["unused.txt", *flags])
                self.assertEqual((status, out), (2, ""))
                self.assertTrue(err)


@unittest.skipUnless(TINA, "No bundled or installed Tina; set TINA_BIN to its executable")
class RealTinaTests(unittest.TestCase):
    def test_cli_automatically_selects_a_binary(self):
        self.assertEqual(run_cli([str(ROOT / "examples/two_dead.txt"), "--timeout", "15"]),
                         (0, "Dead transitions:\n  t3\n  t4\n", ""))

    def test_cli_agent_models_and_export_work_without_backend_flags(self):
        for name in ["example.txt", "shop.txt"]:
            path = ROOT / "examples/agents" / name
            with self.subTest(name=name):
                self.assertEqual(run_cli([str(path), "--agentnet", "--timeout", "15"]),
                                 (0, "No dead transitions.\n", ""))
                with tempfile.TemporaryDirectory() as tmp:
                    output = Path(tmp) / "model.net"
                    self.assertEqual(run_cli([str(path), "--agentnet", "--emit", str(output)]), (0, "", ""))
                    parsed = subprocess.run([TINA, "-NET", "-p", str(output)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_converted_keyword_names_work_with_tina(self):
        source = agentnet.parse(
            "agent: A\n places: PLACE TRUE\n transitions: EF\n labels: EF=AG\n"
            "arcs: (PLACE, EF) (EF, TRUE)\n initial: PLACE=1\n final: TRUE=1\n"
        )
        converted = agentnet.convert(source)
        self.assertEqual(tina.dead_transitions(converted.net, timeout=15), [])
        self.assertEqual(set(converted.place_map), {"PLACE", "TRUE"})

    def test_examples_agree_with_independent_builtin_exploration(self):
        # The producer/consumer example has an unbounded buffer; tested below.
        nets = [load(ROOT / "examples" / name) for name in ["simple.txt", "two_dead.txt"]]
        nets += [agentnet.convert(agentnet.load(path)).net for path in sorted((ROOT / "examples/agents").glob("*.txt"))]
        nets += [agentnet.convert(agentnet.parse(text)).net for text in [INDEXED, PAIRS]]
        for i, net in enumerate(nets):
            with self.subTest(net=i):
                self.assertEqual(tina.dead_transitions(net, TINA, timeout=15), explore.dead_transitions(net))

    def test_dead_indexed_and_escaped_identifiers(self):
        for name in ["step[1][-2]", "step.with.dot", "a b", r"a{b}\c", "t'", "tr", "PLACE", "TRUE", "AG", "oo"]:
            with self.subTest(name=name):
                p, q, t = Place("p[1]"), Place("q[2]"), Transition(name)
                for initial, expected in [(Marking(), [t]), (Marking({p: 2}), [])]:
                    net = PetriNet({p, q}, {t}, {InputArc(p, t, 2), OutputArc(t, q)}, initial)
                    self.assertEqual(tina.dead_transitions(net, TINA, timeout=15), expected)

    def test_unbounded_is_an_error(self):
        nets = [parse("places: p\ntransitions: source\narcs: (source, p)"),
                load(ROOT / "examples/producer_consumer.txt")]
        for net in nets:
            with self.subTest(net=net), self.assertRaisesRegex(tina.TinaError, "unbounded"):
                tina.dead_transitions(net, TINA, timeout=15)

    def test_transition_without_arcs_and_empty_net(self):
        self.assertEqual(tina.dead_transitions(PetriNet(set(), {Transition("t")}, set()), TINA, timeout=15), [])
        self.assertEqual(tina.dead_transitions(PetriNet(set(), set(), set()), TINA, timeout=15), [])


if __name__ == "__main__":
    unittest.main()
