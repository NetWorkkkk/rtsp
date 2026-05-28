import sys, socket, select

from ServerWorker import ServerWorker

class Server:	

	@staticmethod
	def _extract_rtsp_requests(buffer):
		"""Extract complete 3-line RTSP requests from stream buffer."""
		requests = []
		lines = buffer.split('\n')
		consumed = 0
		while len(lines) - consumed >= 3:
			l1 = lines[consumed].strip()
			l2 = lines[consumed + 1].strip()
			l3 = lines[consumed + 2].strip()
			if not l1 or not l2 or not l3:
				break
			if not l2.startswith("CSeq:"):
				break
			if not (l3.startswith("Transport:") or l3.startswith("Session:")):
				break
			requests.append("\n".join([l1, l2, l3]))
			consumed += 3

		remaining = "\n".join(lines[consumed:])
		return requests, remaining

	@staticmethod
	def _cleanup_client(fd, epoll, fd_to_socket, workers, buffers):
		worker = workers.pop(fd, None)
		if worker is not None:
			worker.close()
		buffers.pop(fd, None)
		sock = fd_to_socket.pop(fd, None)
		if sock is not None:
			try:
				epoll.unregister(fd)
			except OSError:
				pass
			try:
				sock.close()
			except OSError:
				pass
		
	def main(self):
		try:
			SERVER_PORT = int(sys.argv[1])
		except:
			print("[Usage: Server.py Server_port]\n")
			return

		rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		rtspSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		rtspSocket.bind(('', SERVER_PORT))
		rtspSocket.listen(5)
		rtspSocket.setblocking(False)

		epoll = select.epoll()
		epoll.register(rtspSocket.fileno(), select.EPOLLIN)

		fd_to_socket = {rtspSocket.fileno(): rtspSocket}
		workers = {}
		buffers = {}

		try:
			while True:
				for fd, event in epoll.poll(1):
					sock = fd_to_socket.get(fd)
					if sock is None:
						continue

					if fd == rtspSocket.fileno():
						while True:
							try:
								conn, addr = rtspSocket.accept()
							except BlockingIOError:
								break
							conn.setblocking(False)
							conn_fd = conn.fileno()
							fd_to_socket[conn_fd] = conn
							buffers[conn_fd] = ""
							clientInfo = {'rtspSocket': (conn, addr)}
							workers[conn_fd] = ServerWorker(clientInfo)
							epoll.register(conn_fd, select.EPOLLIN | select.EPOLLHUP | select.EPOLLERR)
					else:
						if event & (select.EPOLLHUP | select.EPOLLERR):
							self._cleanup_client(fd, epoll, fd_to_socket, workers, buffers)
							continue

						if event & select.EPOLLIN:
							try:
								chunk = sock.recv(4096)
							except BlockingIOError:
								continue
							except OSError:
								self._cleanup_client(fd, epoll, fd_to_socket, workers, buffers)
								continue

							if not chunk:
								self._cleanup_client(fd, epoll, fd_to_socket, workers, buffers)
								continue

							buffers[fd] += chunk.decode("utf-8", errors="ignore").replace("\r", "")
							requests, buffers[fd] = self._extract_rtsp_requests(buffers[fd])
							worker = workers.get(fd)
							for req in requests:
								print("Data received:\n" + req)
								if worker is None:
									break
								keepAlive = worker.processRtspRequest(req)
								if not keepAlive:
									self._cleanup_client(fd, epoll, fd_to_socket, workers, buffers)
									break
		finally:
			for fd in list(workers.keys()):
				self._cleanup_client(fd, epoll, fd_to_socket, workers, buffers)
			try:
				epoll.unregister(rtspSocket.fileno())
			except OSError:
				pass
			epoll.close()
			rtspSocket.close()

if __name__ == "__main__":
	(Server()).main()

