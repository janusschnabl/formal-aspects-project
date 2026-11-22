# Development notes

## Code paths

- `petrinet/parser.py` parses standard nets.
- `petrinet/agentnet/parser.py` and `model.py` handle the source agent format.
- `petrinet/agentnet/convert.py` expands synchronization and variable weights.
- `petrinet/net.py` holds standard-net structure, firing, and final predicates.
- `petrinet/tina.py` exports nets and calls Tina/Muse.
- `petrinet/__main__.py` runs exports, verification, reporting, and pruning.
- `petrinet/dot.py` draws nets. Each agent gets one palette color, assigned in
  declaration order; `to_dot(place_groups=...)` reuses it for converted nets,
  and a transition takes a color only when all its places share one agent.
  Places with tokens in a final declaration are double circles. In agent
  drawings, dotted `constraint=false` edges join same-label transitions
  without moving the layout. Keep node label text stable: tests match it.

Conversion preserves place names. Variable arcs are expanded using each agent
type's initial population; read arcs become matching input/output arcs. Keep
all generated transitions until dead-transition analysis establishes removals.
Pruning preserves places, initial/final declarations, and named properties.

## Verification

Dead-transition analysis uses Tina's full untimed reachability graph (`-R`).
The property check builds a second full graph and evaluates all default and
user formulas in one Muse invocation. Both checks require bounded, completed
analyses. Each stage gets its own timeout; Tina and Muse share the property
stage's time allowance.

Built-in formulas live in `_COMPLETION_PROPERTIES` and `_DEADLOCK_PROPERTIES`.
`_CTL_PRELUDE` defines the temporal operators and `IsDeadlock`. `_check_groups`
maps answers back to section and property names. Add default formulas to these
dictionaries rather than duplicating formulas or indexing answers manually.
User properties need only a `ctl: name = formula` line.

`verify_properties()` returns these report sections:

| Section | Fields |
|---|---|
| `completion` | `option_to_complete`, `all_traces_complete`; `None` without finals |
| `deadlocks` | `reachable`, `non_final_reachable` |
| `properties` | User property names mapped to booleans |
| `agent_completion` | Per-type `can_complete` and `all_traces_complete`; `None` per type without local finals |
| `agent_deadlocks` | Per-type boolean: a reachable unfinished state permanently prevents participation |

`check_completion()` and `check_deadlocks()` run only their respective group.
The default CLI without export flags checks only dead transitions; the full
verification runs under `--pipeline`.

See [Per-type completion checks](completion.md) for final-clause inference,
implicit zero constraints, generated EF/AF queries, and the code path.
See [Per-type deadlock checks](deadlocks.md) for participation mapping and the
`EF ((- LocalFinal) /\ (- EF CanAct))` check. `ConversionResult.agent_transitions`
derives participation from transition origins, including read-only members.

Semantics to preserve:

- Exact finals require zero at every unlisted place. Unrestricted finals
  constrain only listed places, including explicit zero counts. Declarations
  form a union; an empty exact final accepts the zero marking, while an empty
  unrestricted final accepts every marking.
- Completion checks are `AG EF IsFinal` and `AF IsFinal`, without fairness.
  A final at time zero satisfies `AF`; later firing can still leave the final
  set and make `AG EF` false. Finals do not disable transitions.
- `IsDeadlock` is `[T]F` on actual graph edges. Final deadlocks are included in
  `reachable`; only deadlocks outside the final set count as non-final. With
  no final declarations, `IsFinal` is false everywhere.
- Temporal operators treat terminal states as having an implicit self-loop.
  Keep deadlock detection based on the original enabled transitions.
- Boolean syntax follows Muse: `-`, `/\`, `\/`, `=>`. Until is written
  `EU condition target` or `AU condition target`. Names such as `S.{p[1]}`
  reference indexed places. Identifiers must match Tina's quoted-name handling.
- Results are booleans. LTL, fairness, timed properties, and witness paths are
  not currently exposed by the application.

## Pipeline reports and files

Original exports are written before analysis. Pruned diagrams are rendered
before replacing any previous pruned files. Failures preserve original exports
and mark the report `failed`; old pruned files can remain from an earlier run.
Only a report with status `complete` identifies the current pruned outputs.

The JSON also records `input`, `before`/`after` counts, `dead_transitions`,
`removed_transitions`, and `pruned_files`. After a property-check failure,
known dead transitions remain recorded but removals and verdicts are unset.
A false property is a completed check with exit code 0; tool, parse, conversion,
and incomplete-analysis errors use exit code 2.

`.net` files contain Tina structure only. Finals and CTL declarations remain
Python metadata and appear in diagrams/reports as appropriate. The Python CLI
reads the project's text formats, not exported Tina files.

## Tests

Run `python -m unittest discover -s tests -t .` after changing behaviour.
The integration tests use real bundled tools; some diagram tests require
Graphviz on PATH. Executable resolution and Linux permission restoration are
covered separately. Upstream manuals are under `tools/tina/4.0.0/`.

- `test_pipeline_semantics.py` constructs graphs directly from source fields,
  runs the real CLI, parses exports through Tina/PNML, and compares reachable
  states, edges, removals, completion, and deadlock verdicts.
- `tests/ctl_reference.py` is test-only. It evaluates CTL on finite graphs with
  Python sets, independently of production firing and Muse definitions.
- `test_completion.py` compares Muse against that evaluator on small graphs,
  checks terminal-state semantics, and tests failures after successful
  dead-transition analysis. Preserve the independent expected results when
  extending verification.
- `test_final_input.py` checks exact/partial final declarations and preservation
  through conversion and pruning.
- `test_agent_completion.py` checks explicit final inference, zero entries,
  missing local finals, alternative finals, synchronization, and global
  interleaving effects on EF/AF. The pipeline audit independently checks these
  verdicts against the source graph.
- `test_agent_deadlocks.py` checks permanent versus temporary blocking,
  synchronization/read-only participation, missing completion exemptions, and
  reporting before pruning. The pipeline audit uses independent backward
  reachability over source-labelled edges to verify per-type deadlock results.
