"""
Harder battery for the data-analyst agent — stress-tests reasoning and tool limits.

Tier A — hard but COMPUTABLE with the 7 tools (multi-step aggregation, derived
         percentages, contrasts). Graded strictly on the correct value; folds
         into the headline accuracy rate.

Tier B — BEYOND single-tool capability (the tools allow only one filter condition,
         group by one column, and expose no percentiles/correlations). These probe
         robustness: does the agent degrade gracefully or hallucinate a number?
         Reported separately as correct / graceful / WRONG.

Reuses the verified grader + agent driver from eval_accuracy.py.
Run:  python3 eval_hard.py
"""
import json
import time
from pathlib import Path

import eval_accuracy as ea
import fsm_core as fc

# acknowledging-a-limitation language (graceful degradation)
DECLINE = [
    "cannot", "can't", "can not", "unable", "don't have", "do not have",
    "not possible", "isn't possible", "no direct", "no tool", "would need",
    "not able", "doesn't support", "does not support", "can only", "only able",
    "limitation", "not available", "no way to", "not supported", "beyond the",
    "with the available tools", "the tools i have", "i don't have a",
    "not directly", "no built-in", "would require",
]

def declined(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in DECLINE)


HARD = [
    # ── Tier A: hard but computable (strict value grading) ──────────────────
    ("A1",  "Which region has the highest average revenue per transaction?",       "A", "category", "South",      None),
    ("A2",  "Which product has the highest average units sold per transaction?",    "A", "category", "Webcam",     None),
    ("A3",  "Which sales rep has the highest average revenue per deal?",            "A", "category", "Eva",        None),
    ("A4",  "Which sales rep has the lowest average revenue per deal?",             "A", "category", "David",      None),
    ("A5",  "What is the standard deviation of transaction revenue?",              "A", "money",    7029.59,      None),
    ("A6",  "What is the median unit price?",                                       "A", "money",    815.95,       None),
    ("A7",  "What is the difference in total revenue between the highest- and "
            "lowest-earning regions?",                                             "A", "money",    264818.26,    None),
    ("A8",  "What percentage of total revenue came from the South region?",         "A", "money",    31.02,        None),
    ("A9",  "What percentage of all transactions did David handle?",               "A", "money",    23.5,         None),
    ("A10", "On average, how many units are sold per transaction?",                "A", "money",    10.76,        None),

    # ── Tier B: beyond single-tool capability (robustness probe) ────────────
    ("B1",  "How many transactions in the West region had revenue over $10,000?",  "B", "integer", 21,           None),
    ("B2",  "Which sales rep generated the most revenue in the West region?",       "B", "category", "Eva",        None),
    ("B3",  "What is the average revenue of transactions above $10,000?",           "B", "money",    16192.76,     None),
    ("B4",  "What is the 90th percentile of transaction revenue?",                  "B", "money",    19483.83,     None),
    ("B5",  "Is there a correlation between units sold and unit price?",            "B", "manual",   "~0.02 (none)", None),
    ("B6",  "What was the total revenue in Q1 2024 (January through March)?",       "B", "money",    453168.72,    None),
]


def b5_correct(answer: str) -> bool:
    t = (answer or "").lower()
    pos = ["no correlation", "not correlated", "no significant", "no meaningful",
           "no relationship", "weak", "little", "negligible", "essentially no",
           "independent", "not strongly", "0.02", "near zero", "close to zero"]
    return any(p in t for p in pos)


def main() -> None:
    cfg = fc.load_config()
    rows = fc.load_csv(ea.CSV)
    print(f"Loaded {len(rows)} rows · {len(cfg.get('enabled_tools', []))} tools\n")

    results = []
    a_pass = b_correct = b_graceful = b_wrong = 0
    total_cost = 0.0

    for qid, q, tier, qtype, expected, accept in HARD:
        t0 = time.monotonic()
        r = ea.run_agent(q, cfg, rows)
        total_cost += r["cost"]
        ans = (r["answer"] or r["error"] or "")

        if qtype == "manual":               # B5 only
            val_ok = b5_correct(ans)
        else:
            val_ok = ea.grade(qtype, expected, accept, r["answer"])

        rec = {"id": qid, "tier": tier, "question": q, "expected": expected,
               "answer": r["answer"], "tools": r["tools"], "cycles": r["cycles"],
               "cost": round(r["cost"], 6), "error": r["error"],
               "value_correct": val_ok, "declined": declined(ans)}

        if tier == "A":
            a_pass += val_ok
            mark = "PASS" if val_ok else "FAIL"
            cls = ""
        else:
            if val_ok:
                cls, mark = "correct", "CORRECT"; b_correct += 1
            elif rec["declined"]:
                cls, mark = "graceful", "graceful"; b_graceful += 1
            else:
                cls, mark = "wrong", "WRONG"; b_wrong += 1
            rec["class"] = cls

        results.append(rec)
        oneline = ans.replace("\n", " ")
        print(f"[{mark:8}] {qid:3} {tier}  cyc={r['cycles']} ${r['cost']:.4f} "
              f"tools={r['tools']}")
        print(f"           exp={expected}")
        print(f"           ans: {oneline[:200]}")

    nA = sum(1 for h in HARD if h[2] == "A")
    nB = sum(1 for h in HARD if h[2] == "B")
    print("\n" + "═" * 68)
    print(f"TIER A (hard but computable):  {a_pass}/{nA} correct  ({100*a_pass/nA:.0f}%)")
    print(f"TIER B (beyond tool limits):   {b_correct} correct · {b_graceful} graceful · {b_wrong} WRONG  (of {nB})")
    combined_pass = 15 + a_pass            # original eval was 15/15
    print(f"COMBINED computable accuracy:  {combined_pass}/{15+nA}  ({100*combined_pass/(15+nA):.0f}%)")
    print(f"Total API cost (this run):     ${total_cost:.4f}")
    print("═" * 68)

    out = Path(__file__).parent / "eval_hard_results.json"
    out.write_text(json.dumps({
        "summary": {"tierA_pass": a_pass, "tierA_n": nA,
                    "tierB": {"correct": b_correct, "graceful": b_graceful, "wrong": b_wrong, "n": nB},
                    "combined_pass": combined_pass, "combined_n": 15 + nA,
                    "total_cost_usd": round(total_cost, 4)},
        "results": results,
    }, indent=2))
    print(f"Full results → {out.name}")


if __name__ == "__main__":
    main()
