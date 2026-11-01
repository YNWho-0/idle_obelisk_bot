"""Read-only phone dashboard for the bot (Flask).

  venv/bin/python -m obelisk dashboard            # serves on 0.0.0.0:8765

It only READS the bot's files (logs/, state.json, daily reports, screenshots)
and `systemctl --user is-active`; it never talks to the device or the game.

Access needs the secret token from dashboard.conf (gitignored): open
http://<pi>:8765/?t=<token> once; a cookie remembers it on that phone.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import subprocess
from pathlib import Path

import cv2
from flask import Flask, Response, abort, make_response, redirect, render_template_string, request

from .summary import RE_LINE, summarize

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
CONF = ROOT / "dashboard.conf"
COOKIE = "obelisk_dash"

RE_JOB = re.compile(r"^job (\w+) -> (\w+)$")
RE_GEM = re.compile(r"^gem guard: (\d+) -> (\d+) ok$")
RE_SESSION_END = re.compile(r"^session end: (\d+) s online; next session (\d\d:\d\d:\d\d)")
SAFE_NAME = re.compile(r"^[\w.\-]+\.(png|jpg)$")
SAFE_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")


def conf() -> dict:
    out = {}
    try:
        for line in CONF.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def ensure_conf() -> dict:
    c = conf()
    if not c.get("token"):
        c["token"] = secrets.token_urlsafe(24)
        CONF.write_text("# dashboard secret: open http://<pi>:8765/?t=<token> once per phone\n"
                        f"token={c['token']}\nport={c.get('port', '8765')}\n")
        CONF.chmod(0o600)
    return c


# ---------------------------------------------------------------- data

def log_lines(hours: float = 48):
    """[(datetime, level, logger, message)] from obelisk.log*, oldest first."""
    since = dt.datetime.now() - dt.timedelta(hours=hours)
    out = []
    for path in sorted(LOGS.glob("obelisk.log*")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            m = RE_LINE.match(raw)
            if not m:
                continue
            t = dt.datetime.fromisoformat(f"{m.group(1)} {m.group(2)}")
            if t >= since:
                out.append((t, m.group(3), m.group(4), m.group(5)))
    out.sort(key=lambda r: r[0])
    return out


def service_state(unit: str = "obelisk-bot") -> str:
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", unit],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def state() -> dict:
    try:
        return json.loads((LOGS / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def fmt_playtime(sec) -> str:
    if not sec:
        return "-"
    d, r = divmod(int(sec), 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{d:02d}d {h:02d}h {m:02d}m {s:02d}s"


def collect() -> dict:
    rows = log_lines(48)
    today = dt.date.today()
    st = state()
    jobs_today, sessions, online = {}, 0, 0
    gems = []
    last_start = last_end = None
    next_session = None
    last_session_len = None
    for t, level, _, msg in rows:
        if m := RE_GEM.match(msg):
            gems.append((t, int(m.group(2))))
        if msg == "session start":
            last_start = t
        if m := RE_SESSION_END.match(msg):
            last_end, last_session_len, next_session = t, int(m.group(1)), m.group(2)
            if t.date() == today:
                sessions += 1
                online += int(m.group(1))
        if t.date() == today and (m := RE_JOB.match(msg)):
            key = f"{m.group(1)} {m.group(2)}"
            jobs_today[key] = jobs_today.get(key, 0) + 1
    in_session = last_start is not None and (last_end is None or last_start > last_end)
    events = [r for r in rows if "device: press" not in r[2] + r[3]
              and not r[3].startswith(("gem guard", "interrupt: offline"))][-40:]
    failures = sorted((p for p in (LOGS / "failures").glob("*.png")), reverse=True)[:12]
    reports = sorted((p.stem for p in (LOGS / "daily").glob("*.txt")), reverse=True)
    return {
        "now": dt.datetime.now(),
        "service": service_state(),
        "in_session": in_session,
        "last_end": last_end, "last_len": last_session_len, "next_session": next_session,
        "sessions_today": sessions, "online_today_min": online / 60,
        "founder": jobs_today.get("founder claimed", 0),
        "lootbug": jobs_today.get("lootbug looted", 0),
        "freebie": jobs_today.get("freebie claimed", 0),
        "gems": gems[-300:],
        "gem_now": gems[-1][1] if gems else None,
        "cloud_conflict": st.get("cloud_conflict"),
        "cloud_last": fmt_playtime(st.get("cloud_last_upload")),
        "events": events,
        "failures": [p.name for p in failures],
        "reports": reports,
        "paused_until": st.get("paused_until"),
        "session_now": st.get("session_now"),
        "latest_mtime": (dt.datetime.fromtimestamp((LOGS / "latest.jpg").stat().st_mtime)
                         if (LOGS / "latest.jpg").exists() else None),
    }


def gem_svg(points, w=640, h=160) -> str:
    if len(points) < 2:
        return ""
    t0, t1 = points[0][0], points[-1][0]
    span = max((t1 - t0).total_seconds(), 1)
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    rng = max(hi - lo, 1)
    pts = " ".join(f"{(t - t0).total_seconds() / span * (w - 20) + 10:.1f},"
                   f"{h - 18 - (v - lo) / rng * (h - 36):.1f}" for t, v in points)
    return (f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="gems over time">'
            f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2.5" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<text x="10" y="14" class="axis">{hi}</text>'
            f'<text x="10" y="{h - 4}" class="axis">{lo}</text>'
            f'<text x="{w - 10}" y="{h - 4}" class="axis" text-anchor="end">'
            f'{t0:%a %H:%M} – {t1:%a %H:%M}</text></svg>')


# ---------------------------------------------------------------- web

PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Obelisk bot</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--fg:#1b1b1b;--muted:#6b6b6b;--line:#e3e3df;--accent:#2f6fde;
--ok:#1f8a4c;--warn:#b7791f;--bad:#c0392b}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1e1e1e;--fg:#ececec;--muted:#9a9a9a;
--line:#2c2c2c;--accent:#6ea0ff;--ok:#4cc380;--warn:#e0a84a;--bad:#ef6b5e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:720px;margin:0 auto;padding:14px 16px 40px}
h1{font-size:20px;margin:4px 0 2px}h2{font-size:15px;margin:0 0 10px;color:var(--muted);
font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.sub{color:var(--muted);font-size:13px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:12px 0}
.badge{display:inline-block;padding:3px 10px;border-radius:99px;font-weight:600;font-size:13px}
.ok{background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok)}
.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
.bad{background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.tile{border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.tile b{display:block;font-size:22px}.tile span{color:var(--muted);font-size:12px}
.chart{width:100%;height:auto}.axis{fill:var(--muted);font-size:12px}
.shot{width:100%;max-width:360px;border-radius:10px;border:1px solid var(--line);display:block;margin:auto}
.gallery{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.gallery a{display:block;font-size:10px;color:var(--muted);word-break:break-all;text-decoration:none}
.gallery img{width:100%;border-radius:6px;border:1px solid var(--line)}
.ev{font:12px/1.5 ui-monospace,Menlo,monospace;overflow-x:auto;white-space:nowrap}
.ev div{padding:1px 0}.ev .WARNING{color:var(--warn)}.ev .ERROR,.ev .CRITICAL{color:var(--bad)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px}.kv span:nth-child(odd){color:var(--muted)}
a{color:var(--accent)}
.ctl{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:4px}
.ctl button{font:inherit;font-size:14px;padding:7px 12px;border-radius:9px;border:1px solid var(--line);
background:var(--card);color:var(--fg)}.ctl button:disabled{opacity:.5}ul{margin:0;padding-left:18px}pre{white-space:pre-wrap;font-size:13px}
</style></head><body><main>
<h1>Idle Obelisk bot</h1>
<div class="sub">updated {{ d.now.strftime('%a %H:%M:%S') }} · refreshes every minute</div>

<div class="card">
 <h2>Status</h2>
 {% if d.service != 'active' %}<span class="badge bad">bot {{ d.service }}</span>
 {% elif d.paused_until %}<span class="badge warn">paused {{ 'until resumed' if d.paused_until == 'forever' else 'until ' + pause_until_txt }}</span>
 {% elif d.cloud_conflict %}<span class="badge warn">paused: cloud conflict</span>
 {% elif d.in_session %}<span class="badge ok">in a session now</span>
 {% else %}<span class="badge ok">running · game in background</span>{% endif %}
 <div class="kv" style="margin-top:10px">
  <span>Last session</span><span>{% if d.last_end %}{{ d.last_end.strftime('%H:%M') }} ({{ d.last_len }} s in front){% else %}-{% endif %}</span>
  <span>Next session</span><span>{{ d.next_session or '-' }}</span>
  <span>Gems</span><span>{{ d.gem_now if d.gem_now is not none else '-' }}</span>
  <span>Controls</span><span>
   <form method="post" action="control" class="ctl">
    {% if d.paused_until %}<button name="a" value="resume">Resume</button>
    {% else %}<button name="a" value="pause1">Pause 1 h</button><button name="a" value="pause4">Pause 4 h</button><button name="a" value="pause">Pause until resumed</button>{% endif %}
    <button name="a" value="now" {{ 'disabled' if d.paused_until or d.session_now or d.in_session }}>{{ 'Session requested…' if d.session_now else 'Run a session now' }}</button>
   </form>
   <span class="sub">Pause before playing on the Pi; a running session finishes first.</span></span>
  <span>Cloud</span><span>{% if d.cloud_conflict %}<b style="color:var(--warn)">conflict:</b> {{ d.cloud_conflict }}{% else %}in sync · last upload {{ d.cloud_last }}{% endif %}</span>
 </div>
</div>

<div class="card">
 <h2>Today</h2>
 <div class="tiles">
  <div class="tile"><b>{{ d.founder }}</b><span>founder crates</span></div>
  <div class="tile"><b>{{ d.lootbug }}</b><span>lootbug loots</span></div>
  <div class="tile"><b>{{ d.freebie }}</b><span>freebie claims</span></div>
  <div class="tile"><b>{{ d.sessions_today }}</b><span>sessions</span></div>
  <div class="tile"><b>{{ '%.1f' % d.online_today_min }}</b><span>min in front</span></div>
  <div class="tile"><b>{{ d.failures|length }}</b><span>failure shots</span></div>
 </div>
 <p style="margin:10px 0 0"><a href="report/today">Today's full report →</a></p>
</div>

<div class="card"><h2>Gems (48 h)</h2>{{ chart|safe if chart else 'not enough readings yet' }}</div>

<div class="card"><h2>Latest screen</h2>
 {% if d.latest_mtime %}<a href="latest.jpg"><img class="shot" src="latest.jpg?v={{ d.latest_mtime.timestamp()|int }}" alt="last session screenshot"></a>
 <div class="sub" style="text-align:center;margin:6px 0 0">end of the session at {{ d.latest_mtime.strftime('%a %H:%M') }}</div>
 {% else %}none yet (saved at the end of each session){% endif %}
</div>

<div class="card"><h2>Recent activity</h2><div class="ev">
 {% for t, lvl, lg, msg in d.events|reverse %}<div class="{{ lvl }}">{{ t.strftime('%H:%M:%S') }} {{ msg }}</div>{% endfor %}
</div></div>

<div class="card"><h2>Daily reports</h2>
 {% if d.reports %}<ul>{% for r in d.reports[:30] %}<li><a href="report/{{ r }}">{{ r }}</a></li>{% endfor %}</ul>
 {% else %}first one is written at 00:05{% endif %}
</div>

<div class="card"><h2>Failure screenshots</h2>
 {% if d.failures %}<div class="gallery">{% for f in d.failures %}<a href="fail/{{ f }}"><img loading="lazy" src="fail/{{ f }}?w=200" alt="">{{ f[9:30] }}</a>{% endfor %}</div>
 {% else %}none{% endif %}
</div>
</main></body></html>"""

