# Petri net conversion and verification

Convert multi-agent models to standard Petri nets, draw both, and check dead
transitions, deadlocks, and CTL properties.

## Run

You need Python 3.10+ and Graphviz `dot` on PATH. No Python packages are
required. Tina and Muse are bundled for Windows and Linux x86-64.

Run from the project root:

```sh
python -m petrinet examples/agents/airport.txt --agentnet --pipeline out/airport --timeout 60
```

This draws the model, converts and checks it, then exports a copy with dead
transitions removed. The source file stays unchanged.

| Output | Contents |
|---|---|
| `out/airport-agent.svg` | Original agent model |
| `out/airport.svg`, `out/airport.net` | Converted net |
| `out/airport-pruned.svg`, `out/airport-pruned.net` | Net after removing dead transitions |
| `out/airport-results.json` | Verification results and removed transitions |

DOT sources are saved alongside the diagrams. Open the SVG files in a browser.

For a standard Petri net, omit `--agentnet`:

```sh
python -m petrinet examples/two_dead.txt --pipeline out/two_dead
```

To run individual steps, replace `--pipeline out/airport` with:

| Option | Action |
|---|---|
| `--raw-render out/airport` | Draw only the agent model; requires `--agentnet` |
| `--render out/airport` | Convert and draw, without analysis |
| `--emit out/airport.net` | Export only the Tina net |
| No export option | Check only dead transitions, without diagrams |

Use `python -m petrinet --help` for all options. On Linux, use `python3` if needed.

## Input formats

A standard net lists places, transitions, arcs, and its initial marking:

```text
places: ready done
transitions: finish
arcs: (ready, finish) (finish, done)
initial: ready=1
final: done=1
```

Arcs have weight 1 unless a third value supplies the weight, e.g.
`(ready, finish, 2)`. Unlisted places start with zero tokens.
`#` starts a comment; lines without a keyword continue the previous section.

Multi-agent models use one block per agent type:

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

Equal non-silent labels synchronize transitions across agents. Paired `*`
arcs move a variable number of tokens. A `read: (place, transition)` arc
requires a token without consuming it. Names are global.
See [airport.txt](examples/agents/airport.txt) for indexed flights, shared crew,
and synchronized aircraft and passenger activities.

## Verification

The pipeline reports:

- **Dead transitions:** transitions that can never fire. These are removed.
  With `--agentnet`, these are converted variants; a dead weight choice alone
  does not make its source activity dead or the model unsound.
- **Deadlocks:** whether any deadlock is reachable, and whether any is non-final.
  A deadlock is a marking with no enabled transition.
- **Completion:** whether a final remains reachable from every reachable marking,
  and whether every execution eventually reaches a final.
- **Agent soundness:** combines the option to complete, no per-type deadlocks,
  and no dead source activities. See the [definition](agents/soundness.md).

With `--agentnet`, each type reports whether its **local final** can be reached
from the initial marking, remains reachable from every reachable marking, and
is reached on every trace. It also reports whether the initial marking is
locally final for each type. Soundness requires a **global final** to remain
reachable. Local finals use explicit entries for that type; types with no
entries are unspecified.
See [how local finals become CTL checks](agents/completion.md) for a worked
example, including the zero checks for omitted places.
The pipeline also reports [per-type deadlocks](agents/deadlocks.md): unfinished
types that can never participate again.

A loop can prevent eventual completion even when an exit exists. Final
markings do not stop firing.

Both input formats support exact and partial final declarations:

```text
final: done=3 crew_idle=1
unrestricted final: done=3
```

`final:` requires zero tokens at every unlisted place.
`unrestricted final:` leaves unlisted places unconstrained.
Multiple declarations are alternatives. Without finals, completion is skipped
and every deadlock is considered non-final.

Add custom CTL properties as named, single-line formulas without semicolons:

```text
ctl: queue_capacity = AG (S.queue <= 3)
ctl: only_final_deadlocks = AG (IsDeadlock => IsFinal)
```

`IsFinal` matches the declared finals; `IsDeadlock` means no transition is
enabled. Place counts use `S.place`, or `S.{place[1]}` for indexed names.
Temporal operators are `AG`, `AF`, `EG`, `EF`, `AX`, `EX`, `EU`, and `AU`;
Boolean operators are `-`, `/\`, `\/`, and `=>`.

## Tools and tests

Analysis requires bounded nets. A false property is a verification result;
unbounded nets, incomplete checks, and tool errors exit with code 2.
`--timeout 60` limits dead-transition analysis and the combined property check
to 60 seconds each.

Use `--tina PATH` or `TINA_BIN` for another Tina installation, with Muse
beside it. Use `--graphviz PATH` to select Graphviz.
See [bundled-tool notes](tools/tina/README.md) for platform details.

Run the tests with:

```sh
python -m unittest discover -s tests -t .
```
