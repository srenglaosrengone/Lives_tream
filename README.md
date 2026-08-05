# 24/7 Live Streaming Script (Optimized)

This script is designed for continuous 24/7 live streaming to platforms like Facebook and YouTube using FFmpeg and Python. It includes a web-based dashboard for real-time monitoring of stream status, system resources, and FFmpeg performance.

## Features

- **24/7 Streaming**: Automatically restarts FFmpeg if it crashes or the connection drops.
- **Dynamic Configuration**: Reloads `.env` settings on every restart without stopping the script.
- **Real-time Dashboard**: Monitor FPS, Bitrate, CPU/RAM usage, and Uptime via a web interface.
- **Optimized for 9:16**: Default settings are optimized for vertical streaming (1080x1920).
- **Auto-Scaling**: Automatically scales and pads video to fit the target resolution.

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/srenglaosrengone/Lives_tream.git
   cd Lives_tream
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Ensure FFmpeg is installed on your system:
   ```bash
   # Ubuntu/Debian
   sudo apt update && sudo apt install ffmpeg -y
   ```

## Configuration

Copy `.env.example` to `.env` and fill in your details:

```bash
cp .env.example .env
```

Edit `.env`:
- `STREAM_KEY`: Your Facebook or YouTube stream key.
- `PLATFORM`: `facebook` or `youtube`.
- `VIDEO_FILE`: Path to your video file (e.g., `video.mp4`).
- `VIDEO_BITRATE`: Target video bitrate (e.g., `4500k`).
- `PORT`: Port for the dashboard (default: `10000`).

## Usage

Run the script:
```bash
python3 live.py
```

Access the dashboard at `http://your-server-ip:10000`.

## Monitoring Dashboard

The dashboard provides:
- **Status**: Current state of the stream (Starting, Streaming, Error).
- **FPS**: Current encoding frames per second.
- **Bitrate**: Real-time upload speed.
- **System Info**: CPU and RAM usage of the server.
- **Restarts**: Number of times the stream has automatically restarted.

## License
MIT
