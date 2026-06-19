"""
Task-success eval for the data-analyst agent.

Drives the REAL hardened agent (fsm_core: 7 tools, retry, 12-cycle loop guard)
over a fixed question set, then grades each final answer two ways:

  1. Deterministic  — against ground truth computed directly from sales_data.csv
                      (categories by name match; numbers within tolerance).
                      This is the headline metric; it never trusts an LLM.
  2. LLM judge      — reference-based cross-check (judge is GIVEN the gold answer)
                      to catch formatting false-negatives. Corroboration only.

Run:
  python3 eval_accuracy.py --selftest   # grade logic only, no API calls
  python3 eval_accuracy.py              # full eval (uses the Anthropic key)
"""
import json
import re
import sys
import time
from math import floor, log10
from pathlib import Path

import fsm_core as fc

CSV = Path(__file__).parent / "sales_data.csv"

# ── question set with deterministic ground truth ────────────────────────────
# type: category (name must appear) | integer (exact) | money (within 1%)
QUESTIONS = [
    ("Q1",  "Which region had the highest total revenue?",                          "category", "South",      None),
    ("Q2",  "Which region had the lowest total revenue?",                           "category", "East",       None),
    ("Q3",  "Which product generated the most total revenue?",                      "category", "Headset",    None),
    ("Q4",  "Which product sold the most total units?",                             "category", "Webcam",     None),
    ("Q5",  "Which sales rep generated the most total revenue?",                    "category", "Bob",        None),
    ("Q6",  "Which sales rep handled the most transactions (closed the most deals)?","category","David",      None),
    ("Q7",  "Which product generated the least total revenue?",                     "category", "Keyboard",   None),
    ("Q8",  "How many transactions are in the dataset?",                            "integer",  200,          None),
    ("Q9",  "How many distinct products are in the dataset?",                       "integer",  8,            None),
    ("Q10", "How many transactions were in the West region?",                       "integer",  54,           None),
    ("Q11", "How many transactions had revenue greater than 10,000?",              "integer",  79,           None),
    ("Q12", "What is the total number of units sold across all transactions?",      "integer",  2152,         None),
    ("Q13", "What is the total revenue across all transactions?",                   "money",    1761149.77,   None),
    ("Q14", "What is the highest revenue from any single transaction?",             "money",    29144.40,     None),
    ("Q15", "What is the average (mean) revenue per transaction?",                  "money",    8805.75,      None),
]


# ── number extraction (handles $, commas, million/thousand/k/M suffixes) ─────
def extract_numbers(text: str) -> list[float]:
    nums: list[float] = []
    # pass A: numbers with an explicit magnitude word/suffix
    for m in re.finditer(r"\$?\s*([\d,]+\.?\d*)\s*(million|thousand|billion|m|k|bn)(?![a-z])",
                         text, re.I):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        suf = m.group(2).lower()
        if suf in ("million", "m"):
            v *= 1_000_000
        elif suf in ("billion", "bn"):
            v *= 1_000_000_000
        elif suf in ("thousand", "k"):
            v *= 1_000
        nums.append(v)
    # pass B: plain numbers
    for m in re.finditer(r"\$?\s*([\d][\d,]*\.?\d*)", text):
        try:
            nums.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return nums


def grade(qtype: str, expected, accept, answer: str) -> bool:
    if not answer:
        return False
    a = answer.lower()
    if qtype == "category":
        for c in (accept or [expected]):
            if str(c).lower() in a:
                return True
        return False
    nums = extract_numbers(answer)
    if qtype == "integer":
        return any(abs(n - int(expected)) < 0.5 for n in nums)
    if qtype == "money":
        target = float(expected)
        tol = max(abs(target) * 0.01, 0.5)            # within 1% (or 50 cents)...
        return any(abs(n - target) <= tol or _sig2(n) == _sig2(target)
                   for n in nums)                      # ...or equal at 2 sig figs
    return False


def _sig2(x: float) -> float:
    """Round to 2 significant figures, so $1.8M counts as $1,761,149.77."""
    if not x:
        return 0.0
    return round(x, 1 - int(floor(log10(abs(x)))))


# ── self-test: verify grader on synthetic answers, no API calls ─────────────
def selftest() -> None:
    cases = [
        ("category", "South", None, "The South region had the highest total revenue at $546,349.35.", True),
        ("category", "South", None, "The East region led with the most revenue.",                     False),
        ("integer",  200,     None, "There are 200 transactions in the dataset.",                     True),
        ("integer",  200,     None, "I found 199 rows.",                                               False),
        ("integer",  2152,    None, "A total of 2,152 units were sold.",                              True),
        ("integer",  8,       None, "There are 8 distinct products across 200 rows.",                 True),
        ("money", 1761149.77, None, "Total revenue is $1,761,149.77.",                                True),
        ("money", 1761149.77, None, "Total revenue is approximately $1.76 million.",                  True),
        ("money", 1761149.77, None, "Total revenue is about $1.8M.",                                  True),
        ("money", 29144.40,   None, "The largest single sale was $29,144.40.",                        True),
        ("money", 8805.75,    None, "Average revenue per transaction is roughly $8,806.",             True),
        ("money", 8805.75,    None, "The mean is $880.",                                              False),
        ("integer", 54,       None, "There were 54 transactions in the West region (out of 200).",    True),
    ]
    ok = 0
    for i, (qt, exp, acc, ans, want) in enumerate(cases, 1):
        got = grade(qt, exp, acc, ans)
        flag = "ok " if got == want else "FAIL"
        if got == want:
            ok += 1
        else:
            print(f"  [{flag}] case {i}: want={want} got={got} :: {ans!r}")
    print(f"selftest: {ok}/{len(cases)} grader cases passed")
    sys.exit(0 if ok == len(cases) else 1)


