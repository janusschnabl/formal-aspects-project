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
that same converted net for dead transitions and completion. It records and removes all dead
transitions, then saves and draws the resulting net. The original standard-net
files are written before Tina runs and preserved for comparison. It creates
`out/` as needed.

| Output | Contents |
|---|---|
| `out/example-agent.svg` | Original agent model, with synchronization labels, variable weights, and read arcs |
| `out/example.svg` | Converted standard weighted Petri net |
| `out/example.net` | Converted net in Tina format |
| `out/example-agent.dot`, `out/example.dot` | Graphviz sources for the diagrams |
| `out/example-results.json` | Analysis status, completion verdicts, dead/removed transitions, and before/after counts |
| `out/example-pruned.net` | Standard net with all dead transitions and their incident arcs removed |
| `out/example-pruned.svg`, `out/example-pruned.dot` | Diagram and Graphviz source for the resulting net |
| Terminal | Dead transitions, option to complete from every reachable marking, and whether all traces reach a final |

Open the SVG files in a browser to view and zoom. Replace the input filename
and output prefix with your own, for example `--pipeline out/shop`.
The `--timeout 60` option limits each dead-transition or completion check to
60 seconds. Completion checking uses the Muse executable supplied beside Tina.

Pruning preserves every place, initial marking, final marking, and surviving
transition's name and arc weights. Places that become isolated remain visible.
If no transitions are dead, the pruned net is identical to the original, and
the report's `removed_transitions` list is empty. Removal is based on the
complete Tina result; the source input file is never edited.

To see removal on the standard example:

```sh
python -m petrinet examples/two_dead.txt --pipeline out/two_dead
```

The report lists `t3` and `t4` as removed; `out/two_dead-pruned.svg` shows the
remaining transitions `t1` and `t2`.

## Final markings and completion

Both input formats allow a union of final declarations:

```text
final: boarded=3 boarding_closed=2 crew_idle=2
unrestricted final: boarded=3 boarding_closed=2
unrestricted final: cancelled=3 flights_cancelled=2
```

An ordinary `final:` is an exact marking: every unlisted place must have zero
tokens. In `unrestricted final:`, only the listed places are constrained;
all other places, such as crew places, may have any token count. A marking is
final if **any** declaration matches. An explicit `place=0` still constrains
that place. Empty `final:` means the all-zero marking; empty
`unrestricted final:` accepts every marking. Agent index expansion and
continuation lines work in both kinds of final declaration.

The existing `--pipeline` command automatically prints and records:

- **Option to complete from every reachable marking** (`AG EF final`):
  every reachable marking has some finite firing sequence to a final marking.
- **All traces eventually reach a final marking** (`AF final`): every maximal
  execution reaches a final marking. No fairness is assumed, so a loop that
  can avoid completion makes this false even if an exit to a final exists.

A non-final deadlock fails both checks. A final marking counts as already
completed; finals do not stop subsequent firing. In particular, `AF final`
does not require executions to terminate or stay final afterward.

These verdicts appear as booleans `completion.option_to_complete` and
`completion.all_traces_complete` in the result JSON. With no final declarations,
the pipeline prints that completion was not checked and records `completion: null`.
A false property is a completed verification result (exit code 0), not a tool
error. Incomplete analysis or checker errors still produce a failed report
and exit code 2.

### Write your own CTL properties

Add named, single-line CTL declarations to either input format:

```text
ctl: can_always_complete = AG EF IsFinal
ctl: completion_is_inevitable = AF IsFinal
ctl: can_avoid_completion = EG (- IsFinal)
```

`IsFinal` means membership in the union of all exact and unrestricted final
declarations. The pipeline evaluates each property at the initial marking,
prints `CTL name: yes/no`, and records its boolean in `properties.name` in the
JSON report. The default completion checks and named properties share one
Tina reachability graph and Muse session. False properties do not remove any
extra transitions or alter the final set.

Supported temporal operators are `AG`, `AF`, `EG`, `EF`, `AX`, `EX`, and until
expressions `EU condition target` / `AU condition target`. Boolean notation is
`-` (not), `/\` (and), `\/` (or), and `=>` (implies), with parentheses for
grouping. A place count can be referenced as `S.waiting`; indexed names use
braces, for example `AG (S.{boarded[1]} <= 3)`. These expressions are translated
to Muse's fixed-point definitions internally; no mu-calculus is needed in the
input file. LTL declarations are not currently supported.

Each `ctl:` needs a unique name and a nonempty expression; omit trailing
semicolons. No final declarations means `IsFinal` is false everywhere, while
the automatic completion checks remain marked as not specified. Terminal
states are treated as having an implicit temporal self-loop for `AX`/`EX`;
this does not add transitions to the Petri net. CTL properties assume no fairness.

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

Export commands skip analysis. `--pipeline` adds analysis, a removal report,
and pruned-net exports to the full render workflow. Render modes cannot be
combined with individual export options. Original diagrams must render
successfully before their files are written. If Tina or pruned-net rendering
fails afterward, the original exports remain and the report has status
`failed`, with an error message. Only a report with status `complete`
identifies successful pruned outputs for the current run; older pruned files
from a previous run may still exist after a failure.

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
preserved through conversion and pruning and shown in diagrams. The pipeline
checks completion against their union on the original reachable graph; they
do not stop firing or change dead-transition analysis. The `.net` export
contains the net structure, not the final or CTL declarations.

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
