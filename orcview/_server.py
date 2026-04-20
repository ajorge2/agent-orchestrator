import json
import os
import queue
import sys
import threading
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_from_directory


def _start_reload_listener():
    def _listen():
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip().lower() in ("r", "reload", ""):
                print("reloading...")
                os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_listen, daemon=True).start()


def _agent_config_snapshot(agent) -> dict:
    return {
        "label":         agent.label,
        "model":         agent.model,
        "max_cycles":    agent.max_cycles,
        "system_prompt": agent.system,
        "enabled_tools": list(agent.enabled_tools),
    }


def _init_version(agent, db) -> str:
    """Return the current version_id for this session, creating one if needed."""
    latest = db.get_latest_version()
    if latest is None:
        return db.create_version("initial", _agent_config_snapshot(agent))
    if db.get_version_run_count(latest["version_id"]) > 0:
        return db.create_version("server reload", _agent_config_snapshot(agent))
    return latest["version_id"]


def serve(agent, port: int = 5050) -> None:
    from . import _db
    _db.init_db()

    current_version = {"id": _init_version(agent, _db)}

    static_dir = Path(__file__).parent / "static"
    app = Flask(__name__, static_folder=str(static_dir))

    @app.route("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.route("/run")
    def run():
        question = request.args.get("question", "")
        event_queue: queue.Queue = queue.Queue()
        cancelled = threading.Event()

        def worker():
            try:
                def emit_unless_cancelled(event: dict) -> None:
                    if not cancelled.is_set():
                        event_queue.put(event)

                agent.run(question, emit=emit_unless_cancelled,
                          _version_id=current_version["id"], _stop=cancelled)
            except Exception as exc:
                if not cancelled.is_set():
                    event_queue.put({"type": "error", "message": str(exc)})
            finally:
                event_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def stream():
            try:
                while True:
                    try:
                        event = event_queue.get(timeout=60)
                    except queue.Empty:
                        yield 'data: {"type":"heartbeat"}\n\n'
                        continue
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
            except GeneratorExit:
                # client disconnected — signal worker to stop emitting
                cancelled.set()

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/versions")
    def versions():
        return jsonify(_db.list_versions())

    @app.route("/runs")
    def runs():
        limit      = min(int(request.args.get("limit", 50)), 200)
        version_id = request.args.get("version_id")
        return jsonify(_db.list_runs(limit, version_id=version_id))

    @app.route("/runs/<run_id>")
    def run_detail(run_id: str):
        row = _db.get_run(run_id)
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(row)

    @app.route("/config", methods=["GET"])
    def get_config():
        all_tools = [agent._tool_name(t) for t in agent._tools]
        return jsonify({
            "label":         agent.label,
            "model":         agent.model,
            "max_cycles":    agent.max_cycles,
            "system_prompt": agent.system,
            "enabled_tools": list(agent.enabled_tools),
            "all_tools":     all_tools,
            "agent_tools":   [],
        })

    @app.route("/config", methods=["POST"])
    def set_config():
        cfg       = request.json
        all_names = {agent._tool_name(t) for t in agent._tools}

        agent.label      = cfg.get("label",         agent.label)
        agent.model      = cfg.get("model",         agent.model)
        agent.max_cycles = cfg.get("max_cycles",    agent.max_cycles)
        agent.system     = cfg.get("system_prompt", agent.system)
        requested = set(cfg.get("enabled_tools", list(all_names)))
        agent.enabled_tools = requested & all_names

        # bump version if this one already has runs
        if _db.get_version_run_count(current_version["id"]) > 0:
            current_version["id"] = _db.create_version(
                "config saved", _agent_config_snapshot(agent)
            )

        return jsonify({"ok": True})

    print(f"\nOrcView running → http://localhost:{port}  [{agent.label}]")
    print("r + Enter to reload\n")
    _start_reload_listener()
    app.run(port=port, debug=False, threaded=True)
