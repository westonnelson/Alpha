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


database = firestore.Client()


class DetailProcessor(object):
	coinGecko = CoinGeckoAPI()

	def __init__(self):
		self.isServiceAvailable = True
		signal.signal(signal.SIGINT, self.exit_gracefully)
		signal.signal(signal.SIGTERM, self.exit_gracefully)

		self.logging = error_reporting.Client(service="details_server")
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
				if request.timestamp + 60 < time.time(): continue

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
						"exchange": None
					})
				return payload, updatedTradeMessage
			elif updatedTradeMessage is not None:
				tradeMessage = updatedTradeMessage

		return None, tradeMessage

	def request_coingecko_details(self, request):
		ticker = request.get_ticker()

		try:
			try:
				assetData = self.coinGecko.get_coin_by_id(id=ticker.symbol, localization="false", tickers=False, market_data=True, community_data=True, developer_data=True)
				historicData = self.coinGecko.get_coin_ohlc_by_id(id=ticker.symbol, vs_currency="usd", days=365)
			except:
				return None, None

			description = md(assetData["description"].get("en", "No description"))
			descriptionParagraphs = description.split("\r\n\r\n")
			textLength = [len(descriptionParagraphs[0])]
			for i in range(1, len(descriptionParagraphs)):
				nextLength = textLength[-1] + len(descriptionParagraphs[i])
				if nextLength > 500 and textLength[-1] > 300: break
				textLength.append(nextLength)
			description = "\n".join(descriptionParagraphs[:len(textLength)]) + "\n[Read more on CoinGecko](https://www.coingecko.com/coins/{})".format(ticker.symbol)

			highs = [e[2] for e in historicData]
			lows = [e[3] for e in historicData]

			payload = {
				"name": "{} ({})".format(assetData["name"], ticker.base),
				"description": description,
				"rank": assetData["market_data"]["market_cap_rank"],
				"image": assetData["image"]["large"],
				"supply": {},
				"score": {
					"developer": assetData["developer_score"],
					"community": assetData["community_score"],
					"liquidity": assetData["liquidity_score"],
					"public interest": assetData["public_interest_score"]
				},
				"price": {
					"current": assetData["market_data"]["current_price"].get("usd"),
					"ath": assetData["market_data"]["ath"].get("usd"),
					"atl": assetData["market_data"]["atl"].get("usd")
				},
				"change": {
					"past day": assetData["market_data"]["price_change_percentage_24h_in_currency"].get("usd"),
					"past month": assetData["market_data"]["price_change_percentage_30d_in_currency"].get("usd"),
					"past year": assetData["market_data"]["price_change_percentage_1y_in_currency"].get("usd")
				},
				"sourceText": "from CoinGecko",
				"platform": "CoinGecko",
			}

			if assetData["links"]["homepage"][0] != "": payload["url"] = (assetData["links"]["homepage"][0] if assetData["links"]["homepage"][0].startswith("http") else "https://" + assetData["links"]["homepage"][0]).replace(" ", "")
			if assetData["market_data"]["total_volume"] is not None: payload["volume"] = assetData["market_data"]["total_volume"].get("usd")
			if assetData["market_data"]["market_cap"] is not None: payload["marketcap"] = assetData["market_data"]["market_cap"].get("usd")
			if assetData["market_data"]["total_supply"] is not None: payload["supply"]["total"] = assetData["market_data"]["total_supply"]
			if assetData["market_data"]["circulating_supply"] is not None: payload["supply"]["circulating"] = assetData["market_data"]["circulating_supply"]
			if len(highs) != 0: payload["price"]["1y high"] = max(highs)
			if len(lows) != 0: payload["price"]["1y low"] = min(lows)

			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None
	
	def request_iexc_details(self, request):
		ticker = request.get_ticker()

		try:
			try:
				stock = Stock(ticker.id, token=os.environ["IEXC_KEY"])
				companyData = stock.get_company().loc[ticker.id]
				rawData = stock.get_quote().loc[ticker.id]
				historicData = stock.get_historical_prices(range="1y")
			except:
				return None, None

			try: coinThumbnail = stock.get_logo().loc[ticker.id]["url"]
			except: coinThumbnail = None

			payload = {
				"name": companyData["symbol"] if companyData["companyName"] is None else "{} ({})".format(companyData["companyName"], companyData["symbol"]),
				"image": coinThumbnail,
				"industry": companyData["industry"],
				"info": {
					"employees": companyData["employees"]
				},
				"price": {
					"current": rawData["delayedPrice"] if rawData["latestPrice"] is None else rawData["latestPrice"],
					"1y high": historicData.high.max(),
					"1y low": historicData.low.min(),
					"per": rawData["peRatio"]
				},
				"change": {
					"past day": ((historicData.close[-1] / historicData.close[-2] - 1) * 100 if historicData.shape[0] >= 2 and historicData.close[-2] != 0 else None) if rawData["changePercent"] is None else rawData["changePercent"] * 100,
					"past month": (historicData.close[-1] / historicData.close[-21] - 1) * 100 if historicData.shape[0] >= 21 and historicData.close[-21] != 0 else None,
					"past year": (historicData.close[-1] / historicData.close[0] - 1) * 100 if historicData.shape[0] >= 200 and historicData.close[0] != 0 else None
				},
				"sourceText": "provided by IEX Cloud",
				"platform": "IEX Cloud",
			}

			if companyData["description"] is not None: payload["description"] = companyData["description"]
			if "marketCap" in rawData: payload["marketcap"] = rawData["marketCap"]
			if companyData["website"] is not None: payload["url"] = companyData["website"]
			if companyData["country"] is not None: payload["info"]["location"] = "{}{}, {}, {}, {}".format(companyData["address"], "" if companyData["address2"] is None else ", " + companyData["address2"], companyData["city"], companyData["state"], companyData["country"])

			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

if __name__ == "__main__":
	os.environ["PRODUCTION_MODE"] = os.environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in os.environ and os.environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Detail Server is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))
	detailServer = DetailProcessor()
	detailServer.run()