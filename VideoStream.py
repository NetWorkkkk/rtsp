import os


class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		self._open()
		self.frameNum = 0

	def _open(self):
		try:
			self.file = open(self.filename, 'rb')
		except OSError:
			raise IOError
		self.streamFormat = self._detect_format()

	def _detect_format(self):
		"""Detect supported MJPEG container format without consuming bytes."""
		head = self.file.read(5)
		self.file.seek(0)
		if len(head) >= 5 and head.isdigit():
			return 'length-prefixed'
		if head.startswith(b'\xff\xd8'):
			return 'jpeg-stream'
		raise ValueError(
			f"Unsupported MJPEG format in {self.filename!r}: expected a 5-byte "
			"ASCII frame length or a JPEG SOI marker"
		)

	def reset(self):
		"""Rewind stream to beginning."""
		try:
			self.file.close()
		except Exception:
			pass
		self._open()
		self.frameNum = 0
		
	def nextFrame(self):
		"""Get next frame."""
		if self.streamFormat == 'jpeg-stream':
			return self._next_jpeg_stream_frame()

		data = self.file.read(5) # Get the framelength from the first 5 bits
		if data: 
			framelength = int(data.decode('ascii'))
								
			# Read the current frame
			data = self.file.read(framelength)
			self.frameNum += 1
		return data

	def _next_jpeg_stream_frame(self):
		"""Read one JPEG image from a raw concatenated MJPEG stream."""
		prefix = self.file.read(2)
		if not prefix:
			return b''

		if prefix != b'\xff\xd8':
			prefix = self._seek_to_next_soi(prefix)
			if not prefix:
				return b''

		frame = bytearray(prefix)
		while True:
			chunk = self.file.read(8192)
			if not chunk:
				return b''

			eoi_index = chunk.find(b'\xff\xd9')
			if eoi_index != -1:
				end = eoi_index + 2
				frame.extend(chunk[:end])
				unused = len(chunk) - end
				if unused:
					self.file.seek(-unused, os.SEEK_CUR)
				self.frameNum += 1
				return bytes(frame)

			frame.extend(chunk)

	def _seek_to_next_soi(self, initial_data):
		"""Skip bytes until the next JPEG SOI marker."""
		buffer = bytearray(initial_data)
		while True:
			soi_index = buffer.find(b'\xff\xd8')
			if soi_index != -1:
				unused = len(buffer) - (soi_index + 2)
				if unused:
					self.file.seek(-unused, os.SEEK_CUR)
				return b'\xff\xd8'

			chunk = self.file.read(8192)
			if not chunk:
				return b''
			buffer = buffer[-1:] + chunk
		
	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum

	def seekFrame(self, target_frame_num):
		"""Advance/rewind to target frame index."""
		if target_frame_num <= 0:
			if self.frameNum != 0:
				self.reset()
			return

		if target_frame_num < self.frameNum:
			self.reset()

		while self.frameNum < target_frame_num:
			if not self.nextFrame():
				break
	
	
