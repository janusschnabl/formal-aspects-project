"""Dependency-free Graphviz DOT export of standard and raw multi-agent nets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import subprocess

from .agentnet.model import MultiAgentNet
from .net import Marking, PetriNet, PetriNetError, Place, Transition


class GraphvizError(RuntimeError):
    """Graphviz could not render the supplied DOT."""


def render_svg(dot: str, *, graphviz_bin: str = "dot") -> str:
    """Render DOT with the external Graphviz executable, without using a shell."""
    try:
        result = subprocess.run(
            [graphviz_bin, "-Tsvg"], input=dot, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )
    except FileNotFoundError:
        raise GraphvizError(
            f"Graphviz executable {graphviz_bin!r} not found; install Graphviz and add its bin "
            "directory to PATH, or specify its executable with --graphviz PATH"
        ) from None
    except subprocess.TimeoutExpired:
        raise GraphvizError("Graphviz rendering timed out after 60 seconds") from None
    if result.returncode != 0:
        raise GraphvizError(f"Graphviz rendering failed: {result.stderr.strip() or 'exit code ' + str(result.returncode)}")
    return result.stdout


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _attributes(**values: str) -> str:
    return ", ".join(f"{key}={_quote(value)}" for key, value in values.items())


class _DotWriter:
    def __init__(self) -> None:
        self.lines = ["digraph net {", "  rankdir=LR;", '  node [fontname="Helvetica"];']

    def node(self, node_id: str, **attributes: str) -> None:
        self.lines.append(f"  {node_id} [{_attributes(**attributes)}];")

    def edge(self, source: str, target: str, **attributes: str) -> None:
        self.lines.append(f"  {source} -> {target} [{_attributes(**attributes)}];")

    def finish(self, legend: str) -> str:
        self.lines.append(f"  graph [{_attributes(label=legend, labelloc='b')}];")
        return "\n".join([*self.lines, "}", ""])


def _finals_legend(finals: Iterable[Mapping[str, int]]) -> str:
    entries = sorted({
        "{" + ", ".join(f"{p}={n}" for p, n in sorted(m.items()) if n) + "}"
        for m in finals
    })
    if not entries:
        return "Final markings: none specified"
    return "Final markings (unlisted places = 0):\n" + "\n".join(entries)


def to_dot(
    net: PetriNet,
    *,
    marking: Marking | None = None,
    place_labels: Mapping[Place, str] | None = None,
    transition_labels: Mapping[Transition, str] | None = None,
) -> str:
    """Draw every node and arc, using the initial marking unless overridden.

    Labels may annotate nodes without changing model names. For a conversion,
    pass {t: origin.display_name for t, origin in result.transition_origins.items()}.
    """
    if marking is None:
        marking = net.initial_marking
    if not isinstance(marking, Marking) or not marking.marked_places() <= net.places:
        raise PetriNetError("diagram marking must be a Marking over the net's places")
    place_labels = {} if place_labels is None else place_labels
    transition_labels = {} if transition_labels is None else transition_labels
    writer = _DotWriter()
    ids: dict[Place | Transition, str] = {}
    for i, p in enumerate(sorted(net.places)):
        ids[p] = f"p{i}"
        writer.node(ids[p], shape="circle", label=str(marking[p]) if marking[p] else "",
                    xlabel=place_labels.get(p, p.name))
    for i, t in enumerate(sorted(net.transitions)):
        ids[t] = f"t{i}"
        writer.node(ids[t], shape="box", label=transition_labels.get(t, t.name))
    for arc in sorted(net.arcs, key=lambda a: (a.source.name, a.target.name)):
        writer.edge(ids[arc.source], ids[arc.target], label=str(arc.weight) if arc.weight > 1 else "")
    finals = ({p.name: m[p] for p in net.places if m[p]} for m in net.final_markings)
    return writer.finish(_finals_legend(finals))


def to_agent_dot(net: MultiAgentNet) -> str:
    """Draw source agent groups and local transitions without converting them.

    This also works before conservation/variable-pair well-formedness checks.
    Shared labels denote synchronization, not extra arcs between agent nets.
    """
    writer = _DotWriter()
    ids: dict[str, str] = {}
    for i, agent in enumerate(sorted(net.agents.values(), key=lambda a: a.name)):
        writer.lines.append(f"  subgraph cluster_a{i} {{")
        writer.lines.append(f"    label={_quote(agent.name)};")
        for j, p in enumerate(sorted(agent.places)):
            ids[p] = f"a{i}_p{j}"
            count = net.initial.get(p, 0)
            writer.node(ids[p], shape="circle", label=str(count) if count else "", xlabel=p)
        for j, t in enumerate(sorted(agent.transitions)):
            ids[t] = f"a{i}_t{j}"
            label = agent.label(t)
            annotation = "silent" if label is None else f"label: {label}"
            writer.node(ids[t], shape="box", label=f"{t}\n{annotation}")
        for (src, dst), weight in sorted(agent.arcs.items()):
            writer.edge(ids[src], ids[dst], label="" if weight == 1 else str(weight))
        for p, t in sorted(agent.read_arcs):
            writer.edge(ids[p], ids[t], style="dashed", label="read")
        writer.lines.append("  }")
    legend = "Same non-silent label = synchronized firing across agents.\nDashed read arc: requires a token without consuming it."
    return writer.finish(legend + "\n" + _finals_legend(net.finals))
