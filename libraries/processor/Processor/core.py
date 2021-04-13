import os
import time
import base64
import zmq.asyncio
import zlib
import pickle
from io import BytesIO

from DataRequest import ChartRequestHandler
from DataRequest import HeatmapRequestHandler
from DataRequest import PriceRequestHandler
from DataRequest import DetailRequestHandler
from DataRequest import TradeRequestHandler


class Processor(object):
	clientId = b"public"
	services = {
		"candle": "tcp://candle-server:6900",
		"chart": "tcp://image-server:6900",
		"depth": "tcp://quote-server:6900",
		"detail": "tcp://detail-server:6900",
		"heatmap": "tcp://image-server:6900",
		"quote": "tcp://quote-server:6900",
		"ichibot": "tcp://ichibot-server:6900"
	}
	zmqContext = zmq.asyncio.Context.instance()

	@staticmethod
	async def execute_data_server_request(service, request, timeout=60, retries=3):
		socket = Processor.zmqContext.socket(zmq.REQ)
		payload, responseText = None, None
		socket.connect(Processor.services[service])
		socket.setsockopt(zmq.LINGER, 0)
		poller = zmq.asyncio.Poller()
		poller.register(socket, zmq.POLLIN)

		request.timestamp = time.time()
		await socket.send_multipart([Processor.clientId, bytes(service, encoding='utf8'), zlib.compress(pickle.dumps(request, -1))])
		responses = await poller.poll(timeout * 1000)

		if len(responses) != 0:
			[response] = await socket.recv_multipart()
			socket.close()
			payload, responseText = pickle.loads(zlib.decompress(response))
			if service in ["chart", "heatmap", "depth"] and payload is not None:
				payload = BytesIO(base64.decodebytes(payload))
		else:
			socket.close()
			if retries == 1: raise Exception("time out")
			else: payload, responseText = await Processor.execute_data_server_request(service, request, retries=retries-1)

		return payload, responseText

	@staticmethod
	def process_chart_arguments(messageRequest, arguments, tickerId=None, platform=None, platformQueue=None, **kwargs):
		if isinstance(tickerId, str): tickerId = tickerId[:25]
		if platform is not None: platformQueue = [platform]
		elif platformQueue is None: platformQueue = messageRequest.get_platform_order_for("charts")

		for p in kwargs.get("excluded", []):
			if p in platformQueue:
				platformQueue.remove(p)

		if isinstance(messageRequest, int):
			authorId = messageRequest
			accountId = None
			messageRequest = None
		else:
			authorId = messageRequest.authorId
			accountId = messageRequest.accountId

		requestHandler = ChartRequestHandler(accountId, authorId, tickerId, platformQueue, messageRequest=messageRequest, **kwargs)
		for argument in arguments: requestHandler.parse_argument(argument)
		if tickerId is not None: requestHandler.process_ticker()

		requestHandler.set_defaults()
		requestHandler.find_caveats()
		outputMessage = requestHandler.get_preferred_platform()

		return outputMessage, requestHandler

	@staticmethod
	def process_heatmap_arguments(messageRequest, arguments, platform=None, platformQueue=None, **kwargs):
		if platform is not None: platformQueue = [platform]
		elif platformQueue is None: platformQueue = messageRequest.get_platform_order_for("heatmaps")

		for p in kwargs.get("excluded", []):
			if p in platformQueue:
				platformQueue.remove(p)

		if isinstance(messageRequest, int):
			authorId = messageRequest
			accountId = None
			messageRequest = None
		else:
			authorId = messageRequest.authorId
			accountId = messageRequest.accountId

		requestHandler = HeatmapRequestHandler(accountId, authorId, platformQueue, messageRequest=messageRequest, **kwargs)
		for argument in arguments: requestHandler.parse_argument(argument)

		requestHandler.set_defaults()
		requestHandler.find_caveats()
		outputMessage = requestHandler.get_preferred_platform()

		return outputMessage, requestHandler
	
	@staticmethod
	def process_quote_arguments(messageRequest, arguments, tickerId=None, platform=None, platformQueue=None, **kwargs):
		if isinstance(tickerId, str): tickerId = tickerId[:25]
		if platform is not None: platformQueue = [platform]
		elif platformQueue is None: platformQueue = messageRequest.get_platform_order_for("quotes")

		for p in kwargs.get("excluded", []):
			if p in platformQueue:
				platformQueue.remove(p)

		if isinstance(messageRequest, int):
			authorId = messageRequest
			accountId = None
			messageRequest = None
		else:
			authorId = messageRequest.authorId
			accountId = messageRequest.accountId

		requestHandler = PriceRequestHandler(accountId, authorId, tickerId, platformQueue, messageRequest=messageRequest, **kwargs)
		for argument in arguments: requestHandler.parse_argument(argument)
		if tickerId is not None: requestHandler.process_ticker()

		requestHandler.set_defaults()
		requestHandler.find_caveats()
		outputMessage = requestHandler.get_preferred_platform()

		return outputMessage, requestHandler

	@staticmethod
	def process_detail_arguments(messageRequest, arguments, tickerId=None, platform=None, platformQueue=None, **kwargs):
		if isinstance(tickerId, str): tickerId = tickerId[:25]
		if platform is not None: platformQueue = [platform]
		elif platformQueue is None: platformQueue = messageRequest.get_platform_order_for("details")

		for p in kwargs.get("excluded", []):
			if p in platformQueue:
				platformQueue.remove(p)

		if isinstance(messageRequest, int):
			authorId = messageRequest
			accountId = None
			messageRequest = None
		else:
			authorId = messageRequest.authorId
			accountId = messageRequest.accountId

		requestHandler = DetailRequestHandler(accountId, authorId, tickerId, platformQueue, messageRequest=messageRequest, **kwargs)
		for argument in arguments: requestHandler.parse_argument(argument)
		if tickerId is not None: requestHandler.process_ticker()

		requestHandler.set_defaults()
		requestHandler.find_caveats()
		outputMessage = requestHandler.get_preferred_platform()

		return outputMessage, requestHandler

	@staticmethod
	def process_trade_arguments(messageRequest, arguments, tickerId=None, platform=None, platformQueue=None, **kwargs):
		if isinstance(tickerId, str): tickerId = tickerId[:25]
		if platform is not None: platformQueue = [platform]
		elif platformQueue is None: platformQueue = messageRequest.get_platform_order_for("trades")

		for p in kwargs.get("excluded", []):
			if p in platformQueue:
				platformQueue.remove(p)

		if isinstance(messageRequest, int):
			authorId = messageRequest
			accountId = None
			messageRequest = None
		else:
			authorId = messageRequest.authorId
			accountId = messageRequest.accountId

		requestHandler = TradeRequestHandler(accountId, authorId, tickerId, platformQueue, messageRequest=messageRequest, **kwargs)
		for argument in arguments: requestHandler.parse_argument(argument)
		if tickerId is not None: requestHandler.process_ticker()

		requestHandler.set_defaults()
		requestHandler.find_caveats()
		outputMessage = requestHandler.get_preferred_platform()

		return outputMessage, requestHandler

	@staticmethod
	async def process_conversion(messageRequest, fromBase, toBase, amount):
		try: amount = float(amount)
		except: return None, "Provided amount is not a number."

		if fromBase == toBase: return None, "Converting into the same unit is trivial."

		payload1 = {
			"baseTicker": "USD",
			"quoteTicker": "USD",
			"raw": {
				"quotePrice": [1]
			}
		}
		payload2 = {
			"baseTicker": "USD",
			"quoteTicker": "USD",
			"raw": {
				"quotePrice": [1]
			}
		}

		if fromBase not in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			outputMessage, request = Processor.process_quote_arguments(messageRequest, [], tickerId=fromBase + "USD")
			if outputMessage is not None: return None, outputMessage
			payload1, quoteText = await Processor.execute_data_server_request("quote", request)
			if payload1 is None: return None, quoteText
		if toBase not in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			outputMessage, request = Processor.process_quote_arguments(messageRequest, [], tickerId="USD" + toBase)
			if outputMessage is not None: return None, outputMessage
			payload2, quoteText = await Processor.execute_data_server_request("quote", request)
			if payload2 is None: return None, quoteText

		convertedValue = payload1["raw"]["quotePrice"][0] * amount * payload2["raw"]["quotePrice"][0]

		payload = {
			"quotePrice": "{:,.3f}".format(amount),
			"quoteConvertedPrice": "{:,.6f} {}".format(convertedValue, payload2["quoteTicker"]),
			"baseTicker": payload1["baseTicker"],
			"quoteTicker": payload2["quoteTicker"],
			"messageColor":"deep purple",
			"sourceText": "Alpha Currency Conversions",
			"platform": "Alpha Currency Conversions",
			"raw": {
				"quotePrice": [convertedValue],
				"ticker": toBase,
				"timestamp": time.time()
			}
		}
		return payload, None

	@staticmethod
	def get_direct_ichibot_socket(identity):
		socket = Processor.zmqContext.socket(zmq.DEALER)
		socket.identity = identity.encode("ascii")
		socket.connect(Processor.services["ichibot"])
		socket.setsockopt(zmq.LINGER, 0)
		return socket