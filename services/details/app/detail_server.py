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
from iexfinance.stocks import Stock
from google.cloud import firestore, error_reporting
from markdownify import markdownify as md

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
		self.cache = Cache(ttl=60)

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
			elif platform == "IEXC":
				payload, updatedQuoteMessage = self.request_iexc_details(request)

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
				companyData = self.coinGecko.get_coin_by_id(id=ticker.symbol, localization="false", tickers=False, market_data=True, community_data=True, developer_data=True)
			except:
				return None, None

			description = md(companyData["description"].get("en", "No description"))
			descriptionParagraphs = description.split("\r\n\r\n")
			textLength = [len(descriptionParagraphs[0])]
			for i in range(1, len(descriptionParagraphs)):
				nextLength = textLength[-1] + len(descriptionParagraphs[i])
				if nextLength > 1000: break
				textLength.append(nextLength)
			description = "\n".join(descriptionParagraphs[:len(textLength)]) + "\n[Read more on CoinGecko](https://www.coingecko.com/coins/{})".format(ticker.symbol)

			payload = {
				"name": "{} ({})".format(companyData["name"], ticker.base),
				"description": description,
				"url": None if companyData["links"]["homepage"][0] == "" else companyData["links"]["homepage"][0],
				"rank": companyData["market_data"]["market_cap_rank"],
				"image": companyData["image"]["large"],
				"marketcap": None if companyData["market_data"]["market_cap"] is None else companyData["market_data"]["market_cap"].get("usd"),
				"volume": None if companyData["market_data"]["total_volume"] is None else companyData["market_data"]["total_volume"].get("usd"),
				"industry": None,
				"info": None,
				"supply": {
					"total": None if companyData["market_data"]["total_supply"] is None else companyData["market_data"]["total_supply"],
					"circulating": None if companyData["market_data"]["circulating_supply"] is None else companyData["market_data"]["circulating_supply"]
				},
				"score": {
					"developer": companyData["developer_score"],
					"community": companyData["community_score"],
					"liquidity": companyData["liquidity_score"],
					"public interest": companyData["public_interest_score"]
				},
				"price": {
					"current": companyData["market_data"]["current_price"].get("usd"),
					"ath": companyData["market_data"]["ath"].get("usd"),
					"atl": companyData["market_data"]["atl"].get("usd"),
					"per": None
				},
				"change": {
					"past day": companyData["market_data"]["price_change_percentage_24h_in_currency"].get("usd"),
					"past month": companyData["market_data"]["price_change_percentage_30d_in_currency"].get("usd"),
					"past year": companyData["market_data"]["price_change_percentage_1y_in_currency"].get("usd")
				},
				"sourceText": "from CoinGecko",
				"platform": "CoinGecko",
			}
			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			return None, None
	
	def request_iexc_details(self, request):
		ticker = request.get_ticker()

		try:
			try:
				stock = Stock(ticker.id, token=os.environ["IEXC_KEY"])
				companyData = stock.get_company().loc[ticker.id]
				rawData = stock.get_quote().loc[ticker.id]
			except:
				return None, None

			try: coinThumbnail = stock.get_logo().loc[ticker.id]["url"]
			except: coinThumbnail = None

			payload = {
				"name": "{} ({})".format(companyData["companyName"], companyData["symbol"]),
				"description": companyData["description"],
				"url": companyData["website"],
				"rank": None,
				"image": coinThumbnail,
				"marketcap": rawData["marketCap"],
				"volume": None,
				"industry": companyData["industry"],
				"info": {
					"location": "{}{}, {}, {}, {}".format(companyData["address"], "" if companyData["address2"] is None else ", " + companyData["address2"], companyData["city"], companyData["state"], companyData["country"]),
					"employees": companyData["employees"]
				},
				"supply": None,
				"score": None,
				"price": {
					"current": rawData["delayedPrice"] if rawData["latestPrice"] is None else rawData["latestPrice"],
					"ath": None,
					"atl": None,
					"per": rawData["peRatio"]
				},
				"change": {
					"past day": rawData["changePercent"],
					"past month": None,
					"past year": None
				},
				"sourceText": "provided by IEX Cloud",
				"platform": "IEX Cloud",
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