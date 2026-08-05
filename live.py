# live.py - Optimized for 24/7 Streaming with Facebook API Auto-Go-Live
import subprocess
import os
import time
import re
import json
import threading
import psutil
import shutil
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string
import imageio_ffmpeg

# Load environment variables
load_dotenv()

# Global status lock
status_lock = threading.Lock()

# Initial stream status
stream_status = {
    "status":        "starting",
    "start_time":    None,
    "restart_count": 0,
    "last_error":    None,
    "platform":      "facebook",
    "video_file":    "unknown",
    "fps":           0.0,
    "speed":         "0.0x",
    "bitrate":       "0 kb/s",
    "video_bitrate": "0 kb/s",
    "audio_bitrate": "0 kb/s",
    "frame_count":   0,
    "uptime":        "00:00:00",
    "video_resolution": "—",
    "video_codec":      "—",
    "video_fps_src":    "—",
    "audio_codec":      "—",
    "audio_sample_rate":"—",
    "audio_channels":   "—",
    "audio_src_bitrate_kbps": 128,
}

def get_config(key, default=None):
    return os.environ.get(key, default)

def create_facebook_live_video():
    """Create a new Live Video via Facebook Graph API and return the RTMP URL"""
    page_id = get_config("PAGE_ID")
    access_token = get_config("PAGE_ACCESS_TOKEN")
    
    if not page_id or not access_token:
        print("Error: PAGE_ID or PAGE_ACCESS_TOKEN missing in environment.")
        return None

    url = f"https://graph.facebook.com/v18.0/{page_id}/live_videos"
    params = {
        "access_token": access_token,
        "status": "LIVE_NOW", # Automatically go live when stream starts
        "title": get_config("STREAM_TITLE", "24/7 Live Stream"),
        "description": get_config("STREAM_DESCRIPTION", "Enjoy our continuous live stream!"),
    }

    try:
        response = requests.post(url, params=params, timeout=15)
        data = response.json()
        if "stream_url" in data:
            print(f"Successfully created Facebook Live Video: {data.get('id')}")
            return data["stream_url"]
        else:
            print(f"Facebook API Error: {data}")
            return None
    except Exception as e:
        print(f"Exception calling Facebook API: {e}")
        return None

def parse_bitrate_to_int(bitrate_str):
    if not bitrate_str: return 0
    bitrate_str = str(bitrate_str).lower().strip()
    match = re.match(r"(\d+)\s*([km]?)", bitrate_str)
    if not match: return 0
    val, unit = match.groups()
    val = int(val)
    if unit == 'm': return val * 1024
    return val

def _read_cgroup_value(*paths):
    for path in paths:
        try:
            if os.path.exists(path):
                value = Path(path).read_text().strip()
                if value and value != "max": return int(value)
        except (OSError, ValueError): continue
    return None

