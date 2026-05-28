# Project Structure and File Roles

This project is a basic RTP-over-UDP video streaming demo controlled by RTSP-over-TCP.

## Overall Architecture

- **Server side**
  - Accepts RTSP commands (`SETUP`, `PLAY`, `PAUSE`, `TEARDOWN`) over TCP.
  - Reads MJPEG frames from a local video file.
  - Wraps each frame into an RTP packet and sends it to the client over UDP.

- **Client side**
  - Provides a Tkinter GUI with control buttons.
  - Sends RTSP commands to the server.
  - Receives RTP packets over UDP, decodes them, and displays frames in the GUI.

- **Data flow**
  1. Client sends `SETUP`.
  2. Server opens video stream and stores RTP target port from client.
  3. Client sends `PLAY`.
  4. Server starts RTP sender thread and streams frames.
  5. Client displays frames.
  6. `PAUSE`/`TEARDOWN` stop stream (and teardown closes sockets).

## File-by-File Summary

### `Server.py`
- Entry point for the RTSP server process.
- Reads server port from CLI args.
- Creates a TCP socket and listens for incoming client RTSP connections.
- For each accepted client socket, creates a `ServerWorker` instance to handle the session.

### `ServerWorker.py`
- Core RTSP session logic for one connected client.
- Maintains server-side RTSP states: `INIT`, `READY`, `PLAYING`.
- Parses incoming RTSP requests and transitions state accordingly.
- Creates/manages:
  - `VideoStream` for reading frames from file.
  - UDP RTP socket for media transport.
  - Background thread for packet sending.
- Builds RTP packets using `RtpPacket` and sends them to client RTP port.

### `VideoStream.py`
- Handles MJPEG frame file reading.
- Opens input video file in binary mode.
- `nextFrame()`:
  - Reads first 5 bytes as frame length.
  - Reads frame payload of that length.
  - Increments frame counter.
- `frameNbr()` returns current frame sequence number.

### `RtpPacket.py`
- RTP packet representation and encode/decode helper.
- Defines RTP fixed header size (`12` bytes).
- Includes getters for version, sequence number, timestamp, payload type, payload, and full packet bytes.
- **Important:** `encode()` is currently marked `TO COMPLETE`; header packing and payload assignment are not implemented yet.

### `ClientLauncher.py`
- Client app entry point.
- Reads CLI args (`serverAddr`, `serverPort`, `rtpPort`, `fileName`).
- Creates Tk root and starts `Client` GUI session.

### `Client.py`
- Main client implementation (UI + network protocol handling).
- GUI:
  - Buttons: Setup, Play, Pause, Teardown.
  - Label area to render video frames.
- RTSP:
  - Connects to server via TCP.
  - Sends requests and parses server replies.
  - Tracks sequence number, session ID, request type, client state.
- RTP:
  - Opens UDP socket.
  - Listens for RTP packets on a worker thread.
  - Decodes packets with `RtpPacket`, writes frame cache file, updates GUI.
- **Important:** multiple sections are marked `TO COMPLETE`:
  - RTSP request construction/sending in `sendRtspRequest`.
  - State transitions in `parseRtspReply`.
  - RTP socket creation/bind in `openRtpPort`.

### `movie.Mjpeg`
- Sample MJPEG video source used by server streaming logic.
- Expected by `VideoStream`/RTSP `SETUP` flow when requested by filename.

## Current Completeness Notes

- This repository appears to be a teaching/lab skeleton.
- Core structure is present, but client request logic and RTP header encoding are intentionally incomplete.
- The project runs end-to-end only after filling all `TO COMPLETE` sections in:
  - `Client.py`
  - `RtpPacket.py`
