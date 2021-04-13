import os
import zmq
import zlib
import pickle
from io import BytesIO

from .ticker import Ticker
from .exchange import Exchange
from . import supported


class TickerParser(object):
	zmqContext = zmq.Context.instance()

	@staticmethod
	def execute_parser_request(endpoint, parameters, timeout=5):
		socket = TickerParser.zmqContext.socket(zmq.REQ)
		payload, responseText = None, None
		socket.connect("tcp://parser:6900")
		socket.setsockopt(zmq.LINGER, 0)
		poller = zmq.Poller()
		poller.register(socket, zmq.POLLIN)

		socket.send_multipart([endpoint, zlib.compress(pickle.dumps(parameters, -1))])
		responses = poller.poll(timeout * 1000)

		if len(responses) != 0:
			[response] = socket.recv_multipart()
			socket.close()
			return pickle.loads(zlib.decompress(response))
		else:
			socket.close()
			raise Exception("time out")
		return None

	@staticmethod
	def find_exchange(raw, platform, bias):
		return TickerParser.execute_parser_request(b"find_exchange", (raw, platform, bias))

	@staticmethod
	def process_known_tickers(ticker, exchange, platform, defaults, bias):
		return TickerParser.execute_parser_request(b"process_known_tickers", (ticker, exchange, platform, defaults, bias))

	@staticmethod
	def find_ccxt_crypto_market(ticker, exchange, platform, defaults):
		return TickerParser.execute_parser_request(b"find_ccxt_crypto_market", (ticker, exchange, platform, defaults))

	@staticmethod
	def find_coingecko_crypto_market(ticker):
		return TickerParser.execute_parser_request(b"find_coingecko_crypto_market", (ticker))

	@staticmethod
	def find_iexc_market(ticker, exchange):
		return TickerParser.execute_parser_request(b"find_iexc_market", (ticker, exchange))

	@staticmethod
	def find_quandl_market(ticker):
		return TickerParser.execute_parser_request(b"find_quandl_market", (ticker))

	@staticmethod
	def get_coingecko_image(base):
		return TickerParser.execute_parser_request(b"get_coingecko_image", (base))

	@staticmethod
	def check_if_fiat(tickerId):
		return TickerParser.execute_parser_request(b"check_if_fiat", (tickerId))

	@staticmethod
	def get_listings(ticker):
		return TickerParser.execute_parser_request(b"get_listings", (ticker))

	@staticmethod
	def get_formatted_price(exchange, symbol, price):
		return TickerParser.execute_parser_request(b"get_formatted_price", (exchange, symbol, price))

	@staticmethod
	def get_formatted_amount(exchange, symbol, price):
		return TickerParser.execute_parser_request(b"get_formatted_amount", (exchange, symbol, price))
