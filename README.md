# Stream Engine V3 (24/7 Professional Live Stream)

An optimized, professional-grade Python script for 24/7 continuous live streaming to Facebook and YouTube.

## 🚀 New Features in V3
- **Facebook Graph API Integration**: Automatically creates Live Videos and "Goes Live" without manual intervention.
- **Direct Video Download**: Supports streaming from a URL (`VIDEO_URL`). No need to upload large video files to GitHub.
- **Enhanced Dashboard**: Real-time monitoring of download progress, stream metrics (FPS, Bitrate, Speed), and system resources (CPU/RAM).
- **Auto-Recovery**: Robust error handling and infinite loop logic to ensure the stream stays up 24/7.
- **Modular Design**: Class-based structure for better stability and easier maintenance.

## 🛠 Setup & Installation

### 1. Clone the Repository
```bash
git clone https://github.com/srenglaosrengone/Lives_tream.git
cd Lives_tream
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your details:
```bash
cp .env.example .env
```

**Required for Facebook Auto-Go-Live:**
- `PAGE_ACCESS_TOKEN`: Your Facebook Page Lifetime Access Token.
- `PAGE_ID`: Your Facebook Page ID.

**Video Source:**
- `VIDEO_URL`: A direct link to your MP4 video file (e.g., from Google Drive direct link).
- `VIDEO_FILE`: Local filename (default: `video.mp4`).

**Manual Fallback:**
- `STREAM_KEY`: Your manual Facebook/YouTube stream key (used if API fails).

## 🖥 Dashboard
Once running, access the dashboard at `http://your-server-ip:10000`.
The dashboard provides:
- **Real-time Status**: (Streaming, Downloading, Error, etc.)
- **Performance Metrics**: FPS, Bitrate, Speed.
- **System Health**: CPU and RAM usage.
- **Technical Info**: Resolution, Codecs, and Source FPS.

## 📦 Deployment (Render.com)
1. Connect your GitHub repository to Render.
2. Select **Web Service**.
3. Set **Start Command**: `python live.py` (or `gunicorn live:app` if using a production server, but `live.py` handles the stream thread).
4. Add the required **Environment Variables** in the Render dashboard.

## 💡 Tips for 24/7 Stability
- **UptimeRobot**: Use [UptimeRobot](https://uptimerobot.com) to ping your dashboard URL every 5 minutes to prevent the server from sleeping.
- **Continuous Live**: On Facebook, ensure "Persistent Stream Key" is enabled.
- **Video Quality**: For 1080p, a bitrate of 4000k-5000k is recommended.

## License
MIT
