"""Classical Petri nets, a text format for them, and dead-transition checking.

    other formalism ──translate──▶ PetriNet ──to_tina──▶ Tina
    text file ───────parse──────▶ PetriNet ──to_tina──▶ Tina
"""

from .net import Arc, InputArc, Marking, OutputArc, PetriNet, PetriNetError, Place, Transition
from .parser import ParseError, load, parse

__all__ = [
    "Place",
    "Transition",
    "InputArc",
    "OutputArc",
    "Arc",
    "Marking",
    "PetriNet",
    "PetriNetError",
    "ParseError",
    "load",
    "parse",
]
