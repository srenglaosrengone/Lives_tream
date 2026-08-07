# live.py - Professional 24/7 Streaming Engine with Facebook API Integration
import subprocess
import os
import time
import re
import json
import threading
import psutil
import shutil
import requests
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string
import imageio_ffmpeg

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class StreamManager:
    def __init__(self):
        self.status = "starting"
        self.start_time = None
        self.restart_count = 0
        self.last_error = None
        self.download_progress = 0
        self.video_info = {
            "resolution": "—",
            "codec": "—",
            "fps": "—",
            "bitrate": "0 kb/s"
        }
        self.stream_metrics = {
            "fps": 0.0,
            "speed": "0.0x",
            "total_bitrate": "0 kb/s",
            "frame_count": 0
        }
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    def get_config(self, key, default=None):
        return os.environ.get(key, default)

    def update_status(self, **kwargs):
        with self.lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)
                elif key in ["fps", "speed", "total_bitrate", "frame_count"]:
                    self.stream_metrics[key] = value
                elif key in ["resolution", "codec", "fps_src", "bitrate_src"]:
                    self.video_info[key] = value

    def download_video(self):
        url = self.get_config("VIDEO_URL")
        dest = self.get_config("VIDEO_FILE", "video.mp4")
        if not url:
            return os.path.exists(dest)

        if os.path.exists(dest) and self.get_config("FORCE_DOWNLOAD", "false").lower() != "true":
            logger.info(f"Video already exists: {dest}")
            return True

        try:
            logger.info(f"Downloading video from {url}...")
            self.update_status(status="downloading", download_progress=0)
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            
            downloaded = 0
            with open(dest, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = int((downloaded / total_size) * 100)
                            if progress != self.download_progress:
                                self.update_status(download_progress=progress)
            
            logger.info(f"Download complete: {dest}")
            return True
        except Exception as e:
            err_msg = f"Download failed: {str(e)}"
            logger.error(err_msg)
            self.update_status(status="error", last_error=err_msg)
            return False

    def create_fb_live(self):
        page_id = self.get_config("PAGE_ID")
        token = self.get_config("PAGE_ACCESS_TOKEN")
        if not page_id or not token:
            logger.warning("Missing PAGE_ID or PAGE_ACCESS_TOKEN. Falling back to manual key.")
            return None

        url = f"https://graph.facebook.com/v18.0/{page_id}/live_videos"
        params = {
            "access_token": token,
            "status": "LIVE_NOW",
            "title": self.get_config("STREAM_TITLE", "24/7 Live Stream"),
            "description": self.get_config("STREAM_DESCRIPTION", "Continuous automated stream"),
        }

        try:
            resp = requests.post(url, params=params, timeout=15)
            data = resp.json()
            if "stream_url" in data:
                logger.info(f"Created FB Live Video ID: {data.get('id')}")
                return data["stream_url"]
            else:
                logger.error(f"FB API Error: {data}")
                return None
        except Exception as e:
            logger.error(f"FB API Exception: {e}")
            return None

    def probe_video(self):
        video_file = self.get_config("VIDEO_FILE", "video.mp4")
        ffprobe = shutil.which("ffprobe") or "ffprobe"
        try:
            cmd = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", video_file]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            info = json.loads(result.stdout)
            for s in info.get("streams", []):
                if s.get("codec_type") == "video":
                    res = f"{s.get('width')}x{s.get('height')}"
                    fps_raw = s.get("r_frame_rate", "30/1")
                    num, den = map(int, fps_raw.split("/")) if "/" in fps_raw else (float(fps_raw), 1)
                    fps = round(num/den, 2) if den else 0
                    self.update_status(resolution=res, codec=s.get("codec_name", "").upper(), fps_src=f"{fps} fps")
                elif s.get("codec_type") == "audio":
                    br = int(s.get("bit_rate", 0)) // 1000
                    self.update_status(bitrate_src=f"{br} kbps")
        except Exception as e:
            logger.debug(f"Probe failed: {e}")

    def get_ffmpeg_cmd(self, rtmp_url):
        video_file = self.get_config("VIDEO_FILE", "video.mp4")
        v_bitrate = self.get_config("VIDEO_BITRATE", "4000k")
        a_bitrate = self.get_config("AUDIO_BITRATE", "128k")
        preset = self.get_config("FFMPEG_PRESET", "ultrafast")
        fps = self.get_config("STREAM_FPS", "30")
        width = self.get_config("OUTPUT_WIDTH", "1080")
        height = self.get_config("OUTPUT_HEIGHT", "1920")
        gop = int(float(fps)) * 2
        
        # Parse bitrate for buffer size
        m = re.match(r"(\d+)", v_bitrate)
        v_kbps = int(m.group(1)) if m else 4000
        buf_size = f"{v_kbps * 2}k"

        return [
            shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe(),
            "-re", "-stream_loop", "-1", 
            "-i", video_file,
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
            "-c:v", "libx264", "-preset", preset, "-profile:v", "high", "-level", "4.1",
            "-b:v", v_bitrate, "-maxrate", v_bitrate, "-bufsize", buf_size,
            "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(gop), "-keyint_min", str(gop),
            "-sc_threshold", "0", "-threads", str(os.cpu_count() or 2),
            "-c:a", "aac", "-b:a", a_bitrate, "-ar", "44100", "-ac", "2",
            "-f", "flv", "-progress", "pipe:2", "-v", "error", "-nostats",
            rtmp_url
        ]

    def parse_progress(self, line):
        if "=" not in line: return
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        
        if key == "fps":
            try: self.update_status(fps=round(float(val), 1))
            except: pass
        elif key == "speed":
            self.update_status(speed=val)
        elif key == "bitrate":
            self.update_status(total_bitrate=val)
        elif key == "frame":
            try: self.update_status(frame_count=int(val))
            except: pass

    def run(self):
        logger.info("Starting Stream Manager...")
        while not self.stop_event.is_set():
            load_dotenv(override=True)
            
            # 1. Download Video
            if not self.download_video():
                time.sleep(10); continue

            # 2. Prepare RTMP URL
            rtmp_url = self.create_fb_live()
            if not rtmp_url:
                manual_key = self.get_config("STREAM_KEY")
                if manual_key:
                    rtmp_url = f"rtmps://live-api-s.facebook.com:443/rtmp/{manual_key}"
                else:
                    self.update_status(status="error", last_error="No RTMP URL or Stream Key")
                    time.sleep(10); continue

            # 3. Start Streaming
            self.probe_video()
            cmd = self.get_ffmpeg_cmd(rtmp_url)
            
            try:
                self.update_status(status="streaming", start_time=datetime.now(), last_error=None)
                logger.info("FFmpeg started.")
                process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, bufsize=1, universal_newlines=True)
                
                for line in process.stderr:
                    if self.stop_event.is_set():
                        process.terminate(); break
                    self.parse_progress(line.strip())
                
                process.wait()
                if process.returncode != 0 and not self.stop_event.is_set():
                    logger.error(f"FFmpeg exited with code {process.returncode}")
                    self.update_status(status="error", last_error=f"FFmpeg Exit Code {process.returncode}")
            except Exception as e:
                logger.error(f"Stream Exception: {e}")
                self.update_status(status="error", last_error=str(e))

            self.update_status(restart_count=self.restart_count + 1)
            if not self.stop_event.is_set():
                logger.info("Restarting in 5s...")
                time.sleep(5)

