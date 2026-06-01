from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback
import io, queue
from datetime import datetime

from RtpPacket import RtpPacket, HEADER_SIZE as RTP_HEADER_SIZE

# Tile-mode constants (must match server). SD/UDP only — each frame is
# delivered as GRID_N x GRID_M independent JPEG tiles so that UDP packet
# loss only blanks parts of a frame, and missing tiles can be back-filled
# from the previously rendered frame.
GRID_N = 8
GRID_M = 8
NUM_TILES = GRID_N * GRID_M
MIN_TILES_TO_RENDER = 1 # (NUM_TILES + 1) // 2   # >= 50% threshold


class socketBaseHandler:
	"""Base class for media receive socket handlers."""

	def initSocket(self, port):
		raise NotImplementedError

	def recvData(self, max_size):
		raise NotImplementedError

	def destroy(self):
		raise NotImplementedError


class socketUDPHandler(socketBaseHandler):
	"""Receive media packets over UDP."""

	def __init__(self):
		self.sock = None

	def initSocket(self, port):
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.sock.settimeout(0.5)
		self.sock.bind(('', int(port)))

	def recvData(self, max_size):
		if self.sock is None:
			raise RuntimeError("UDP socket handler is not initialized")
		data = self.sock.recv(max_size)
		return data

	def destroy(self):
		if self.sock is not None:
			try:
				self.sock.close()
			except OSError:
				pass
			self.sock = None


class socketTCPHandler(socketBaseHandler):
	"""Receive media packets over TCP (server connects to client RTP port)."""

	def __init__(self):
		self.listen_sock = None
		self.conn_sock = None
		# TCP is a byte stream; buffer partial reads so recvData can hand back
		# exactly one RTP packet per call (matching the UDP handler's contract).
		self.recv_buffer = bytearray()

	def initSocket(self, port):
		self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self.listen_sock.bind(('', int(port)))
		self.listen_sock.listen(1)
		# Server connects to us during its SETUP handling (after we send the
		# RTSP SETUP). Accept on a background thread so initSocket doesn't block.
		threading.Thread(target=self._acceptConn, daemon=True).start()

	def _acceptConn(self):
		try:
			conn, _ = self.listen_sock.accept()
			conn.settimeout(0.5)
			self.conn_sock = conn
		except OSError:
			return
		finally:
			if self.listen_sock is not None:
				try:
					self.listen_sock.close()
				except OSError:
					pass
				self.listen_sock = None

	def recvData(self, max_size):
		if self.conn_sock is None:
			# Connection not yet accepted; treat as a transient read timeout
			# so listenRtp keeps looping until the server connects.
			raise socket.timeout()
		# Fill buffer until we have a complete RTP header, then parse the
		# payload length out of the SSRC field (sender-side convention).
		self._fill(RTP_HEADER_SIZE, max_size)
		h = self.recv_buffer
		payload_len = (h[8] << 24) | (h[9] << 16) | (h[10] << 8) | h[11]
		total_len = RTP_HEADER_SIZE + payload_len
		self._fill(total_len, max_size)
		packet = bytes(self.recv_buffer[:total_len])
		del self.recv_buffer[:total_len]
		return packet

	def _fill(self, needed, max_size):
		"""Read from the TCP socket until the buffer holds `needed` bytes."""
		while len(self.recv_buffer) < needed:
			chunk = self.conn_sock.recv(max_size)
			if not chunk:
				raise OSError("TCP media connection closed")
			self.recv_buffer.extend(chunk)

	def destroy(self):
		if self.conn_sock is not None:
			try:
				self.conn_sock.close()
			except OSError:
				pass
			self.conn_sock = None
		if self.listen_sock is not None:
			try:
				self.listen_sock.close()
			except OSError:
				pass
			self.listen_sock = None


