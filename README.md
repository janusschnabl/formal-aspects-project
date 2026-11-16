# Petri net dead-transition checker

Describe a Petri net in a text file and find out which transitions are **dead**,
i.e. can never fire from the initial marking. The checking is done by
[LoLA 2.0](https://theo.informatik.uni-rostock.de/theo-forschung/tools/lola/).

```
$ python -m petrinet examples/two_dead.txt
Dead transitions:
  t3
  t4
```

You need Python 3.10+ and LoLA. No Python packages are required.

## Installing LoLA

LoLA comes as a source tarball (`lola-2.0.tar.gz`) that you compile. You need
`gcc`, `g++` and `make` (Arch: `sudo pacman -S base-devel`, Ubuntu/WSL:
`sudo apt install build-essential`).

```sh
# 1. unpack
mkdir -p ~/.local/src
tar -xzf ~/Downloads/lola-2.0.tar.gz -C ~/.local/src
cd ~/.local/src/lola-2.0

# 2. configure, build, install into ~/.local/bin
./configure --prefix="$HOME/.local"
make -j"$(nproc)" CFLAGS="-O2 -fcommon"
make install

# 3. check
lola --version        # should print: LoLA 2.0
```

**Don't skip `-fcommon`.** Without it, GCC 10+ fails at the end with
`multiple definition of 'rank'`. If that already happened, run `make clean` and
build again with the flag.

If `lola --version` says *command not found*, add this line to `~/.bashrc` or
`~/.zshrc` and open a new terminal:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

## Writing a net

The file follows the formal definition N = (P, T, F, m_0), where
F ⊆ (P × T × w) ∪ (T × P × w) and w is a positive integer:

```
places: p1 p2 p3 p4
transitions: t1 t2 t3 t4
initial: p1=1

arcs: (p1, t1) (t1, p2)
      (p2, t2) (t2, p1)
      (p3, t3) (t3, p4)
      (p2, t4, 2) (t4, p4)
```

- **`places:` / `transitions:`** list the names, separated by spaces or commas. Names use
  letters, digits and `_`, and can't start with a digit.
- **`initial:`** gives the initial marking as `place=N`. Places you don't list start with 0.
- **`arcs:`** lists one tuple per arc:
  - `(p, t)` means `t` takes a token from `p`.
  - `(t, p)` means `t` puts a token in `p`.
  - `(p, t, 2)` or `(t, p, 2)` sets the arc weight; it defaults to 1.
- A transition with **several input places** is just several arcs: `(p1, t) (p2, t) (t, p3)`.
  `t` can only fire when *all* of its input places have enough tokens.
- A line without a keyword continues the previous section, so long arc lists can span
  lines. `#` starts a comment.

Mistakes are reported with a line number, e.g.
`error: line 3: arc (a, b) connects two places`.

## Multi-agent nets (our formalism)

`petrinet.agentnet` reads nets in our formalism N = ((A, {N_a}, L), M0, MF), with
N_a = (P_a, T_a, F_a, W_a, C_a, l_a). It does not convert them to LoLA yet. See
`examples/agents/shop.txt`:

```
index: K = 1..2                                   # index sets: a..b or {x, y}

agent: Customer                                   # one block per agent type
  places: home, queue, done
  transitions: enter, leave, buy[k] for k in K    # 'for' repeats the line per k
  labels: buy[k]=serve[k] for k in K              # same label = synchronised; unlisted = silent
  arcs: (home, enter, *) (enter, queue, *)        # weight 1 (default), n, or * (variable)
        (queue, buy[k]) (buy[k], done) for k in K
  read: (done, leave)                             # read arcs (place, transition)

initial: home=3                                   # M0
final: home=3                                     # one 'final:' per marking in MF
```

A `for` clause repeats its whole line (several variables: `for i in I, j in J`, names like
`q[i,j]`); items on the line that don't use the index are kept once. Inside brackets you can
use `k`, `k+1` and `k-1`. Names are global, so overlap between agents is a parse error.

```python
from petrinet.agentnet import load
net = load("examples/agents/shop.txt")
net.global_transitions()       # G, with synchronised transitions merged
net.well_formedness_errors()   # [] if well-formed (agent preservation, variable-arc pairing, ...)
```

## Running it

Run from the project root (the folder containing `petrinet/`):

```sh
python -m petrinet mynet.txt                  # check with LoLA
python -m petrinet mynet.txt --emit -         # show the net as LoLA sees it
python -m petrinet mynet.txt --builtin        # check without LoLA (small nets only)
python -m petrinet mynet.txt --lola PATH      # use a LoLA that isn't on PATH
python -m petrinet mynet.txt --timeout 60     # max seconds per LoLA query
```

`--builtin` uses a simple Python search over all reachable markings. It is useful
for double-checking LoLA, but gives up on unbounded or very large nets.

Examples to try: `examples/two_dead.txt` (t3 and t4 dead) and
`examples/producer_consumer.txt` (weighted arcs, nothing dead).

## How it works

A transition `t` is dead if no reachable marking enables it. For each transition,
the program writes the net in LoLA's format (weights are passed through unchanged)
and runs:

```sh
lola net.lola --formula="EF FIREABLE(t)" --json=result.json
```

If LoLA answers *false*, `t` is dead.

## Code structure

Everything goes through one class, `PetriNet`, which is the classical net
N = (P, T, F, m_0):

```
our formalism ──translate──▶ PetriNet ──lola.to_lola──▶ LoLA
text file ──────parse──────▶ PetriNet ──explore───────▶ built-in check
```

| File | What it does |
|---|---|
| `petrinet/net.py` | `Place`, `Transition`, `InputArc`, `OutputArc`, `Marking` and `PetriNet`: the definition, W(x, y), •t, t•, enabling and firing |
| `petrinet/parser.py` | text format → `PetriNet` |
| `petrinet/lola.py` | `PetriNet` → LoLA syntax, and running LoLA |
| `petrinet/explore.py` | built-in reachability search |

## Building a net in Python

Each part of N = (P, T, F, m_0) has its own class, and each class enforces
its part of the definition:

| Definition | Class | Enforces |
|---|---|---|
| p ∈ P | `Place(name)` | `Place` and `Transition` are different types, so P ∩ T = ∅ |
| t ∈ T | `Transition(name)` | |
| (p, t, w) ∈ P × T × w | `InputArc(place, transition, weight=1)` | the first end is a place, the second a transition, w ≥ 1 |
| (t, p, w) ∈ T × P × w | `OutputArc(transition, place, weight=1)` | the first end is a transition, the second a place, w ≥ 1 |
| m : P → ℕ | `Marking({place: tokens})` | token counts are natural numbers; unlisted places have 0 |
| N | `PetriNet(P, T, F, m_0)` | arcs only use places in P and transitions in T, at most one arc per (x, y), m_0 only marks places in P |

A translation from another formalism builds the four sets and hands them over:

```python
from petrinet import InputArc, Marking, OutputArc, PetriNet, Place, Transition
from petrinet.lola import dead_transitions, to_lola

idle, busy = Place("idle"), Place("busy")
start = Transition("start")

net = PetriNet(
    places={idle, busy},                                    # P
    transitions={start},                                    # T
    arcs={InputArc(idle, start), OutputArc(start, busy, weight=2)},    # F
    initial_marking=Marking({idle: 1}),                     # m_0
)

print(to_lola(net))
print(dead_transitions(net))
```

The semantics are methods on the net: `net.weight(x, y)` is W(x, y),
`net.preset(t)` / `net.postset(t)` are •t / t•, and `net.is_enabled(m, t)`
and `net.fire(m, t)` follow the firing rule. Firing returns a new `Marking`.

## Tests

```sh
python -m unittest discover -s tests -t .
```

The tests that need LoLA are skipped if it isn't installed.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No module named petrinet` | `cd` to the project root first. |
| `LoLA executable 'lola' not found` | Install LoLA, or fix PATH (see above), or use `--lola`. |
| LoLA build: `multiple definition of 'rank'` | `make clean`, then rebuild with `CFLAGS="-O2 -fcommon"`. |
| Every transition is dead | Check the `initial:` line. Transitions with several inputs need tokens in all of them. |
