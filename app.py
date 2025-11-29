#!/usr/bin/env python3
"""
Minimal web UI for YouTube downloads with batch support.
- Browse channel (auto /videos) and pick items.
- Paste arbitrary URLs (one per line).
- Choose audio/video + format.
- Starts background jobs; frontend polls status.
Requires: yt-dlp, ffmpeg in PATH, Flask.
Run: python app.py  (opens http://127.0.0.1:5000)
"""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

from flask import Flask, Response, jsonify, request
from yt_dlp import YoutubeDL

app = Flask(__name__)
FFMPEG_BIN = Path(__file__).parent / "ffmpeg" / "ffmpeg-8.0.1-essentials_build" / "bin"
DEFAULT_WORKERS = 4
MAX_WORKERS = 8


LANG = {
    "en": {
        "title": "YouTube Batch Downloader",
        "channel_label": "Channel URL (handle/channel/user/videos)",
        "paste_label": "Paste URLs (one per line)",
        "fetch": "Fetch",
        "add": "Add selected",
        "queue": "Queue",
        "download": "Start download",
        "mode": "Mode",
        "video": "Video",
        "audio": "Audio",
        "format": "Format",
        "status": "Status",
        "clear": "Clear queue",
        "parallel": "Parallel jobs",
    },
    "zh": {
        "title": "YouTube 批量下载器",
        "channel_label": "频道链接（handle/channel/user/videos）",
        "paste_label": "粘贴链接（一行一个）",
        "fetch": "获取列表",
        "add": "加入选中",
        "queue": "待下载",
        "download": "开始下载",
        "mode": "模式",
        "video": "视频",
        "audio": "音频",
        "format": "格式",
        "status": "状态",
        "clear": "清空",
        "parallel": "并行数",
    },
}

jobs_lock = threading.Lock()
jobs: Dict[str, dict] = {}


def normalize_channel_url(url: str) -> str:
    """Ensure channel URL points to a list of real videos."""
    url = (url or "").strip()
    if not url:
        return url
    if url.startswith("@"):
        url = f"https://www.youtube.com/{url}"
    if url.startswith("youtube.com/"):
        url = "https://" + url
    lower = url.lower().rstrip("/")
    if any(tag in lower for tag in ["/videos", "/streams", "/shorts", "/playlist", "/watch", "list="]):
        return url
    if any(key in lower for key in ["/channel/", "/user/", "/c/", "/@"]):
        return url.rstrip("/") + "/videos"
    return url


def build_opts(is_video: bool, fmt_choice: str):
    if is_video:
        fmt_map = {
            "best": "bestvideo+bestaudio/best",
            "1080p": "bestvideo[height<=1080]+bestaudio/best",
            "720p": "bestvideo[height<=720]+bestaudio/best",
        }
        opts = {
            "format": fmt_map.get(fmt_choice, fmt_map["best"]),
            "merge_output_format": "mp4",
            "outtmpl": "%(title)s.%(ext)s",
            "quiet": True,
        }
        if FFMPEG_BIN.exists():
            opts["ffmpeg_location"] = str(FFMPEG_BIN)
        return opts
    codec = {"mp3": "mp3", "m4a": "m4a", "opus": "opus"}.get(fmt_choice, "mp3")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": "%(title)s.%(ext)s",
        "quiet": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": codec,
                "preferredquality": "192",
            }
        ],
    }
    if FFMPEG_BIN.exists():
        opts["ffmpeg_location"] = str(FFMPEG_BIN)
    return opts


def fetch_channel_entries(channel_url: str, start: int, count: int) -> List[dict]:
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
        "playliststart": start + 1,
        "playlistend": start + count,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    return info.get("entries", [])


def run_job(job_id: str, urls: List[str], is_video: bool, fmt_choice: str, workers: int):
    def download_one(u: str):
        try:
            opts = build_opts(is_video, fmt_choice)
            with YoutubeDL(opts) as ydl:
                ydl.download([u])
            return "done", ""
        except Exception as exc:  # pragma: no cover - runtime error display
            return "error", str(exc)

    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["items"] = [{"url": u, "status": "pending", "message": ""} for u in urls]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {}
        for idx, item in enumerate(job["items"]):
            item["status"] = "running"
            future_map[executor.submit(download_one, item["url"])] = idx

        for future in as_completed(future_map):
            idx = future_map[future]
            status, message = future.result()
            with jobs_lock:
                if job_id not in jobs:
                    continue
                job_item = job["items"][idx]
                job_item["status"] = status
                job_item["message"] = message

    with jobs_lock:
        if job_id in jobs:
            job["status"] = "done" if all(i["status"] == "done" for i in job["items"]) else "error"


