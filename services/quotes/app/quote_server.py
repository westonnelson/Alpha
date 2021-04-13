import os
import signal
import time
import uuid
import zmq
import zlib
import pickle
import requests
from io import BytesIO
import base64
import datetime
import pytz
import traceback

import ccxt
from pycoingecko import CoinGeckoAPI
from iexfinance.stocks import Stock
import quandl

from PIL import Image
from matplotlib import pyplot as plt
from matplotlib import ticker as tkr
import matplotlib.transforms as mtransforms
from google.cloud import firestore, storage, error_reporting

from TickerParser import TickerParser, Ticker, Exchange, supported

from assets import static_storage
from helpers.utils import Utils
from helpers import constants


database = firestore.Client()
storage_client = storage.Client()
bucket = storage_client.get_bucket("nlc-bot-36685.appspot.com")

plt.switch_backend("Agg")
plt.ion()
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams['figure.figsize'] = (8, 6)
plt.rcParams["figure.dpi"] = 200.0
plt.rcParams['savefig.facecolor'] = "#131722"


class QuoteProcessor(object):
	imageOverlays = {
		"Alpha depth": Image.open("app/assets/overlays/quotes/depth.png").convert("RGBA")
	}

	stableCoinTickers = ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]
	lastBitcoinQuote = {}

	def __init__(self):
		self.isServiceAvailable = True
		signal.signal(signal.SIGINT, self.exit_gracefully)
		signal.signal(signal.SIGTERM, self.exit_gracefully)

		self.logging = error_reporting.Client(service="quote_server")

		self.coinGecko = CoinGeckoAPI()
		self.lastBitcoinQuote = {
			"quotePrice": [0],
			"quoteVolume": None,
			"ticker": Ticker("BTCUSD", "BTCUSD", "BTC", "USD", "BTC/USD", hasParts=False),
			"exchange": None,
			"timestamp": time.time()
		}

		try:
			rawData = self.coinGecko.get_coin_by_id(id="bitcoin", localization="false", tickers=False, market_data=True, community_data=False, developer_data=False)
			self.lastBitcoinQuote["quotePrice"] = [rawData["market_data"]["current_price"]["usd"]]
			self.lastBitcoinQuote["quoteVolume"] = rawData["market_data"]["total_volume"]["usd"]
		except: pass

		context = zmq.Context.instance()
		self.socket = context.socket(zmq.ROUTER)
		self.socket.bind("tcp://*:6900")

		print("[Startup]: Quote Server is online")

	def exit_gracefully(self):
		print("[Startup]: Quote Server is exiting")
		self.socket.close()
		self.isServiceAvailable = False

	def run(self):
		while self.isServiceAvailable:
			try:
				response = None, None
				origin, delimeter, clientId, service, request = self.socket.recv_multipart()
				request = pickle.loads(zlib.decompress(request))
				if request.timestamp + 60 < time.time(): continue

				if service == b"quote":
					response = self.request_quote(request)
				elif service == b"depth":
					response = self.request_depth(request)

			except (KeyboardInterrupt, SystemExit): return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			finally:
				try: self.socket.send_multipart([origin, delimeter, zlib.compress(pickle.dumps(response, -1))])
				except: pass

	def request_depth(self, request):
		payload, quoteMessage, updatedQuoteMessage = None, None, None

		for platform in request.platforms:
			request.set_current(platform=platform)

			if platform == "CCXT":
				payload, updatedQuoteMessage = self.request_ccxt_depth(request)
			elif platform == "IEXC":
				payload, updatedQuoteMessage = self.request_iexc_depth(request)

			if payload is not None:
				if request.authorId != 401328409499664394 and request.requests[platform].ticker.base is not None and request.authorId not in constants.satellites:
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
				return payload, updatedQuoteMessage
			elif updatedQuoteMessage is not None:
				quoteMessage = updatedQuoteMessage

		return None, quoteMessage

	def request_quote(self, request):
		payload, quoteMessage, updatedQuoteMessage = None, None, None

		for platform in request.platforms:
			request.set_current(platform=platform)

			if platform == "Alternative.me":
				payload, updatedQuoteMessage = self.request_fear_greed_index(request)
			elif platform == "LLD":
				payload, updatedQuoteMessage = self.request_lld_quote(request)
			elif platform == "CoinGecko":
				payload, updatedQuoteMessage = self.request_coingecko_quote(request)
			elif platform == "CCXT":
				payload, updatedQuoteMessage = self.request_ccxt_quote(request)
			elif platform == "IEXC":
				payload, updatedQuoteMessage = self.request_iexc_quote(request)
			elif platform == "Quandl":
				pass

			if payload is not None:
				if request.authorId != 401328409499664394 and request.requests[platform].ticker.base is not None and request.authorId not in constants.satellites:
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
				return payload, updatedQuoteMessage
			elif updatedQuoteMessage is not None:
				quoteMessage = updatedQuoteMessage

		return None, quoteMessage

	def request_coingecko_quote(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()

		try:
			try:
				rawData = self.coinGecko.get_coin_by_id(id=ticker.symbol, localization="false", tickers=False, market_data=True, community_data=False, developer_data=False)
			except:
				return None, None

			if ticker.quote.lower() not in rawData["market_data"]["current_price"] or ticker.quote.lower() not in rawData["market_data"]["total_volume"]: return None, "Requested price for `{}` is not available.".format(ticker.name)

			price = rawData["market_data"]["current_price"][ticker.quote.lower()]
			if ticker.isReversed: price = 1 / price
			volume = rawData["market_data"]["total_volume"][ticker.quote.lower()]
			priceChange = rawData["market_data"]["price_change_percentage_24h_in_currency"][ticker.quote.lower()] if ticker.quote.lower() in rawData["market_data"]["price_change_percentage_24h_in_currency"] else 0
			if ticker.isReversed: priceChange = (1 / (priceChange / 100 + 1) - 1) * 100

			payload = {
				"quotePrice": str(price),
				"quoteVolume": volume,
				"title": ticker.name,
				"baseTicker": ticker.base,
				"quoteTicker": ticker.quote,
				"change": priceChange,
				"thumbnailUrl": TickerParser.get_coingecko_image(ticker.base),
				"messageColor": "amber" if priceChange == 0 else ("green" if priceChange > 0 else "red"),
				"sourceText": "from CoinGecko",
				"platform": "CoinGecko",
				"raw": {
					"quotePrice": [price],
					"quoteVolume": volume,
					"ticker": ticker,
					"exchange": exchange,
					"timestamp": time.time()
				}
			}
			if ticker.quote != "USD":
				payload["quoteConvertedPrice"] = "≈ ${:,.6f}".format(rawData["market_data"]["current_price"]["usd"])
				payload["quoteConvertedVolume"] = "≈ ${:,.4f}".format(rawData["market_data"]["total_volume"]["usd"])

			if ticker == Ticker("BTCUSD", "BTCUSD", "BTC", "USD", "BTC/USD", hasParts=False): self.lastBitcoinQuote = payload["raw"]
			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

	def request_ccxt_quote(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()

		try:
			if exchange is None: return None, None
			exchange = Exchange(exchange.id, "crypto")

			tf, limitTimestamp, candleOffset = Utils.get_highest_supported_timeframe(exchange.properties, datetime.datetime.now().astimezone(pytz.utc))
			try:
				rawData = exchange.properties.fetch_ohlcv(ticker.symbol, timeframe=tf.lower(), since=limitTimestamp, limit=300)
				if len(rawData) == 0 or rawData[-1][4] is None or rawData[0][1] is None: return None, None
			except:
				return None, None

			price = [rawData[-1][4], rawData[0][1]] if len(rawData) < candleOffset else [rawData[-1][4], rawData[-candleOffset][1]]
			if ticker.isReversed: price = [1 / price[0], 1 / price[1]]
			volume = None if price[0] is None else sum([candle[5] for candle in rawData if int(candle[0] / 1000) >= int(exchange.properties.milliseconds() / 1000) - 86400]) / (price[0] if exchange.id in ["bitmex", "binancefutures"] else 1)
			priceChange = 0 if tf == "1m" or price[1] == 0 else (price[0] / price[1]) * 100 - 100

			payload = {
				"quotePrice": "{:,.8f}".format(price[0]) if ticker.isReversed else TickerParser.get_formatted_price(exchange.id, ticker.symbol, price[0]),
				"quoteVolume": volume,
				"title": ticker.name,
				"baseTicker": "USD" if ticker.base in QuoteProcessor.stableCoinTickers else ticker.base,
				"quoteTicker": "USD" if ticker.quote in QuoteProcessor.stableCoinTickers else ticker.quote,
				"change": priceChange,
				"thumbnailUrl": TickerParser.get_coingecko_image(ticker.base),
				"messageColor": "amber" if priceChange == 0 else ("green" if priceChange > 0 else "red"),
				"sourceText": "on {}".format(exchange.name),
				"platform": "CCXT",
				"raw": {
					"quotePrice": [price[0]] if tf == "1m" else price[:1],
					"quoteVolume": volume,
					"ticker": ticker,
					"exchange": exchange,
					"timestamp": time.time()
				}
			}
			if ticker.quote == "BTC":
				payload["quoteConvertedPrice"] = "≈ ${:,.6f}".format(price[0] * self.lastBitcoinQuote["quotePrice"][0])
				payload["quoteConvertedVolume"] = "≈ ${:,.4f}".format(volume * self.lastBitcoinQuote["quotePrice"][0])

			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

	def request_lld_quote(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()
		filters = request.get_filters()
		action = request.find_parameter_in_list("lld", filters)

		try:
			if exchange is not None: exchange = Exchange(exchange.id, "crypto")

			if action == "funding":
				if exchange.id in ["bitmex"]:
					try: rawData = exchange.properties.public_get_instrument({"symbol": ticker.id})[0]
					except: return None, "Requested funding data for `{}` is not available.".format(ticker.name)

					if rawData["fundingTimestamp"] is not None:
						fundingDate = datetime.datetime.strptime(rawData["fundingTimestamp"], "%Y-%m-%dT%H:%M:00.000Z").replace(tzinfo=pytz.utc)
					else:
						fundingDate = datetime.datetime.now().replace(tzinfo=pytz.utc)
					indicativeFundingTimestamp = datetime.datetime.timestamp(fundingDate) + 28800
					indicativeFundingDate = datetime.datetime.utcfromtimestamp(indicativeFundingTimestamp).replace(tzinfo=pytz.utc)
					deltaFunding = fundingDate - datetime.datetime.now().astimezone(pytz.utc)
					deltaIndicative = indicativeFundingDate - datetime.datetime.now().astimezone(pytz.utc)

					hours1, seconds1 = divmod(deltaFunding.days * 86400 + deltaFunding.seconds, 3600)
					minutes1 = int(seconds1 / 60)
					hoursFunding = "{:d} {} ".format(hours1, "hours" if hours1 > 1 else "hour") if hours1 > 0 else ""
					minutesFunding = "{:d} {}".format(minutes1 if hours1 > 0 or minutes1 > 0 else seconds1, "{}".format("minute" if minutes1 == 1 else "minutes") if hours1 > 0 or minutes1 > 0 else ("second" if seconds1 == 1 else "seconds"))
					deltaFundingText = "{}{}".format(hoursFunding, minutesFunding)

					hours2, seconds2 = divmod(deltaIndicative.days * 86400 + deltaIndicative.seconds, 3600)
					minutes2 = int(seconds2 / 60)
					hoursIndicative = "{:d} {} ".format(hours2, "hours" if hours2 > 1 else "hour") if hours2 > 0 else ""
					minutesIndicative = "{:d} {}".format(minutes2 if hours2 > 0 or minutes2 > 0 else seconds2, "{}".format("minute" if minutes2 == 1 else "minutes") if hours2 > 0 or minutes2 > 0 else ("second" if seconds2 == 1 else "seconds"))
					deltaIndicativeText = "{}{}".format(hoursIndicative, minutesIndicative)

					fundingRate = float(rawData["fundingRate"]) * 100
					predictedFundingRate = float(rawData["indicativeFundingRate"]) * 100
					averageFundingRate = (fundingRate + predictedFundingRate) / 2

					payload = {
						"quotePrice": "Funding Rate: {:+.4f} % *(in {})*\nPredicted Rate: {:+.4f} % *(in {})*".format(fundingRate, deltaFundingText, predictedFundingRate, deltaIndicativeText),
						"title": ticker.name,
						"baseTicker": ticker.base,
						"quoteTicker": ticker.quote,
						"thumbnailUrl": TickerParser.get_coingecko_image(ticker.base),
						"messageColor": "yellow" if averageFundingRate == 0.01 else ("light green" if averageFundingRate < 0.01 else "deep orange"),
						"sourceText": "Contract details on {}".format(exchange.name),
						"platform": "LLD",
						"raw": {
							"quotePrice": [fundingRate, predictedFundingRate],
							"ticker": ticker,
							"exchange": exchange,
							"timestamp": time.time()
						}
					}
					return payload, None
				return None, "Funding data is only available on BitMEX."
			elif action == "oi":
				if exchange.id in ["bitmex"]:
					try: rawData = exchange.properties.public_get_instrument({"symbol": ticker.id})[0]
					except: return None, "Requested open interest data for `{}` is not available.".format(ticker.name)

					payload = {
						"quotePrice": "Open interest: {:,.0f} {}\nOpen value: {:,.4f} XBT".format(float(rawData["openInterest"]), "USD" if ticker.id == "XBTUSD" else "contracts", float(rawData["openValue"]) / 100000000),
						"title": ticker.name,
						"baseTicker": ticker.base,
						"quoteTicker": ticker.quote,
						"thumbnailUrl": TickerParser.get_coingecko_image(ticker.base),
						"messageColor": "deep purple",
						"sourceText": "Contract details on {}".format(exchange.name),
						"platform": "LLD",
						"raw": {
							"quotePrice": [float(rawData["openInterest"]), float(rawData["openValue"]) / 100000000],
							"ticker": ticker,
							"exchange": exchange,
							"timestamp": time.time()
						}
					}
					return payload, None
				return None, "Open interest and open value data is only available on BitMEX."
			elif action == "ls":
				if exchange.id in ["bitfinex2"]:
					try:
						longs = exchange.properties.publicGetStats1KeySizeSymbolLongLast({"key": "pos.size", "size": "1m", "symbol": "t{}".format(ticker.id), "side": "long", "section": "last"})
						shorts = exchange.properties.publicGetStats1KeySizeSymbolShortLast({"key": "pos.size", "size": "1m", "symbol": "t{}".format(ticker.id), "side": "long", "section": "last"})
						ratio = longs[1] / (longs[1] + shorts[1]) * 100
					except:
						return None, None

					payload = {
						"quotePrice": "{:.1f} % longs / {:.1f} % shorts".format(ratio, 100 - ratio),
						"title": "{} longs/shorts ratio".format(ticker.name),
						"baseTicker": ticker.base,
						"quoteTicker": ticker.quote,
						"thumbnailUrl": TickerParser.get_coingecko_image(ticker.base),
						"messageColor": "deep purple",
						"sourceText": "Data on {}".format(exchange.name),
						"platform": "LLD",
						"raw": {
							"quotePrice": [longs[1], shorts[1]],
							"ticker": ticker,
							"exchange": exchange,
							"timestamp": time.time()
						}
					}
					return payload, None
				return None, "Longs and shorts data is only available on Bitfinex."
			elif action == "sl":
				if exchange.id in ["bitfinex2"]:
					try:
						longs = exchange.properties.publicGetStats1KeySizeSymbolLongLast({"key": "pos.size", "size": "1m", "symbol": "t{}".format(ticker.id), "side": "short", "section": "last"})
						shorts = exchange.properties.publicGetStats1KeySizeSymbolShortLast({"key": "pos.size", "size": "1m", "symbol": "t{}".format(ticker.id), "side": "short", "section": "last"})
						ratio = shorts[1] / (longs[1] + shorts[1]) * 100
					except:
						return None, None

					payload = {
						"quotePrice": "{:.1f} % shorts / {:.1f} % longs".format(ratio, 100 - ratio),
						"title": "{} shorts/longs ratio".format(ticker.name),
						"baseTicker": ticker.base,
						"quoteTicker": ticker.quote,
						"thumbnailUrl": TickerParser.get_coingecko_image(ticker.base),
						"messageColor": "deep purple",
						"sourceText": "Data on {}".format(exchange.name),
						"platform": "LLD",
						"raw": {
							"quotePrice": [longs[1], shorts[1]],
							"ticker": ticker,
							"exchange": exchange,
							"timestamp": time.time()
						}
					}
					return payload, None
				return None, "Longs and shorts data is only available on Bitfinex."
			elif action == "dom":
				try: rawData = self.coinGecko.get_global()
				except: return None, "Requested dominance data for `{}` is not available.".format(ticker.name)
				if ticker.base.lower() not in rawData["market_cap_percentage"]: return None, "Dominance for {} does not exist.".format(ticker.base)
				coinDominance = rawData["market_cap_percentage"][ticker.base.lower()]

				payload = {
					"quotePrice": "{} dominance: {:,.2f} %".format(ticker.base, coinDominance),
					"title": "Market Dominance",
					"baseTicker": ticker.base,
					"quoteTicker": ticker.quote,
					"thumbnailUrl": TickerParser.get_coingecko_image(ticker.base),
					"messageColor": "deep purple",
					"sourceText": "Market information from CoinGecko",
					"platform": "LLD",
					"raw": {
						"quotePrice": coinDominance,
						"ticker": ticker,
						"timestamp": time.time()
					}
				}
				return payload, None
			else:
				return None, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

	def request_iexc_quote(self, request):
		payload, quoteMessage, updatedQuoteMessage = None, None, None

		for platform in ["Stocks", "Forex"]:
			if platform == "Stocks":
				payload, updatedQuoteMessage = self.request_iexc_stocks(request)
			elif platform == "Forex":
				payload, updatedQuoteMessage = self.request_iexc_forex(request)

			if payload is not None:
				return payload, updatedQuoteMessage
			elif updatedQuoteMessage is not None:
				quoteMessage = updatedQuoteMessage

		return None, quoteMessage

	def request_iexc_stocks(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()

		try:
			try:
				stock = Stock(ticker.id, token=os.environ["IEXC_KEY"])
				rawData = stock.get_quote().loc[ticker.id]
				if ticker.quote is None and exchange is not None: return None, "Price for `{}` is only available on `{}`.".format(ticker.id, rawData["primaryExchange"])
				if rawData is None or (rawData["latestPrice"] is None and rawData["delayedPrice"] is None): return None, None
			except:
				return None, None

			try: coinThumbnail = stock.get_logo().loc[ticker.id]["url"]
			except: coinThumbnail = static_storage.icon

			latestPrice = rawData["delayedPrice"] if rawData["latestPrice"] is None else rawData["latestPrice"]
			price = float(latestPrice if "isUSMarketOpen" not in rawData or rawData["isUSMarketOpen"] or "extendedPrice" not in rawData or rawData["extendedPrice"] is None else rawData["extendedPrice"])
			if ticker.isReversed: price = 1 / price
			volume = float(rawData["latestVolume"])
			priceChange = (1 / rawData["change"] if ticker.isReversed and rawData["change"] != 0 else rawData["change"]) / price * 100 if "change" in rawData and rawData["change"] is not None else 0

			payload = {
				"quotePrice": "{:,.5f}".format(price) if ticker.isReversed else "{}".format(price),
				"quoteVolume": volume,
				"title": ticker.name,
				"baseTicker": "contracts",
				"quoteTicker": "USD" if ticker.quote is None else ticker.quote,
				"change": priceChange,
				"thumbnailUrl": coinThumbnail,
				"messageColor": "amber" if priceChange == 0 else ("green" if priceChange > 0 else "red"),
				"sourceText": "provided by IEX Cloud",
				"platform": "IEXC",
				"raw": {
					"quotePrice": [price],
					"quoteVolume": volume,
					"ticker": ticker,
					"exchange": exchange,
					"timestamp": time.time()
				}
			}
			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

	def request_iexc_forex(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()

		try:
			try:
				if exchange is not None: return None, None
				rawData = requests.get("https://cloud.iexapis.com/stable/fx/latest?symbols={}&token={}".format(ticker.id, os.environ["IEXC_KEY"])).json()
				if rawData is None or type(rawData) is not list or len(rawData) == 0: return None, None
			except:
				return None, None

			price = rawData[0]["rate"]
			if ticker.isReversed: price = 1 / price

			payload = {
				"quotePrice": "{:,.5f}".format(price),
				"title": ticker.name,
				"baseTicker": ticker.base,
				"quoteTicker": ticker.quote,
				"thumbnailUrl": static_storage.icon,
				"messageColor": "deep purple",
				"sourceText": "provided by IEX Cloud",
				"platform": "IEXC",
				"raw": {
					"quotePrice": [price],
					"ticker": ticker,
					"exchange": exchange,
					"timestamp": time.time()
				}
			}
			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

	def request_fear_greed_index(self, request):
		try:
			requestUrl, _ = request.build_url()
			r = requests.get(requestUrl).json()
			greedIndex = int(r["data"][0]["value"])

			payload = {
				"quotePrice": greedIndex,
				"quoteConvertedPrice": "≈ {}".format(r["data"][0]["value_classification"].lower()),
				"title": "Fear & Greed Index",
				"change": greedIndex - int(r["data"][1]["value"]),
				"thumbnailUrl": static_storage.icon,
				"messageColor": "deep purple",
				"sourceText": "Data provided by Alternative.me",
				"platform": "Alternative.me",
				"raw": {
					"quotePrice": [greedIndex],
					"ticker": request.get_ticker(),
					"timestamp": time.time()
				}
			}
			return payload, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

	def request_ccxt_depth(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()

		imageStyle = request.get_image_style()
		forceMode = "force" in imageStyle and request.authorId == 361916376069439490
		uploadMode = "upload" in imageStyle and request.authorId == 361916376069439490

		try:
			if exchange is None: return None, None
			exchange = Exchange(exchange.id, "crypto")

			try:
				depthData = exchange.properties.fetch_order_book(ticker.symbol)
				bestBid = depthData["bids"][0]
				bestAsk = depthData["asks"][0]
				lastPrice = (bestBid[0] + bestAsk[0]) / 2
			except:
				return None, None

			imageData = self.generate_depth_image(depthData, bestBid, bestAsk, lastPrice)
			if uploadMode:
				bucket.blob("uploads/{}.png".format(int(time.time() * 1000))).upload_from_string(base64.decodebytes(imageData))

			return imageData, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

	def request_iexc_depth(self, request):
		ticker = request.get_ticker()
		exchange = request.get_exchange()

		imageStyle = request.get_image_style()
		forceMode = "force" in imageStyle and request.authorId == 361916376069439490
		uploadMode = "upload" in imageStyle and request.authorId == 361916376069439490

		try:
			try:
				stock = Stock(ticker.id, token=os.environ["IEXC_KEY"])
				depthData = stock.get_book()[ticker.id]
				rawData = stock.get_quote().loc[ticker.id]
				if ticker.quote is None and exchange is not None: return None, "Orderbook visualization for `{}` is only available on `{}`.".format(ticker.id, rawData["primaryExchange"])
				depthData = {"bids": [[e.get("price"), e.get("size")] for e in depthData["bids"]], "asks": [[e.get("price"), e.get("size")] for e in depthData["asks"]]}
				bestBid = depthData["bids"][0]
				bestAsk = depthData["asks"][0]
				lastPrice = (bestBid[0] + bestAsk[0]) / 2
			except:
				return None, None

			imageData = self.generate_depth_image(depthData, bestBid, bestAsk, lastPrice)
			if uploadMode:
				bucket.blob("uploads/{}.png".format(int(time.time() * 1000))).upload_from_string(base64.decodebytes(imageData))

			return imageData, None
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=ticker.id)
			return None, None

	def generate_depth_image(self, depthData, bestBid, bestAsk, lastPrice):
		bidTotal = 0
		xBids = [bestBid[0]]
		yBids = [0]
		for bid in depthData['bids']:
			if len(xBids) < 10 or bid[0] > lastPrice * 0.9:
				bidTotal += bid[1]
				xBids.append(bid[0])
				yBids.append(bidTotal)

		askTotal = 0
		xAsks = [bestAsk[0]]
		yAsks = [0]
		for ask in depthData['asks']:
			if len(xAsks) < 10 or ask[0] < lastPrice * 1.1:
				askTotal += ask[1]
				xAsks.append(ask[0])
				yAsks.append(askTotal)

		fig = plt.figure(facecolor="#131722")
		ax = fig.add_subplot(1, 1, 1)
		ax.tick_params(color="#787878", labelcolor="#D9D9D9")
		ax.step(xBids, yBids, where="post", color="#27A59A")
		ax.step(xAsks, yAsks, where="post", color="#EF534F")
		ax.fill_between(xBids, yBids, 0, facecolor="#27A59A", interpolate=True, step="post", alpha=0.33, zorder=2)
		ax.fill_between(xAsks, yAsks, 0, facecolor="#EF534F", interpolate=True, step="post", alpha=0.33, zorder=2)
		plt.axvline(x=lastPrice, color="#758696", linestyle="--")

		ax.set_facecolor("#131722")
		for spine in ax.spines.values():
			spine.set_edgecolor("#787878")
		ax.autoscale(enable=True, axis="both", tight=True)

		def on_draw(event):
			bboxes = []
			for label in ax.get_yticklabels():
				bbox = label.get_window_extent()
				bboxi = bbox.transformed(fig.transFigure.inverted())
				bboxes.append(bboxi)

			bbox = mtransforms.Bbox.union(bboxes)
			if fig.subplotpars.left < bbox.width:
				fig.subplots_adjust(left=1.1 * bbox.width)
				fig.canvas.draw()
			return False

		ax.yaxis.set_major_formatter(tkr.FuncFormatter(lambda x, p: format(int(x), ',')))
		plt.setp(ax.get_xticklabels(), rotation=45, horizontalalignment='right')
		lastPriceLabel = bestAsk[0] if bestAsk[1] >= bestBid[1] else bestBid[0]
		xLabels = list(plt.xticks()[0][1:])
		yLabels = list(plt.yticks()[0][1:])
		for label in xLabels:
			plt.axvline(x=label, color="#363C4F", linewidth=1, zorder=1)
		for label in yLabels:
			plt.axhline(y=label, color="#363C4F", linewidth=1, zorder=1)
		diffLabels = 1 - xLabels[0] / xLabels[1]
		bottomBound, topBound = lastPriceLabel * (1 - diffLabels * (1/4)), lastPriceLabel * (1 + diffLabels * (1/4))
		xLabels = [l for l in xLabels if not (bottomBound <= l <= topBound)]

		plt.xticks(xLabels + [lastPriceLabel])
		plt.yticks(yLabels)
		ax.set_xlim([xBids[-1], xAsks[-1]])
		ax.set_ylim([0, max(bidTotal, askTotal)])

		fig.canvas.mpl_connect("draw_event", on_draw)
		plt.tight_layout()

		rawImageData = BytesIO()
		plt.savefig(rawImageData, format="png", edgecolor="none")
		rawImageData.seek(0)

		imageBuffer = BytesIO()
		chartImage = Image.new("RGBA", (1600, 1200))
		chartImage.paste(Image.open(rawImageData))
		chartImage = Image.alpha_composite(chartImage, self.imageOverlays["Alpha depth"])
		chartImage.save(imageBuffer, format="png")
		imageData = base64.b64encode(imageBuffer.getvalue())
		imageBuffer.close()

		return imageData


if __name__ == "__main__":
	os.environ["PRODUCTION_MODE"] = os.environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in os.environ and os.environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Quote Server is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))
	quoteServer = QuoteProcessor()
	quoteServer.run()