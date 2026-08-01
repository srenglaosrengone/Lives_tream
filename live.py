# live.py - Optimized for 24/7 Streaming
import subprocess
import os
import time
import re
import json
import threading
import psutil
import shutil
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string
import imageio_ffmpeg

# ផ្ទុកការកំណត់ចេញពី .env
load_dotenv()

# ការកំណត់ Server និង Hardware
CPU_MODEL = os.environ.get("CPU_MODEL", "AMD Ryzen 9 7950X3D")
MEMORY_LIMIT_BYTES = int(os.environ.get("MEMORY_LIMIT_MB", "2560")) * 1024 * 1024

# ប្រើ Lock ដើម្បីការពារការជាន់គ្នានៃទិន្នន័យរវាង Flask និង Streaming Thread
status_lock = threading.Lock()

def _read_cgroup_value(*paths):
    for path in paths:
        try:
            value = Path(path).read_text().strip()
            if value and value != "max":
                return int(value)
        except (OSError, ValueError):
            continue
    return None

def system_metrics():
    """គណនាការប្រើប្រាស់ធនធានប្រព័ន្ធ (CPU/RAM)"""
    memory_used = _read_cgroup_value(
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",
    )
    memory_limit = _read_cgroup_value(
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ) or MEMORY_LIMIT_BYTES

    if memory_limit <= 0 or memory_limit > 1024 ** 5:
        memory_limit = MEMORY_LIMIT_BYTES
    if memory_used is None:
        memory_used = psutil.Process().memory_info().rss

    cpu_percent = psutil.cpu_percent(interval=None)
    process = psutil.Process()

    return {
        "cpu": round(cpu_percent, 1),
        "cpu_model": CPU_MODEL,
        "cpu_cores": psutil.cpu_count() or 1,
        "process_cpu": round(process.cpu_percent(interval=None), 1),
        "ram": round(min(memory_used / memory_limit * 100, 100), 1),
        "memory_used_mb": round(memory_used / 1024 / 1024, 1),
        "memory_limit_mb": round(memory_limit / 1024 / 1024, 1),
        "process_memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
    }

# ការកំណត់ការឡាយ
VIDEO_FILE = os.environ.get("VIDEO_FILE", "video.mp4")
PLATFORM   = os.environ.get("PLATFORM", "facebook").lower()
STREAM_KEY = os.environ.get("STREAM_KEY")
PORT       = int(os.environ.get("PORT", 10000))

stream_status = {
    "status":        "starting",
    "start_time":    datetime.now(),
    "restart_count": 0,
    "last_error":    None,
    "platform":      PLATFORM,
    "video_file":    VIDEO_FILE,
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
    "audio_src_bitrate_kbps": 0,
}

app = Flask(__name__)

def probe_video():
    """ពិនិត្យព័ត៌មានបច្ចេកទេសរបស់វីដេអូ"""
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-show_streams", VIDEO_FILE],
            capture_output=True, text=True, timeout=10
        )
        info = json.loads(result.stdout)
        with status_lock:
            for s in info.get("streams", []):
                if s.get("codec_type") == "video":
                    stream_status["video_resolution"] = f"{s.get('width','?')}×{s.get('height','?')}"
                    stream_status["video_codec"]      = s.get("codec_name", "—").upper()
                    rfr = s.get("r_frame_rate", "0/1")
                    try:
                        num, den = map(int, rfr.split("/"))
                        fps_val = round(num / den, 2) if den else 0
                    except Exception:
                        fps_val = 0
                    stream_status["video_fps_src"] = f"{fps_val} fps"
                elif s.get("codec_type") == "audio":
                    stream_status["audio_codec"]       = s.get("codec_name", "—").upper()
                    stream_status["audio_sample_rate"] = f"{int(s.get('sample_rate',0))//1000} kHz"
                    ch = s.get("channels", 0)
                    stream_status["audio_channels"]    = "Stereo" if ch == 2 else ("Mono" if ch == 1 else str(ch))
                    br = int(s.get("bit_rate", 0))
                    stream_status["audio_src_bitrate_kbps"] = round(br / 1000)
        print(f"Probed: {stream_status['video_resolution']} | {stream_status['video_codec']}")
    except Exception as e:
        print(f"ffprobe error: {e}")

def get_ffmpeg_exe():
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()