@app.route("/api/channel")
def api_channel():
    raw_url = request.args.get("url", "")
    start = int(request.args.get("start", "0"))
    count = int(request.args.get("count", "10"))
    url = normalize_channel_url(raw_url)
    try:
        entries = fetch_channel_entries(url, start, count)
        simplified = [
            {
                "title": e.get("title", "N/A"),
                "url": e.get("url") or e.get("webpage_url"),
            }
            for e in entries
        ]
        return jsonify({"entries": simplified})
    except Exception as exc:  # pragma: no cover - runtime error display
        return jsonify({"error": str(exc)}), 400


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    urls = [u.strip() for u in data.get("urls", []) if u and u.strip()]
    mode = data.get("mode", "video")
    fmt_choice = data.get("format", "best")
    workers = data.get("workers", DEFAULT_WORKERS)
    try:
        workers = max(1, min(int(workers), MAX_WORKERS))
    except Exception:
        workers = DEFAULT_WORKERS
    if not urls:
        return jsonify({"error": "No URLs provided"}), 400
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "pending",
            "items": [],
            "workers": workers,
        }
    thread = threading.Thread(
        target=run_job, args=(job_id, urls, mode == "video", fmt_choice, workers), daemon=True
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<job_id>")
def api_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/")
def index():
    # Inline HTML for simplicity; avoids external assets.
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <style>
    :root {
      --bg: #050913;
      --glass: rgba(11, 16, 28, 0.82);
      --glass-2: rgba(23, 33, 54, 0.72);
      --border: rgba(255, 255, 255, 0.08);
      --text: #e9f1ff;
      --muted: #9fb2ca;
      --accent: #5eead4;
      --accent-2: #8b5cf6;
      --accent-3: #22d3ee;
      --danger: #f87171;
      --success: #34d399;
      --glow: rgba(94, 234, 212, 0.14);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Space Grotesk", "Segoe UI", system-ui, -apple-system, sans-serif;
      background:
        radial-gradient(circle at 15% 20%, rgba(94,234,212,0.18), transparent 28%),
        radial-gradient(circle at 80% 0%, rgba(139,92,246,0.18), transparent 30%),
        radial-gradient(circle at 65% 75%, rgba(34,211,238,0.14), transparent 26%),
        var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 24px;
    }
    h1 { margin: 6px 0 6px; font-weight: 800; letter-spacing: 0.6px; }
    .grid {
      display: grid;
      gap: 18px;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }
    .card {
      background: linear-gradient(150deg, var(--glass), var(--glass-2));
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 24px 60px rgba(0,0,0,0.36);
      backdrop-filter: blur(14px);
    }
    label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }
    input[type="text"], textarea, select {
      width: 100%;
      background: rgba(7, 11, 20, 0.9);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 11px 12px;
      font-size: 14px;
      outline: none;
      transition: border 0.15s ease, box-shadow 0.15s ease, transform 0.08s ease;
    }
    input[type="text"]:focus, textarea:focus, select:focus {
      border-color: rgba(94,234,212,0.5);
      box-shadow: 0 0 0 3px var(--glow);
      transform: translateY(-1px);
    }
    textarea { min-height: 140px; resize: vertical; }
    button {
      cursor: pointer;
      background: linear-gradient(120deg, var(--accent), var(--accent-2));
      color: #0b1220;
      border: none;
      border-radius: 12px;
      padding: 11px 14px;
      font-weight: 800;
      letter-spacing: 0.3px;
      transition: transform 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease;
      box-shadow: 0 12px 30px rgba(94,234,212,0.18);
    }
    button:hover { transform: translateY(-1px); filter: brightness(1.05); box-shadow: 0 16px 34px rgba(139,92,246,0.22); }
    button.secondary {
      background: rgba(12,18,30,0.9);
      color: var(--text);
      border: 1px solid var(--border);
      box-shadow: none;
    }
    .list {
      margin-top: 10px;
      max-height: 280px;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(12, 18, 30, 0.6);
    }
    .row {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 11px 12px;
      border-bottom: 1px solid var(--border);
      transition: background 0.12s ease, transform 0.08s ease;
    }
    .row:hover { background: rgba(255,255,255,0.02); transform: translateY(-1px); }
    .row:last-child { border-bottom: none; }
    .title { flex: 1; font-size: 14px; line-height: 1.4; }
    .status-pill {
      border-radius: 999px;
      padding: 2px 10px;
      font-size: 12px;
      border: 1px solid var(--border);
    }
    .done { color: var(--success); }
    .error { color: var(--danger); }
    .muted { color: var(--muted); }
    .lang-toggle { text-align: right; margin-bottom: 12px; color: var(--muted); font-size: 13px; }
    .lang-toggle button { margin-left: 6px; padding: 6px 10px; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--muted);
    }
    .hero {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 18px 20px;
      margin-bottom: 14px;
      border-radius: 18px;
      background: linear-gradient(120deg, rgba(94,234,212,0.14), rgba(139,92,246,0.12));
      border: 1px solid rgba(255,255,255,0.08);
      box-shadow: 0 22px 55px rgba(0,0,0,0.32);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      padding: 6px 10px;
      border-radius: 12px;
      background: rgba(94,234,212,0.12);
      border: 1px solid rgba(94,234,212,0.25);
      color: var(--text);
      letter-spacing: 0.2px;
    }
    .sub { margin: 4px 0 0; color: var(--muted); font-size: 14px; }
    .tagline {
      font-size: 14px;
      color: var(--muted);
      text-align: right;
      max-width: 220px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 10px;
      font-size: 12px;
      border-radius: 12px;
      background: rgba(255,255,255,0.04);
      border: 1px solid var(--border);
      color: var(--muted);
    }
  </style>
