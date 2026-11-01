"""
Lightweight Flask dashboard: status, live log stream, last screenshot,
pause/resume, and manual workflow triggers.

Deliberately polling-based (browser fetch() every couple seconds) rather
than websockets/Socket.IO, to keep dependencies minimal on a Pi -- an
idle game's UI doesn't change fast enough to need push updates.

Security: there is NO authentication by default. This is meant for your
own LAN. If you set dashboard.host to 0.0.0.0 to reach it from your
phone, at minimum set dashboard.token in config.yaml (see README) so a
random device on the network can't pause your bot or trigger workflows.
"""

from __future__ import annotations

import os
import logging
from flask import Flask, jsonify, request, Response, send_from_directory, abort

logger = logging.getLogger("idle_obelisk_bot.dashboard")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def create_app(state, bot, dashboard_cfg: dict) -> Flask:
    token = dashboard_cfg.get("token") or None

    app = Flask(
        __name__,
        static_folder=os.path.join(BASE_DIR, "static"),
        template_folder=os.path.join(BASE_DIR, "templates"),
    )

    def check_auth():
        if not token:
            return
        supplied = request.headers.get("X-Auth-Token") or request.args.get("token")
        if supplied != token:
            abort(401)

    @app.before_request
    def _auth():
        if request.path.startswith("/api/"):
            check_auth()

    @app.route("/")
    def index():
        return send_from_directory(app.template_folder, "index.html")

    @app.route("/api/status")
    def api_status():
        snap = state.snapshot()
        snap["workflows"] = [
            {
                "name": w.name,
                "trigger": w.trigger,
                "interval_seconds": w.interval_seconds,
                "last_run": snap["workflow_last_run"].get(w.name, 0),
            }
            for w in bot.workflows.values()
        ]
        return jsonify(snap)

    @app.route("/api/logs")
    def api_logs():
        limit = int(request.args.get("limit", 200))
        return jsonify(state.get_logs(limit))

    @app.route("/api/screenshot")
    def api_screenshot():
        jpeg = state.get_screenshot()
        if jpeg is None:
            abort(404)
        return Response(jpeg, mimetype="image/jpeg")

    @app.route("/api/pause", methods=["POST"])
    def api_pause():
        bot.set_tasks_enabled(False)
        return jsonify({"ok": True, "tasks_enabled": False})

    @app.route("/api/resume", methods=["POST"])
    def api_resume():
        bot.set_tasks_enabled(True)
        return jsonify({"ok": True, "tasks_enabled": True})

    @app.route("/api/workflow/<name>/run", methods=["POST"])
    def api_run_workflow(name):
        try:
            bot.request_workflow(name)
        except KeyError:
            abort(404)
        return jsonify({"ok": True, "queued": name})

    return app


def run_dashboard(state, bot, dashboard_cfg: dict):
    app = create_app(state, bot, dashboard_cfg)
    host = dashboard_cfg.get("host", "127.0.0.1")
    port = dashboard_cfg.get("port", 6001)
    logger.info("dashboard listening on http://%s:%s", host, port)
    # use_reloader must stay False: this runs inside a background thread,
    # and Flask's reloader tries to spawn a subprocess otherwise.
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