REPORT = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Report {{ day }}</title>
<style>body{font:14px/1.5 ui-monospace,Menlo,monospace;margin:16px;background:#f6f6f4;color:#1b1b1b}
@media (prefers-color-scheme:dark){body{background:#141414;color:#ececec}}pre{white-space:pre-wrap}
a{color:#6ea0ff}</style></head><body><a href="../">← dashboard</a><pre>{{ text }}</pre></body></html>"""


def create_app() -> Flask:
    app = Flask(__name__)
    token = ensure_conf()["token"]

    @app.before_request
    def auth():
        if request.args.get("t") == token:
            resp = make_response(redirect(request.path))
            resp.set_cookie(COOKIE, token, max_age=365 * 86400, httponly=True, samesite="Lax")
            return resp
        if not secrets.compare_digest(request.cookies.get(COOKIE, ""), token):
            abort(403)

    @app.get("/")
    def index():
        d = collect()
        pu = d["paused_until"]
        txt = (dt.datetime.fromtimestamp(float(pu)).strftime("%H:%M")
               if pu not in (None, "forever") else "")
        return render_template_string(PAGE, d=d, chart=gem_svg(d["gems"]), pause_until_txt=txt)

    @app.post("/control")
    def control():
        from .jobs import State
        st = State()
        a = request.form.get("a")
        now = dt.datetime.now().timestamp()
        if a == "pause1":
            st.set("paused_until", now + 3600)
        elif a == "pause4":
            st.set("paused_until", now + 4 * 3600)
        elif a == "pause":
            st.set("paused_until", "forever")
        elif a == "resume":
            st.set("paused_until", None)
        elif a == "now":
            st.set("session_now", True)
        else:
            abort(400)
        return redirect("./", code=303)

    @app.get("/latest.jpg")
    def latest():
        p = LOGS / "latest.jpg"
        if not p.exists():
            abort(404)
        return Response(p.read_bytes(), mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/fail/<name>")
    def failure(name):
        if not SAFE_NAME.match(name):
            abort(404)
        p = LOGS / "failures" / name
        if not p.exists():
            abort(404)
        img = cv2.imread(str(p))
        w = request.args.get("w", type=int)
        if img is not None and w and 50 <= w < img.shape[1]:
            img = cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return Response(buf.tobytes(), mimetype="image/jpeg")

    @app.get("/report/<day>")
    def report(day):
        if day == "today":
            day = dt.date.today().isoformat()
            text = summarize(day)
        elif SAFE_DATE.match(day):
            p = LOGS / "daily" / f"{day}.txt"
            text = p.read_text() if p.exists() else summarize(day)
        else:
            abort(404)
        return render_template_string(REPORT, day=day, text=text)

    return app


def serve(host: str = "0.0.0.0", port: int | None = None) -> None:
    c = ensure_conf()
    app = create_app()
    app.run(host=host, port=port or int(c.get("port", 8765)), threaded=True, use_reloader=False)
