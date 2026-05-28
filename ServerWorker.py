from random import randint
import sys, traceback, threading, socket

from VideoStream import VideoStream
from RtpPacket import RtpPacket


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

	def initSocket(self, address, port):
		self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.sock.connect((address, int(port)))

	def sendData(self, data):
		if self.sock is None:
			raise RuntimeError("TCP socket handler is not initialized")
		self.sock.sendall(data)


class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	
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
			if self.state == self.INIT:
				# Update state
				print("processing SETUP\n")
				
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				### lo client session id trung nhau thi sao huuhhu
				# Generate a randomized RTSP session ID
				self.clientInfo['session'] = randint(100000, 999999)
				
				# Send RTSP reply
				self.replyRtsp(self.OK_200, seq[1])

				# Get selected media transport (UDP/TCP) and client RTP port.
				transport = request[2].split(';')[0].upper()
				self.clientInfo['rtpProtocol'] = "TCP" if "TCP" in transport else "UDP"
				self.clientInfo['rtpPort'] = int(request[2].split(' ')[3])
				self.clientInfo['rtpSocketHandler'] = socketTCPHandler() if self.clientInfo['rtpProtocol'] == "TCP" else socketUDPHandler()
			
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				
				# Create a new socket for RTP/UDP
				address = self.clientInfo['rtspSocket'][1][0]
				port = self.clientInfo['rtpPort']
				try:
					self.clientInfo['rtpSocketHandler'].initSocket(address, port)
				except:
					self.replyRtsp(self.CON_ERR_500, seq[1])
					return False
				
				self.replyRtsp(self.OK_200, seq[1])
				
				# Create a new thread and start sending RTP packets
				self.clientInfo['event'] = threading.Event()
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
				self.clientInfo['worker'].start()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				
				self.clientInfo['event'].set()
			
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")
			if 'event' in self.clientInfo:
				self.clientInfo['event'].set()
			
			self.replyRtsp(self.OK_200, seq[1])
			
			# Close the RTP socket
			if 'rtpSocketHandler' in self.clientInfo:
				self.clientInfo['rtpSocketHandler'].destroy()
			shouldKeepAlive = False
		return shouldKeepAlive
			
	def sendRtp(self):
		"""Send RTP packets over UDP."""
		while True:
			self.clientInfo['event'].wait(0.05) 
			
			# Stop sending if request is PAUSE or TEARDOWN
			if self.clientInfo['event'].isSet(): 
				break 
				
			data = self.clientInfo['videoStream'].nextFrame()
			if data: 
				frameNumber = self.clientInfo['videoStream'].frameNbr()
				try:
					self.clientInfo['rtpSocketHandler'].sendData(self.makeRtp(data, frameNumber))
				except:
					print("Connection Error")
					#print('-'*60)
					#traceback.print_exc(file=sys.stdout)
					#print('-'*60)

	def makeRtp(self, payload, frameNbr):
		"""RTP-packetize the video data."""
		version = 2
		padding = 0
		extension = 0
		cc = 0
		marker = 0
		pt = 26 # MJPEG type
		seqnum = frameNbr
		ssrc = 0 
		
		rtpPacket = RtpPacket()
		
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
		
		return rtpPacket.getPacket()
		
	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
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
		if 'event' in self.clientInfo:
			self.clientInfo['event'].set()
		if 'rtpSocketHandler' in self.clientInfo:
			try:
				self.clientInfo['rtpSocketHandler'].destroy()
			except OSError:
				pass