</head>
<body>
  <div class="lang-toggle">
    <span id="lang-label">Language:</span>
    <button class="secondary" onclick="setLang('zh')">中文</button>
    <button class="secondary" onclick="setLang('en')">English</button>
  </div>
  <div class="hero">
    <div>
      <div class="badge">Batch · Channel · Audio/Video</div>
      <h1 id="title">__TITLE_TEXT__</h1>
      <p class="sub">Queue videos from a channel or paste URLs, then download all at once.</p>
    </div>
    <div class="tagline">Local web app with yt-dlp + ffmpeg.<br/>No uploads, just file saves.</div>
  </div>
  <div class="grid">
    <div class="card">
      <label id="channel-label">__CHANNEL_LABEL__</label>
      <input id="channel-url" type="text" placeholder="https://www.youtube.com/@handle or /channel/ID" />
      <div style="margin-top:10px; display:flex; gap:8px; align-items:center;">
        <button onclick="fetchChannel()"><span id="fetch-btn">__FETCH__</span></button>
        <button class="secondary" onclick="addSelected()"><span id="add-btn">__ADD__</span></button>
      </div>
      <div class="list" id="channel-list"></div>
      <div style="margin-top:8px; display:flex; gap:8px;">
        <button class="secondary" onclick="pageChannel(-1)">Prev</button>
        <button class="secondary" onclick="pageChannel(1)">Next</button>
      </div>
    </div>
    <div class="card">
      <label id="paste-label">__PASTE__</label>
      <textarea id="urls-box" placeholder="https://youtu.be/..."></textarea>
      <div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
        <div>
          <label id="mode-label">__MODE__</label>
          <select id="mode" onchange="onModeChange()">
            <option value="video" id="video-label">__VIDEO__</option>
            <option value="audio" id="audio-label">__AUDIO__</option>
          </select>
        </div>
        <div>
          <label id="format-label">__FORMAT__</label>
          <select id="format"></select>
        </div>
        <button class="secondary" onclick="pushFromTextarea()">__ADD__</button>
      </div>
    </div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div id="queue-label" style="font-weight:700;">__QUEUE__</div>
        <div style="display:flex; gap:8px;">
          <button class="secondary" onclick="clearQueue()">__CLEAR__</button>
          <button onclick="startDownload()">__DOWNLOAD__</button>
        </div>
      </div>
      <div style="display:flex; gap:10px; align-items:center; margin:10px 0 6px;">
        <span id="parallel-label" class="chip" style="border:none; padding:0;">__PARALLEL__</span>
        <select id="workers" style="width:120px;"></select>
        <span class="chip">max __MAX_WORKERS__</span>
      </div>
      <div class="list" id="queue-list"></div>
    </div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div id="status-label" style="font-weight:700;">__STATUS__</div>
        <div id="job-id" class="muted"></div>
      </div>
      <div class="list" id="status-list"></div>
    </div>
  </div>

  <script>
    const LANG = __JSON_LANG__;
    const DEFAULT_WORKERS = __DEFAULT_WORKERS__;
    const MAX_WORKERS = __MAX_WORKERS__;
    let lang = 'en';
    let queue = [];
    let queueSet = new Set();
    let selectedChannel = new Map(); // url -> title
    let channelPage = 0;
    let lastChannelUrl = '';
    let activeJob = null;

    function setLang(code) {{
      lang = code;
      const t = LANG[code];
      document.getElementById('title').textContent = t.title;
      document.getElementById('channel-label').textContent = t.channel_label;
      document.getElementById('fetch-btn').textContent = t.fetch;
      document.getElementById('add-btn').textContent = t.add;
      document.getElementById('paste-label').textContent = t.paste_label;
      document.getElementById('mode-label').textContent = t.mode;
      document.getElementById('video-label').textContent = t.video;
      document.getElementById('audio-label').textContent = t.audio;
      document.getElementById('format-label').textContent = t.format;
      document.getElementById('queue-label').textContent = t.queue;
      document.getElementById('status-label').textContent = t.status;
      document.getElementById('status-label').textContent = t.status;
      document.getElementById('status-label').textContent = t.status;
      const parallel = document.getElementById('parallel-label');
      if (parallel) parallel.textContent = t.parallel;
      document.querySelector('button[onclick="startDownload()"]').textContent = t.download;
      document.querySelector('button[onclick="clearQueue()"]').textContent = t.clear;
      refreshFormats();
      refreshWorkers();
      renderQueue();
    }}

    function refreshFormats() {{
      const mode = document.getElementById('mode').value;
      const fmt = document.getElementById('format');
      fmt.innerHTML = '';
      let options = mode === 'video'
        ? [ ['best','Best'], ['1080p','1080p'], ['720p','720p'] ]
        : [ ['mp3','mp3'], ['m4a','m4a'], ['opus','opus'] ];
      for (const [val, label] of options) {{
        const o = document.createElement('option');
        o.value = val; o.textContent = label;
        fmt.appendChild(o);
      }}
    }}

    function onModeChange() {{ refreshFormats(); }}
    refreshFormats();
    refreshWorkers();

    function refreshWorkers() {{
      const sel = document.getElementById('workers');
      if (!sel) return;
      const current = parseInt(sel.value || DEFAULT_WORKERS, 10);
      sel.innerHTML = '';
      for (let i = 1; i <= MAX_WORKERS; i++) {{
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = `${{i}}x`;
        if (i === current) opt.selected = true;
        sel.appendChild(opt);
      }}
      if (!sel.value) sel.value = DEFAULT_WORKERS;
    }}

    async function fetchChannel() {{
      const url = document.getElementById('channel-url').value.trim();
      if (!url) return;
      if (lastChannelUrl !== url) channelPage = 0;
      lastChannelUrl = url;
      const start = channelPage * 10;
      const resp = await fetch(`/api/channel?url=${{encodeURIComponent(url)}}&start=${{start}}&count=10`);
      const data = await resp.json();
      const list = document.getElementById('channel-list');
      list.innerHTML = '';
      if (data.error) {{
        list.innerHTML = `<div class="row"><div class="title error">${{data.error}}</div></div>`;
        return;
      }}
      data.entries.forEach((e, idx) => {{
        const row = document.createElement('div');
        row.className = 'row';
        const checked = selectedChannel.has(e.url) ? 'checked' : '';
        row.innerHTML = `
          <input type="checkbox" data-url="${{e.url}}" data-title="${{e.title}}" ${checked} onchange="toggleSelect(this)" />
          <div class="title">${{idx + 1 + start}}. ${{e.title}}</div>
        `;
        list.appendChild(row);
      }});
    }}

    function pageChannel(delta) {{
      channelPage = Math.max(0, channelPage + delta);
      if (lastChannelUrl) fetchChannel();
    }}

    function toggleSelect(cb) {{
      const url = cb.dataset.url;
      const title = cb.dataset.title || url;
      if (cb.checked) {{
        selectedChannel.set(url, title);
      }} else {{
        selectedChannel.delete(url);
      }}
    }}

    function addSelected() {{
      selectedChannel.forEach((title, url) => {{
        if (!queueSet.has(url)) {{
          queue.push({{ url, title }});
          queueSet.add(url);
        }}
      }});
      renderQueue();
    }}

    function pushFromTextarea() {{
      const lines = document.getElementById('urls-box').value.split(/\\n+/).map(l => l.trim()).filter(Boolean);
      lines.forEach(u => {{
        if (!queueSet.has(u)) {{
          queue.push({{ url: u, title: u }});
          queueSet.add(u);
        }}
      }});
      renderQueue();
    }}

    function clearQueue() {{
      queue = [];
      queueSet.clear();
      renderQueue();
    }}

    function renderQueue() {{
      const list = document.getElementById('queue-list');
      list.innerHTML = '';
      queue.forEach((item, i) => {{
        const row = document.createElement('div');
        row.className = 'row';
        row.innerHTML = `
          <div class="title">${{i+1}}. ${{item.title || item.url}}</div>
          <button class="secondary" onclick="removeFromQueue(${{i}})">x</button>
        `;
        list.appendChild(row);
      }});
    }}

    function removeFromQueue(idx) {{
      if (queue[idx]) queueSet.delete(queue[idx].url);
      queue.splice(idx, 1);
      renderQueue();
    }}

    async function startDownload() {{
      if (!queue.length) return;
      const mode = document.getElementById('mode').value;
      const format = document.getElementById('format').value;
      const workers = parseInt(document.getElementById('workers').value || DEFAULT_WORKERS, 10);
      const urls = queue.map(q => q.url);
      const resp = await fetch('/api/download', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ urls, mode, format, workers }})
      }});
      const data = await resp.json();
      if (data.error) {{
        alert(data.error);
        return;
      }}
      activeJob = data.job_id;
      document.getElementById('job-id').textContent = data.job_id;
      pollJob();
    }}

    async function pollJob() {{
      if (!activeJob) return;
      const resp = await fetch(`/api/jobs/${{activeJob}}`);
      if (!resp.ok) return;
      const data = await resp.json();
      const list = document.getElementById('status-list');
      list.innerHTML = '';
      data.items.forEach((item, idx) => {{
        const row = document.createElement('div');
        row.className = 'row';
        const statusClass = item.status === 'done' ? 'done' : item.status === 'error' ? 'error' : 'muted';
        row.innerHTML = `
          <div class="title">${{idx+1}}. ${{item.url}}</div>
          <span class="status-pill ${{statusClass}}">${{item.status}}</span>
        `;
        list.appendChild(row);
      }});
      if (data.status !== 'done' && data.status !== 'error') {{
        setTimeout(pollJob, 1200);
      }}
    }}
  </script>
