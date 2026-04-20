import anthropic
import csv
import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

# load .env from same directory as this script
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"

def load_csv(path: str) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader(f))

# ── tools ──────────────────────────────────────────────────────────────────

def summarize_dataset(rows: list[dict]) -> dict:
    columns = list(rows[0].keys()) if rows else []
    return {"row_count": len(rows), "columns": columns}

def get_column_stats(rows: list[dict], column: str) -> dict:
    values = [row[column] for row in rows]
    try:
        nums = [float(v) for v in values]
        return {
            "column": column,
            "type": "numeric",
            "min": min(nums),
            "max": max(nums),
            "mean": round(statistics.mean(nums), 2),
            "median": round(statistics.median(nums), 2),
            "stdev": round(statistics.stdev(nums), 2) if len(nums) > 1 else 0,
        }
    except ValueError:
        freq = defaultdict(int)
        for v in values:
            freq[v] += 1
        top = sorted(freq.items(), key=lambda x: -x[1])[:5]
        return {"column": column, "type": "categorical", "unique_values": len(freq), "top_values": top}

def group_and_aggregate(rows: list[dict], group_by: str, agg_column: str, agg_fn: str) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        try:
            groups[row[group_by]].append(float(row[agg_column]))
        except (ValueError, KeyError):
            pass
    fns = {"sum": sum, "mean": statistics.mean, "min": min, "max": max, "count": len}
    fn = fns.get(agg_fn, sum)
    result = [{"group": k, agg_fn: round(fn(v), 2)} for k, v in groups.items()]
    return sorted(result, key=lambda x: -x[agg_fn])

def filter_rows(rows: list[dict], column: str, operator: str, value: str) -> dict:
    ops = {
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">":  lambda a, b: float(a) > float(b),
        "<":  lambda a, b: float(a) < float(b),
        ">=": lambda a, b: float(a) >= float(b),
        "<=": lambda a, b: float(a) <= float(b),
    }
    op = ops.get(operator)
    if not op:
        return {"error": f"Unknown operator: {operator}"}
    matched = [r for r in rows if op(r.get(column, ""), value)]
    return {"matched_rows": len(matched), "sample": matched[:5]}

TOOL_SCHEMAS = [
    {
        "name": "summarize_dataset",
        "description": "Get an overview of the dataset: row count and column names.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_column_stats",
        "description": "Get statistics for a single column (numeric or categorical).",
        "input_schema": {
            "type": "object",
            "properties": {"column": {"type": "string", "description": "Column name"}},
            "required": ["column"],
        },
    },
    {
        "name": "group_and_aggregate",
        "description": "Group rows by a column and aggregate another column.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by":   {"type": "string"},
                "agg_column": {"type": "string"},
                "agg_fn":     {"type": "string", "enum": ["sum", "mean", "min", "max", "count"]},
            },
            "required": ["group_by", "agg_column", "agg_fn"],
        },
    },
    {
        "name": "filter_rows",
        "description": "Filter rows where column satisfies a condition.",
        "input_schema": {
            "type": "object",
            "properties": {
                "column":   {"type": "string"},
                "operator": {"type": "string", "enum": ["==", "!=", ">", "<", ">=", "<="]},
                "value":    {"type": "string"},
            },
            "required": ["column", "operator", "value"],
        },
    },
]

# ── tool dispatcher ────────────────────────────────────────────────────────

def dispatch(name: str, inputs: dict, rows: list[dict]) -> str:
    if name == "summarize_dataset":
        result = summarize_dataset(rows)
    elif name == "get_column_stats":
        result = get_column_stats(rows, **inputs)
    elif name == "group_and_aggregate":
        result = group_and_aggregate(rows, **inputs)
    elif name == "filter_rows":
        result = filter_rows(rows, **inputs)
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)

# ── agent loop ─────────────────────────────────────────────────────────────

SYSTEM = """You are a data analyst assistant. You have access to a sales dataset.
Use tools to explore the data and answer the user's question accurately.
Decide for yourself whether a single tool call is enough or whether you need multiple turns of analysis.
When you have a complete answer, respond in plain language — no code, no raw JSON."""

def run(question: str, csv_path: str = "sales_data.csv"):
    rows = load_csv(csv_path)
    messages = [{"role": "user", "content": question}]

    print(f"\nQuestion: {question}\n{'-'*50}")

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        # collect any text so far
        for block in response.content:
            if hasattr(block, "text"):
                print(f"Agent: {block.text}")

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [tool] {block.name}({json.dumps(block.input)})")
                    result = dispatch(block.name, block.input, rows)
                    print(f"  [result] {result[:200]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

if __name__ == "__main__":
    import sys
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Give me a summary of this dataset."
    run(question)