# Initialize Global Manager
manager = StreamManager()

# Flask App
app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route("/api/status")
def api_status():
    with manager.lock:
        uptime = "00:00:00"
        if manager.start_time:
            delta = datetime.now() - manager.start_time
            uptime = str(delta).split(".")[0]
        
        # System Metrics
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent()
        
        return jsonify({
            "status": manager.status,
            "uptime": uptime,
            "restarts": manager.restart_count,
            "error": manager.last_error,
            "download_progress": manager.download_progress,
            "metrics": manager.stream_metrics,
            "video": manager.video_info,
            "system": {
                "cpu": cpu,
                "ram": mem.percent
            }
        })

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stream Engine V3</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <style>
        :root { --bg: #0a0a0f; --card: #14141f; --border: #232333; --text: #e1e1e6; --sub: #828291; --green: #00e676; --red: #ff5252; --blue: #40c4ff; }
        body { background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; margin: 0; padding: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 15px; max-width: 1200px; margin: 0 auto; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 20px; transition: 0.3s; }
        .card:hover { border-color: var(--blue); }
        .label { color: var(--sub); font-size: 11px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; font-weight: 600; }
        .value { font-size: 24px; font-weight: 800; font-family: 'JetBrains Mono'; }
        .status-tag { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; text-transform: uppercase; }
        .status-streaming { background: rgba(0, 230, 118, 0.1); color: var(--green); }
        .status-error { background: rgba(255, 82, 82, 0.1); color: var(--red); }
        .status-other { background: rgba(64, 196, 255, 0.1); color: var(--blue); }
        .progress-bar { height: 6px; background: #1e1e2e; border-radius: 3px; overflow: hidden; margin-top: 10px; }
        .progress-fill { height: 100%; background: var(--blue); transition: 0.5s; }
        .header { max-width: 1200px; margin: 0 auto 30px; display: flex; justify-content: space-between; align-items: center; }
        .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 13px; margin-top: 15px; }
        .info-item { color: var(--sub); }
        .info-val { color: var(--text); text-align: right; font-weight: 600; }
    </style>
</head>
<body>
    <div class="header">
        <div style="font-size: 24px; font-weight: 800;">Stream Engine <span style="color: var(--blue);">V3</span></div>
        <div id="status-badge" class="status-tag">Starting</div>
    </div>
    <div class="grid">
        <div class="card">
            <div class="label">Uptime</div>
            <div class="value" id="uptime">00:00:00</div>
            <div class="info-grid">
                <div class="info-item">Restarts</div><div class="info-val" id="restarts">0</div>
            </div>
        </div>
        <div class="card">
            <div class="label">Stream Performance</div>
            <div class="value" id="fps">0.0 <span style="font-size: 14px; color: var(--sub);">FPS</span></div>
            <div class="info-grid">
                <div class="info-item">Bitrate</div><div class="info-val" id="bitrate">0 kb/s</div>
                <div class="info-item">Speed</div><div class="info-val" id="speed">0.0x</div>
            </div>
        </div>
        <div class="card">
            <div class="label">System Resources</div>
            <div class="value" id="cpu">0% <span style="font-size: 14px; color: var(--sub);">CPU</span></div>
            <div class="info-grid">
                <div class="info-item">RAM Usage</div><div class="info-val" id="ram">0%</div>
            </div>
        </div>
        <div class="card" id="download-card" style="display: none;">
            <div class="label">Downloading Video</div>
            <div class="value" id="dl-percent">0%</div>
            <div class="progress-bar"><div class="progress-fill" id="dl-fill" style="width: 0%;"></div></div>
        </div>
    </div>
    <div class="grid" style="margin-top: 15px;">
        <div class="card" style="grid-column: 1 / -1;">
            <div class="label">Technical Information</div>
            <div class="info-grid" style="grid-template-columns: repeat(4, 1fr);">
                <div><div class="info-item">Resolution</div><div class="info-val" id="v-res">—</div></div>
                <div><div class="info-item">Video Codec</div><div class="info-val" id="v-codec">—</div></div>
                <div><div class="info-item">Source FPS</div><div class="info-val" id="v-fps">—</div></div>
                <div><div class="info-item">Audio Source</div><div class="info-val" id="v-audio">—</div></div>
            </div>
            <div id="error-box" style="margin-top: 20px; color: var(--red); font-size: 12px; display: none; font-family: 'JetBrains Mono';"></div>
        </div>
    </div>

    <script>
        function update() {
            fetch('/api/status').then(r => r.json()).then(d => {
                document.getElementById('uptime').textContent = d.uptime;
                document.getElementById('restarts').textContent = d.restarts;
                document.getElementById('fps').childNodes[0].textContent = d.metrics.fps + ' ';
                document.getElementById('bitrate').textContent = d.metrics.total_bitrate;
                document.getElementById('speed').textContent = d.metrics.speed;
                document.getElementById('cpu').childNodes[0].textContent = d.system.cpu + '% ';
                document.getElementById('ram').textContent = d.system.ram + '%';
                
                // Status Badge
                const badge = document.getElementById('status-badge');
                badge.textContent = d.status;
                badge.className = 'status-tag ' + (d.status === 'streaming' ? 'status-streaming' : (d.status === 'error' ? 'status-error' : 'status-other'));

                // Video Info
                document.getElementById('v-res').textContent = d.video.resolution;
                document.getElementById('v-codec').textContent = d.video.codec;
                document.getElementById('v-fps').textContent = d.video.fps_src;
                document.getElementById('v-audio').textContent = d.video.bitrate_src;

                // Download Progress
                const dlCard = document.getElementById('download-card');
                if (d.status === 'downloading') {
                    dlCard.style.display = 'block';
                    document.getElementById('dl-percent').textContent = d.download_progress + '%';
                    document.getElementById('dl-fill').style.width = d.download_progress + '%';
                } else {
                    dlCard.style.display = 'none';
                }

                // Error Box
                const errBox = document.getElementById('error-box');
                if (d.error) {
                    errBox.style.display = 'block';
                    errBox.textContent = 'LAST ERROR: ' + d.error;
                } else {
                    errBox.style.display = 'none';
                }
            }).catch(e => console.error(e));
        }
        setInterval(update, 2000);
        update();
    </script>
</body>
</html>
"""

def signal_handler(sig, frame):
    logger.info("Shutdown signal received. Cleaning up...")
    manager.stop_event.set()
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    port = int(manager.get_config("PORT", 10000))
    
    # Start Manager Thread
    thread = threading.Thread(target=manager.run, daemon=True)
    thread.start()
    
    # Start Flask
    logger.info(f"Dashboard running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
