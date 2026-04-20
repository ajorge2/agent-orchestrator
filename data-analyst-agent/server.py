import json
import queue
import threading
from flask import Flask, Response, jsonify, request, send_from_directory
from pathlib import Path

import db

app = Flask(__name__, static_folder="static")
db.init_db()


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/run")
def run():
    question = request.args.get("question", "Give me a summary of this dataset.")
    event_queue: queue.Queue = queue.Queue()
    all_events: list[dict]   = []
    run_meta: dict           = {}

    def worker():
        from fsm_streaming import run_with_events
        try:
            run_with_events(question, event_queue.put)
        except Exception as e:
            event_queue.put({"type": "error", "message": str(e)})
        finally:
            event_queue.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def stream():
        while True:
            try:
                event = event_queue.get(timeout=60)
            except queue.Empty:
                yield 'data: {"type":"heartbeat"}\n\n'
                continue

            if event is None:
                break

            all_events.append(event)

            if event["type"] in ("run_metrics", "run_config"):
                run_meta.update(event)

            yield f"data: {json.dumps(event)}\n\n"

        # persist after stream ends
        if run_meta.get("run_id"):
            try:
                db.save_run(
                    run_id=run_meta["run_id"],
                    question=question,
                    metrics=run_meta,
                    events=all_events,
                )
            except Exception:
                pass

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/runs")
def runs():
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(db.list_runs(limit))


@app.route("/config", methods=["GET"])
def get_config():
    from fsm_core import load_config
    return jsonify(load_config())


@app.route("/config", methods=["POST"])
def set_config():
    from fsm_core import CONFIG_PATH, TOOL_REGISTRY
    cfg = request.json
    # validate enabled_tools are known
    cfg["enabled_tools"] = [t for t in cfg.get("enabled_tools", []) if t in TOOL_REGISTRY]
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    return jsonify({"ok": True})


@app.route("/data")
def data():
    from fsm_core import load_csv
    from pathlib import Path
    rows = load_csv(Path(__file__).parent / "sales_data.csv")
    return jsonify(rows)


@app.route("/runs/<run_id>")
def run_detail(run_id: str):
    row = db.get_run(run_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row)


if __name__ == "__main__":
    print("Starting server at http://localhost:5050")
    app.run(port=5050, debug=False, threaded=True)
