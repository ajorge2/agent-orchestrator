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


def create_app(agent):
    import copy
    import secrets
    import time
    from flask import g
    from urllib.parse import urlsplit
    static_dir = Path(__file__).parent / "static"
    app = Flask(__name__, static_folder=str(static_dir))
    app.config["MAX_CONTENT_LENGTH"] = 64000
    sessions = {}
    session_lock = threading.Lock()

    @app.before_request
    def private_session():
        # MCP endpoints have no visitor state and their own transport checks.
        if request.path.startswith("/mcp/"):
            return
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            return jsonify(error="Use the same-origin application"), 403
        if request.path == "/run" and request.args.get("question") is not None:
            return jsonify(error="Submit questions in the POST body"), 405
        sid = request.cookies.get("orcview_session", "")
        with session_lock:
            now = time.monotonic()
            for key in list(sessions):
                if sessions[key]["expires"] < now:
                    del sessions[key]
            if sid not in sessions:
                if len(sessions) >= 256:
                    return jsonify(error="Demo busy; retry later"), 429
                sid = secrets.token_urlsafe(32)
                local_agent = copy.deepcopy(agent)
                local_agent.record_runs = False
                sessions[sid] = {"agent": local_agent, "runs": [], "tickets": {}, "expires": now + 3600, "busy": False, "version": 1, "versions": [{"version_id": "v1", "label": "Initial session configuration"}]}
            g.demo_session = sessions[sid]
            g.demo_sid = sid
            g.demo_session["expires"] = now + 3600

    @app.after_request
    def private_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if hasattr(g, "demo_sid"):
            response.set_cookie("orcview_session", g.demo_sid, httponly=True,
                                secure=request.is_secure or bool(os.getenv("PORT")), samesite="Strict", max_age=3600)
        return response

    @app.route("/run", methods=["POST"])
    def prepare_run():
        question = str((request.get_json(silent=True) or {}).get("question", "")).strip()
        if not question or len(question) > 8000:
            return jsonify(error="Question must contain 1–8000 characters"), 400
        state = g.demo_session
        if len(state["tickets"]) >= 4:
            return jsonify(error="Too many pending runs"), 429
        token = secrets.token_urlsafe(32)
        state["tickets"][token] = question
        return jsonify(ticket=token)

    @app.route("/privacy")
    def privacy():
        return Response((Path(__file__).parent.parent / "PRIVACY.md").read_text(), mimetype="text/plain")

    @app.route("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.route("/run")
    def run():
        state = g.demo_session
        agent = copy.deepcopy(state["agent"])
        version_id = "v" + str(state["version"])
        question = state["tickets"].pop(request.args.get("ticket", ""), None)
        if question is None:
            return jsonify(error="Unknown or expired run ticket"), 404
        with session_lock:
            if state["busy"]:
                return jsonify(error="A run is already active"), 409
            state["busy"] = True
        event_queue: queue.Queue = queue.Queue()
        cancelled = threading.Event()

        def worker():
            try:
                events = []
                def emit_unless_cancelled(event: dict) -> None:
                    events.append(event)
                    if not cancelled.is_set():
                        event_queue.put(event)

                agent.run(question, emit=emit_unless_cancelled, _stop=cancelled)
                metrics = next((e for e in reversed(events) if e.get("type") == "run_metrics" and not e.get("_sub_run_id")), {})
                state["runs"].insert(0, {**metrics, "question": question, "events": events, "version_id": version_id, "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()})
                del state["runs"][20:]
            except Exception as exc:
                if not cancelled.is_set():
                    event_queue.put({"type": "error", "message": str(exc)})
            finally:
                state["busy"] = False
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
        return jsonify([{**v, "run_count": sum(r.get("version_id") == v["version_id"] for r in g.demo_session["runs"])} for v in g.demo_session["versions"]])

    @app.route("/runs")
    def runs():
        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 200))
        except ValueError:
            return jsonify(error="Invalid limit"), 400
        return jsonify([{k: v for k, v in row.items() if k != "events"} for row in g.demo_session["runs"] if not request.args.get("version_id") or row.get("version_id") == request.args["version_id"]][:limit])

    @app.route("/runs/<run_id>")
    def run_detail(run_id: str):
        row = next((r for r in g.demo_session["runs"] if r.get("run_id") == run_id), None)
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(row)

    @app.route("/config", methods=["GET"])
    def get_config():
        agent = g.demo_session["agent"]
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
        agent = g.demo_session["agent"]
        cfg = request.get_json(silent=True)
        if not isinstance(cfg, dict):
            return jsonify(error="Expected a JSON object"), 400
        all_names = {agent._tool_name(t) for t in agent._tools}

        agent.label      = cfg.get("label",         agent.label)
        agent.model      = cfg.get("model",         agent.model)
        agent.max_cycles = cfg.get("max_cycles",    agent.max_cycles)
        agent.system     = cfg.get("system_prompt", agent.system)
        requested = set(cfg.get("enabled_tools", list(all_names)))
        agent.enabled_tools = requested & all_names

        agent.max_cycles = max(1, min(int(agent.max_cycles), 12))
        state = g.demo_session
        if any(r.get("version_id") == "v" + str(state["version"]) for r in state["runs"]):
            state["version"] += 1
            state["versions"].append({"version_id": "v" + str(state["version"]), "label": "Session configuration " + str(state["version"])})
            del state["versions"][:-20]
        return jsonify({"ok": True})

    return app


def serve(agent, port: int = 5050) -> None:
    app = create_app(agent)
    from .mcp_public import create_public_app
    import uvicorn
    uvicorn.run(create_public_app(app), host="0.0.0.0" if os.getenv("PORT") else "127.0.0.1", port=port,
                log_level="critical", access_log=False)
