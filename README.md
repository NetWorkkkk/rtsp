# RTSP/RTP Video Streaming

Python implementation of a simple video streaming system for the Socket Programming lab.

The project uses:

- **RTSP over TCP** for control messages: `SETUP`, `PLAY`, `PAUSE`, `TEARDOWN`, and custom `PACE` flow control.
- **RTP over UDP** for SD streaming, with 8 x 8 JPEG tile fragmentation.
- **RTP over TCP** for HD streaming, with full-frame delivery and explicit stream framing.
- **Client-side buffering** for smoother playback and SD/HD switching.
- **`epoll` I/O multiplexing** on the server RTSP control plane.

## Requirements

- Python 3.8+
- Linux or WSL recommended, because `Server.py` uses `select.epoll()`
- `tkinter` for the GUI client
- `Pillow` for image decoding/rendering
- `opencv-python` only for video conversion utilities

Install Python dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install pillow opencv-python
```

If `tkinter` is missing on Linux, install it with your system package manager. For example, on Ubuntu/Debian:

```bash
sudo apt install python3-tk
```

## Project Files

| File | Description |
|------|-------------|
| `ClientLauncher.py` | Client entry point. Parses CLI arguments and starts the Tkinter UI. |
| `Client.py` | RTSP client, RTP receiver, GUI, frame buffer, tile reconstruction, and SD/HD switching. |
| `Server.py` | RTSP server entry point using `epoll` for multiplexing client control sockets. |
| `ServerWorker.py` | Per-client RTSP state machine and RTP sender logic. |
| `RtpPacket.py` | RTP fixed-header encode/decode helper. |
| `VideoStream.py` | Custom MJPEG reader: 5-byte frame length header + JPEG payload. |
| `utils/ConvertVideo.py` | Utility for converting MP4/MJPEG assets into the custom MJPEG format. |

## Video Asset Preparation

The client requests a logical file name, for example:

```text
movie.Mjpeg
```

The server maps that request to two profile files:

```text
SD_movie.Mjpeg
HD_movie.Mjpeg
```

So before running the full SD/HD demo, make sure both files exist in the project root.

### Option A: Convert from a source MP4

```bash
python utils/ConvertVideo.py --mode mp4_to_mjpeg \
  --input source.mp4 --output HD_movie.Mjpeg --quality 25

python utils/ConvertVideo.py --mode mp4_to_mjpeg_480p \
  --input source.mp4 --output SD_movie.Mjpeg --quality 25
```

### Option B: Quick smoke test using the provided sample

If you only want to verify that the protocol and GUI run, copy the sample file to both expected profile names:

```bash
cp movie.Mjpeg HD_movie.Mjpeg
cp movie.Mjpeg SD_movie.Mjpeg
```

This shortcut does not demonstrate a real HD/SD quality difference; it only allows the server to find the expected profile files.

## Running the Project

Open two terminals in the project directory.

### 1. Start the server

```bash
python Server.py 8554
```

`8554` is the RTSP/TCP server port. Use a port greater than 1024 to avoid permission issues.

### 2. Start the client

```bash
python ClientLauncher.py 127.0.0.1 8554 5004 movie.Mjpeg
```

Arguments:

| Argument | Meaning |
|----------|---------|
| `127.0.0.1` | Server address. |
| `8554` | Server RTSP port. |
| `5004` | Client RTP media port. |
| `movie.Mjpeg` | Logical requested video name. Server opens `SD_movie.Mjpeg` or `HD_movie.Mjpeg`. |

## GUI Usage

1. Click **Setup** to create the RTSP session and media transport.
2. Click **Play** to start streaming.
3. Select **SD** for RTP/UDP tiled streaming.
4. Select **HD** for RTP/TCP full-frame streaming.
5. Click **Pause** to pause playback.
6. Click **Teardown** to close the session.

## Implemented Features

- RTSP client requests with `CSeq`, `Transport`, and `Session` headers.
- RTP packetization with version 2, payload type 26, timestamp, sequence number, and payload.
- SD/UDP frame fragmentation using 64 JPEG tiles per frame.
- Missing-tile concealment using cached tiles from previous frames.
- Server-side RTSP multiplexing with `epoll`.
- HD/TCP media path with client-side packet boundary reconstruction.
- Client frame buffer with pre-roll to reduce playback jitter.
- Custom `PACE PAUSE` / `PACE RESUME` control messages for buffer back-pressure.
- SD/HD switching through a new `SETUP` request while preserving playback continuity.

## Notes

- `Server.py` depends on `select.epoll()`, so it should be run on Linux/WSL.
- The project uses a lab-specific custom MJPEG format, not a standard `.mjpeg` container.
- In this implementation, the RTP SSRC field is reused as lightweight metadata: tile index for UDP mode and payload length for TCP mode. A production implementation should use an RTP extension or application-level header instead.
