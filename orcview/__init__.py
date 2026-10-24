from ._tool import tool
__all__ = ["Agent", "tool", "CyclicAgentError"]
def __getattr__(name):
    if name in {"Agent", "CyclicAgentError"}:
        from . import _agent
        return getattr(_agent, name)
    raise AttributeError(name)
