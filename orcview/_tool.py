import inspect
from typing import get_type_hints, get_origin, get_args, Union, Literal


def tool(fn):
    """Decorator that makes a function usable as an OrcView agent tool.

    Generates an Anthropic-compatible JSON schema from type hints and docstring.
    Parameters with defaults are optional; all others are required.
    Literal["a", "b"] becomes an enum field.
    """
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    sig = inspect.signature(fn)
    properties = {}
    required = []

    for name, param in sig.parameters.items():
        if name == "self":
            continue
        hint = hints.get(name, str)
        properties[name] = _hint_to_schema(hint)
        if param.default is inspect.Parameter.empty:
            required.append(name)

    fn._is_orcview_tool = True
    fn._tool_schema = {
        "name": fn.__name__,
        "description": (inspect.getdoc(fn) or "").strip(),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }
    return fn


def _hint_to_schema(hint) -> dict:
    origin = get_origin(hint)
    args = get_args(hint)

    if origin is Literal:
        return {"type": _py_to_json(type(args[0])), "enum": list(args)}

    # Optional[X] / X | None
    if origin is Union and len(args) == 2 and type(None) in args:
        inner = next(a for a in args if a is not type(None))
        return _hint_to_schema(inner)

    # list[X] / List[X]
    if origin is list or hint is list:
        item = args[0] if args else str
        return {"type": "array", "items": _hint_to_schema(item)}

    return {"type": _py_to_json(hint)}


def _py_to_json(t) -> str:
    return {int: "integer", float: "number", bool: "boolean"}.get(t, "string")
