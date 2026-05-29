from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"


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
		return self.sock.recv(max_size)

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

	def initSocket(self, port):
		self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self.listen_sock.bind(('', int(port)))
		self.listen_sock.listen(1)
		self.listen_sock.settimeout(0.5)

	def recvData(self, max_size):
		if self.listen_sock is None:
			raise RuntimeError("TCP socket handler is not initialized")
		if self.conn_sock is None:
			self.conn_sock, _ = self.listen_sock.accept()
			self.conn_sock.settimeout(0.5)
		data = self.conn_sock.recv(max_size)
		if not data:
			raise OSError("TCP media connection closed")
		return data

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
		self.sendRtspRequest(self.TEARDOWN)		
		self.master.destroy() # Close the gui window
		os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT) # Delete the cache image from video

	def pauseMovie(self):
		"""Pause button handler."""
		print("Pausing movie before if...", self.state)

		if self.state == self.PLAYING:
			print("Pausing movie...")
			self.sendRtspRequest(self.PAUSE)
	
	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY:
			# Create a new thread to listen for RTP packets
			threading.Thread(target=self.listenRtp).start()
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)
	
	def listenRtp(self):		
		"""Listen for RTP packets."""
		while True:
			try:
				data = self.rtpSocketHandler.recvData(20480)
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					
					currFrameNbr = rtpPacket.seqNum()
										
					if currFrameNbr > self.frameNbr: # Discard the late packet
						self.frameNbr = currFrameNbr
						self.updateMovie(self.writeFrame(rtpPacket.getPayload()))
			except Exception:
				# Stop listening upon requesting PAUSE or TEARDOWN
				if self.playEvent.isSet(): 
					break

				# Upon receiving ACK for TEARDOWN request, close RTP socket.
				if self.teardownAcked == 1:
					if self.rtpSocketHandler is not None:
						self.rtpSocketHandler.destroy()
						self.rtpSocketHandler = None
					break
						
	def writeFrame(self, data):
		"""Write the received frame to a temp image file. Return the image file."""
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		file = open(cachename, "wb")
		file.write(data)
		file.close()
		
		return cachename
	
	def updateMovie(self, imageFile):
		"""Update the image file as video frame in the GUI."""
		photo = ImageTk.PhotoImage(Image.open(imageFile))
		self.label.configure(image = photo, height=288) 
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
		
		# Send the RTSP request using rtspSocket.
		self.rtspSocket.send(request.encode())
		
		print('\nData sent:\n' + request)
	
	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			reply = self.rtspSocket.recv(1024)
			
			if reply: 
				self.parseRtspReply(reply.decode("utf-8"))
			
		# Close the RTSP socket upon requesting Teardown
				if self.requestSent == self.TEARDOWN:
					self.rtspSocket.shutdown(socket.SHUT_RDWR)
					self.rtspSocket.close()
					self.rtspReceiverStarted = False
					break
	
	def parseRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		print("[+] Received Data:\n", data)
		print("[+] Current State:", self.state)
		print("[+] Current sent request:", self.requestSent)
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
		print("[+] Updated State:", self.state)
	
	def openRtpPort(self):
		"""Open RTP socket binded to a specified port."""
		if self.rtpSocketHandler is not None:
			self.rtpSocketHandler.destroy()
			self.rtpSocketHandler = None
		self.rtpSocketHandler = socketTCPHandler() if self.transport == "TCP" else socketUDPHandler()
		self.rtpSocketHandler.initSocket(self.rtpPort)

	def handler(self):
		"""Handler on explicitly closing the GUI window."""
		self.pauseMovie()
		if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: # When the user presses cancel, resume playing.
			self.playMovie()