# ── drive the real fsm_core agent and capture the final text answer ─────────
def run_agent(question: str, cfg: dict, rows: list[dict]) -> dict:
    ctx = fc.AgentContext(
        question=question,
        rows=rows,
        model=cfg.get("model", fc.MODEL),
        max_cycles=cfg.get("max_cycles", fc.MAX_CYCLES),
        system=cfg.get("system_prompt", fc.SYSTEM),
        enabled_tools=cfg.get("enabled_tools", list(fc.TOOL_REGISTRY)),
    )
    ctx.messages = [{"role": "user", "content": question}]
    tools_used: list[str] = []
    emit = lambda _e: None
    state = fc.State.REASONING
    try:
        while True:
            if state is fc.State.REASONING:
                ctx.last_response = fc.call_llm_with_retry(ctx, emit)
                state = (fc.State.ACTING
                         if ctx.last_response.stop_reason == "tool_use"
                         else fc.State.DONE)
                if state is fc.State.DONE:
                    break
            elif state is fc.State.ACTING:
                ctx.messages.append({"role": "assistant", "content": ctx.last_response.content})
                results = []
                for block in ctx.last_response.content:
                    if getattr(block, "type", None) == "tool_use":
                        tools_used.append(block.name)
                        out = fc.execute_tool_safe(block.name, rows, block.input)
                        results.append({"type": "tool_result",
                                        "tool_use_id": block.id,
                                        "content": json.dumps(out)})
                ctx.messages.append({"role": "user", "content": results})
                state = fc.State.REASONING
    except Exception as exc:
        return {"answer": "", "error": f"{type(exc).__name__}: {exc}",
                "cost": ctx.total_cost_usd, "cycles": ctx.cycles, "tools": tools_used}

    answer = "".join(b.text for b in ctx.last_response.content
                     if hasattr(b, "text") and b.text)
    return {"answer": answer, "error": None,
            "cost": ctx.total_cost_usd, "cycles": ctx.cycles, "tools": tools_used}


def judge(question: str, expected, answer: str) -> bool:
    if not answer:
        return False
    prompt = (f"Question: {question}\n"
              f"Known-correct answer: {expected}\n"
              f"Candidate answer: {answer}\n\n"
              "Does the candidate answer state the known-correct answer "
              "(ignoring formatting, rounding, or extra wording)? "
              "Reply with exactly YES or NO.")
    try:
        r = fc.client.messages.create(model=fc.MODEL, max_tokens=5,
                                      messages=[{"role": "user", "content": prompt}])
        return r.content[0].text.strip().upper().startswith("Y")
    except Exception:
        return False


def main() -> None:
    cfg = fc.load_config()
    rows = fc.load_csv(CSV)
    print(f"Loaded {len(rows)} rows · model={cfg.get('model', fc.MODEL)} · "
          f"{len(cfg.get('enabled_tools', []))} tools enabled\n")

    results = []
    det_pass = judge_pass = completed = 0
    total_cost = 0.0

    for qid, q, qtype, expected, accept in QUESTIONS:
        t0 = time.monotonic()
        r = run_agent(q, cfg, rows)
        secs = time.monotonic() - t0
        total_cost += r["cost"]

        if r["error"] is None:
            completed += 1
        d = grade(qtype, expected, accept, r["answer"])
        j = judge(q, expected, r["answer"])
        det_pass += d
        judge_pass += j

        results.append({"id": qid, "question": q, "type": qtype,
                        "expected": expected, "answer": r["answer"],
                        "tools": r["tools"], "cycles": r["cycles"],
                        "cost": round(r["cost"], 6), "secs": round(secs, 1),
                        "error": r["error"], "deterministic": d, "judge": j})

        mark = "PASS" if d else "FAIL"
        ans = (r["answer"] or r["error"] or "").replace("\n", " ")
        print(f"[{mark}] {qid:4} det={int(d)} judge={int(j)} "
              f"cyc={r['cycles']} ${r['cost']:.4f}  exp={expected}")
        print(f"        ans: {ans[:140]}")

    n = len(QUESTIONS)
    print("\n" + "═" * 64)
    print(f"Questions:            {n}")
    print(f"Completed (no error): {completed}/{n}  ({100*completed/n:.0f}%)")
    print(f"TASK SUCCESS (deterministic): {det_pass}/{n}  ({100*det_pass/n:.0f}%)")
    print(f"LLM-judge agreement:          {judge_pass}/{n}  ({100*judge_pass/n:.0f}%)")
    disagree = [r["id"] for r in results if r["deterministic"] != r["judge"]]
    if disagree:
        print(f"Grader disagreements (review): {', '.join(disagree)}")
    print(f"Total API cost: ${total_cost:.4f}")
    print("═" * 64)

    out = Path(__file__).parent / "eval_results.json"
    out.write_text(json.dumps({
        "summary": {"n": n, "completed": completed, "det_pass": det_pass,
                    "judge_pass": judge_pass, "total_cost_usd": round(total_cost, 4)},
        "results": results,
    }, indent=2))
    print(f"Full results → {out.name}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    main()
