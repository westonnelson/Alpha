import os
import sys
import time
import datetime
import pytz
import zmq
import zlib
import pickle
import traceback
from threading import Thread
import requests

import ccxt
from pycoingecko import CoinGeckoAPI
from iexfinance.refdata import get_symbols
from google.cloud import error_reporting

from TickerParser import Ticker, Exchange, supported

from assets import static_storage
from helpers.utils import Utils


class TickerParserServer(object):
	coinGecko = CoinGeckoAPI()

	exchanges = {}
	ccxtIndex = {}
	coinGeckoIndex = {}
	iexcStocksIndex = {}
	iexcForexIndex = {}
	iexcOtcIndex = {}

	exchangeRates = {}
	coingeckoVsCurrencies = []
	coingeckoFiatCurrencies = []

	def __init__(self):
		self.logging = error_reporting.Client()

		TickerParserServer.refresh_parser_index()
		Thread(target=self.job_queue, daemon=True).start()

		context = zmq.Context.instance()
		self.socket = context.socket(zmq.ROUTER)
		self.socket.bind("tcp://*:6900")

		print("[Startup]: Ticker Parser is online")

	def run(self):
		while True:
			origin, delimeter, service, request = self.socket.recv_multipart()
			try:
				response = None, None
				request = pickle.loads(zlib.decompress(request))

				if service == b"find_exchange":
					(raw, platform) = request
					response = TickerParserServer.find_exchange(raw, platform)
				elif service == b"process_known_tickers":
					(ticker, exchange, platform, defaults, bias) = request
					response = TickerParserServer.process_known_tickers(ticker, exchange, platform, defaults, bias)
				elif service == b"find_ccxt_crypto_market":
					(ticker, exchange, platform, defaults) = request
					response = TickerParserServer.find_ccxt_crypto_market(ticker, exchange, platform, defaults)
				elif service == b"find_coingecko_crypto_market":
					(ticker) = request
					response = TickerParserServer.find_coingecko_crypto_market(ticker)
				elif service == b"find_iexc_market":
					(ticker) = request
					response = TickerParserServer.find_iexc_market(ticker)
				elif service == b"find_quandl_market":
					(ticker) = request
					response = TickerParserServer.find_quandl_market(ticker)
				elif service == b"get_coingecko_image":
					(base) = request
					response = TickerParserServer.get_coingecko_image(base)
				elif service == b"check_if_fiat":
					(tickerId) = request
					response = TickerParserServer.check_if_fiat(tickerId)
				elif service == b"get_listings":
					(ticker) = request
					response = TickerParserServer.get_listings(ticker)

			except (KeyboardInterrupt, SystemExit): return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			finally:
				self.socket.send_multipart([origin, delimeter, zlib.compress(pickle.dumps(response, -1))])

	def job_queue(self):
		while True:
			try:
				time.sleep(Utils.seconds_until_cycle())
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "1D" in timeframes:
					TickerParserServer.refresh_parser_index()

			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	@staticmethod
	def refresh_parser_index():
		TickerParserServer.refresh_coingecko_index()
		processes = [
			Thread(target=TickerParserServer.refresh_coingecko_exchange_rates),
			Thread(target=TickerParserServer.refresh_ccxt_index),
			Thread(target=TickerParserServer.refresh_iexc_index)
		]
		for p in processes: p.start()
		for p in processes: p.join()

	@staticmethod
	def refresh_ccxt_index():
		difference = set(ccxt.exchanges).symmetric_difference(supported.ccxtExchanges)
		newExchanges = []
		newSupportedExchanges = []
		unsupportedCryptoExchanges = []
		for e in difference:
			try:
				ex = getattr(ccxt, e)()
			except:
				unsupportedCryptoExchanges.append(e)
				continue
			if e not in supported.ccxtExchanges:
				if ex.has['fetchOHLCV'] != False and ex.has['fetchOrderBook'] != False and ex.timeframes is not None and len(ex.timeframes) != 0: newSupportedExchanges.append(e)
				else: newExchanges.append(e)
		if len(newSupportedExchanges) != 0: print("New supported CCXT exchanges: {}".format(newSupportedExchanges))
		if len(newExchanges) != 0: print("New partially unsupported CCXT exchanges: {}".format(newExchanges))
		if len(unsupportedCryptoExchanges) != 0: print("New deprecated CCXT exchanges: {}".format(unsupportedCryptoExchanges))

		completedTasks = set()
		sortedIndexReference = {}

		for platform in supported.cryptoExchanges:
			if platform not in sortedIndexReference: sortedIndexReference[platform] = {}
			for exchange in supported.cryptoExchanges[platform]:
				if exchange not in completedTasks:
					if exchange not in TickerParserServer.exchanges: TickerParserServer.exchanges[exchange] = Exchange(exchange)
					try: TickerParserServer.exchanges[exchange].properties.load_markets()
					except: continue
					completedTasks.add(exchange)

				for symbol in TickerParserServer.exchanges[exchange].properties.symbols:
					if '.' not in symbol and ("active" not in TickerParserServer.exchanges[exchange].properties.markets[symbol] or TickerParserServer.exchanges[exchange].properties.markets[symbol]["active"] is None or TickerParserServer.exchanges[exchange].properties.markets[symbol]["active"]):
						base = TickerParserServer.exchanges[exchange].properties.markets[symbol]["base"]
						quote = TickerParserServer.exchanges[exchange].properties.markets[symbol]["quote"]
						marketPair = symbol.split("/")

						isIdentifiable = quote in TickerParserServer.coinGeckoIndex and TickerParserServer.coinGeckoIndex[quote]["market_cap_rank"] is not None

						if base != marketPair[0] or quote != marketPair[-1]:
							if marketPair[0] != marketPair[-1]: base, quote = marketPair[0], marketPair[-1]
							else: continue
						if base not in sortedIndexReference[platform]:
							sortedIndexReference[platform][base] = {}
						if quote not in sortedIndexReference[platform][base]:
							if isIdentifiable:
								sortedIndexReference[platform][base][quote] = TickerParserServer.coinGeckoIndex[quote]["market_cap_rank"]
							else:
								sortedIndexReference[platform][base][quote] = sys.maxsize

		for platform in sortedIndexReference:
			TickerParserServer.ccxtIndex[platform] = {}
			for base in sortedIndexReference[platform]:
				if base not in TickerParserServer.ccxtIndex[platform]: TickerParserServer.ccxtIndex[platform][base] = []
				TickerParserServer.ccxtIndex[platform][base] = sorted(sortedIndexReference[platform][base].keys(), key=lambda quote: sortedIndexReference[platform][base][quote])
				try: TickerParserServer.ccxtIndex[platform][base].insert(1 if TickerParserServer.ccxtIndex[platform][base][0] == "BTC" and base not in ["ETH", "XRP", "BCH", "LTC"] else 0, TickerParserServer.ccxtIndex[platform][base].pop(TickerParserServer.ccxtIndex[platform][base].index("USDT")))
				except: pass
				try: TickerParserServer.ccxtIndex[platform][base].insert(1 if TickerParserServer.ccxtIndex[platform][base][0] == "BTC" and base not in ["ETH", "XRP", "BCH", "LTC"] else 0, TickerParserServer.ccxtIndex[platform][base].pop(TickerParserServer.ccxtIndex[platform][base].index("USD")))
				except: pass

	@staticmethod
	def refresh_coingecko_index():
		try:
			blacklist = ["UNIUSD"]
			indexReference, i = {}, 0
			while True:
				i += 1
				rawData = TickerParserServer.coinGecko.get_coins_markets(vs_currency="btc", order="market_cap_desc", per_page=250, page=i)
				if len(rawData) == 0: break
				for e in rawData:
					if e["symbol"].upper() not in indexReference and e["symbol"].upper() not in blacklist:
						indexReference[e["symbol"].upper()] = {"id": e["id"], "name": e["name"], "base": e["symbol"], "quote": "BTC", "image": e["image"], "market_cap_rank": e["market_cap_rank"]}
			TickerParserServer.coinGeckoIndex = indexReference
		except Exception:
			print(traceback.format_exc())

	@staticmethod
	def refresh_coingecko_exchange_rates():
		try:
			coingeckoVsCurrencies = TickerParserServer.coinGecko.get_supported_vs_currencies()
			TickerParserServer.coingeckoVsCurrencies = [e.upper() for e in coingeckoVsCurrencies]
			exchangeRates = TickerParserServer.coinGecko.get_exchange_rates()
			for ticker in exchangeRates["rates"]:
				TickerParserServer.exchangeRates[ticker.upper()] = exchangeRates["rates"][ticker]
			for ticker in TickerParserServer.exchangeRates:
				if TickerParserServer.exchangeRates[ticker.upper()]["type"] == "fiat":
					TickerParserServer.coingeckoFiatCurrencies.append(ticker.upper())
		except Exception:
			print(traceback.format_exc())

	@staticmethod
	def refresh_iexc_index():
		try:
			stockSymbols = get_symbols(token=os.environ["IEXC_KEY"])
			for index, stock in stockSymbols.iterrows():
				if stock["exchange"] not in TickerParserServer.exchanges:
					TickerParserServer.exchanges[stock["exchange"]] = Exchange(stock["exchange"])
				tickerId = stock["symbol"] if stock["iexId"] is None else stock["iexId"]
				TickerParserServer.iexcStocksIndex[stock["symbol"]] = {"id": tickerId, "name": stock["name"], "base": stock["symbol"], "quote": stock["currency"], "exchange": stock["exchange"]}
				TickerParserServer.exchanges[stock["exchange"]].properties.symbols.append(stock["symbol"])
			
			forexSymbols = requests.get("https://cloud.iexapis.com/stable/ref-data/fx/symbols?token={}".format(os.environ["IEXC_KEY"])).json()
			for pair in forexSymbols["pairs"]:
				TickerParserServer.iexcForexIndex[pair["symbol"]] = {"id": pair["symbol"], "name": pair["symbol"], "base": pair["fromCurrency"], "quote": pair["toCurrency"], "reversed": False}
				TickerParserServer.iexcForexIndex[pair["toCurrency"] + pair["fromCurrency"]] = {"id": pair["symbol"], "name": pair["toCurrency"] + pair["fromCurrency"], "base": pair["toCurrency"], "quote": pair["fromCurrency"], "reversed": True}

			otcSymbols = requests.get("https://cloud.iexapis.com/stable/ref-data/otc/symbols?token={}".format(os.environ["IEXC_KEY"])).json()
			for stock in otcSymbols:
				if stock["exchange"] not in TickerParserServer.exchanges:
					TickerParserServer.exchanges[stock["exchange"]] = Exchange(stock["exchange"])
				tickerId = stock["symbol"] if stock["iexId"] is None else stock["iexId"]
				TickerParserServer.iexcOtcIndex[stock["symbol"]] = {"id": tickerId, "name": stock["name"], "base": stock["symbol"], "quote": stock["currency"], "exchange": stock["exchange"]}

		except Exception:
			print(traceback.format_exc())

	@staticmethod
	def find_exchange(raw, platform):
		if platform not in supported.cryptoExchanges: return None, None
		if raw in ["pro"]: return None, None

		checked = []
		shortcuts = {
			"binance": ["bin", "bi", "b"],
			"bitmex": ["bmx", "mex", "btmx", "bx"],
			"binancefutures": ["binancef", "fbin", "binf", "bif", "bf"],
			"coinbasepro": ["cbp", "coin", "base", "cb", "coinbase", "coinbasepro", "cbpro"],
			"bitfinex2": ["bfx", "finex", "bf"],
			"bittrex": ["btrx", "brx"],
			"huobipro": ["hpro"],
			"poloniex": ["po", "polo"],
			"kraken": ["k", "kra"],
			"gemini": ["ge", "gem"]
		}

		for id in supported.cryptoExchanges[platform]:
			checked.append(id)

			if id in shortcuts and raw in shortcuts[id]:
				return True, TickerParserServer.exchanges[id]
			if id in TickerParserServer.exchanges and TickerParserServer.exchanges[id].name is not None:
				name = TickerParserServer.exchanges[id].name.split(" ")[0].lower()
				nameNoSpaces = TickerParserServer.exchanges[id].name.replace(" ", "").lower()
			else:
				name, nameNoSpaces = id, id

			if len(name) * 0.33 > len(raw): continue

			if name.startswith(raw) or name.endswith(raw): return True, TickerParserServer.exchanges[id]
			elif nameNoSpaces.startswith(raw) or nameNoSpaces.endswith(raw): return True, TickerParserServer.exchanges[id]
			elif id.startswith(raw) or id.endswith(raw): return True, TickerParserServer.exchanges[id]

		for platform in supported.cryptoExchanges:
			if platform == platform: continue
			for id in supported.cryptoExchanges[platform]:
				if id in checked: continue
				else: checked.append(id)

				if id in shortcuts:
					if raw in shortcuts[id]: return True, TickerParserServer.exchanges[id]
				if id in TickerParserServer.exchanges and TickerParserServer.exchanges[id].name is not None:
					name = TickerParserServer.exchanges[id].name.split(" ")[0].lower()
					nameNoSpaces = TickerParserServer.exchanges[id].name.replace(" ", "").lower()
				else:
					name, nameNoSpaces = id, id

				if name.startswith(raw) or name.endswith(raw): return False, TickerParserServer.exchanges[id]
				elif nameNoSpaces.startswith(raw) or nameNoSpaces.endswith(raw): return False, TickerParserServer.exchanges[id]
				elif id.startswith(raw) or id.endswith(raw): return False, TickerParserServer.exchanges[id]
		return None, None

	@staticmethod
	def process_known_tickers(ticker, exchange, platform, defaults, bias):
		if (ticker.id.startswith("'") and ticker.id.endswith("'")) or (ticker.id.startswith('"') and ticker.id.endswith('"')) or (ticker.id.startswith("‘") and ticker.id.endswith("’")) or (ticker.id.startswith("“") and ticker.id.endswith("”")):
			ticker = Ticker(ticker.id[1:-1], ticker.id[1:-1], ticker.id[1:-1], "", ticker.id[1:-1], hasParts=False)
		else:
			if ticker.id.startswith("$"): ticker = Ticker(ticker.id[1:] + "USD", base=ticker.id[1:], quote="USD", hasParts=False)
			elif ticker.id.startswith("€"): ticker = Ticker(ticker.id[1:] + "EUR", base=ticker.id[1:], quote="EUR", hasParts=False)

			tickerOverrides = {
				"TradingView": [
					(Ticker("(DJ:DJI)", "DJI", "DJI", "", "DJI", hasParts=False), None, ["DJI"]),
					(Ticker("SPX500USD", "SPX500USD", "SPX500USD", "", "SPX500USD", hasParts=False), None, ["SPX"])
				]
			}
			cryptoTickerOverrides = {
				"TradingLite": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"])
				],
				"TradingView": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"]),
					(Ticker("(DJ:DJI)", "DJI", "DJI", "", "DJI", hasParts=False), None, ["DJI"]),
					(Ticker("SPX500USD", "SPX500USD", "SPX500USD", "", "SPX500USD", hasParts=False), None, ["SPX"]),
					(Ticker("(BNC:BLX)", "BLX", "BTC", "USD", "BTC/USD", hasParts=False), None, ["BNC", "BLX"]),
					(Ticker("BTCUSDLONGS", "BTCUSD Longs", "BTC", "USD", "BTCUSDLONGS", hasParts=False), None, ["L", "LONGS"]),
					(Ticker("BTCUSDSHORTS", "BTCUSD Shorts", "BTC", "USD", "BTCUSDSHORTS", hasParts=False), None, ["S", "SHORTS"]),
					(Ticker("(BTCUSDLONGS/(BTCUSDLONGS+BTCUSDSHORTS))", "BTCUSD Longs/Shorts", None, "%", None), None, ["LS", "LONGS/SHORTS"]),
					(Ticker("(BTCUSDSHORTS/(BTCUSDLONGS+BTCUSDSHORTS))", "BTCUSD Shorts/Longs", None, "%", None), None, ["SL", "SHORTS/LONGS"])
				],
				"Bookmap": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"])
				],
				"GoCharting": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"])
				],
				"CoinGecko": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"])
				],
				"LLD": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"])
				],
				"CCXT": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"])
				],
				"Alpha Paper Trader": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"])
				],
				"Ichibot": [
					(Ticker("BTCUSD", "XBTUSD", "BTC", "USD", "BTC/USD", hasParts=False, mcapRank=1), TickerParserServer.exchanges["bitmex"], ["XBT", "XBTUSD"])
				]
			}
			parsedTicker, parsedExchange = None, None

			if (platform in ["TradingView", "Bookmap", "GoCharting"] and bias == "crypto") or platform in ["TradingLite", "LLD", "CoinGecko", "CCXT", "Alpha Paper Trader", "Ichibot"]:
				if platform in cryptoTickerOverrides:
					for tickerOverride, exchangeOverride, triggers in cryptoTickerOverrides[platform]:
						if ticker.id in triggers:
							ticker = tickerOverride
							if exchangeOverride is not None: exchange = exchangeOverride
							break
				
				if platform == "CoinGecko" and defaults["exchange"] is None and exchange is None: parsedTicker, parsedExchange = TickerParserServer.find_coingecko_crypto_market(ticker)
				else: parsedTicker, parsedExchange = TickerParserServer.find_ccxt_crypto_market(ticker, exchange, platform, defaults)
			else:
				if platform in tickerOverrides:
					for tickerOverride, exchangeOverride, triggers in tickerOverrides[platform]:
						if ticker.id in triggers:
							ticker = tickerOverride
							if exchangeOverride is not None: exchange = exchangeOverride
							break

				if platform == "IEXC": parsedTicker, parsedExchange = TickerParserServer.find_iexc_market(ticker)
				elif platform == "Quandl": parsedTicker, parsedExchange = TickerParserServer.find_quandl_market(ticker)

			if parsedTicker is not None: ticker, exchange = parsedTicker, parsedExchange

		return ticker, exchange

	@staticmethod
	def find_ccxt_crypto_market(ticker, exchange, platform, defaults):
		if platform not in supported.cryptoExchanges: return ticker, exchange
		exchanges = [TickerParserServer.exchanges[e] for e in supported.cryptoExchanges[platform]] if exchange is None else [exchange]
		if exchange is None and defaults["exchange"] is not None: exchanges.insert(0, TickerParserServer.exchanges[defaults["exchange"]])

		for e in exchanges:
			if e.properties is not None and e.properties.symbols is not None:
				tokenizedStock = exchange is None and ticker.id in TickerParserServer.iexcStocksIndex and e.id in ["ftx", "bittrex"]

				if ticker.id in TickerParserServer.ccxtIndex[platform]:
					for quote in TickerParserServer.ccxtIndex[platform][ticker.id]:
						symbol = "{}/{}".format(ticker.id, quote)
						if symbol in e.properties.symbols and not tokenizedStock:
							base = e.properties.markets[symbol]["base"]
							quote = e.properties.markets[symbol]["quote"]
							if not base in TickerParserServer.coingeckoFiatCurrencies and ("active" not in e.properties.markets[symbol] or e.properties.markets[symbol]["active"]): return Ticker(Ticker.generate_market_name(symbol, e), Ticker.generate_market_name(symbol, e), ticker.id, quote, symbol, hasParts=False, mcapRank=(TickerParserServer.coinGeckoIndex[ticker.id]["market_cap_rank"] if ticker.id in TickerParserServer.coinGeckoIndex else None)), e

				else:
					currentBestMatch = sys.maxsize
					currentBestFit = sys.maxsize
					currentResult = None, exchange
					for symbol in e.properties.symbols:
						base = e.properties.markets[symbol]["base"]
						quote = e.properties.markets[symbol]["quote"]
						marketPair = symbol.split("/")
						marketPairName = Ticker.generate_market_name(symbol, e)
						mcapRank = TickerParserServer.coinGeckoIndex[base]["market_cap_rank"] if base in TickerParserServer.coinGeckoIndex else None
						isReversed = False
						if "active" not in e.properties.markets[symbol] or e.properties.markets[symbol]["active"]:
							if len(marketPair) == 1:
								for _ in range(2):
									if (ticker.id == marketPair[0] or (marketPairName.startswith(ticker.id) and len(marketPairName) * 0.5 <= len(ticker.id))) and currentBestFit > 2:
										currentBestFit = 2
										currentResult = Ticker(marketPairName, marketPairName, base, quote, symbol, hasParts=False, mcapRank=mcapRank, isReversed=isReversed), e
									if platform not in ["CoinGecko", "CCXT", "IEXC", "Quandl"]: break
									marketPair.reverse()
									base, quote, marketPairName, isReversed = quote, base, "".join(marketPair), True

							elif marketPair[0] in TickerParserServer.ccxtIndex[platform] and marketPair[1] in TickerParserServer.ccxtIndex[platform][marketPair[0]]:
								rankScore = TickerParserServer.ccxtIndex[platform][marketPair[0]].index(marketPair[1])
								for _ in range(2):
									if (ticker.id == marketPair[0] + marketPair[1] or (marketPairName.startswith(ticker.id) and len(marketPairName) * 0.5 <= len(ticker.id))) and currentBestFit >= 1 and base not in TickerParserServer.coingeckoFiatCurrencies and rankScore < currentBestMatch and not tokenizedStock:
										currentBestMatch = rankScore
										currentBestFit = 1
										currentResult = Ticker(marketPairName, marketPairName, base, quote, symbol, hasParts=False, mcapRank=mcapRank, isReversed=isReversed), e
										break
									if platform not in ["CoinGecko", "CCXT", "IEXC", "Quandl"]: break
									marketPair.reverse()
									base, quote, marketPairName, isReversed = quote, base, "".join(marketPair), True

					if currentResult[0] is not None: return currentResult

		return None, exchange

	@staticmethod
	def find_coingecko_crypto_market(ticker):
		if ticker.id in TickerParserServer.coinGeckoIndex:
			if ticker.id in TickerParserServer.ccxtIndex["CCXT"]:
				quote = TickerParserServer.ccxtIndex["CCXT"][ticker.id][0]
				return Ticker("{}{}".format(ticker.id, quote), "{}{}".format(ticker.id, quote), ticker.id, quote, TickerParserServer.coinGeckoIndex[ticker.id]["id"], hasParts=False, mcapRank=TickerParserServer.coinGeckoIndex[ticker.id]["market_cap_rank"]), None
			else:
				return Ticker("{}BTC".format(ticker.id), "{}BTC".format(ticker.id), ticker.id, "BTC", TickerParserServer.coinGeckoIndex[ticker.id]["id"], hasParts=False, mcapRank=TickerParserServer.coinGeckoIndex[ticker.id]["market_cap_rank"]), None

		else:
			for base in TickerParserServer.coinGeckoIndex:
				if ticker.id.startswith(base):
					for quote in TickerParserServer.coingeckoVsCurrencies:
						if ticker.id == "{}{}".format(base, quote):
							return Ticker(ticker.id, ticker.id, base, quote, TickerParserServer.coinGeckoIndex[base]["id"], hasParts=False, mcapRank=TickerParserServer.coinGeckoIndex[base]["market_cap_rank"]), None

			for base in TickerParserServer.coinGeckoIndex:
				if base.startswith(ticker.id):
					return Ticker("{}BTC".format(base), "{}BTC".format(base), base, "BTC", TickerParserServer.coinGeckoIndex[base]["id"], hasParts=False, mcapRank=TickerParserServer.coinGeckoIndex[base]["market_cap_rank"]), None

			for base in TickerParserServer.coinGeckoIndex:
				if ticker.id.endswith(base):
					for quote in TickerParserServer.coingeckoVsCurrencies:
						if ticker.id == "{}{}".format(quote, base):
							return Ticker(ticker.id, ticker.id, quote, base, TickerParserServer.coinGeckoIndex[base]["id"], hasParts=False, mcapRank=TickerParserServer.coinGeckoIndex[base]["market_cap_rank"], isReversed=True), None

		return None, None

	@staticmethod
	def find_iexc_market(ticker):
		if ticker.id in TickerParserServer.iexcForexIndex:
			return Ticker(TickerParserServer.iexcForexIndex[ticker.id]["id"], TickerParserServer.iexcForexIndex[ticker.id]["name"], TickerParserServer.iexcForexIndex[ticker.id]["base"], TickerParserServer.iexcForexIndex[ticker.id]["quote"], "{}/{}".format(TickerParserServer.iexcForexIndex[ticker.id]["base"], TickerParserServer.iexcForexIndex[ticker.id]["quote"]), hasParts=False, isReversed=TickerParserServer.iexcForexIndex[ticker.id]["reversed"]), None
		elif ticker.id in TickerParserServer.iexcStocksIndex:
			return Ticker(ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["name"], ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["quote"], "{}/{}".format(ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["quote"]), hasParts=False), TickerParserServer.exchanges[TickerParserServer.iexcStocksIndex[ticker.id]["exchange"]]
		elif ticker.id in TickerParserServer.iexcOtcIndex:
			return Ticker(ticker.id, TickerParserServer.iexcOtcIndex[ticker.id]["name"], ticker.id, TickerParserServer.iexcOtcIndex[ticker.id]["quote"], "{}/{}".format(ticker.id, TickerParserServer.iexcOtcIndex[ticker.id]["quote"]), hasParts=False), TickerParserServer.exchanges[TickerParserServer.iexcOtcIndex[ticker.id]["exchange"]]
		elif ticker.id.endswith("USD") and ticker.id[:-3] in TickerParserServer.iexcStocksIndex:
			ticker.id = ticker.id[:-3]
			return Ticker(ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["name"], ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["quote"], "{}/{}".format(ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["quote"]), hasParts=False), TickerParserServer.exchanges[TickerParserServer.iexcStocksIndex[ticker.id]["exchange"]]
		elif ticker.id.startswith("USD") and ticker.id[3:] in TickerParserServer.iexcStocksIndex:
			ticker.id = ticker.id[3:]
			return Ticker(ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["name"], ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["quote"], "{}/{}".format(ticker.id, TickerParserServer.iexcStocksIndex[ticker.id]["quote"]), hasParts=False, isReversed=True), TickerParserServer.exchanges[TickerParserServer.iexcStocksIndex[ticker.id]["exchange"]]

		return None, None

	@staticmethod
	def find_quandl_market(ticker):
		return None, None

	@staticmethod
	def get_coingecko_image(base):
		if base in TickerParserServer.coinGeckoIndex:
			response = TickerParserServer.coinGeckoIndex[base].get("image", "")
			if response.startswith("https://"): return response
		return static_storage.icon

	@staticmethod
	def check_if_fiat(tickerId):
		for fiat in TickerParserServer.coingeckoFiatCurrencies:
			if fiat.upper() in tickerId: return True, fiat.upper()
		return False, tickerId

	@staticmethod
	def get_listings(ticker):
		listings = {ticker.quote: []}
		total = 0
		for id in supported.cryptoExchanges["CCXT"]:
			if TickerParserServer.exchanges[id].properties is not None and TickerParserServer.exchanges[id].properties.symbols is not None:
				for symbol in TickerParserServer.exchanges[id].properties.symbols:
					base = TickerParserServer.exchanges[id].properties.markets[symbol]["base"]
					quote = TickerParserServer.exchanges[id].properties.markets[symbol]["quote"]
					if ticker.base == base:
						if quote not in listings: listings[quote] = []
						if TickerParserServer.exchanges[id].name not in listings[quote]:
							listings[quote].append(TickerParserServer.exchanges[id].name)
							total += 1

		response = [(ticker.quote, listings.pop(ticker.quote))]
		if ticker.base in TickerParserServer.ccxtIndex["CCXT"]:
			for quote in TickerParserServer.ccxtIndex["CCXT"][ticker.base]:
				if quote in listings:
					response.append((quote, listings.pop(quote)))

		return response, total


if __name__ == "__main__":
	os.environ["PRODUCTION_MODE"] = os.environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in os.environ and os.environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Ticker Parser Server is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))
	tickerParser = TickerParserServer()
	tickerParser.run()