import os
import signal
import time
import uuid
from threading import Thread
import zmq
import zlib
import pickle
import traceback

import ccxt
from iexfinance.stocks import Stock
from google.cloud import error_reporting

from Cache import Cache
from TickerParser import TickerParser, Ticker, Exchange, supported

from helpers.utils import Utils


class CandleProcessor(object):
	stableCoinTickers = ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]

	def __init__(self):
		self.isServiceAvailable = True
		signal.signal(signal.SIGINT, self.exit_gracefully)
		signal.signal(signal.SIGTERM, self.exit_gracefully)

		self.logging = error_reporting.Client()
		self.cache = Cache(ttl=30)

		context = zmq.Context.instance()
		self.socket = context.socket(zmq.ROUTER)
		self.socket.bind("tcp://*:6900")

		print("[Startup]: Candle Server is online")

	def exit_gracefully(self):
		print("[Startup]: Candle Server is exiting")
		self.socket.close()
		self.isServiceAvailable = False

	def run(self):
		while self.isServiceAvailable:
			try:
				response = None, None
				origin, delimeter, clientId, service, request = self.socket.recv_multipart()
				request = pickle.loads(zlib.decompress(request))
				if request.timestamp + 30 < time.time(): continue

				if service == b"candle":
					response = self.request_candle(request)

			except (KeyboardInterrupt, SystemExit): return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			finally:
				try: self.socket.send_multipart([origin, delimeter, zlib.compress(pickle.dumps(response, -1))])
				except: pass

	def request_candle(self, request):
		payload, candleMessage, updatedCandleMessage = None, None, None

		for platform in request.platforms:
			request.set_current(platform=platform)
			hashCode = hash(request.requests[platform])
			fromCache = False

			if request.can_cache() and self.cache.has(hashCode):
				payload, updatedCandleMessage = self.cache.get(hashCode), None
				fromCache = True
			elif platform == "CCXT":
				payload, updatedCandleMessage = self.request_ccxt_candles(request)
			elif platform == "IEXC":
				payload, updatedCandleMessage = self.request_iexc_candles(request)
			elif platform == "Quandl":
				pass

			if payload is not None:
				if request.can_cache() and not fromCache: self.cache.set(hashCode, payload)
				return payload, updatedCandleMessage
			elif updatedCandleMessage is not None:
				candleMessage = updatedCandleMessage

		return None, candleMessage

	def request_ccxt_candles(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()

		try:
			if exchange is None: return None, None
			exchange = Exchange(exchange.id)

			try:
				rawData = exchange.properties.fetch_ohlcv(ticker.symbol, timeframe="1m", limit=60)
				if len(rawData) == 0 or rawData[-1][4] is None or rawData[0][1] is None: return None, None
			except:
				return None, None

			payload = {
				"candles": [],
				"title": ticker.name,
				"baseTicker": "USD" if ticker.base in CandleProcessor.stableCoinTickers else ticker.base,
				"quoteTicker": "USD" if ticker.quote in CandleProcessor.stableCoinTickers else ticker.quote,
				"sourceText": "on {}".format(exchange.name),
				"platform": "CCXT"
			}

			for e in rawData:
				timestamp = e[0] / 1000
				if ticker.isReversed:
					payload["candles"].append([timestamp, 1 / e[1], 1 / e[2], 1 / e[3], 1 / e[4]])
				else:
					payload["candles"].append([timestamp, e[1], e[2], e[3], e[4]])

			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			return None, None

	def request_iexc_candles(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()

		try:
			try:
				stock = Stock(ticker.id, token=os.environ["IEXC_KEY"])
				rawData = stock.get_quote().loc[ticker.id]
				if rawData is None: return None, None
			except:
				return None, None

			latestPrice = rawData["delayedPrice"] if rawData["latestPrice"] is None else rawData["latestPrice"]
			lastPrice = float(latestPrice if "isUSMarketOpen" not in rawData or rawData["isUSMarketOpen"] or "extendedPrice" not in rawData or rawData["extendedPrice"] is None else rawData["extendedPrice"])
			openPrice, highPrice, lowPrice = None, None, None
			if "open" in rawData: openPrice = rawData["open"]
			if "high" in rawData: highPrice = rawData["high"]
			if "low" in rawData: lowPrice = rawData["low"]
			if openPrice is None: openPrice = lastPrice
			if highPrice is None: highPrice = lastPrice
			if lowPrice is None: lowPrice = lastPrice

			payload = {
				"candles": [[float(rawData["latestUpdate"]) / 1000, float(openPrice), float(highPrice), float(lowPrice), lastPrice]],
				"title": ticker.name,
				"baseTicker": "shares",
				"quoteTicker": ticker.quote,
				"sourceText": "provided by IEX Cloud ● {} on {}".format(rawData["latestSource"], rawData["primaryExchange"]),
				"platform": "IEXC",
			}

			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			return None, None

if __name__ == "__main__":
	os.environ["PRODUCTION_MODE"] = os.environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in os.environ and os.environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Candle Server is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))
	candleServer = CandleProcessor()
	candleServer.run()