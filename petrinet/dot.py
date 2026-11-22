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


# (border, fill) per agent or place group, in order of first appearance.
_PALETTE = [
    ("#3f6fa8", "#dce8f5"),  # blue
    ("#a8792a", "#f7e6bd"),  # gold
    ("#2a8a70", "#d3efe5"),  # teal
    ("#7d55a8", "#e8ddf4"),  # purple
    ("#b0503f", "#f6dcd6"),  # red
    ("#5d6b78", "#e2e6ea"),  # slate
]
_NEUTRAL = ("#444444", "#eeeeee")


def _group_colors(names: Iterable[str]) -> dict[str, tuple[str, str]]:
    colors: dict[str, tuple[str, str]] = {}
    for name in names:
        if name not in colors:
            colors[name] = _PALETTE[len(colors) % len(_PALETTE)]
    return colors


def _final_place_names(finals: Iterable[Mapping[str, int]], unrestricted: Iterable[Mapping[str, int]]) -> set[str]:
    """Places that hold tokens in some final declaration; drawn as double circles."""
    return {p for m in [*finals, *unrestricted] for p, n in m.items() if n}


def _place_style(final: bool, colors: tuple[str, str]) -> dict[str, str]:
    border, fill = colors
    return {"shape": "doublecircle" if final else "circle", "color": border, "fillcolor": fill,
            "style": "filled", "fixedsize": "false", "width": "0.6", "margin": "0.03"}


def _transition_style(colors: tuple[str, str]) -> dict[str, str]:
    border, fill = colors
    return {"shape": "box", "color": border, "fillcolor": fill, "style": "filled",
            "height": "0.3", "fontsize": "9", "margin": "0.06,0.03"}


class _DotWriter:
    def __init__(self) -> None:
        self.lines = [
            "digraph net {",
            "  rankdir=LR;",
            '  graph [fontname="Helvetica", fontsize="11", nodesep="0.2", ranksep="0.35", pad="0.2"];',
            '  node [fontname="Helvetica", fontsize="10", penwidth="1.3"];',
            '  edge [fontname="Helvetica", fontsize="9", arrowsize="0.6", color="#555555"];',
        ]

    def node(self, node_id: str, **attributes: str) -> None:
        self.lines.append(f"  {node_id} [{_attributes(**attributes)}];")

    def edge(self, source: str, target: str, **attributes: str) -> None:
        self.lines.append(f"  {source} -> {target} [{_attributes(**attributes)}];")

    def finish(self, legend: str) -> str:
        self.lines.append(f"  graph [{_attributes(label=legend, labelloc='b')}];")
        return "\n".join([*self.lines, "}", ""])


def _finals_legend(finals: Iterable[Mapping[str, int]], unrestricted: Iterable[Mapping[str, int]] = ()) -> str:
    entries = sorted({
        "{" + ", ".join(f"{p}={n}" for p, n in sorted(m.items()) if n) + "}"
        for m in finals
    })
    unrestricted_entries = sorted({
        "{" + ", ".join(f"{p}={n}" for p, n in sorted(m.items())) + "}"
        for m in unrestricted
    })
    if not entries and not unrestricted_entries:
        return "Final markings: none specified"
    sections = []
    if entries:
        sections.append("Final markings (unlisted places = 0):\n" + "\n".join(entries))
    if unrestricted_entries:
        sections.append("Unrestricted final markings (unlisted places = any):\n" + "\n".join(unrestricted_entries))
    return "\n".join(sections)


