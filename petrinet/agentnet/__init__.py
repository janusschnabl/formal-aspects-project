"""Multi-agent nets: N = ((A, {N_a | a in A}, L), M0, MF)."""

from .model import VAR, AgentNet, GlobalTransition, MultiAgentNet
from .parser import ParseError, load, parse

__all__ = ["VAR", "AgentNet", "GlobalTransition", "MultiAgentNet", "ParseError", "load", "parse"]
