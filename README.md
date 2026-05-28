# RTP/RTSP Python Skeleton

This project is a Python skeleton for a simple video streaming system:
- **RTSP over TCP** for control (`SETUP`, `PLAY`, `PAUSE`, `TEARDOWN`)
- **RTP over UDP** for media frame delivery (MJPEG payload)

For full architecture and file-by-file details, see [`PROJECT_STRUCTURE.md`](./PROJECT_STRUCTURE.md).

## Requirements

- Python 3.8+ (recommended)
- `tkinter` (usually bundled with standard Python installers)
- `Pillow` (for image rendering in client GUI)

## Install Dependencies

From the project directory:

```powershell
python -m pip install --upgrade pip
python -m pip install pillow
```

If `tkinter` is missing, install the Python distribution variant that includes Tk support.

## Run the Project

Open two terminals in the project directory.

### 1) Start server

```powershell
python Server.py 8554
```

- `8554` is the RTSP server port.

### 2) Start client

```powershell
python ClientLauncher.py 127.0.0.1 8554 5004 movie.Mjpeg
```

Arguments:
- `127.0.0.1`: server address
- `8554`: RTSP server port
- `5004`: client RTP UDP port
- `movie.Mjpeg`: video file requested from server

## Usage Flow

In the client GUI:
1. Click **Setup**
2. Click **Play**
3. Use **Pause** and **Teardown** as needed

## Important Note About This Repository

This codebase is a **skeleton/lab template** and has unfinished `TO COMPLETE` sections in:
- `Client.py`
- `RtpPacket.py`

You need to complete those parts before full streaming behavior works reliably.