</body>
</html>
"""
    # Unescape doubled braces so CSS/JS render correctly.
    html = html.replace("{{", "{").replace("}}", "}")
    html = (
        html.replace("__TITLE__", f"{LANG['zh']['title']} / {LANG['en']['title']}")
        .replace("__TITLE_TEXT__", LANG["en"]["title"])
        .replace("__CHANNEL_LABEL__", LANG["en"]["channel_label"])
        .replace("__FETCH__", LANG["en"]["fetch"])
        .replace("__ADD__", LANG["en"]["add"])
        .replace("__PASTE__", LANG["en"]["paste_label"])
        .replace("__MODE__", LANG["en"]["mode"])
        .replace("__VIDEO__", LANG["en"]["video"])
        .replace("__AUDIO__", LANG["en"]["audio"])
        .replace("__FORMAT__", LANG["en"]["format"])
        .replace("__QUEUE__", LANG["en"]["queue"])
        .replace("__CLEAR__", LANG["en"]["clear"])
        .replace("__DOWNLOAD__", LANG["en"]["download"])
        .replace("__STATUS__", LANG["en"]["status"])
        .replace("__PARALLEL__", LANG["en"]["parallel"])
        .replace("__DEFAULT_WORKERS__", str(DEFAULT_WORKERS))
        .replace("__MAX_WORKERS__", str(MAX_WORKERS))
        .replace("__JSON_LANG__", json.dumps(LANG, ensure_ascii=False))
    )
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