def to_dot(
    net: PetriNet,
    *,
    marking: Marking | None = None,
    place_labels: Mapping[Place, str] | None = None,
    transition_labels: Mapping[Transition, str] | None = None,
    place_groups: Mapping[Place, str] | None = None,
) -> str:
    """Draw every node and arc, using the initial marking unless overridden.

    Labels may annotate nodes without changing model names. For a conversion,
    pass {t: origin.display_name for t, origin in result.transition_origins.items()}.
    `place_groups` (e.g. the owning agent of each place) colors places; a
    transition takes its group's color when all its places share one group.
    Places holding tokens in a final declaration are drawn as double circles.
    """
    if marking is None:
        marking = net.initial_marking
    if not isinstance(marking, Marking) or not marking.marked_places() <= net.places:
        raise PetriNetError("diagram marking must be a Marking over the net's places")
    place_labels = {} if place_labels is None else place_labels
    transition_labels = {} if transition_labels is None else transition_labels
    place_groups = {} if place_groups is None else place_groups
    finals = [{p.name: m[p] for p in net.places if m[p]} for m in net.final_markings]
    unrestricted = [{p.name: n for p, n in clause.items()} for clause in net.unrestricted_final_markings]
    final_places = _final_place_names(finals, unrestricted)
    # Mapping order decides colors, so agent order matches to_agent_dot.
    colors = _group_colors(place_groups.values())
    writer = _DotWriter()
    ids: dict[Place | Transition, str] = {}
    for i, p in enumerate(sorted(net.places)):
        ids[p] = f"p{i}"
        # The older Graphviz bundled with Tina ignores xlabel. Keep the name
        # and marking in a normal label so every supported renderer shows them.
        name = place_labels.get(p, p.name)
        style = _place_style(p.name in final_places, colors.get(place_groups.get(p), _NEUTRAL))
        writer.node(ids[p], **style, label=f"{name}\n{marking[p]}" if marking[p] else name)
    for i, t in enumerate(sorted(net.transitions)):
        ids[t] = f"t{i}"
        groups = {place_groups.get(p) for p in net.preset(t) | net.postset(t)}
        group = groups.pop() if len(groups) == 1 else None
        writer.node(ids[t], **_transition_style(colors.get(group, _NEUTRAL)),
                    label=transition_labels.get(t, t.name))
    for arc in sorted(net.arcs, key=lambda a: (a.source.name, a.target.name)):
        writer.edge(ids[arc.source], ids[arc.target], label=str(arc.weight) if arc.weight > 1 else "")
    return writer.finish(_finals_legend(finals, unrestricted))


def to_agent_dot(net: MultiAgentNet) -> str:
    """Draw source agent groups and local transitions without converting them.

    This also works before conservation/variable-pair well-formedness checks.
    Shared labels denote synchronization, not extra arcs between agent nets;
    they are drawn as dotted lines that do not affect the layout.
    Agents and their nodes keep declaration order, which keeps indexed
    families (p[1], p[2], ...) in order.
    """
    writer = _DotWriter()
    ids: dict[str, str] = {}
    final_places = _final_place_names(net.finals, net.unrestricted_finals)
    colors = _group_colors(net.agents)
    for i, agent in enumerate(net.agents.values()):
        border, _ = colors[agent.name]
        writer.lines.append(f"  subgraph cluster_a{i} {{")
        writer.lines.append(f"    label={_quote(agent.name)};")
        writer.lines.append(f'    style="rounded"; color="{border}"; fontcolor="{border}"; penwidth="1.3";')
        for j, p in enumerate(agent.places):
            ids[p] = f"a{i}_p{j}"
            count = net.initial.get(p, 0)
            writer.node(ids[p], **_place_style(p in final_places, colors[agent.name]),
                        label=f"{p}\n{count}" if count else p)
        for j, t in enumerate(agent.transitions):
            ids[t] = f"a{i}_t{j}"
            label = agent.label(t)
            annotation = "silent" if label is None else f"label: {label}"
            writer.node(ids[t], **_transition_style(colors[agent.name]), label=f"{t}\n{annotation}")
        for (src, dst), weight in agent.arcs.items():
            writer.edge(ids[src], ids[dst], label="" if weight == 1 else str(weight))
        for p, t in agent.read_arcs:
            writer.edge(ids[p], ids[t], style="dashed", label="read")
        writer.lines.append("  }")
    for g in net.global_transitions():
        members = [t for _, t in g.members if t in ids]
        for a, b in zip(members, members[1:]):
            writer.edge(ids[a], ids[b], style="dotted", color="#999999", arrowhead="none",
                        constraint="false", penwidth="1.2")
    legend = ("Same non-silent label = synchronized firing across agents (dotted line).\n"
              "Dashed read arc: requires a token without consuming it. Double circle: place in a final marking.")
    return writer.finish(legend + "\n" + _finals_legend(net.finals, net.unrestricted_finals))
