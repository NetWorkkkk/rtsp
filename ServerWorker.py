from random import randint
import os
import sys, traceback, threading, socket
from io import BytesIO

from PIL import Image

from VideoStream import VideoStream
from RtpPacket import RtpPacket

# Tile-mode constants (SD/UDP only). Each MJPEG frame is split into a
# GRID_N x GRID_M grid of independently-encoded JPEG tiles so individual
# UDP packet drops affect only a fraction of the frame.
GRID_N = 8
GRID_M = 8
NUM_TILES = GRID_N * GRID_M


class socketBaseHandler:
	"""Base class for media transport handlers."""

	def __init__(self):
		self.sock = None

	def initSocket(self, address, port):
		raise NotImplementedError

	def sendData(self, data):
		raise NotImplementedError

	def destroy(self):
		if self.sock is not None:
			try:
				self.sock.close()
			except OSError:
				pass
			self.sock = None


class socketUDPHandler(socketBaseHandler):
	"""Send media packets over UDP."""

	def __init__(self):
		super().__init__()
		self.remote = None

	def initSocket(self, address, port):
		self.destroy()
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.remote = (address, int(port))

	def sendData(self, data):
		if self.sock is None or self.remote is None:
			raise RuntimeError("UDP socket handler is not initialized")
		self.sock.sendto(data, self.remote)

	def destroy(self):
		self.remote = None
		super().destroy()


class socketTCPHandler(socketBaseHandler):
	"""Send media packets over TCP."""

	def __init__(self):
		super().__init__()
		self.remote = None

	def initSocket(self, address, port):
		self.destroy()
		self.remote = (address, int(port))
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.sock.connect(self.remote)

	def sendData(self, data):
		if self.sock is None:
			raise RuntimeError("TCP socket handler is not initialized")
		self.sock.sendall(data)

	def destroy(self):
		self.remote = None
		super().destroy()


