"""Names that can be used unchanged in both LoLA nets and formulas.

Based on LoLA 2's LexicNet.ll and LexicFormula.ll. Keywords are case-sensitive;
the formula lexer also reserves every nonempty sequence of A, G, E, F, X, U, R.
"""

import re


LOLA_KEYWORDS = frozenset(
    "CONSUME FAIR MARKING PLACE PRODUCE SAFE STRONG TRANSITION WEAK "
    "FIREABLE INITIAL DEADLOCK MAX FORMULA AND NOT OR XOR TRUE FALSE "
    "ALLPATH EXPATH ALWAYS EVENTUALLY UNTIL NEXTSTATE RELEASE "
    "REACHABLE INVARIANT IMPOSSIBLE oo".split()
)
_OPERATORS = frozenset("<-> != <> -> = + - * > < # >= <= /*".split())


def validate_lola_name(name: str) -> None:
    """Raise ValueError if a name would be split or interpreted as a token."""
    if not isinstance(name, str) or not re.fullmatch(r"[^,;:()\s{}]+", name):
        raise ValueError(
            f"name {name!r} is not a LoLA identifier: names cannot contain "
            "commas, semicolons, colons, parentheses, braces, or whitespace"
        )
    if name in LOLA_KEYWORDS or re.fullmatch(r"[AGEFXUR]+", name):
        raise ValueError(f"name {name!r} is reserved by LoLA")
    if name in _OPERATORS or re.fullmatch(r"-?\d+", name):
        raise ValueError(f"name {name!r} is a LoLA number or operator, not an identifier")