def get_ffmpeg_command():
    rtmp_url = (
        f"rtmps://live-api-s.facebook.com:443/rtmp/{STREAM_KEY}"
        if PLATFORM != "youtube"
        else f"rtmp://a.rtmp.youtube.com/live2/{STREAM_KEY}"
    )
    cpu_cores     = os.cpu_count() or 2
    video_bitrate = os.environ.get("VIDEO_BITRATE",  "4500k")
    audio_bitrate = os.environ.get("AUDIO_BITRATE",  "192k")
    preset        = os.environ.get("FFMPEG_PRESET",  "ultrafast")
    fps           = os.environ.get("STREAM_FPS",     "30")
    out_w         = os.environ.get("OUTPUT_WIDTH",   "1080")
    out_h         = os.environ.get("OUTPUT_HEIGHT",  "1920")
    try:
        gop = int(float(fps)) * 2
    except ValueError:
        gop = 60

    return [
        get_ffmpeg_exe(),
        "-re", "-stream_loop", "-1", "-i", VIDEO_FILE,
        # Scale to 1080×1920 (YouTube Shorts, 9:16) — preserve aspect ratio, pad with black
        "-vf", f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=fast_bilinear,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
        # Video: H.264 High Profile, all cores
        "-c:v", "libx264",
        "-preset", preset,
        "-profile:v", "high", "-level", "4.1",
        "-b:v", video_bitrate,
        "-maxrate", video_bitrate,
        "-bufsize", str(int(video_bitrate.rstrip("k")) * 2) + "k",
        "-r", fps,
        "-g", str(gop),         # keyframe every 2 s (YouTube requirement)
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-threads", str(cpu_cores),
        # Audio: AAC 192 kbps stereo
        "-c:a", "aac", "-b:a", audio_bitrate, "-ar", "44100", "-ac", "2",
        # Output
        "-tls_verify", "0", "-rtmp_live", "live",
        "-f", "flv",
        "-progress", "pipe:2",
        "-nostats",
        "-v", "error",
        rtmp_url
    ]

_prog_buf = {}

def parse_progress_line(line):
    """បកស្រាយទិន្នន័យ Progress ចេញពី FFmpeg"""
    global _prog_buf
    if "=" not in line:
        return
    key, _, val = line.partition("=")
    key = key.strip(); val = val.strip()
    _prog_buf[key] = val

    if key != "progress":
        return

    buf = _prog_buf
    _prog_buf = {}

    with status_lock:
        try:
            fps = float(buf.get("fps", 0))
            stream_status["fps"] = round(fps, 1)
        except ValueError: pass

        spd = buf.get("speed", "")
        m = re.search(r"([\d.]+)x", spd)
        if m: stream_status["speed"] = m.group(1) + "x"

        total_br_str = buf.get("bitrate", "")
        m = re.search(r"([\d.]+)kbits/s", total_br_str)
        if m:
            total_kbps = float(m.group(1))
            audio_kbps = stream_status["audio_src_bitrate_kbps"]
            video_kbps = max(0, total_kbps - audio_kbps)
            stream_status["bitrate"]       = f"{round(total_kbps)} kb/s"
            stream_status["video_bitrate"] = f"{round(video_kbps)} kb/s"
            stream_status["audio_bitrate"] = f"{audio_kbps} kb/s"

        try:
            stream_status["frame_count"] = int(buf.get("frame", 0))
        except ValueError: pass

@app.route("/api/status")
def api_status():
    with status_lock:
        if stream_status["start_time"]:
            delta = datetime.now() - stream_status["start_time"]
            stream_status["uptime"] = str(delta).split(".")[0]

        metrics = system_metrics()
        response = {
            **stream_status,
            **metrics,
            "restarts":    stream_status["restart_count"],
            "preset":      os.environ.get("FFMPEG_PRESET", "ultrafast"),
            "scaling_algo":os.environ.get("SCALING_ALGO", "bilinear"),
            "start_time":  stream_status["start_time"].isoformat() if stream_status["start_time"] else None,
        }
    return jsonify(response)

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

# Dashboard HTML (ដូចដើម ប៉ុន្តែធានាថាវាបង្ហាញទិន្នន័យបានត្រឹមត្រូវ)
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<title>Stream Monitor</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:      #080810;
  --surface: #0f0f1a;
  --card:    #13131f;
  --border:  #1c1c2e;
  --text:    #ddddf0;
  --sub:     #5c5c78;
  --green:   #22c55e;
  --red:     #ef4444;
  --blue:    #60a5fa;
  --amber:   #fbbf24;
  --purple:  #c084fc;
  --glass:   rgba(19,19,31,.78);
}
body {
  background: var(--bg); color: var(--text);
  font-family: 'Inter', sans-serif; min-height: 100vh;
  padding: 20px 24px 32px;
}
.header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.brand { display: flex; align-items: center; gap: 10px; }
.pulse-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--green); animation: pulse 1.8s infinite; }
@keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(34,197,94,.5); } 50% { box-shadow: 0 0 0 7px rgba(34,197,94,0); } }
.metrics-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
.metric-card { background: var(--glass); border: 1px solid var(--border); border-radius: 14px; padding: 16px; }
.metric-label { font-size: 10px; font-weight: 600; color: var(--sub); text-transform: uppercase; margin-bottom: 8px; }
.metric-val { font-size: 20px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
.col-green { color: var(--green); }
.col-blue { color: var(--blue); }
.panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-top: 20px; }
.info-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.info-row:last-child { border-bottom: none; }
.k { color: var(--sub); }
</style>
</head>
<body>
<div class="header">
  <div class="brand">
    <div class="pulse-dot" id="dot"></div>
    <div style="font-weight:700">Stream Monitor By Srengone</div>
  </div>
  <div id="h-uptime" style="font-size:12px; color:var(--sub)">Uptime: --:--:--</div>
