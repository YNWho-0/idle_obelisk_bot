const $ = (sel) => document.querySelector(sel);

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

function fmtSeconds(s) {
  if (!s || s < 0) return "-";
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(s)}s`;
}

async function refreshStatus() {
  try {
    const data = await fetchJSON("/api/status");
    const pill = $("#status-pill");

    if (data.last_error) {
      pill.textContent = "error";
      pill.className = "pill err";
    } else if (data.tasks_enabled) {
      pill.textContent = "running";
      pill.className = "pill ok";
    } else {
      pill.textContent = "paused";
      pill.className = "pill warn";
    }

    const counts = Object.entries(data.task_counts)
      .map(([name, n]) => `<li><span>${name}</span><span>${n}</span></li>`)
      .join("") || "<li class='muted'>none yet</li>";
    $("#task-counts").innerHTML = counts;

    const workflows = (data.workflows || [])
      .map((w) => {
        const last = w.last_run
          ? `last ran ${fmtSeconds(Date.now() / 1000 - w.last_run)} ago`
          : "never run";
        return `<li><span>${w.name} <span class="muted">(${w.trigger}, ${last})</span></span>
          <button data-run="${w.name}">Run now</button></li>`;
      })
      .join("") || "<li class='muted'>none configured</li>";
    $("#workflows").innerHTML = workflows;
    document.querySelectorAll("[data-run]").forEach((btn) => {
      btn.onclick = () => fetchJSON(`/api/workflow/${btn.dataset.run}/run`, { method: "POST" });
    });

    $("#screenshot-age").textContent = data.last_screenshot_at
      ? `updated ${fmtSeconds(Date.now() / 1000 - data.last_screenshot_at)} ago · uptime ${fmtSeconds(data.uptime_seconds)}`
      : "no screenshot yet";
  } catch (e) {
    $("#status-pill").textContent = "disconnected";
    $("#status-pill").className = "pill err";
  }
}

async function refreshLogs() {
  try {
    const lines = await fetchJSON("/api/logs?limit=200");
    const el = $("#log");
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 10;
    el.textContent = lines.join("\n");
    if (atBottom) el.scrollTop = el.scrollHeight;
  } catch (e) {
    /* transient, ignore */
  }
}

function refreshScreenshot() {
  $("#screenshot").src = `/api/screenshot?t=${Date.now()}`;
}

$("#pause-btn").onclick = () => fetchJSON("/api/pause", { method: "POST" }).then(refreshStatus);
$("#resume-btn").onclick = () => fetchJSON("/api/resume", { method: "POST" }).then(refreshStatus);

refreshStatus();
refreshLogs();
refreshScreenshot();
setInterval(refreshStatus, 2000);
setInterval(refreshLogs, 3000);
setInterval(refreshScreenshot, 2000);
