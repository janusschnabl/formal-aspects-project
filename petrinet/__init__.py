"""Classical Petri nets, a text format for them, and dead-transition checking.

    other formalism ──translate──▶ PetriNet ──to_lola──▶ LoLA
    text file ───────parse──────▶ PetriNet ──explore──▶ built-in check
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