</div>

<div class="metrics-row">
  <div class="metric-card"><div class="metric-label">Status</div><div class="metric-val col-green" id="m-status">Starting</div></div>
  <div class="metric-card"><div class="metric-label">FPS</div><div class="metric-val" id="m-fps">0.0</div></div>
  <div class="metric-card"><div class="metric-label">Bitrate</div><div class="metric-val" id="m-bitrate">0 kb/s</div></div>
  <div class="metric-card"><div class="metric-label">Speed</div><div class="metric-val" id="m-speed">0.0x</div></div>
</div>

<div class="panel">
  <div style="font-size:12px; font-weight:700; margin-bottom:15px; color:var(--sub)">SYSTEM & STREAM INFO</div>
  <div class="info-row"><span class="k">CPU Usage</span><span id="i-cpu">--%</span></div>
  <div class="info-row"><span class="k">RAM Usage</span><span id="i-ram">--%</span></div>
  <div class="info-row"><span class="k">Video Res</span><span id="i-res">--</span></div>
  <div class="info-row"><span class="k">Restarts</span><span id="i-restarts">0</span></div>
  <div class="info-row"><span class="k">Last Error</span><span id="i-err" style="color:var(--sub)">none</span></div>
</div>
<script>
function update() {
  fetch('/api/status').then(r=>r.json()).then(d=>{
    document.getElementById('m-status').textContent = d.status.toUpperCase();
    document.getElementById('m-fps').textContent = d.fps;
    document.getElementById('m-bitrate').textContent = d.bitrate;
    document.getElementById('m-speed').textContent = d.speed;
    document.getElementById('h-uptime').textContent = 'Uptime: ' + d.uptime;
    document.getElementById('i-cpu').textContent = d.cpu + '%';
    document.getElementById('i-ram').textContent = d.ram + '%';
    document.getElementById('i-res').textContent = d.video_resolution;
    document.getElementById('i-restarts').textContent = d.restarts;
    document.getElementById('i-err').textContent = d.last_error || 'none';
    document.getElementById('i-err').style.color = d.last_error ? 'var(--red)' : 'var(--sub)';
    document.getElementById('dot').style.background = d.status === 'streaming' ? 'var(--green)' : 'var(--red)';
  }).catch(()=>{});
}
setInterval(update, 2000);
update();
</script>
</body>
</html>"""

def streaming_loop():
    """Loop ឡាយ ២៤/៧ ជាមួយនឹងការអានទិន្នន័យមានប្រសិទ្ធភាព"""
    while True:
        # ឆែកមើលឯកសារវីដេអូ
        if not os.path.exists(VIDEO_FILE):
            with status_lock:
                stream_status.update({"status": "error", "last_error": f"Video file not found: {VIDEO_FILE}"})
            print(f"Error: {VIDEO_FILE} not found. Retrying in 10s...")
            time.sleep(10)
            continue

        if not STREAM_KEY:
            with status_lock:
                stream_status.update({"status": "error", "last_error": "Missing STREAM_KEY"})
            print("Error: STREAM_KEY is missing. Retrying in 10s...")
            time.sleep(10)
            continue

        probe_video()
        print(f"Starting stream to {PLATFORM.upper()}...")

        load_dotenv(override=True)
        cmd = get_ffmpeg_command()

        try:
            with status_lock:
                stream_status.update({
                    "status": "streaming", 
                    "start_time": datetime.now(),
                    "last_error": None, 
                    "frame_count": 0, 
                    "fps": 0.0
                })

            # បើក FFmpeg Process
            proc = subprocess.Popen(
                cmd, 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, 
                text=True, 
                bufsize=1,
                universal_newlines=True
            )

            # អាន Progress ម្តងមួយបន្ទាត់ (ប្រសិទ្ធភាពខ្ពស់)
            for line in proc.stderr:
                line = line.strip()
                if line:
                    parse_progress_line(line)
                    if "=" not in line: # បង្ហាញតែកំហុសពិតប្រាកដក្នុង Console
                        print(f"FFmpeg: {line}")

            proc.wait()
            if proc.returncode != 0:
                with status_lock:
                    stream_status["status"] = "error"
                    stream_status["last_error"] = f"FFmpeg exit code {proc.returncode}"
                print(f"Stream exited with code {proc.returncode}")

        except Exception as e:
            with status_lock:
                stream_status.update({"status": "error", "last_error": str(e)})
            print(f"Exception in streaming loop: {e}")

        with status_lock:
            stream_status["restart_count"] += 1

        print(f"Restarting in 5s... (#{stream_status['restart_count']})")
        time.sleep(5)

if __name__ == "__main__":
    # ចាប់ផ្តើម Thread សម្រាប់ការឡាយ
    threading.Thread(target=streaming_loop, daemon=True).start()
    # ចាប់ផ្តើម Flask Web Server
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