def system_metrics():
    cpu_model = get_config("CPU_MODEL", "Standard Instance")
    memory_limit_mb = int(get_config("MEMORY_LIMIT_MB", "2048"))
    memory_limit_bytes = memory_limit_mb * 1024 * 1024
    memory_used = _read_cgroup_value("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes")
    memory_max = _read_cgroup_value("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if memory_max and 0 < memory_max < 1024 ** 5: memory_limit = memory_max
    else: memory_limit = memory_limit_bytes
    if memory_used is None: memory_used = psutil.Process().memory_info().rss
    cpu_percent = psutil.cpu_percent(interval=None)
    process = psutil.Process()
    return {
        "cpu": round(cpu_percent, 1),
        "cpu_model": cpu_model,
        "cpu_cores": psutil.cpu_count() or 1,
        "process_cpu": round(process.cpu_percent(interval=None), 1),
        "ram": round(min(memory_used / memory_limit * 100, 100), 1) if memory_limit > 0 else 0,
        "memory_used_mb": round(memory_used / 1024 / 1024, 1),
        "memory_limit_mb": round(memory_limit / 1024 / 1024, 1),
        "process_memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
    }

app = Flask(__name__)

def probe_video(video_file):
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    if not os.path.exists(video_file): return
    try:
        result = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", video_file], capture_output=True, text=True, timeout=10)
        if result.returncode != 0: return
        info = json.loads(result.stdout)
        with status_lock:
            for s in info.get("streams", []):
                if s.get("codec_type") == "video":
                    stream_status["video_resolution"] = f"{s.get('width','?')}×{s.get('height','?')}"
                    stream_status["video_codec"] = s.get("codec_name", "—").upper()
                    rfr = s.get("r_frame_rate", "0/1")
                    try:
                        num, den = map(int, rfr.split("/")) if "/" in rfr else (float(rfr), 1)
                        fps_val = round(num / den, 2) if den else 0
                    except: fps_val = 0
                    stream_status["video_fps_src"] = f"{fps_val} fps"
                elif s.get("codec_type") == "audio":
                    stream_status["audio_codec"] = s.get("codec_name", "—").upper()
                    stream_status["audio_sample_rate"] = f"{int(s.get('sample_rate',0))//1000} kHz"
                    ch = s.get("channels", 0)
                    stream_status["audio_channels"] = "Stereo" if ch == 2 else ("Mono" if ch == 1 else str(ch))
                    br = int(s.get("bit_rate", 0))
                    if br > 0: stream_status["audio_src_bitrate_kbps"] = round(br / 1000)
    except Exception as e: print(f"ffprobe error: {e}")

def get_ffmpeg_command(rtmp_url):
    video_file = get_config("VIDEO_FILE", "video.mp4")
    cpu_cores = os.cpu_count() or 2
    video_bitrate = get_config("VIDEO_BITRATE", "4500k")
    audio_bitrate = get_config("AUDIO_BITRATE", "192k")
    preset = get_config("FFMPEG_PRESET", "ultrafast")
    fps = get_config("STREAM_FPS", "30")
    out_w = get_config("OUTPUT_WIDTH", "1080")
    out_h = get_config("OUTPUT_HEIGHT", "1920")
    try: gop = int(float(fps)) * 2
    except: gop = 60
    v_bitrate_kbps = parse_bitrate_to_int(video_bitrate)
    buf_size = f"{v_bitrate_kbps * 2}k" if v_bitrate_kbps > 0 else "9000k"

    return [
        shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe(),
        "-re", "-stream_loop", "-1", "-i", video_file,
        "-vf", f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=fast_bilinear,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
        "-c:v", "libx264", "-preset", preset, "-profile:v", "high", "-level", "4.1",
        "-b:v", video_bitrate, "-maxrate", video_bitrate, "-bufsize", buf_size,
        "-r", fps, "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0", "-threads", str(cpu_cores),
        "-c:a", "aac", "-b:a", audio_bitrate, "-ar", "44100", "-ac", "2",
        "-tls_verify", "0", "-rtmp_live", "live", "-f", "flv", "-progress", "pipe:2", "-nostats", "-v", "error",
        rtmp_url
    ]

_prog_buf = {}

def parse_progress_line(line):
    global _prog_buf
    if "=" not in line: return
    key, _, val = line.partition("=")
    _prog_buf[key.strip()] = val.strip()
    if key.strip() != "progress": return
    buf = _prog_buf
    _prog_buf = {}
    with status_lock:
        try: stream_status["fps"] = round(float(buf.get("fps", 0)), 1)
        except: pass
        m = re.search(r"([\d.]+)x", buf.get("speed", ""))
        if m: stream_status["speed"] = m.group(1) + "x"
        m = re.search(r"([\d.]+)kbits/s", buf.get("bitrate", ""))
        if m:
            total_kbps = float(m.group(1))
            audio_kbps = stream_status["audio_src_bitrate_kbps"]
            stream_status["bitrate"] = f"{round(total_kbps)} kb/s"
            stream_status["video_bitrate"] = f"{round(max(0, total_kbps - audio_kbps))} kb/s"
            stream_status["audio_bitrate"] = f"{audio_kbps} kb/s"
        try: stream_status["frame_count"] = int(buf.get("frame", 0))
        except: pass

@app.route("/api/status")
def api_status():
    with status_lock:
        if stream_status["start_time"]:
            delta = datetime.now() - stream_status["start_time"]
            stream_status["uptime"] = str(delta).split(".")[0]
        metrics = system_metrics()
        response = {**stream_status, **metrics, "restarts": stream_status["restart_count"], "preset": get_config("FFMPEG_PRESET", "ultrafast"), "start_time": stream_status["start_time"].isoformat() if stream_status["start_time"] else None}
    return jsonify(response)

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<title>Stream Monitor (Auto-API)</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
<style>
:root { --bg: #080810; --card: #13131f; --border: #1c1c2e; --text: #ddddf0; --sub: #5c5c78; --green: #22c55e; --red: #ef4444; }
body { background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; padding: 20px; }
.header { display: flex; justify-content: space-between; margin-bottom: 24px; }
.metrics-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.metric-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
.metric-label { font-size: 10px; color: var(--sub); text-transform: uppercase; margin-bottom: 8px; }
.metric-val { font-size: 20px; font-weight: 700; font-family: 'JetBrains Mono'; }
.col-green { color: var(--green); }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-top: 20px; }
.info-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
</style>
</head>
<body>
<div class="header"><strong>Stream Monitor (API Auto-Go-Live)</strong><div id="h-uptime">Uptime: --:--:--</div></div>
<div class="metrics-row">
<div class="metric-card"><div class="metric-label">Status</div><div class="metric-val col-green" id="m-status">Starting</div></div>
<div class="metric-card"><div class="metric-label">FPS</div><div class="metric-val" id="m-fps">0.0</div></div>
<div class="metric-card"><div class="metric-label">Bitrate</div><div class="metric-val" id="m-bitrate">0 kb/s</div></div>
<div class="metric-card"><div class="metric-label">Restarts</div><div class="metric-val" id="i-restarts">0</div></div>
</div>
<div class="panel">
<div class="info-row"><span>CPU Usage</span><span id="i-cpu">--%</span></div>
<div class="info-row"><span>RAM Usage</span><span id="i-ram">--%</span></div>
<div class="info-row"><span>Resolution</span><span id="i-res">--</span></div>
<div class="info-row"><span>Last Error</span><span id="i-err">none</span></div>
</div>
<script>
function update() {
  fetch('/api/status').then(r=>r.json()).then(d=>{
    document.getElementById('m-status').textContent = (d.status || 'unknown').toUpperCase();
    document.getElementById('m-fps').textContent = d.fps;
    document.getElementById('m-bitrate').textContent = d.bitrate;
    document.getElementById('h-uptime').textContent = 'Uptime: ' + d.uptime;
    document.getElementById('i-cpu').textContent = d.cpu + '%';
    document.getElementById('i-ram').textContent = d.ram + '%';
    document.getElementById('i-res').textContent = d.video_resolution;
    document.getElementById('i-restarts').textContent = d.restarts;
    document.getElementById('i-err').textContent = d.last_error || 'none';
    document.getElementById('i-err').style.color = d.last_error ? 'var(--red)' : 'var(--sub)';
  }).catch(()=>{});
}
setInterval(update, 2000); update();
</script>
</body>
</html>"""

def streaming_loop():
    while True:
        load_dotenv(override=True)
        video_file = get_config("VIDEO_FILE", "video.mp4")
        
        if not os.path.exists(video_file):
            with status_lock: stream_status.update({"status": "error", "last_error": f"Video file not found: {video_file}"})
            time.sleep(10); continue

        # Auto-create Facebook Live Video via API
        print("Requesting new Live Stream from Facebook API...")
        rtmp_url = create_facebook_live_video()
        
        if not rtmp_url:
            # Fallback to manual STREAM_KEY if API fails
            stream_key = get_config("STREAM_KEY")
            if stream_key:
                print("API failed, falling back to manual STREAM_KEY...")
                rtmp_url = f"rtmps://live-api-s.facebook.com:443/rtmp/{stream_key}"
            else:
                with status_lock: stream_status.update({"status": "error", "last_error": "Facebook API failed and no manual STREAM_KEY found."})
                time.sleep(15); continue

        probe_video(video_file)
        cmd = get_ffmpeg_command(rtmp_url)

        try:
            with status_lock: stream_status.update({"status": "streaming", "start_time": datetime.now(), "last_error": None, "fps": 0.0, "video_file": video_file})
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
            for line in proc.stderr:
                line = line.strip()
                if line:
                    parse_progress_line(line)
                    if "=" not in line: print(f"FFmpeg: {line}")
            proc.wait()
            if proc.returncode != 0:
                with status_lock: stream_status.update({"status": "error", "last_error": f"FFmpeg exit code {proc.returncode}"})
        except Exception as e:
            with status_lock: stream_status.update({"status": "error", "last_error": str(e)})
            print(f"Exception: {e}")

        with status_lock: stream_status["restart_count"] += 1
        print(f"Restarting in 5s... (#{stream_status['restart_count']})")
        time.sleep(5)

if __name__ == "__main__":
    port = int(get_config("PORT", 10000))
    threading.Thread(target=streaming_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