class Client:
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT
	
	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3
	PACE = 4   # Internal marker for client-driven flow-control control message
	
	# Initiation..
	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.master.protocol("WM_DELETE_WINDOW", self.handler)
		self.createWidgets()
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.connectToServer()
		self.frameNbr = 0
		self.transport = "UDP"
		self.rtpSocketHandler = None
		self.wasPlayingBeforeSetup = False
		self.rtspReceiverStarted = False
		# --- Client-side frame buffer (hides SD<->HD switch gap) ---
		# Bounded FIFO between the RTP receiver thread (producer) and the Tk
		# main-thread render tick (consumer). Producer BLOCKS on overflow,
		# consumer SKIPS on underflow (keeps the last drawn frame on screen).
		self.frameBufferSize = 100          # 5s at 20fps
		self.preRollTarget = 15             # frames to accumulate before first render
		self.renderTickMs = 50              # matches server pacing (~20fps)
		self.frameBuffer = queue.Queue(maxsize=self.frameBufferSize)
		self.renderTickScheduled = False
		self.preRollDone = False
		# --- Flow control over RTSP ---
		# When the buffer crosses the high-water mark the client tells the
		# server to hold off; when it drops to the low-water mark it tells
		# the server to resume. Hysteresis prevents oscillation.
		self.highWaterMark = 90
		self.lowWaterMark = 60
		self.flowPaced = False
		self.paceSeq = 0
		# Serialises writes to rtspSocket across producer/consumer/Tk threads.
		self.rtspLock = threading.Lock()
		# --- Tile reconstruction state (SD/UDP only) ---
		# currentTileFrameNbr is the frame whose tiles we're presently
		# collecting; currentTiles maps tile_idx -> JPEG bytes for it;
		# lastTiles caches the most recently received decoded tile per
		# position, used as fallback when a tile is missing in a new frame.
		self.currentTileFrameNbr = -1
		self.currentTiles = {}
		self.lastTiles = {}
		# --- Debug instrumentation ---
		self.debugEnabled = True
		self.debugPacketEvery = 20
		self.debugRenderEvery = 10
		self.debugRenderTickCount = 0
		self.debugRtpPacketCount = 0
		self.debugRenderFrameCount = 0
		self.debugPreRollLastQsize = -1

	def _dbg(self, message):
		"""Debug logger with timestamp + thread name."""
		if not self.debugEnabled:
			return
		ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
		tname = threading.current_thread().name
		print(f"[DBG {ts}][{tname}] {message}")
		
	def createWidgets(self):
		"""Build GUI."""
		self.qualityMode = StringVar(value="SD")
		bg_main = "#f4f6f8"
		bg_panel = "#ffffff"
		text_main = "#1f2937"
		text_muted = "#475569"
		primary = "#0a9396"
		primary_dark = "#005f73"
		accent = "#e9f5f5"
		warn = "#bb3e03"

		self.master.configure(bg=bg_main, padx=12, pady=12)
		self.master.grid_rowconfigure(0, weight=1)
		self.master.grid_columnconfigure(0, weight=1)

		# Video area
		self.videoFrame = Frame(self.master, bg=bg_panel, bd=1, relief=RIDGE)
		self.videoFrame.grid(row=0, column=0, sticky=N+S+E+W)
		self.videoFrame.grid_rowconfigure(0, weight=1)
		self.videoFrame.grid_columnconfigure(0, weight=1)

		self.label = Label(
			self.videoFrame,
			height=19,
			bg="white",
			fg="#dbeafe",
			text="No video stream yet",
			font=("Trebuchet MS", 12, "bold")
		)
		self.label.grid(row=0, column=0, sticky=N+S+E+W, padx=8, pady=8)

		# Control bar
		self.controlsFrame = Frame(self.master, bg=bg_main)
		self.controlsFrame.grid(row=1, column=0, sticky=E+W, pady=(10, 0))
		for i in range(4):
			self.controlsFrame.grid_columnconfigure(i, weight=1)

		btn_font = ("Trebuchet MS", 11, "bold")
		self.setup = Button(
			self.controlsFrame,
			text="Setup",
			command=self.setupMovie,
			font=btn_font,
			bg=primary,
			fg="white",
			activebackground=primary_dark,
			activeforeground="white",
			bd=0,
			padx=10,
			pady=8
		)
		self.setup.grid(row=0, column=0, sticky=E+W, padx=(0, 6))

		self.start = Button(
			self.controlsFrame,
			text="Play",
			command=self.playMovie,
			font=btn_font,
			bg=primary,
			fg="white",
			activebackground=primary_dark,
			activeforeground="white",
			bd=0,
			padx=10,
			pady=8
		)
		self.start.grid(row=0, column=1, sticky=E+W, padx=6)

		self.pause = Button(
			self.controlsFrame,
			text="Pause",
			command=self.pauseMovie,
			font=btn_font,
			bg="#94a3b8",
			fg="white",
			activebackground="#64748b",
			activeforeground="white",
			bd=0,
			padx=10,
			pady=8
		)
		self.pause.grid(row=0, column=2, sticky=E+W, padx=6)

		self.teardown = Button(
			self.controlsFrame,
			text="Teardown",
			command=self.exitClient,
			font=btn_font,
			bg=warn,
			fg="white",
			activebackground="#9a3412",
			activeforeground="white",
			bd=0,
			padx=10,
			pady=8
		)
		self.teardown.grid(row=0, column=3, sticky=E+W, padx=(6, 0))

		# Footer: quality selector + status
		self.footerFrame = Frame(self.master, bg=bg_main)
		self.footerFrame.grid(row=2, column=0, sticky=E+W, pady=(10, 0))
		self.footerFrame.grid_columnconfigure(0, weight=0)
		self.footerFrame.grid_columnconfigure(1, weight=1)

		self.qualityFrame = LabelFrame(
			self.footerFrame,
			text="Streaming Quality",
			font=("Trebuchet MS", 10, "bold"),
			bg=bg_panel,
			fg=text_main,
			padx=8,
			pady=6,
			bd=1,
			relief=RIDGE
		)
		self.qualityFrame.grid(row=0, column=0, sticky=W)

		self.sdRadio = Radiobutton(
			self.qualityFrame,
			text="SD",
			variable=self.qualityMode,
			value="SD",
			command=self.onQualityModeChanged,
			indicatoron=False,
			font=("Trebuchet MS", 10, "bold"),
			bg=accent,
			fg=text_main,
			selectcolor=primary,
			activebackground=accent,
			padx=14,
			pady=4
		)
		self.sdRadio.grid(row=0, column=0, padx=(0, 6), pady=2, sticky=W)

		self.hdRadio = Radiobutton(
			self.qualityFrame,
			text="HD",
			variable=self.qualityMode,
			value="HD",
			command=self.onQualityModeChanged,
			indicatoron=False,
			font=("Trebuchet MS", 10, "bold"),
			bg=accent,
			fg=text_main,
			selectcolor=primary,
			activebackground=accent,
			padx=14,
			pady=4
		)
		self.hdRadio.grid(row=0, column=1, padx=(0, 2), pady=2, sticky=W)

		self.statusLabel = Label(
			self.footerFrame,
			text="Mode: SD | Transport: UDP",
			bg=bg_main,
			fg=text_muted,
			font=("Trebuchet MS", 10)
		)
		self.statusLabel.grid(row=0, column=1, sticky=E, padx=(12, 0))
		
	def setupMovie(self):
		"""Setup button handler."""
		if self.state in (self.INIT, self.READY, self.PLAYING):
			self.sendRtspRequest(self.SETUP)

	def onQualityModeChanged(self):
		"""Sample handler: called whenever SD/HD radio selection changes."""
		selectedMode = self.qualityMode.get()
		print("Selected streaming quality:", selectedMode)
		self.transport = "TCP" if selectedMode == "HD" else "UDP"
		self.statusLabel.configure(text=f"Mode: {selectedMode} | Transport: {self.transport}")
		# TODO: Add behavior here, e.g. switch transport/profile before SETUP.
		if self.state == self.INIT:
			# Do nothing, the transport will be used in the upcoming SETUP request.
			return
		self.sendRtspRequest(self.SETUP)
	
	def exitClient(self):
		"""Teardown button handler."""
		# Server resets flowEvent when starting a worker; clearing locally
		# keeps the client side of the flow-control state consistent.
		self.flowPaced = False
		self.sendRtspRequest(self.TEARDOWN)
		self.master.destroy() # Close the gui window

	def pauseMovie(self):
		"""Pause button handler."""
		print("Pausing movie before if...", self.state)

		if self.state == self.PLAYING:
			print("Pausing movie...")
			self.flowPaced = False
			self.sendRtspRequest(self.PAUSE)
	
	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY:
			self._dbg(
				f"PLAY pressed; state={self.state}, requestSent={self.requestSent}, "
				f"buffer={self.frameBuffer.qsize()}/{self.frameBufferSize}, transport={self.transport}"
			)
			self.playEvent = threading.Event()
			self.playEvent.clear()
			# Create a new thread to listen for RTP packets
			threading.Thread(target=self.listenRtp).start()
			self.sendRtspRequest(self.PLAY)
			# Pre-roll again on each PLAY so a long pause doesn't show a stale
			# frame instantly; the existing buffered frames are preserved.
			self.preRollDone = False
			if not self.renderTickScheduled:
				self.renderTickScheduled = True
				self.master.after(self.renderTickMs, self._renderTick)
	
	def listenRtp(self):
		"""Listen for RTP packets and push payloads into the frame buffer."""
		self._dbg("listenRtp started")
		while True:
			try:
				data = self.rtpSocketHandler.recvData(20480)
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					self.debugRtpPacketCount += 1

					if self.transport == "UDP":
						# SD/UDP path: each packet is one tile of an N x M grid.
						self._handleTilePacket(rtpPacket)
					else:
						# HD/TCP path: each packet is a complete MJPEG frame.
						currFrameNbr = rtpPacket.seqNum()
						if currFrameNbr > self.frameNbr: # Discard the late packet
							self.frameNbr = currFrameNbr
							self._enqueuePayload(rtpPacket.getPayload())
					if self.debugRtpPacketCount % self.debugPacketEvery == 0:
						self._dbg(
							f"RTP received={self.debugRtpPacketCount}, frameNbr={self.frameNbr}, "
							f"buffer={self.frameBuffer.qsize()}/{self.frameBufferSize}, "
							f"state={self.state}, preRollDone={self.preRollDone}"
						)
			except Exception:
				self._dbg(
					f"listenRtp exception; state={self.state}, requestSent={self.requestSent}, "
					f"teardownAcked={self.teardownAcked}, buffer={self.frameBuffer.qsize()}/{self.frameBufferSize}"
				)
				traceback.print_exc()
				# Stop listening upon requesting PAUSE or TEARDOWN
				if self.playEvent.isSet():
					self._dbg("listenRtp stopping because playEvent is set")
					break

				# Upon receiving ACK for TEARDOWN request, close RTP socket.
				if self.teardownAcked == 1:
					if self.rtpSocketHandler is not None:
						self.rtpSocketHandler.destroy()
						self.rtpSocketHandler = None
					self._dbg("listenRtp stopping because teardownAcked=1")
					break

	def _handleTilePacket(self, rtpPacket):
		"""Collect tiles for one frame; close & compose when the frame turns over
		or all tiles arrived. Late packets for older frames are discarded.
		"""
		frame_nbr = rtpPacket.seqNum()
		tile_idx = rtpPacket.ssrc() & 0xFF

		if frame_nbr > self.currentTileFrameNbr:
			# A new frame has started — finalise the previous one (if any).
			if self.currentTiles:
				self._closeFrame()
			self.currentTileFrameNbr = frame_nbr
			self.currentTiles = {tile_idx: rtpPacket.getPayload()}
		elif frame_nbr == self.currentTileFrameNbr:
			self.currentTiles[tile_idx] = rtpPacket.getPayload()
		else:
			# Late packet for an already-closed frame: drop.
			return

		# Fast path: all tiles in, no need to wait for next frame to close.
		if len(self.currentTiles) == NUM_TILES:
			self._closeFrame()

	def _closeFrame(self):
		"""Compose the in-progress tile set into a frame and enqueue, or drop."""
		tiles = self.currentTiles
		self.currentTiles = {}
		if len(tiles) < MIN_TILES_TO_RENDER:
			return  # below 50%, frame is unusable
		composed = self._composeFrame(tiles)
		if composed is not None:
			self._enqueuePayload(composed)

	def _composeFrame(self, tiles):
		"""Build a full-frame PIL Image from received tiles, filling missing
		positions from lastTiles or with black.
		"""
		decoded = {}
		for idx, jpg in tiles.items():
			try:
				decoded[idx] = Image.open(io.BytesIO(jpg)).convert("RGB")
			except Exception:
				continue
		if not decoded:
			return None
		sample = next(iter(decoded.values()))
		tile_w, tile_h = sample.size
		canvas = Image.new("RGB", (tile_w * GRID_N, tile_h * GRID_M), (0, 0, 0))
		for idx in range(NUM_TILES):
			col = idx % GRID_N
			row = idx // GRID_N
			pos = (col * tile_w, row * tile_h)
			if idx in decoded:
				tile_img = decoded[idx]
				# Refresh fallback only with freshly received tiles, never
				# with previously-borrowed ones — otherwise stale tiles would
				# propagate forever.
				self.lastTiles[idx] = tile_img
			elif idx in self.lastTiles:
				tile_img = self.lastTiles[idx]
			else:
				continue  # leave black
			canvas.paste(tile_img, pos)
		return canvas

	def _enqueuePayload(self, payload):
		"""Block producer when the buffer is full; bail out on stop signals."""
		while True:
			try:
				self.frameBuffer.put(payload, timeout=0.2)
				if self.frameBuffer.qsize() in (1, self.preRollTarget, self.highWaterMark):
					self._dbg(
						f"enqueue ok, qsize={self.frameBuffer.qsize()}/{self.frameBufferSize}, "
						f"state={self.state}, preRollDone={self.preRollDone}"
					)
				break
			except queue.Full:
				self._dbg(
					f"enqueue blocked (FULL), qsize={self.frameBuffer.qsize()}/{self.frameBufferSize}, "
					f"state={self.state}"
				)
				if self.playEvent.isSet() or self.teardownAcked == 1:
					self._dbg("enqueue abort due to stop/teardown flag")
					return
				# else keep waiting for consumer to drain
		# High-water: ask the server to hold off so the kernel UDP recv
		# buffer can't fill while we're behind on rendering.
		if not self.flowPaced and self.frameBuffer.qsize() >= self.highWaterMark:
			self.flowPaced = True
			self.sendPaceRequest("PAUSE")

	def _renderTick(self):
		"""Tk-thread consumer: pop one frame per tick, skip if buffer empty."""
		self.debugRenderTickCount += 1
		if self.state != self.PLAYING:
			# VPN/high-latency links can delay PLAY ACK past the first tick.
			# Keep the render loop alive while PLAY is in-flight, otherwise the
			# producer fills the buffer but nothing ever consumes it.
			if self.state == self.READY and self.requestSent == self.PLAY:
				if self.debugRenderTickCount % self.debugRenderEvery == 0:
					self._dbg(
						f"render waiting PLAY ACK, tick={self.debugRenderTickCount}, "
						f"qsize={self.frameBuffer.qsize()}/{self.frameBufferSize}"
					)
				self.master.after(self.renderTickMs, self._renderTick)
				return
			self._dbg(
				f"render loop stop; state={self.state}, requestSent={self.requestSent}, "
				f"tick={self.debugRenderTickCount}, qsize={self.frameBuffer.qsize()}/{self.frameBufferSize}"
			)
			self.renderTickScheduled = False
			return

		# Pre-roll: wait until the buffer has built up before drawing the
		# first frame; smooths out the producer ramp-up after PLAY.
		if not self.preRollDone:
			qsize = self.frameBuffer.qsize()
			if qsize < self.preRollTarget:
				if qsize != self.debugPreRollLastQsize:
					self._dbg(
						f"preRoll waiting: qsize={qsize}/{self.preRollTarget}, "
						f"tick={self.debugRenderTickCount}"
					)
					self.debugPreRollLastQsize = qsize
				self.master.after(self.renderTickMs, self._renderTick)
				return
			self.preRollDone = True
			self._dbg(
				f"preRoll done at qsize={qsize}, tick={self.debugRenderTickCount}; start rendering"
			)

		try:
			payload = self.frameBuffer.get_nowait()
			self.updateMovie(payload)
			self.debugRenderFrameCount += 1
			if self.debugRenderFrameCount % self.debugRenderEvery == 0:
				self._dbg(
					f"rendered frames={self.debugRenderFrameCount}, "
					f"qsize={self.frameBuffer.qsize()}/{self.frameBufferSize}, flowPaced={self.flowPaced}"
				)
			# Low-water: tell the server it's safe to send again.
			if self.flowPaced and self.frameBuffer.qsize() <= self.lowWaterMark:
				self.flowPaced = False
				self.sendPaceRequest("RESUME")
		except queue.Empty:
			# Underflow: keep showing the last drawn frame, try again next tick.
			if self.debugRenderTickCount % self.debugRenderEvery == 0:
				self._dbg(
					f"render underflow, qsize={self.frameBuffer.qsize()}/{self.frameBufferSize}, "
					f"state={self.state}"
				)
		except Exception:
			self._dbg("updateMovie raised exception")
			traceback.print_exc()

		self.master.after(self.renderTickMs, self._renderTick)

	def updateMovie(self, payload):
		"""Render a queued frame into the GUI. Accepts either a JPEG byte
		string (HD/TCP whole-frame path) or a pre-composed PIL Image
		(SD/UDP tile-reconstruction path).
		"""
		if isinstance(payload, (bytes, bytearray)):
			img = Image.open(io.BytesIO(payload))
		else:
			img = payload
		photo = ImageTk.PhotoImage(img)
		self.label.configure(image=photo, height=288)
		self.label.image = photo
		
	def connectToServer(self):
		"""Connect to the Server. Start a new RTSP/TCP session."""
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)
	
	def sendRtspRequest(self, requestCode):
		"""Send RTSP request to the server."""	
		# Setup request
		if requestCode == self.SETUP:
			if not self.rtspReceiverStarted:
				threading.Thread(target=self.recvRtspReply).start()
				self.rtspReceiverStarted = True
			# Update RTSP sequence number.
			self.rtspSeq += 1
			self.wasPlayingBeforeSetup = (self.state == self.PLAYING)

			try:
				self.openRtpPort()
			except:
				tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)
				return
			
			# Write the RTSP request to be sent.
			request = (
				f"SETUP {self.fileName} RTSP/1.0\n"
				f"CSeq: {self.rtspSeq}\n"
				f"Transport: RTP/{self.transport}; client_port= {self.rtpPort}"
			)
			
			# Keep track of the sent request.
			self.requestSent = self.SETUP
		
		# Play request
		elif requestCode == self.PLAY and self.state == self.READY:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			
			# Write the RTSP request to be sent.
			request = (
				f"PLAY {self.fileName} RTSP/1.0\n"
				f"CSeq: {self.rtspSeq}\n"
				f"Session: {self.sessionId}"
			)
			
			# Keep track of the sent request.
			self.requestSent = self.PLAY
		
		# Pause request
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			
			# Write the RTSP request to be sent.
			request = (
				f"PAUSE {self.fileName} RTSP/1.0\n"
				f"CSeq: {self.rtspSeq}\n"
				f"Session: {self.sessionId}"
			)
			
			# Keep track of the sent request.
			self.requestSent = self.PAUSE
			
		# Teardown request
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			# Update RTSP sequence number.
			self.rtspSeq += 1
			
			# Write the RTSP request to be sent.
			request = (
				f"TEARDOWN {self.fileName} RTSP/1.0\n"
				f"CSeq: {self.rtspSeq}\n"
				f"Session: {self.sessionId}"
			)
			
			# Keep track of the sent request.
			self.requestSent = self.TEARDOWN
		else:
			return
		
		# Send the RTSP request using rtspSocket. Locked because PACE
		# messages may also write to this socket from other threads.
		with self.rtspLock:
			self.rtspSocket.send(request.encode())

		print('\nData sent:\n' + request)

	def sendPaceRequest(self, action):
		"""Send a flow-control PACE message (PAUSE | RESUME) to the server.

		Uses its own CSeq counter so PACE replies never match the playback
		request seq — they're effectively ignored by parseRtspReply.
		"""
		if self.sessionId == 0 or self.teardownAcked == 1:
			return
		with self.rtspLock:
			self.paceSeq += 1
			request = (
				f"PACE {action} RTSP/1.0\n"
				f"CSeq: -{self.paceSeq}\n"
				f"Session: {self.sessionId}"
			)
			try:
				self.rtspSocket.send(request.encode())
			except OSError:
				pass
		print(f"\n[PACE] {action} sent (buffer={self.frameBuffer.qsize()}/{self.frameBufferSize})")
	
	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			reply = self.rtspSocket.recv(1024)
			if not reply:
				self._dbg("RTSP recv returned EOF; stopping recvRtspReply")
				break
			self._dbg(f"RTSP raw reply bytes={len(reply)}")
			
			if reply: 
				try:
					self.parseRtspReply(reply.decode("utf-8"))
				except Exception:
					self._dbg("parseRtspReply failed")
					traceback.print_exc()
			
		# Close the RTSP socket upon requesting Teardown
				if self.requestSent == self.TEARDOWN:
					self.rtspSocket.shutdown(socket.SHUT_RDWR)
					self.rtspSocket.close()
					self.rtspReceiverStarted = False
					break
	
	def parseRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
		self._dbg(f"RTSP reply text: {data!r}")
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		# Process only if the server reply's sequence number is the same as the request's
		if seqNum == self.rtspSeq:
			session = int(lines[2].split(' ')[1])
			# New RTSP session ID
			if self.sessionId == 0:
				self.sessionId = session
			
			# Process only if the session ID is the same
			if self.sessionId == session:
				if int(lines[0].split(' ')[1]) == 200:
					if self.requestSent == self.SETUP:
						# For server-side media reconnect on SETUP:
						# keep PLAYING if this SETUP was sent while playing.
						self.state = self.PLAYING if self.wasPlayingBeforeSetup else self.READY
						self.wasPlayingBeforeSetup = False
					elif self.requestSent == self.PLAY:
						self.state = self.PLAYING
					elif self.requestSent == self.PAUSE:
						self.state = self.READY
						
						# The play thread exits. A new thread is created on resume.
						self.playEvent.set()
					elif self.requestSent == self.TEARDOWN:
						self.state = self.INIT
						
						# Flag the teardownAcked to close the socket.
						self.teardownAcked = 1 
		else:
			self._dbg(f"Ignore RTSP reply with seq={seqNum}, expected={self.rtspSeq}")
		print("[+] Updated State:", self.state)
	
	def openRtpPort(self):
		"""Open RTP socket binded to a specified port."""
		if self.rtpSocketHandler is not None:
			self.rtpSocketHandler.destroy()
			self.rtpSocketHandler = None
		self.rtpSocketHandler = socketTCPHandler() if self.transport == "TCP" else socketUDPHandler()
		self.rtpSocketHandler.initSocket(self.rtpPort)
		# Drop any in-progress tile assembly so a transport switch doesn't
		# carry partial state into the new session. lastTiles is kept
		# intentionally so it can still back-fill the first new SD frame.
		self.currentTileFrameNbr = -1
		self.currentTiles = {}

	def handler(self):
		"""Handler on explicitly closing the GUI window."""
		self.pauseMovie()
		if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: # When the user presses cancel, resume playing.
			self.playMovie()
