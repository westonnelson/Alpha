import os
import sys
import time
import zmq
import zlib
import pickle
import traceback
import datetime
import pytz
from threading import Thread

from pycoingecko import CoinGeckoAPI
from google.cloud import firestore, error_reporting

from engine.cache import Cache
from TickerParser import TickerParser


database = firestore.Client()


class DetailProcessor(object):
	coinGecko = CoinGeckoAPI()

	def __init__(self):
		self.logging = error_reporting.Client()
		self.cache = Cache()

		context = zmq.Context.instance()
		self.socket = context.socket(zmq.ROUTER)
		self.socket.bind("tcp://*:6900")

		print("[Startup]: Detail Server is online")

	def run(self):
		while True:
			origin, delimeter, clientId, service, request = self.socket.recv_multipart()
			try:
				response = None, None
				request = pickle.loads(zlib.decompress(request))
				if request.timestamp + 30 < time.time(): continue

				if service == b"detail":
					response = self.request_detail(request)

			except (KeyboardInterrupt, SystemExit): return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			finally:
				self.socket.send_multipart([origin, delimeter, zlib.compress(pickle.dumps(response, -1))])

	def request_detail(self, request):
		payload, tradeMessage, updatedTradeMessage = None, None, None

		for platform in request.platforms:
			request.set_current(platform=platform)
			hashCode = hash(request.requests[platform])
			fromCache = False

			if platform == "CoinGecko":
				payload, updatedQuoteMessage = self.request_coingecko_details(request)

			if payload is not None:
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
					"current": rawData["market_data"]["current_price"]["usd"],
					"ath": rawData["market_data"]["ath"]["usd"],
					"atl": rawData["market_data"]["atl"]["usd"]
				},
				"change": {
					"past day": rawData["market_data"]["price_change_percentage_24h_in_currency"].get("usd", None),
					"past month": rawData["market_data"]["price_change_percentage_30d_in_currency"].get("usd", None),
					"past year": rawData["market_data"]["price_change_percentage_1y_in_currency"].get("usd", None)
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