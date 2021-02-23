import os
import signal
import time
import uuid
import zmq
import zlib
import pickle
import traceback
import datetime
from threading import Thread

from pycoingecko import CoinGeckoAPI
from google.cloud import firestore, error_reporting

from Cache import Cache
from TickerParser import TickerParser


database = firestore.Client()


class DetailProcessor(object):
	coinGecko = CoinGeckoAPI()

	def __init__(self):
		self.isServiceAvailable = True
		signal.signal(signal.SIGINT, self.exit_gracefully)
		signal.signal(signal.SIGTERM, self.exit_gracefully)

		self.logging = error_reporting.Client()
		self.cache = Cache()

		context = zmq.Context.instance()
		self.socket = context.socket(zmq.ROUTER)
		self.socket.bind("tcp://*:6900")

		print("[Startup]: Detail Server is online")

	def exit_gracefully(self):
		print("[Startup]: Detail Server is exiting")
		self.socket.close()
		self.isServiceAvailable = False

	def run(self):
		while self.isServiceAvailable:
			try:
				response = None, None
				origin, delimeter, clientId, service, request = self.socket.recv_multipart()
				request = pickle.loads(zlib.decompress(request))
				if request.timestamp + 30 < time.time(): continue

				if service == b"detail":
					response = self.request_detail(request)

			except (KeyboardInterrupt, SystemExit): return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			finally:
				try: self.socket.send_multipart([origin, delimeter, zlib.compress(pickle.dumps(response, -1))])
				except: pass

	def request_detail(self, request):
		payload, tradeMessage, updatedTradeMessage = None, None, None

		for platform in request.platforms:
			request.set_current(platform=platform)
			hashCode = hash(request.requests[platform])
			fromCache = False

			if request.can_cache() and self.cache.has(hashCode):
				payload, updatedQuoteMessage = self.cache.get(hashCode), None
				fromCache = True
			elif platform == "CoinGecko":
				payload, updatedQuoteMessage = self.request_coingecko_details(request)

			if payload is not None:
				if request.can_cache() and not fromCache: self.cache.set(hashCode, payload)
				if request.authorId != 401328409499664394 and request.requests[platform].ticker.base is not None:
					database.document("dataserver/statistics/{}/{}".format(platform, str(uuid.uuid4()))).set({
						"timestamp": time.time(),
						"authorId": str(request.authorId),
						"ticker": {
							"base": request.requests[platform].ticker.base,
							"quote": request.requests[platform].ticker.quote,
							"id": request.requests[platform].ticker.id,
							"bias": request.parserBias
						},
						"exchange": None if request.requests[platform].exchange is None else request.requests[platform].exchange.id
					})
				return payload, updatedTradeMessage
			elif updatedTradeMessage is not None:
				tradeMessage = updatedTradeMessage

		return None, tradeMessage

	def request_coingecko_details(self, request):
		ticker = request.get_ticker()

		try:
			try:
				rawData = self.coinGecko.get_coin_by_id(id=ticker.symbol, localization="false", tickers=False, market_data=True, community_data=True, developer_data=True)
			except:
				return None, None

			payload = {
				"name": "{} ({})".format(rawData["name"], ticker.base),
				"rank": rawData["market_data"]["market_cap_rank"],
				"image": rawData["image"]["large"],
				"marketcap": "" if rawData["market_data"]["market_cap"] is None else rawData["market_data"]["market_cap"].get("usd", ""),
				"volume": None if rawData["market_data"]["total_volume"] is None else rawData["market_data"]["total_volume"].get("usd"),
				"supply": {
					"total": None if rawData["market_data"]["total_supply"] is None else rawData["market_data"]["total_supply"],
					"circulating": None if rawData["market_data"]["circulating_supply"] is None else rawData["market_data"]["circulating_supply"]
				},
				"score": {
					"developer": rawData["developer_score"],
					"community": rawData["community_score"],
					"liquidity": rawData["liquidity_score"],
					"public interest": rawData["public_interest_score"]
				},
				"price": {
					"current": rawData["market_data"]["current_price"].get("usd"),
					"ath": rawData["market_data"]["ath"].get("usd"),
					"atl": rawData["market_data"]["atl"].get("usd")
				},
				"change": {
					"past day": rawData["market_data"]["price_change_percentage_24h_in_currency"].get("usd"),
					"past month": rawData["market_data"]["price_change_percentage_30d_in_currency"].get("usd"),
					"past year": rawData["market_data"]["price_change_percentage_1y_in_currency"].get("usd")
				},
				"sourceText": "from CoinGecko",
				"platform": "CoinGecko",
			}
			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			return None, None

if __name__ == "__main__":
	os.environ["PRODUCTION_MODE"] = os.environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in os.environ and os.environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Detail Server is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))
	detailServer = DetailProcessor()
	detailServer.run()