import os
import zmq.asyncio
import pickle
from io import BytesIO


class DatabaseConnector(object):
	zmqContext = zmq.asyncio.Context.instance()

	def __init__(self, mode):
		self.mode = mode

	@staticmethod
	async def execute_parser_request(endpoint, parameters, timeout=0.5):
		socket = DatabaseConnector.zmqContext.socket(zmq.REQ)
		payload, responseText = None, None
		socket.connect("tcp://database:6900")
		socket.setsockopt(zmq.LINGER, 0)
		poller = zmq.asyncio.Poller()
		poller.register(socket, zmq.POLLIN)

		await socket.send_multipart([endpoint, parameters])
		responses = await poller.poll(timeout * 1000)

		if len(responses) != 0:
			[response] = await socket.recv_multipart()
			socket.close()
			return pickle.loads(response)
		else:
			socket.close()
		return None

	async def check_status(self):
		try: return await DatabaseConnector.execute_parser_request(bytes(self.mode + "_status", encoding='utf8'), b"")
		except: return False

	async def keys(self, default={}):
		try: response = await DatabaseConnector.execute_parser_request(bytes(self.mode + "_keys", encoding='utf8'), b"", timeout=5.0)
		except: return default

		if response is None:
			return default
		return response

	async def get(self, value, default=None):
		try: response = await DatabaseConnector.execute_parser_request(bytes(self.mode + "_fetch", encoding='utf8'), bytes(str(value), encoding='utf8'))
		except: return default

		if response is None:
			return default
		return response

	async def match(self, value, default=None):
		try: response = await DatabaseConnector.execute_parser_request(bytes(self.mode + "_match", encoding='utf8'), bytes(str(value), encoding='utf8'))
		except: return default

		if response is None:
			return default
		return response