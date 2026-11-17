# Petri net conversion and dead-transition checker

Parse multi-agent models, convert them to standard weighted Petri nets, draw
both models, and find transitions that can never fire using bundled Tina.

## Run the full pipeline

Run commands from the project root (the folder containing `petrinet/`). You
need **Python 3.10+** and **Graphviz `dot` on PATH** for SVG diagrams. No Python
packages are required. Tina is included for Windows and Linux x86-64.
Use `python3` on Linux if `python` is unavailable.

```sh
python -m petrinet examples/agents/example.txt --agentnet --pipeline out/example --timeout 60
```

This single command parses the agent model, creates its diagram, converts it
once to a standard Petri net, creates the standard-net diagram, and checks
that same converted net for dead transitions. It creates `out/` as needed.

| Output | Contents |
|---|---|
| `out/example-agent.svg` | Original agent model, with synchronization labels, variable weights, and read arcs |
| `out/example.svg` | Converted standard weighted Petri net |
| `out/example.net` | Converted net in Tina format |
| `out/example-agent.dot`, `out/example.dot` | Graphviz sources for the diagrams |
| Terminal | Dead-transition result; this example prints `No dead transitions.` |

Open the SVG files in a browser to view and zoom. Replace the input filename
and output prefix with your own, for example `--pipeline out/shop`.
The `--timeout 60` option limits the Tina analysis to 60 seconds.

## Run each step individually

### 1. Parse and visualize the agent model

```sh
python -m petrinet examples/agents/example.txt --agentnet --raw-render out/example
```

Writes `out/example-agent.dot` and `out/example-agent.svg`. This step only
parses and draws the source model; it skips conversion and analysis. It can
help inspect a model that does not yet pass conversion checks.

### 2. Convert to a standard Petri net and visualize it

```sh
python -m petrinet examples/agents/example.txt --agentnet --render out/example
```

Writes `out/example.net`, `out/example.dot`, and `out/example.svg`, along
with the agent diagram files. This command stops after exporting and drawing;
it does not check dead transitions.

If you only want the converted net, without diagrams or Graphviz:

```sh
python -m petrinet examples/agents/example.txt --agentnet --emit example.net
```

### 3. Check dead transitions

```sh
python -m petrinet examples/agents/example.txt --agentnet --timeout 60
```

Converts the agent source and passes the resulting standard net to Tina,
then prints the dead transitions. It creates no diagrams. When run separately,
this step converts the source again; `--pipeline` reuses one conversion for all
steps. The exported `.net` file is for Tina and its editor; the Python CLI
reads the project's `.txt` input formats.

For an already standard Petri net in the project's text format, omit `--agentnet`:

```sh
python -m petrinet examples/two_dead.txt
```

Expected output:

```text
Dead transitions:
  t3
  t4
```

## Other export options

Use `--raw-dot FILE` for the agent model's DOT source or `--dot FILE` for
the standard model's DOT source. `--emit FILE` writes Tina format
(`--emit-tina` is an alias). Each accepts `-` for stdout, but only one output
can use stdout at a time. These individual exports can be combined:

```sh
python -m petrinet examples/agents/shop.txt --agentnet --emit shop.net --dot shop.dot --raw-dot shop-agent.dot
```

Export commands skip analysis. `--pipeline` adds analysis to the full render
workflow. Render modes cannot be combined with individual export options.
Rendering must succeed before any output files are written. If Tina fails
after a pipeline export, the diagrams and `.net` file remain available.

## Input formats

### Standard Petri nets

See `examples/two_dead.txt`:

```text
places: p1 p2 p3 p4
transitions: t1 t2 t3 t4
initial: p1=1
arcs: (p1, t1) (t1, p2)
      (p2, t2) (t2, p1)
      (p3, t3) (t3, p4)
      (p2, t4, 2) (t4, p4)
```

Names use letters, digits, and underscores and cannot start with a digit.
Unlisted places start with zero tokens. Arcs have weight 1 unless a third
value supplies the weight. A transition needs enough tokens in every input
place to fire. Lines without a keyword continue the preceding section;
`#` begins a comment.

### Multi-agent nets

Use `--agentnet` and one `agent:` block per agent type. See
`examples/agents/example.txt` for a small synchronized model and
`examples/agents/shop.txt` for indexing, variable weights, and read arcs.

```text
agent: Customer
  places: home queue done
  transitions: enter buy
  labels: buy=serve
  arcs: (home, enter, *) (enter, queue, *)
        (queue, buy) (buy, done)
initial: home=3
final: done=3
```

Equal non-silent labels synchronize transitions across agents; unlisted labels
are silent. Paired `*` arcs move a variable number of agents. `read: (p, t)`
requires a token without consuming it. Named index sets and trailing `for`
clauses expand names such as `buy[k]`; multiple indices use `q[i][j]`.
Names are global, and place names are preserved after expansion.

Conversion checks well-formedness and token conservation, merges synchronized
transitions, expands variable weights using each agent type's initial
population, and replaces read arcs with matching input/output arcs. It keeps
all generated transitions, including unreachable ones. Final markings are
preserved as Python metadata and shown in diagrams; they do not stop firing
or change dead-transition analysis.

## Tools and limits

Tina is selected automatically for Windows x86-64 or Linux x86-64 (glibc 2.27+).
For another installation, supply `--tina PATH` or set `TINA_BIN`. See
[the bundled-tool notes](tools/tina/README.md) for package details and checksums.

Rendering uses Graphviz `dot` on PATH, or an executable passed with
`--graphviz PATH`. Paths containing spaces must be quoted. The Tina packages
also contain Graphviz binaries; for example, Windows can use
`--graphviz tools/tina/4.0.0/windows-amd64/bin/dot.exe`.

The checker requires **bounded nets**. Tina constructs the full untimed
reachability graph and reports transitions that never fire. Unbounded nets,
incomplete searches, and timeouts produce an error instead of a verdict.
`examples/producer_consumer.txt` has an unbounded buffer and triggers this error.
Parse, conversion, rendering, and checking errors use exit code 2.

## Tests

```sh
python -m unittest discover -s tests -t .
```

Tests compare conversion against direct source semantics and compare Tina
against an independent Python search. They also check names, arc weights,
exports, diagrams, and CLI workflows. Graphviz integration tests are skipped
when `dot` is unavailable. Run `python -m petrinet --help` for all options.
