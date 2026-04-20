"""
Codebase Explorer — OrcView SDK example

A three-tier agent hierarchy that answers questions about any code directory:

  Orchestrator
  ├── File Explorer   (lists dirs, reads files, searches text)
  └── Code Analyzer   (parses Python AST: functions, classes, imports)

Run:
  python -m examples.codebase_explorer
  python -m examples.codebase_explorer --headless "What does this project do?"
"""

import ast
import glob
import re
import sys
from pathlib import Path
from typing import Literal

# make `import orcview` work when running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orcview import Agent, tool

# All file operations are sandboxed to this directory.
ROOT = Path.cwd().resolve()


def _safe_path(p: str) -> Path:
    """Resolve p and verify it stays inside ROOT. Raises ValueError if not."""
    resolved = (ROOT / p).resolve()
    if not str(resolved).startswith(str(ROOT)):
        raise ValueError(f"path '{p}' is outside the allowed root ({ROOT})")
    return resolved


def _safe_dir(d: str) -> Path:
    """Like _safe_path but also checks the result is a directory."""
    p = _safe_path(d)
    if not p.is_dir():
        raise ValueError(f"'{d}' is not a directory inside {ROOT}")
    return p


# ── File Explorer tools ────────────────────────────────────────────────────────

@tool
def list_files(directory: str, pattern: str = "**/*.py") -> dict:
    """List files matching a glob pattern inside a directory (relative to project root)."""
    base = _safe_dir(directory)
    matches = sorted(str(Path(m).relative_to(ROOT))
                     for m in glob.glob(f"{base}/{pattern}", recursive=True))
    return {"files": matches, "count": len(matches)}


@tool
def read_file(path: str, max_lines: int = 80) -> dict:
    """Read a file (relative to project root) and return its contents (up to max_lines lines)."""
    try:
        resolved = _safe_path(path)
        lines = resolved.read_text(errors="replace").splitlines()
        truncated = len(lines) > max_lines
        return {
            "path": str(resolved.relative_to(ROOT)),
            "content": "\n".join(lines[:max_lines]),
            "total_lines": len(lines),
            "truncated": truncated,
        }
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


@tool
def search_in_directory(directory: str, pattern: str,
                         file_glob: str = "**/*.py") -> dict:
    """Search for a regex pattern across files in a directory (relative to project root).

    Returns up to 30 matches with file path, line number, and matching line.
    """
    try:
        base = _safe_dir(directory)
    except ValueError as exc:
        return {"error": str(exc)}

    results = []
    for match_path in sorted(glob.glob(f"{base}/{file_glob}", recursive=True)):
        try:
            p = Path(match_path).resolve()
            if not str(p).startswith(str(ROOT)):
                continue
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if re.search(pattern, line):
                    results.append({"file": str(p.relative_to(ROOT)), "line": i, "content": line.strip()})
                    if len(results) >= 30:
                        break
        except Exception:
            pass
        if len(results) >= 30:
            break
    return {"matches": results, "total_shown": len(results)}


file_explorer = Agent(
    label="File Explorer",
    tools=[list_files, read_file, search_in_directory],
    system=(
        "You are a file system expert. All paths are relative to the project root. "
        "Use '.' as the directory to start from the root. "
        "Use list_files to discover files, read_file to inspect their contents, "
        "and search_in_directory to find patterns. "
        "Stop as soon as you have enough information to answer — do not explore further than needed."
    ),
)

# ── Code Analyzer tools ────────────────────────────────────────────────────────

@tool
def analyze_python_file(path: str) -> dict:
    """Parse a Python file (relative to project root) and extract its functions, classes, and imports."""
    try:
        resolved = _safe_path(path)
        source = resolved.read_text(errors="replace")
        tree   = ast.parse(source)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}

    functions, classes, imports = [], [], []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            doc = ast.get_docstring(node) or ""
            functions.append({"name": node.name, "line": node.lineno,
                               "docstring": doc[:120]})
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            classes.append({"name": node.name, "line": node.lineno,
                             "docstring": doc[:120]})
        elif isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    return {
        "path": path,
        "functions": functions,
        "classes": classes,
        "imports": sorted(set(imports)),
        "total_lines": len(source.splitlines()),
    }


@tool
def count_complexity(directory: str) -> dict:
    """Count Python files, total lines, functions, and classes in a directory (relative to project root)."""
    try:
        base = _safe_dir(directory)
    except ValueError as exc:
        return {"error": str(exc)}
    totals = {"files": 0, "lines": 0, "functions": 0, "classes": 0}
    for path in glob.glob(f"{base}/**/*.py", recursive=True):
        try:
            source = Path(path).read_text(errors="replace")
            tree   = ast.parse(source)
            totals["files"]     += 1
            totals["lines"]     += len(source.splitlines())
            totals["functions"] += sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
            totals["classes"]   += sum(1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
        except Exception:
            pass
    return totals


code_analyzer = Agent(
    label="Code Analyzer",
    tools=[analyze_python_file, count_complexity, read_file],
    system=(
        "You are a code structure expert. All paths are relative to the project root. "
        "Use analyze_python_file to understand individual modules and count_complexity "
        "to get project-wide stats. Use read_file only when the question cannot be answered "
        "from structure alone. Use the minimum number of tool calls needed, then stop."
    ),
)

# ── Orchestrator ───────────────────────────────────────────────────────────────

orchestrator = Agent(
    label="Codebase Orchestrator",
    tools=[file_explorer, code_analyzer],
    system=(
        "You answer questions about the current software project by coordinating two specialist agents.\n\n"
        "File Explorer — use for: finding files, reading source, searching patterns.\n"
        "Code Analyzer — use for: understanding structure, functions, classes, complexity.\n\n"
        "All file paths are relative to the project root. Start exploration from '.'.\n"
        "Delegate clearly. Synthesize the responses into one concise, accurate answer. "
        "Do not repeat raw tool output — interpret and summarise it."
    ),
)

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if args and args[0] == "--headless":
        question = " ".join(args[1:]) or "What does this project do?"
        print(f"\nQuestion: {question}\n")
        answer = orchestrator.run(question)
        print(f"\nAnswer:\n{answer}\n")
    else:
        orchestrator.serve(port=5050)