class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	PACE = 'PACE'  # Client-driven flow control: PACE PAUSE / PACE RESUME
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2
	
	clientInfo = {}
	
	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		# Flow-control gate driven by the client's PACE messages.
		# set = sender may emit; clear = sender must hold off.
		self.clientInfo['flowEvent'] = threading.Event()
		self.clientInfo['flowEvent'].set()

	def _stop_streaming(self):
		"""Stop current RTP sender thread if running."""
		event = self.clientInfo.get('event')
		if event is not None:
			event.set()
		worker = self.clientInfo.get('worker')
		if worker is not None and worker.is_alive():
			worker.join(timeout=0.2)
		self.clientInfo.pop('event', None)
		self.clientInfo.pop('worker', None)

	def _recreate_media_handler(self, transport, address, port):
		"""Tear down old media handler and create a new one for requested transport."""
		old_handler = self.clientInfo.pop('rtpSocketHandler', None)
		if old_handler is not None:
			old_handler.destroy()
		new_handler = socketTCPHandler() if transport == "TCP" else socketUDPHandler()
		new_handler.initSocket(address, port)
		self.clientInfo['rtpSocketHandler'] = new_handler

	def _start_streaming_worker(self):
		"""Start RTP sender worker thread."""
		# Reset flow-control to GO; client will re-pace if it falls behind again.
		self.clientInfo['flowEvent'].set()
		self.clientInfo['event'] = threading.Event()
		self.clientInfo['worker'] = threading.Thread(target=self.sendRtp)
		self.clientInfo['worker'].start()

	def _close_video_stream(self, stream):
		"""Close one VideoStream instance safely."""
		if stream is None:
			return
		try:
			stream.file.close()
		except Exception:
			pass

	def _close_all_profile_streams(self):
		"""Close SD/HD streams and clear profile metadata."""
		streams = self.clientInfo.pop('videoStreams', None)
		if streams is not None:
			for stream in streams.values():
				self._close_video_stream(stream)
		self.clientInfo.pop('videoStream', None)
		self.clientInfo.pop('requestedBaseName', None)

	def _build_profile_paths(self, requested_name):
		"""Map requested `video.mjpeg` to `SD_video.mjpeg` and `HD_video.mjpeg`."""
		base_dir = os.path.dirname(requested_name)
		base_name = os.path.basename(requested_name)
		sd_name = "SD_" + base_name
		hd_name = "HD_" + base_name
		return (
			os.path.join(base_dir, sd_name),
			os.path.join(base_dir, hd_name),
		)

	def _prepare_profile_streams(self, requested_name):
		"""Open both SD/HD streams once and keep them for fast transport switching."""
		current_name = self.clientInfo.get('requestedBaseName')
		if current_name == requested_name and 'videoStreams' in self.clientInfo:
			return

		self._close_all_profile_streams()
		sd_path, hd_path = self._build_profile_paths(requested_name)
		streams = {
			'SD': VideoStream(sd_path),
			'HD': VideoStream(hd_path),
		}
		self.clientInfo['videoStreams'] = streams
		self.clientInfo['requestedBaseName'] = requested_name

	def _select_active_profile_stream(self):
		"""Select stream by transport policy: UDP->SD, TCP->HD."""
		protocol = self.clientInfo.get('rtpProtocol')
		profile = 'HD' if protocol == 'TCP' else 'SD'
		self.clientInfo['videoStream'] = self.clientInfo['videoStreams'][profile]

	def _sync_profile_streams(self):
		"""Align SD/HD streams to the same frame index for seamless switching."""
		streams = self.clientInfo.get('videoStreams')
		if not streams:
			return
		target = max(stream.frameNbr() for stream in streams.values())
		for stream in streams.values():
			stream.seekFrame(target)
		
	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		shouldKeepAlive = True
		# Get the request type
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		### tai sao moi request deu co filename, ton dung luong qua
		# Get the media file name
		filename = line1[1]
		### Tai sao line 1 [0] khong su dung, seq nay gom nhung gi
		# Get the RTSP sequence number 
		seq = request[1].split(' ')
		
		# Process SETUP request
		if requestType == self.SETUP:
			print("processing SETUP\n")
			# Parse selected media transport (UDP/TCP) and client RTP port.
			transport_header = request[2].split(';')[0].upper()
			self.clientInfo['rtpProtocol'] = "TCP" if "TCP" in transport_header else "UDP"
			self.clientInfo['rtpPort'] = int(request[2].split(' ')[3])

			address = self.clientInfo['rtspSocket'][1][0]
			port = self.clientInfo['rtpPort']

			was_playing = (self.state == self.PLAYING)
			if was_playing:
				self._stop_streaming()

			try:
				self._prepare_profile_streams(filename)
			except IOError:
				self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				return shouldKeepAlive

			self._sync_profile_streams()
			self._select_active_profile_stream()

			if 'session' not in self.clientInfo:
				self.clientInfo['session'] = randint(100000, 999999)

			try:
				self._recreate_media_handler(self.clientInfo['rtpProtocol'], address, port)
			except:
				self.replyRtsp(self.CON_ERR_500, seq[1])
				return shouldKeepAlive

			# INIT -> READY, READY stays READY, PLAYING resumes PLAYING.
			self.state = self.PLAYING if was_playing else self.READY

			if was_playing:
				self._start_streaming_worker()

			self.replyRtsp(self.OK_200, seq[1])

		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				self.replyRtsp(self.OK_200, seq[1])
				
				# Create a new thread and start sending RTP packets
				self._start_streaming_worker()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				
				self._stop_streaming()
			
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process PACE request (client-driven flow control)
		elif requestType == self.PACE:
			action = filename.upper()  # filename slot carries PAUSE | RESUME
			if action == 'PAUSE':
				self.clientInfo['flowEvent'].clear()
			elif action == 'RESUME':
				self.clientInfo['flowEvent'].set()
			if 'session' in self.clientInfo:
				self.replyRtsp(self.OK_200, seq[1])

		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")
			self._stop_streaming()
			
			self.replyRtsp(self.OK_200, seq[1])
			
			# Close the RTP socket
			handler = self.clientInfo.pop('rtpSocketHandler', None)
			if handler is not None:
				handler.destroy()
			shouldKeepAlive = False
		return shouldKeepAlive
			
	def sendRtp(self):
		"""Send RTP packets to the client."""
		while True:
			self.clientInfo['event'].wait(0.05)

			# Stop sending if request is PAUSE or TEARDOWN
			if self.clientInfo['event'].isSet():
				break

			# Honour client-driven flow control. Wait with a timeout so the
			# stop signal above is still polled while we're flow-paused.
			if not self.clientInfo['flowEvent'].wait(timeout=0.5):
				continue

			streams = self.clientInfo.get('videoStreams')
			if streams:
				sd_stream = streams.get('SD')
				hd_stream = streams.get('HD')
				if sd_stream is None or hd_stream is None:
					continue
				sd_data = sd_stream.nextFrame()
				hd_data = hd_stream.nextFrame()
				if self.clientInfo.get('rtpProtocol') == "UDP":
					data = sd_data
					frameNumber = sd_stream.frameNbr()
				else:
					data = hd_data
					frameNumber = hd_stream.frameNbr()
			else:
				stream = self.clientInfo.get('videoStream')
				if stream is None:
					continue
				data = stream.nextFrame()
				frameNumber = stream.frameNbr()

			if not data:
				continue
			try:
				if self.clientInfo.get('rtpProtocol') == "UDP":
					self._sendTiledFrame(data, frameNumber)
				else:
					pkt = self.makeRtp(data, frameNumber, len(data))
					self.clientInfo['rtpSocketHandler'].sendData(pkt)
			except:
				print("Connection Error")

	def _sendTiledFrame(self, jpeg_bytes, frameNumber):
		"""Split an MJPEG frame into GRID_N x GRID_M JPEG tiles and send
		each as its own RTP packet. Tile index is carried in the SSRC field.
		"""
		img = Image.open(BytesIO(jpeg_bytes))
		w, h = img.size
		tile_w = w // GRID_N
		tile_h = h // GRID_M
		for idx in range(NUM_TILES):
			col = idx % GRID_N
			row = idx // GRID_N
			box = (col * tile_w, row * tile_h, (col + 1) * tile_w, (row + 1) * tile_h)
			tile = img.crop(box)
			buf = BytesIO()
			tile.save(buf, format='JPEG')
			pkt = self.makeRtp(buf.getvalue(), frameNumber, idx)
			self.clientInfo['rtpSocketHandler'].sendData(pkt)

	def makeRtp(self, payload, frameNbr, ssrc):
		"""RTP-packetize the video data.

		ssrc carries transport-specific metadata: TCP framing uses
		len(payload); UDP tile mode uses the tile index.
		"""
		version = 2
		padding = 0
		extension = 0
		cc = 0
		marker = 0
		pt = 26 # MJPEG type
		seqnum = frameNbr

		rtpPacket = RtpPacket()
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
		return rtpPacket.getPacket()
		
	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			if 'session' not in self.clientInfo:
				self.clientInfo['session'] = randint(100000, 999999)
			#print("200 OK")
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		
		# Error messages
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")

	def close(self):
		"""Release session resources."""
		self._stop_streaming()
		handler = self.clientInfo.pop('rtpSocketHandler', None)
		if handler is not None:
			try:
				handler.destroy()
			except OSError:
				pass
		self._close_all_profile_streams()
