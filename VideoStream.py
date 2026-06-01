class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		self._open()
		self.frameNum = 0

	def _open(self):
		try:
			self.file = open(self.filename, 'rb')
		except:
			raise IOError

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
		data = self.file.read(5) # Get the framelength from the first 5 bits
		if data: 
			framelength = int(data)
							
			# Read the current frame
			data = self.file.read(framelength)
			self.frameNum += 1
		return data
		
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
	
	
