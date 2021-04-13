import os
import sys
import urllib
import time
import re

from TickerParser import TickerParser
from .parameter import ChartParameter as Parameter
from TickerParser import Ticker


class ChartRequestHandler(object):
	def __init__(self, accountId, authorId, tickerId, platforms, isPro=False, messageRequest=None, **kwargs):
		self.accountId = accountId
		self.authorId = authorId
		self.timestamp = time.time()
		self.hash = "C{}{}".format(int(time.time() * 1000), authorId)
		self.platforms = platforms
		self.defaults = {"exchange": None} if messageRequest is None else messageRequest.guildProperties["settings"]["charts"]["defaults"]
		self.parserBias = "traditional" if messageRequest is None else messageRequest.marketBias
		
		self.isDelayed = not isPro

		self.currentPlatform = self.platforms[0]

		self.requests = {}
		for platform in self.platforms:
			self.requests[platform] = ChartRequest(tickerId, platform, self.parserBias)

	def parse_argument(self, argument):
		for platform, request in self.requests.items():
			if request.errorIsFatal: continue

			# None None - No successeful parse
			# None True - Successful parse and add
			# "" False - Successful parse and error

			finalOutput = None

			outputMessage, success = request.add_timeframe(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_timeframe_range(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_indicator(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_chart_style(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_image_style(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_filters(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.process_special_tickers(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_exchange(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_numerical_parameters(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			if finalOutput is None:
				request.set_error("`{}` is not a valid argument.".format(argument), isFatal=True)
			else:
				request.set_error(finalOutput)

	def process_ticker(self):
		for platform, request in self.requests.items():
			request.process_ticker(self.defaults, self.parserBias)

	def get_preferred_platform(self):
		currentMinimumErrors = sys.maxsize
		preferredPlatformOrder = []
		preferredRequestOrder = []

		for platform in self.platforms:
			request = self.requests[platform]
			numberOfErrors = sys.maxsize if len(request.errors) > 0 and request.errors[0] is None else len(request.errors)
			if currentMinimumErrors > numberOfErrors:
				currentMinimumErrors = numberOfErrors
				preferredPlatformOrder = [platform]
				preferredRequestOrder = [request]
			elif numberOfErrors == 0:
				preferredPlatformOrder.append(platform)
				preferredRequestOrder.append(request)

		i = 0
		while i < len(self.platforms):
			platform = self.platforms[i]
			if platform not in preferredPlatformOrder:
				self.platforms.remove(platform)
				self.requests.pop(platform, None)
			else: i += 1
		if len(self.platforms) > 0: self.currentPlatform = self.platforms[0]

		outputMessage = None if currentMinimumErrors == 0 else (preferredRequestOrder[0].errors[0] if len(preferredRequestOrder) > 0 else "Requested chart is not available.")
		return outputMessage

	def set_current(self, platform=None, timeframe=None):
		if platform is not None: self.currentPlatform = platform
		if timeframe is not None:
			for platform in self.requests:
				self.requests[platform].currentTimeframe = timeframe

	def build_url(self, addMessageUrl=False):
		requestUrl, messageUrl = None, None
		if self.currentPlatform == "Alternative.me":
			requestUrl = "https://alternative.me/crypto/fear-and-greed-index.png"
			if addMessageUrl: messageUrl = requestUrl
		elif self.currentPlatform == "Woobull Charts":
			if self.get_ticker().id == "NVT": requestUrl = "https://charts.woobull.com/bitcoin-nvt-ratio/"
			elif self.get_ticker().id == "DRBN": requestUrl = "https://charts.woobull.com/bitcoin-difficulty-ribbon/"
			if addMessageUrl: messageUrl = requestUrl
		elif self.currentPlatform == "TradingLite":
			exchange = self.get_exchange()
			ticker = self.get_ticker()
			if exchange == "binancefutures": exchange = "binancef"
			elif exchange == "bitstamp": ticker = ticker.id.lower()
			elif exchange == "coinbase": ticker = ticker.symbol.replace("/", "-")
			else: ticker = ticker.id
			requestUrl = "https://tradinglite.com/chart/RZq6kMEQ/{}/{}/{}".format(urllib.parse.quote(exchange, safe=""), urllib.parse.quote(ticker, safe=""), self.get_current_timeframe())
			if addMessageUrl: messageUrl = requestUrl
		elif self.currentPlatform == "TradingView":
			exchange = self.get_exchange()
			tickerId = self.get_ticker().id
			if exchange == "BINANCEFUTURES:":
				exchange = "BINANCE:"
				tickerId += "PERP"
			requestUrl = "https://www.tradingview.com/widgetembed/?symbol={}{}&interval={}{}{}&hidetoptoolbar=1".format(urllib.parse.quote(exchange, safe=""), urllib.parse.quote(tickerId, safe=""), self.get_current_timeframe(), self.get_indicators(), self.get_chart_style())
			if addMessageUrl: messageUrl = requestUrl[:-17] + "&symboledit=1&saveimage=1&withdateranges=1&enablepublishing=true"
		elif self.currentPlatform == "Bookmap":
			exchange = self.get_exchange()
			tickerId = self.get_ticker().id
			requestUrl = "http://web.bookmap.com/?provider={}&symbol={}".format(urllib.parse.quote(exchange, safe=""), urllib.parse.quote(tickerId, safe=""))
			if addMessageUrl: messageUrl = requestUrl
		elif self.currentPlatform == "GoCharting":
			exchange = self.get_exchange()
			tickerId = self.get_ticker().id
			if exchange == "BINANCEFUTURES":
				exchange = "BINANCE:FUTURE"
			requestUrl = "https://origin.alphabot.gocharting.com/terminal?ticker={}:{}&resolution={}{}{}&hidetoptoolbar=1&etag=alpha".format(urllib.parse.quote(exchange, safe=""), urllib.parse.quote(tickerId, safe=""), self.get_current_timeframe(), self.get_indicators(), self.get_chart_style())
			if addMessageUrl: messageUrl = requestUrl
		elif self.currentPlatform == "Finviz":
			barStyle = self.find_parameter_in_list("candleStyle", self.get_filters())
			requestUrl = "https://finviz.com/chart.ashx?t={}{}{}&p={}&s=l".format(urllib.parse.quote(self.get_ticker().id, safe=""), barStyle, self.get_chart_style(), self.get_current_timeframe())
			if addMessageUrl: messageUrl = requestUrl
		elif self.currentPlatform == "Alpha Flow":
			tickerId = self.get_ticker().id
			if tickerId != "OPTIONS":
				listStyle = "/list" if "flowlist" in self.get_image_style() else "/ticker"
				tickerId = "&ticker={}".format(urllib.parse.quote(tickerId, safe=""))
				timeframe = "&tf={}".format(self.get_current_timeframe()) if "flowlist" in self.get_image_style() else ""
			else:
				listStyle = ""
				tickerId = ""
				timeframe = ""
			requestUrl = "https://www.alphabotsystem.com/flow{}?key={}{}{}".format(listStyle, os.environ["ALPHA_FLOW_KEY"], tickerId, timeframe)

		return requestUrl, messageUrl

	def set_defaults(self):
		for platform, request in self.requests.items():
			if request.errorIsFatal: continue
			for type in request.requestParameters:
				request.set_default_for(type)

	def find_caveats(self):
		for platform, request in self.requests.items():
			if platform == "Alternative.me":
				if request.ticker.id not in ["FGI"]: request.set_error(None, isFatal=True)
			elif platform == "Woobull Charts":
				if request.ticker.id not in ["NVT", "DRBN"]: request.set_error(None, isFatal=True)
			elif platform == "TradingLite":
				if request.ticker.symbol is None: request.set_error("Requested chart for `{}` is not available.".format(request.ticker.id), isFatal=True)
				if request.exchange is None: request.set_error(None, isFatal=True)
				elif request.exchange.id in ["binancefutures", "ftx", "okex"]: request.set_error("{} exchange is not available. ||Yet.||".format(request.exchange.name), isFatal=True)
			elif platform == "TradingView":
				if self.find_parameter_in_list_for("heatmapIntensity", request.filters, platform) != "":
					request.set_error("Heat map intensity control is not available on TradingView.", isFatal=True)
				elif self.find_parameter_in_list_for("candleStyle", request.chartStyle, platform) == "&style=6" and self.find_parameter_in_list_for("log", request.imageStyle, platform) == "log":
					request.set_error("Point & Figure chart can't be viewed in log scale.", isFatal=True)
			elif platform == "Bookmap":
				if request.exchange is None: request.set_error(None, isFatal=True)
				if self.find_parameter_in_list_for("heatmapIntensity", request.filters, platform) != "":
					request.set_error("Heat map intensity control is not available on Bookmap.", isFatal=True)
			elif platform == "GoCharting":
				if request.exchange is None: request.set_error(None, isFatal=True)
				if self.find_parameter_in_list_for("heatmapIntensity", request.filters, platform) != "":
					request.set_error("Heat map intensity control is not available on GoCharting.", isFatal=True)
				indicators = self.requests[platform].indicators
				parameters = self.requests[platform].numericalParameters
				lengths = {i: [] for i in range(len(indicators))}
				cursor = len(parameters) - 1
				for i in reversed(range(len(indicators))):
					while parameters[cursor] != -1:
						lengths[i].insert(0, parameters[cursor])
						cursor -= 1
					cursor -= 1

					if indicators[i].dynamic is not None and lengths[i] != 0 and len(lengths[i]) > len(indicators[i].dynamic[platform]):
						request.set_error("{} indicator takes in `{}` {}, but `{}` were given.".format(indicators[i].name, len(indicators[i].dynamic[platform]), "parameters" if len(indicators[i].dynamic[platform]) > 1 else "parameter", len(lengths[i])), isFatal=True)
						break
			elif platform == "Finviz":
				if self.find_parameter_in_list_for("heatmapIntensity", request.filters, platform) != "":
					request.set_error("Heat map intensity control is not available on Finviz.", isFatal=True)
			elif platform == "Alpha Flow":
				if request.ticker.id != "OPTIONS":
					if len(request.timeframes) != 0 and self.find_parameter_in_list_for("flowlist", request.imageStyle, platform) != "flowlist":
						request.imageStyle.append(request.find_parameter_with_id("flowlist", name="list", type="imageStyle"))
					elif len(request.timeframes) == 0:
						request.timeframes.append(request.find_parameter_with_id(10080, type="timeframes"))
				else:
					if len(request.timeframes) != 0:
						request.set_error("Timeframes are not available for options flow overview on Alpha Flow.", isFatal=True)
					request.timeframes.append(Parameter(None, None, None, alphaflow=None))
				if self.find_parameter_in_list_for("heatmapIntensity", request.filters, platform) != "":
					request.set_error("Heat map intensity control is not available on Alpha Flow.", isFatal=True)

	def requires_pro(self):
		return self.requests[self.currentPlatform].requiresPro

	def get_ticker(self): return self.get_ticker_for(self.currentPlatform)

	def get_exchange(self): return self.get_exchange_for(self.currentPlatform)

	def get_current_timeframe(self): return self.get_current_timeframe_for(self.currentPlatform)

	def get_timeframes(self): return self.get_timeframes_for(self.currentPlatform)

	def get_indicators(self): return self.get_indicators_for(self.currentPlatform)

	def get_chart_style(self): return self.get_chart_style_for(self.currentPlatform)

	def get_image_style(self): return self.get_image_style_for(self.currentPlatform)

	def get_filters(self): return self.get_filters_for(self.currentPlatform)

	def get_numerical_parameters(self): return self.get_numerical_parameters_for(self.currentPlatform)

	def find_parameter_in_list(self, id, list, default=""): return self.find_parameter_in_list_for(id, list, self.currentPlatform, default)

	def get_ticker_for(self, platform):
		if platform not in self.requests: return None
		ticker = self.requests[platform].ticker

		if platform == "Alternative.me": pass
		elif platform == "Woobull Charts": pass
		elif platform == "TradingLite": pass
		elif platform == "TradingView": pass
		elif platform == "Bookmap": pass
		elif platform == "GoCharting": pass
		elif platform == "Finviz": pass
		elif platform == "Alpha Flow": pass

		return ticker

	def get_exchange_for(self, platform):
		if platform not in self.requests: return None
		exchange = self.requests[platform].exchange

		if platform == "Alternative.me": pass
		elif platform == "Woobull Charts": pass
		elif platform == "TradingLite":
			if exchange.id in ["coinbasepro", "huobipro"]: exchange = exchange.id[:-3]
			else: exchange = exchange.id.replace("2", "").replace("3", "")
		elif platform == "TradingView":
			if exchange is None or self.get_ticker().isAggregatedTicker: return ""
			elif exchange.id in ["coinbasepro", "huobipro"]: exchange = exchange.id[:-3].upper() + ":"
			else: exchange = exchange.id.replace("2", "").replace("3", "").upper() + ":"
		elif platform == "Bookmap":
			if exchange.id in ["binancefutures"]: exchange = "binance-futures"
			else: exchange = exchange.id.replace("2", "").replace("3", "")
		elif platform == "GoCharting":
			if exchange is None: return ""
			elif exchange.id in ["coinbasepro", "huobipro"]: exchange = exchange.id[:-3].upper()
			else: exchange = exchange.id.replace("2", "").replace("3", "").upper()
		elif platform == "Finviz": pass
		elif platform == "Alpha Flow": pass

		return exchange

	def get_current_timeframe_for(self, platform):
		if platform not in self.requests: return None
		timeframes = [e.parsed[platform] for e in self.requests[platform].timeframes]
		return self.requests[platform].currentTimeframe

	def get_timeframes_for(self, platform):
		if platform not in self.requests: return []
		timeframes = [e.parsed[platform] for e in self.requests[platform].timeframes]
		return timeframes

	def get_indicators_for(self, platform):
		if platform not in self.requests: return ""
		indicators = self.requests[platform].indicators

		if platform == "Alternative.me": pass
		elif platform == "Woobull Charts": pass
		elif platform == "TradingLite": pass
		elif platform == "TradingView":
			if len(indicators) == 0:
				indicators = ""
			else:
				indicators = "&studies=" + "%1F".join([e.parsed[platform] for e in indicators])
		elif platform == "Bookmap": pass
		elif platform == "GoCharting":
			if len(indicators) == 0:
				indicators = ""
			else:
				parameters = self.requests[platform].numericalParameters
				lengths = {i: [] for i in range(len(indicators))}
				cursor = len(parameters) - 1
				for i in reversed(range(len(indicators))):
					while parameters[cursor] != -1:
						lengths[i].insert(0, parameters[cursor])
						cursor -= 1
					cursor -= 1

					if indicators[i].dynamic is not None and lengths[i] != 0 and len(lengths[i]) < len(indicators[i].dynamic[platform]):
						for j in range(len(lengths[i]), len(indicators[i].dynamic[platform])):
							lengths[i].append(indicators[i].dynamic[platform][j])

					indicators[i].parsed[platform] = "{}_{}".format(indicators[i].parsed[platform], "_".join([str(l) for l in lengths[i]]))

				indicators = "&studies=" + "-".join([e.parsed[platform] for e in indicators])
		elif platform == "Finviz": pass
		elif platform == "Alpha Flow": pass

		return indicators

	def get_chart_style_for(self, platform):
		if platform not in self.requests: return []
		return "".join([e.parsed[platform] for e in self.requests[platform].chartStyle])

	def get_image_style_for(self, platform):
		if platform not in self.requests: return []
		return [e.parsed[platform] for e in self.requests[platform].imageStyle]

	def get_filters_for(self, platform):
		if platform not in self.requests: return []
		return self.requests[platform].filters

	def get_numerical_parameters_for(self, platform):
		if platform not in self.requests: return []
		return self.requests[platform].numericalParameters

	def find_parameter_in_list_for(self, id, list, platform, default=""):
		for e in list:
			if e.id == id: return e.parsed[platform]
		return default

	def can_cache(self):
		return self.requests[self.currentPlatform].canCache

	def __str__(self):
		return "<Request: {}, {}, {}, (indicators: {}, style: {})>".format(self.get_ticker(), self.get_exchange(), self.get_timeframes(), self.get_indicators(), self.get_chart_style())


class ChartRequest(object):
	requestParameters = {
		"timeframes": [
			Parameter(1, "1m", ["1", "1m", "1min", "1mins", "1minute", "1minutes", "min", "m"], tradinglite="1", tradingview="1", gocharting="1m"),
			Parameter(2, "2m", ["2", "2m", "2min", "2mins", "2minute", "2minutes"]),
			Parameter(3, "3m", ["3", "3m", "3min", "3mins", "3minute", "3minutes"], tradinglite="3", tradingview="3", gocharting="3m"),
			Parameter(4, "4m", ["4", "4m", "4min", "4mins", "4minute", "4minutes"]),
			Parameter(5, "5m", ["5", "5m", "5min", "5mins", "5minute", "5minutes"], tradinglite="5", tradingview="5", gocharting="5m"),
			Parameter(6, "6m", ["6", "6m", "6min", "6mins", "6minute", "6minutes"]),
			Parameter(10, "10m", ["10", "10m", "10min", "10mins", "10minute", "10minutes"], bookmap="bm-btn-time-frame-10m"),
			Parameter(15, "15m", ["15", "15m", "15min", "15mins", "15minute", "15minutes"], tradinglite="15", tradingview="15", gocharting="15m"),
			Parameter(20, "20m", ["20", "20m", "20min", "20mins", "20minute", "20minutes"]),
			Parameter(30, "30m", ["30", "30m", "30min", "30mins", "30minute", "30minutes"], tradinglite="30", tradingview="30", gocharting="30m"),
			Parameter(45, "45m", ["45", "45m", "45min", "45mins", "45minute", "45minutes"], tradingview="45"),
			Parameter(60, "1H", ["60", "60m", "60min", "60mins", "60minute", "60minutes", "1", "1h", "1hr", "1hour", "1hours", "hourly", "hour", "hr", "h"], tradinglite="60", tradingview="60", bookmap="bm-btn-time-frame-1h", gocharting="1h"),
			Parameter(120, "2H", ["120", "120m", "120min", "120mins", "120minute", "120minutes", "2", "2h", "2hr", "2hrs", "2hour", "2hours"], tradinglite="120", tradingview="120", gocharting="2h"),
			Parameter(180, "3H", ["180", "180m", "180min", "180mins", "180minute", "180minutes", "3", "3h", "3hr", "3hrs", "3hour", "3hours"], tradingview="180"),
			Parameter(240, "4H", ["240", "240m", "240min", "240mins", "240minute", "240minutes", "4", "4h", "4hr", "4hrs", "4hour", "4hours"], tradinglite="240", tradingview="240", gocharting="4h"),
			Parameter(360, "6H", ["360", "360m", "360min", "360mins", "360minute", "360minutes", "6", "6h", "6hr", "6hrs", "6hour", "6hours"], tradinglite="360"),
			Parameter(480, "8H", ["480", "480m", "480min", "480mins", "480minute", "480minutes", "8", "8h", "8hr", "8hrs", "8hour", "8hours"], tradinglite="480"),
			Parameter(720, "12H", ["720", "720m", "720min", "720mins", "720minute", "720minutes", "12", "12h", "12hr", "12hrs", "12hour", "12hours"], tradinglite="720", gocharting="12h"),
			Parameter(1440, "1D", ["24", "24h", "24hr", "24hrs", "24hour", "24hours", "d", "day", "1", "1d", "1day", "daily", "1440", "1440m", "1440min", "1440mins", "1440minute", "1440minutes"], tradinglite="1440", tradingview="D", bookmap="bm-btn-time-frame-1d", gocharting="1D", finviz="d", alphaflow="yesterday"),
			Parameter(2880, "2D", ["48", "48h", "48hr", "48hrs", "48hour", "48hours", "2", "2d", "2day", "2880", "2880m", "2880min", "2880mins", "2880minute", "2880minutes"]),
			Parameter(3420, "3D", ["72", "72h", "72hr", "72hrs", "72hour", "72hours", "3", "3d", "3day", "3420", "3420m", "3420min", "3420mins", "3420minute", "3420minutes"]),
			Parameter(5760, "4D", ["96", "96h", "96hr", "96hrs", "96hour", "96hours", "4", "4d", "4day", "5760", "5760m", "5760min", "5760mins", "5760minute", "5760minutes"]),
			Parameter(7200, "5D", ["120", "120h", "120hr", "120hrs", "120hour", "120hours", "5", "5d", "5day", "7200", "7200m", "7200min", "7200mins", "7200minute", "7200minutes"]),
			Parameter(8640, "6D", ["144", "144h", "144hr", "144hrs", "144hour", "144hours", "4", "4d", "4day", "8640", "8640m", "8640min", "8640mins", "8640minute", "8640minutes"]),
			Parameter(10080, "1W", ["7", "7d", "7day", "7days", "w", "week", "1w", "1week", "weekly"], tradingview="W", bookmap="bm-btn-time-frame-1W", gocharting="1W", finviz="w", alphaflow="lastweek"),
			Parameter(20160, "2W", ["14", "14d", "14day", "14days", "2w", "2week"]),
			Parameter(30240, "3W", ["21", "21d", "21day", "21days", "3w", "3week"]),
			Parameter(43829, "1M", ["30d", "30day", "30days", "1", "1m", "m", "mo", "month", "1mo", "1month", "monthly"], tradingview="M", bookmap="bm-btn-time-frame-1Mo", gocharting="1M", finviz="m"),
			Parameter(87658, "2M", ["2", "2m", "2m", "2mo", "2month", "2months"]),
			Parameter(131487, "3M", ["3", "3m", "3m", "3mo", "3month", "3months"]),
			Parameter(175316, "4M", ["4", "4m", "4m", "4mo", "4month", "4months"]),
			Parameter(262974, "6M", ["6", "6m", "5m", "6mo", "6month", "6months"]),
			Parameter(525949, "1Y", ["12", "12m", "12mo", "12month", "12months", "year", "yearly", "1year", "1y", "y", "annual", "annually"]),
			Parameter(1051898, "2Y", ["24", "24m", "24mo", "24month", "24months", "2year", "2y"]),
			Parameter(1577847, "3Y", ["36", "36m", "36mo", "36month", "36months", "3year", "3y"]),
			Parameter(2103796, "4Y", ["48", "48m", "48mo", "48month", "48months", "4year", "4y"]),
			Parameter(2628000, "5Y", ["60", "60m", "60mo", "60month", "60months", "5year", "5y"])
		],
		"indicators": [
			Parameter("ab", "Abandoned Baby", ["ab", "abandonedbaby"], gocharting="ABANDONEDBABY"),
			Parameter("accd", "Accumulation/Distribution", ["accd", "accumulationdistribution", "ad", "acc"], tradingview="ACCD@tv-basicstudies", gocharting="ACC", dynamic={"GoCharting": [20]}),
			Parameter("accumulationswingindex", "Accumulation Swing Index", ["accumulationswingindex", "accsi", "asi"], gocharting="ACCSWINGINDEX"),
			Parameter("admi", "Average Directional Movement Index", ["admi", "adx"], gocharting="ADX", dynamic={"GoCharting": [20]}),
			Parameter("adr", "ADR", ["adr"], tradingview="studyADR@tv-basicstudies"),
			Parameter("alligator", "Alligator", ["alligator"], gocharting="ALLIGATOR"),
			Parameter("aroon", "Aroon", ["aroon"], tradingview="AROON@tv-basicstudies", gocharting="AROON", dynamic={"GoCharting": [20]}),
			Parameter("aroonoscillator", "Aroon Oscillator", ["aroonoscillator"], gocharting="AROONOSCILLATOR", dynamic={"GoCharting": [20]}),
			Parameter("atr", "ATR", ["atr"], tradingview="ATR@tv-basicstudies", gocharting="ATR", dynamic={"GoCharting": [20]}),
			Parameter("atrb", "ATR Bands", ["atrb"], gocharting="ATRBAND", dynamic={"GoCharting": [14, 2]}),
			Parameter("atrts", "ATR Trailing Stop", ["trailingstop", "atrts", "atrstop", "atrs"], gocharting="ATRTRAILINGSTOP", dynamic={"GoCharting": [14, 2]}),
			Parameter("awesome", "Awesome Oscillator", ["awesome", "ao"], tradingview="AwesomeOscillator@tv-basicstudies", gocharting="AWESOMEOSCILLATOR", dynamic={"GoCharting": [20]}),
			Parameter("balanceofpower", "Balance of Power", ["balanceofpower", "bop"], gocharting="BOP", dynamic={"GoCharting": [20]}),
			Parameter("bearish", "All Bearish Candlestick Patterns", ["bear", "bearish", "bearishpatterns", "bp"], gocharting="BEARISH"),
			Parameter("bearishengulfing", "Bearish Engulfing Pattern", ["bearishengulfing"], gocharting="BEARISHENGULFINGPATTERN"),
			Parameter("bearishhammer", "Bearish Hammer Pattern", ["bearishhammer"], gocharting="BEARISHHAMMER"),
			Parameter("bearishharami", "Bearish Harami Pattern", ["bearishharami"], gocharting="BEARISHHARAMI"),
			Parameter("bearishharamicross", "Bearish Harami Cross Pattern", ["bearishharamicross"], gocharting="BEARISHHARAMICROSS"),
			Parameter("bearishinvertedhammer", "Bearish Inverted Hammer", ["bearishinvertedhammer"], gocharting="BEARISHINVERTEDHAMMER"),
			Parameter("bearishmarubozu", "Bearish Marubozu Pattern", ["bearishmarubozu"], gocharting="BEARISHMARUBOZU"),
			Parameter("bearishspinningtop", "Bearish Spinning Top Pattern", ["bearishspinningtop"], gocharting="BEARISHSPINNINGTOP"),
			Parameter("width", "Bollinger Bands Width", ["width", "bbw"], tradingview="BollingerBandsWidth@tv-basicstudies"),
			Parameter("bullish", "All Bullish Candlestick Patterns", ["bull", "bullish", "bullishpatterns", "bp"], gocharting="BULLISH"),
			Parameter("bullishengulfing", "Bullish Engulfing Pattern", ["bullishengulfing"], gocharting="BULLISHENGULFINGPATTERN"),
			Parameter("bullishhammer", "Bullish Hammer Pattern", ["bullishhammer"], gocharting="BULLISHHAMMER"),
			Parameter("bullishharami", "Bullish Harami Pattern", ["bullishharami"], gocharting="BULLISHHARAMI"),
			Parameter("bullishharamicross", "Bullish Harami Cross Pattern", ["bullishharamicross"], gocharting="BULLISHHARAMICROSS"),
			Parameter("bullishinvertedhammer", "Bullish Inverted Hammer Pattern", ["bullishinvertedhammer"], gocharting="BULLISHINVERTEDHAMMER"),
			Parameter("bullishmarubozu", "Bullish Marubozu Pattern", ["bullishmarubozu"], gocharting="BULLISHMARUBOZU"),
			Parameter("bullishspinningtop", "Bullish Spinning Top Pattern", ["bullishspinningtop"], gocharting="BULLISHSPINNINGTOP"),
			Parameter("bollinger", "Bollinger Bands", ["bollinger", "bbands", "bb"], tradingview="BB@tv-basicstudies", gocharting="BOLLINGERBAND", dynamic={"GoCharting": [14, 2]}),
			Parameter("cmf", "Chaikin Money Flow Index", ["cmf"], tradingview="CMF@tv-basicstudies", gocharting="CHAIKINMFI", dynamic={"GoCharting": [20]}),
			Parameter("chaikin", "Chaikin Oscillator", ["chaikin", "co"], tradingview="ChaikinOscillator@tv-basicstudies"),
			Parameter("cv", "Chaikin Volatility", ["cv", "chaikinvolatility"], gocharting="CHAIKINVOLATILITY"),
			Parameter("cf", "Chande Forecast", ["cf", "chandeforecast"], gocharting="CHANDEFORECAST", dynamic={"GoCharting": [20]}),
			Parameter("chande", "Chande MO", ["chande", "cmo"], tradingview="chandeMO@tv-basicstudies", gocharting="CMO", dynamic={"GoCharting": [20]}),
			Parameter("choppiness", "Choppiness Index", ["choppiness", "ci"], tradingview="ChoppinessIndex@tv-basicstudies", gocharting="CHOPPINESS"),
			Parameter("cci", "CCI", ["cci"], tradingview="CCI@tv-basicstudies", gocharting="CCI", dynamic={"GoCharting": [14, 20, 80]}),
			Parameter("crsi", "CRSI", ["crsi"], tradingview="CRSI@tv-basicstudies"),
			Parameter("cog", "Center of Gravity", ["cog"], gocharting="COG", dynamic={"GoCharting": [20]}),
			Parameter("coppock", "Coppock", ["coppock"], gocharting="COPPOCK"),
			Parameter("cumtick", "Cumulative Tick", ["cumtick"], gocharting="CUMTICK", dynamic={"GoCharting": [20]}),
			Parameter("correlation", "Correlation Coefficient", ["correlation", "cc"], tradingview="CorrelationCoefficient@tv-basicstudies"),
			Parameter("darkcloudcoverpattern", "Dark Cloud Cover Pattern", ["darkcloudcover", "dccp"], gocharting="DARKCLOUDCOVER"),
			Parameter("detrended", "Detrended Price Oscillator", ["detrended", "dpo"], tradingview="DetrendedPriceOscillator@tv-basicstudies", gocharting="DPO", dynamic={"GoCharting": [20]}),
			Parameter("disparityoscillator", "Disparity Oscillator", ["disparityoscillator"], gocharting="DISPARITY", dynamic={"GoCharting": [20]}),
			Parameter("donchainwidth", "Donchain Width", ["donchainwidth"], gocharting="DONCHIANWIDTH", dynamic={"GoCharting": [20]}),
			Parameter("dm", "DM", ["dm", "directional"], tradingview="DM@tv-basicstudies"),
			Parameter("dojipattern", "Doji Pattern", ["doji"], gocharting="DOJI"),
			Parameter("donch", "DONCH", ["donch", "donchainchannel"], tradingview="DONCH@tv-basicstudies", gocharting="DONCHIANCHANNEL", dynamic={"GoCharting": [14, 2]}),
			Parameter("downsidetasukigappattern", "Downside Tasuki Gap Pattern", ["downsidetasukigap", "dtgp"], gocharting="DOWNSIDETASUKIGAP"),
			Parameter("dema", "Double EMA", ["dema", "2ema"], tradingview="DoubleEMA@tv-basicstudies", gocharting="DEMA", dynamic={"GoCharting": [20]}),
			Parameter("dragonflydojipattern", "Dragonfly Doji Pattern", ["dragonflydoji", "ddp"], gocharting="DRAGONFLYDOJI"),
			Parameter("efi", "EFI", ["efi"], tradingview="EFI@tv-basicstudies"),
			Parameter("ema", "EMA", ["ema"], tradingview="MAExp@tv-basicstudies", gocharting="EMA", dynamic={"GoCharting": [20]}),
			Parameter("elderray", "Elder Ray", ["elderray"], gocharting="ELDERRAY"),
			Parameter("elliott", "Elliott Wave", ["elliott", "ew"], tradingview="ElliottWave@tv-basicstudies"),
			Parameter("env", "ENV", ["env"], tradingview="ENV@tv-basicstudies"),
			Parameter("eom", "Ease of Movement", ["eom"], tradingview="EaseOfMovement@tv-basicstudies", gocharting="EOM", dynamic={"GoCharting": [20]}),
			Parameter("eveningdojistarpattern", "Evening Doji Star Pattern", ["eveningdojistar", "edsp"], gocharting="EVENINGDOJISTAR"),
			Parameter("eveningstarpattern", "Evening Star Pattern", ["eveningstar", "esp"], gocharting="EVENINGSTAR"),
			Parameter("fisher", "Fisher Transform", ["fisher", "ft"], tradingview="FisherTransform@tv-basicstudies", gocharting="EHLERFISHERTRANSFORM", dynamic={"GoCharting": [20]}),
			Parameter("forceindex", "Force Index", ["forceindex"], gocharting="FORCEINDEX"),
			Parameter("fullstochasticoscillator", "Full Stochastic Oscillator", ["fso"], gocharting="FULLSTOCHASTICOSCILLATOR"),
			Parameter("gravestonedojipattern", "Gravestone Doji Pattern", ["gravestonedoji", "gd"], gocharting="GRAVESTONEDOJI"),
			Parameter("gatoroscillator", "Gator Oscillator", ["gatoroscillator", "gatoro"], gocharting="GATOROSCILLATOR"),
			Parameter("gopalakrishnanrangeindex", "Gopalakrishnan Range Index", ["gopalakrishnanrangeindex", "gri", "gapo"], gocharting="GAPO", dynamic={"GoCharting": [20]}),
			Parameter("guppy", "Guppy Moving Average", ["guppy", "gma", "rainbow", "rma"], gocharting="GUPPY", dynamic={"GoCharting": [20]}),
			Parameter("guppyoscillator", "Guppy Oscillator", ["guppyoscillator", "guppyo", "rainbowoscillator", "rainbowo"], gocharting="GUPPYOSCILLATOR"),
			Parameter("hangmanpattern", "Hangman Pattern", ["hangman", "hangingman"], gocharting="HANGINGMAN"),
			Parameter("hhv", "High High Volume", ["highhighvolume", "hhv"], gocharting="HHV", dynamic={"GoCharting": [20]}),
			Parameter("hml", "High Minus Low", ["highminuslow", "hml"], gocharting="HIGHMINUSLOW"),
			Parameter("hv", "HV", ["historicalvolatility", "hv"], tradingview="HV@tv-basicstudies", gocharting="HISTVOLATILITY"),
			Parameter("hull", "Hull MA", ["hull", "hma"], tradingview="hullMA@tv-basicstudies", gocharting="HULL"),
			Parameter("ichimoku", "Ichimoku Cloud", ["ichimoku", "cloud", "ichi", "ic"], tradingview="IchimokuCloud@tv-basicstudies", gocharting="ICHIMOKU"),
			Parameter("imi", "Intraday Momentum Index", ["intradaymomentumindex", "imi", "intradaymi"], gocharting="INTRADAYMI", dynamic={"GoCharting": [20]}),
			Parameter("keltner", "KLTNR", ["keltner", "kltnr"], tradingview="KLTNR@tv-basicstudies", gocharting="KELTNERCHANNEL", dynamic={"GoCharting": [14, 2]}),
			Parameter("klinger", "Klinger", ["klinger"], gocharting="KLINGER"),
			Parameter("kst", "Know Sure Thing", ["knowsurething", "kst"], gocharting="KST"),
			Parameter("kst", "KST", ["kst"], tradingview="KST@tv-basicstudies"),
			Parameter("llv", "Lowest Low Volume", ["llv", "lowestlowvolume"], gocharting="LLV", dynamic={"GoCharting": [20]}),
			Parameter("regression", "Linear Regression", ["regression", "lr"], tradingview="LinearRegression@tv-basicstudies"),
			Parameter("macd", "MACD", ["macd"], tradingview="MACD@tv-basicstudies", gocharting="MACD"),
			Parameter("massindex", "Mass Index", ["massindex", "mi"], gocharting="MASSINDEX"),
			Parameter("medianprice", "Median Price", ["medianprice", "mp"], gocharting="MP", dynamic={"GoCharting": [20]}),
			Parameter("mom", "Momentum", ["mom", "momentum"], tradingview="MOM@tv-basicstudies", gocharting="MOMENTUMINDICATOR", dynamic={"GoCharting": [20]}),
			Parameter("morningdojistarpattern", "Morning Doji Star Pattern", ["morningdojistar", "mds"], gocharting="MORNINGDOJISTAR"),
			Parameter("morningstarpattern", "Morning Star Pattern", ["morningstar", "ms"], gocharting="MORNINGSTAR"),
			Parameter("mf", "Money Flow", ["mf", "mfi"], tradingview="MF@tv-basicstudies", gocharting="MONEYFLOWINDEX", dynamic={"GoCharting": [14, 20, 80]}),
			Parameter("moon", "Moon Phases", ["moon"], tradingview="MoonPhases@tv-basicstudies", gocharting="MOONPHASE"),
			Parameter("ma", "Moving Average", ["ma", "sma"], tradingview="MASimple@tv-basicstudies", gocharting="SMA", dynamic={"GoCharting": [20]}),
			Parameter("maenvelope", "Moving Average Envelope", ["maenvelope", "mae"], gocharting="MAENVELOPE", dynamic={"GoCharting": [14, 2]}),
			Parameter("nvi", "Negative Volume Index", ["nvi", "negvolindex", "negativevolumeindex"], gocharting="NEGVOLINDEX"),
			Parameter("obv", "On Balance Volume", ["obv"], tradingview="OBV@tv-basicstudies", gocharting="ONBALANCEVOLUME", dynamic={"GoCharting": [20]}),
			Parameter("parabolic", "PSAR", ["parabolic", "sar", "psar"], tradingview="PSAR@tv-basicstudies", gocharting="SAR"),
			Parameter("performanceindex", "Performance Index", ["performanceindex", "pi"], gocharting="PERFORMANCEINDEX"),
			Parameter("pgo", "Pretty Good Oscillator", ["prettygoodoscillator", "pgo"], gocharting="PRETTYGOODOSCILLATOR", dynamic={"GoCharting": [20]}),
			Parameter("piercinglinepattern", "Piercing Line Pattern", ["piercingline", "pl"], gocharting="PIERCINGLINE"),
			Parameter("pmo", "Price Momentum Oscillator", ["pmo", "pricemomentum"], gocharting="PMO"),
			Parameter("po", "Price Oscillator", ["po", "price"], tradingview="PriceOsc@tv-basicstudies", gocharting="PRICEOSCILLATOR"),
			Parameter("pphl", "Pivot Points High Low", ["pphl"], tradingview="PivotPointsHighLow@tv-basicstudies"),
			Parameter("pps", "Pivot Points Standard", ["pps", "pivot"], tradingview="PivotPointsStandard@tv-basicstudies", gocharting="PIVOTPOINTS"),
			Parameter("primenumberbands", "Prime Number Bands", ["primenumberbands", "pnb"], gocharting="PRIMENUMBERBANDS", dynamic={"GoCharting": [14, 2]}),
			Parameter("primenumberoscillator", "Prime Number Oscillator", ["primenumberoscillator", "pno"], gocharting="PRIMENUMBEROSCILLATOR"),
			Parameter("psychologicalline", "Psychological Line", ["psychologicalline", "psy", "psychological"], gocharting="PSY", dynamic={"GoCharting": [20]}),
			Parameter("pvi", "Positive Volume Index", ["pvi", "positivevolumeindex", "posvolindex"], gocharting="POSVOLINDEX"),
			Parameter("pvt", "Price Volume Trend", ["pvt"], tradingview="PriceVolumeTrend@tv-basicstudies"),
			Parameter("qstickindicator", "Qstick Indicator", ["qstickindicator", "qi", "qsticks"], gocharting="QSTICKS", dynamic={"GoCharting": [20]}),
			Parameter("randomwalk", "Random Walk", ["randomwalk", "ra"], gocharting="RANDOMWALK", dynamic={"GoCharting": [20]}),
			Parameter("ravi", "Ravi Oscillator", ["ravi"], gocharting="RAVI"),
			Parameter("rvi", "Relative Volatility", ["rvi"], gocharting="RELATIVEVOLATILITY"),
			Parameter("roc", "Price ROC", ["roc", "priceroc", "proc"], tradingview="ROC@tv-basicstudies", gocharting="PRICEROC", dynamic={"GoCharting": [20]}),
			Parameter("rsi", "RSI", ["rsi"], tradingview="RSI@tv-basicstudies", gocharting="RSI", dynamic={"GoCharting": [14, 20, 80]}),
			Parameter("schaff", "Schaff", ["schaff"], gocharting="SCHAFF"),
			Parameter("shinohara", "Shinohara", ["shinohara", "shin"], gocharting="SHINOHARA", dynamic={"GoCharting": [20]}),
			Parameter("shootingstarpattern", "Shooting Star Pattern", ["shootingstar", "ss"], gocharting="SHOOTINGSTAR"),
			Parameter("smiei", "SMI Ergodic Indicator", ["smiei"], tradingview="SMIErgodicIndicator@tv-basicstudies"),
			Parameter("smieo", "SMI Ergodic Oscillator", ["smieo"], tradingview="SMIErgodicOscillator@tv-basicstudies"),
			Parameter("stdev", "Standard Deviation", ["stdev", "stddev", "standarddeviation"], gocharting="SD"),
			Parameter("stochastic", "Stochastic", ["stochastic", "stoch"], tradingview="Stochastic@tv-basicstudies"),
			Parameter("stolleraveragerangechannelbands", "Stoller Average Range Channel Bands", ["stolleraveragerange", "sarc", "sarcb"], gocharting="STARCBAND", dynamic={"GoCharting": [14, 2]}),
			Parameter("srsi", "Stochastic RSI", ["srsi"], tradingview="StochasticRSI@tv-basicstudies"),
			Parameter("supertrend", "Supertrend", ["supertrend"], gocharting="SUPERTREND", dynamic={"GoCharting": [14, 2]}),
			Parameter("swing", "Swing Index", ["swing", "swingindex", "si"], gocharting="SWINGINDEX"),
			Parameter("tema", "Triple EMA", ["tema", "3ema"], tradingview="TripleEMA@tv-basicstudies", gocharting="TEMA", dynamic={"GoCharting": [20]}),
			Parameter("tpo", "Market Profile", ["tpo", "marketprofile"], gocharting="MARKETPROFILE"),
			Parameter("trix", "Triple Exponential Average", ["trix", "txa", "texa"], tradingview="Trix@tv-basicstudies", gocharting="TRIX", dynamic={"GoCharting": [20]}),
			Parameter("ts", "Time Series Moving Average", ["timeseriesmovingaverage", "ts"], gocharting="TS", dynamic={"GoCharting": [20]}),
			Parameter("threeblackcrowspattern", "Three Black Crows Pattern", ["threeblackcrows", "tbc"], gocharting="THREEBLACKCROWS"),
			Parameter("threewhitesoldierspattern", "Three White Soldiers Pattern", ["threewhitesoldiers", "tws"], gocharting="THREEWHITESOLDIERS"),
			Parameter("tradevolumeindex", "Trade Volume Index", ["tradevolumeindex", "tvi"], gocharting="TRADEVOLUMEINDEX", dynamic={"GoCharting": [20]}),
			Parameter("trendintensity", "Trend Intensity", ["trendintensity", "ti"], gocharting="TRENDINTENSITY"),
			Parameter("triangularmovingaverage", "Triangular Moving Average", ["tringularmovingaverage", "trma"], gocharting="TRIANGULAR", dynamic={"GoCharting": [20]}),
			Parameter("tweezerbottompattern", "Tweezer Bottom Pattern", ["tweezerbottom", "tbp"], gocharting="TWEEZERBOTTOM"),
			Parameter("tweezertoppattern", "Tweezer Top Pattern", ["tweezertop", "ttp"], gocharting="TWEEZERTOP"),
			Parameter("tmfi", "Twiggs Money Flow Index", ["tmfi", "twiggsmfi"], gocharting="TWIGGSMONEYFLOWINDEX", dynamic={"GoCharting": [20]}),
			Parameter("typicalprice", "Typical Price", ["typicalprice", "tp"], gocharting="TP", dynamic={"GoCharting": [20]}),
			Parameter("ulcer", "Ulcer Index", ["ulcer", "ulcerindex", "ui"], gocharting="ULCERINDEX", dynamic={"GoCharting": [14, 2]}),
			Parameter("ultimate", "Ultimate Oscillator", ["ultimate", "uo"], tradingview="UltimateOsc@tv-basicstudies"),
			Parameter("vidya", "VIDYA Moving Average", ["vidya"], gocharting="VIDYA", dynamic={"GoCharting": [20]}),
			Parameter("vigor", "Vigor Index", ["vigor", "rvi"], tradingview="VigorIndex@tv-basicstudies"),
			Parameter("vma", "Variable Moving Average", ["vma", "variablema", "varma"], gocharting="VMA", dynamic={"GoCharting": [20]}),
			Parameter("volatility", "Volatility Index", ["volatility", "vi"], tradingview="VolatilityIndex@tv-basicstudies"),
			Parameter("volumeoscillator", "Volume Oscillator", ["volosc", "volumeoscillator"], gocharting="VOLUMEOSCILLATOR"),
			Parameter("volumeprofile", "Volume Profile", ["volumeprofile"], gocharting="VOLUMEPROFILE"),
			Parameter("volumeroc", "Volume ROC", ["vroc", "volumeroc"], gocharting="VOLUMEROC", dynamic={"GoCharting": [20]}),
			Parameter("volumeunderlay", "Volume Underlay", ["volund", "volumeunderlay"], gocharting="VOLUMEUNDERLAY", dynamic={"GoCharting": [20]}),
			Parameter("vortex", "Vortex", ["vortex"], gocharting="VORTEX", dynamic={"GoCharting": [20]}),
			Parameter("vstop", "VSTOP", ["vstop"], tradingview="VSTOP@tv-basicstudies"),
			Parameter("vwap", "VWAP", ["vwap"], tradingview="VWAP@tv-basicstudies", gocharting="VWAP"),
			Parameter("vwma", "VWMA", ["mavw", "vw", "vwma"], tradingview="MAVolumeWeighted@tv-basicstudies", dynamic={"GoCharting": [20]}),
			Parameter("weightedclose", "Weighted Close", ["weightedclose"], gocharting="WC", dynamic={"GoCharting": [20]}),
			Parameter("williamsr", "Williams %R", ["williamsr", "wr"], tradingview="WilliamR@tv-basicstudies", gocharting="WILLIAMSR", dynamic={"GoCharting": [14, 20, 80]}),
			Parameter("williamsa", "Williams Alligator", ["williamsa", "williamsalligator", "wa"], tradingview="WilliamsAlligator@tv-basicstudies"),
			Parameter("williamsf", "Williams Fractal", ["williamsf", "williamsfractal", "wf"], tradingview="WilliamsFractal@tv-basicstudies"),
			Parameter("wma", "Weighted Moving Average", ["wma"], tradingview="MAWeighted@tv-basicstudies", gocharting="WMA"),
			Parameter("zz", "Zig Zag", ["zz", "zigzag"], tradingview="ZigZag@tv-basicstudies", gocharting="ZIGZAG"),
		],
		"chartStyle": [
			Parameter("ta", "advanced TA", ["ta", "advanced"], finviz="&ta=1"),
			Parameter("nv", "no volume", ["hv", "nv", "novol"], tradingview="&hidevolume=1"),
			Parameter("np", "no price", ["hp", "np", "nopri"], gocharting="&showmainchart=false"),
			Parameter("theme", "light theme", ["light", "white"], tradingview="&theme=light", gocharting="&theme=light"),
			Parameter("theme", "dark theme", ["dark", "black"], tradingview="&theme=dark", gocharting="&theme=dark"),
			Parameter("candleStyle", "bars", ["bars", "bar"], tradingview="&style=0"),
			Parameter("candleStyle", "candles", ["candles", "candle"], tradingview="&style=1", gocharting="&charttype=CANDLESTICK", finviz="&ty=c"),
			Parameter("candleStyle", "hollow candles", ["hollow"], gocharting="&charttype=HOLLOW_CANDLESTICK"),
			Parameter("candleStyle", "heikin ashi", ["heikin", "heiken", "heikinashi", "heikenashi", "ashi", "ha"], tradingview="&style=8", gocharting="&charttype=HEIKIN_ASHI"),
			Parameter("candleStyle", "line break", ["break", "linebreak", "lb"], tradingview="&style=7", gocharting="&charttype=LINEBREAK"),
			Parameter("candleStyle", "line", ["line"], tradingview="&style=2", gocharting="&charttype=LINE", finviz="&ty=l"),
			Parameter("candleStyle", "area", ["area"], tradingview="&style=3", gocharting="&charttype=AREA"),
			Parameter("candleStyle", "renko", ["renko"], tradingview="&style=4", gocharting="&charttype=RENKO"),
			Parameter("candleStyle", "kagi", ["kagi"], tradingview="&style=5", gocharting="&charttype=KAGI"),
			Parameter("candleStyle", "point&figure", ["point", "figure", "pf", "paf"], tradingview="&style=6", gocharting="&charttype=POINT_FIGURE")
		],
		"imageStyle": [
			Parameter("theme", "light theme", ["light", "white"], tradinglite="light", finviz="light"),
			Parameter("theme", "dark theme", ["dark", "black"], tradinglite="dark", finviz="dark"),
			Parameter("log", "log", ["log", "logarithmic"], tradingview="log"),
			Parameter("wide", "wide", ["wide"], tradinglite="wide", tradingview="wide", bookmap="wide", gocharting="wide"),
			Parameter("link", "link", ["link"], tradinglite="link", tradingview="link", bookmap="link", gocharting="link", finviz="link"),
			Parameter("flowlist", "list", ["list", "old", "legacy"], alphaflow="flowlist"),
			Parameter("force", "force", ["--force"], tradinglite="force", tradingview="force", bookmap="force", gocharting="force", finviz="force", alternativeme="force", woobull="force", alphaflow="force"),
			Parameter("upload", "upload", ["--upload"], tradinglite="upload", tradingview="upload", bookmap="upload", gocharting="upload", finviz="upload", alternativeme="upload", woobull="upload", alphaflow="upload")
		],
		"filters": [
			Parameter("heatmapIntensity", "whales heatmap intensity", ["whale", "whales"], tradinglite=(50,100)),
			Parameter("heatmapIntensity", "low heatmap intensity", ["low"], tradinglite=(10,100)),
			Parameter("heatmapIntensity", "medium heatmap intensity", ["medium", "med"], tradinglite=(0,62)),
			Parameter("heatmapIntensity", "high heatmap intensity", ["high"], tradinglite=(0,39)),
			Parameter("heatmapIntensity", "crazy heatmap intensity", ["crazy"], tradinglite=(0,16)),
			Parameter("autoDeleteOverride", "autodelete", ["del", "delete", "autodelete"], tradinglite=True, tradingview=True, bookmap=True, gocharting=True, finviz=True, alternativeme=True, woobull=True, alphaflow=True),
			Parameter("forcePlatform", "Force chart on TradingLite", ["tl", "tradinglite"], tradinglite=True),
			Parameter("forcePlatform", "Force chart on TradingView", ["tv", "tradingview"], tradingview=True),
			Parameter("forcePlatform", "Force chart on Bookmap", ["bm", "bookmap"], bookmap=True),
			Parameter("forcePlatform", "Force chart on GoCharting", ["gc", "gocharting"], gocharting=True),
			Parameter("forcePlatform", "Force chart on Finviz", ["fv", "finviz"], finviz=True),
			Parameter("forcePlatform", "Force chart on Alternative.me", ["am", "alternativeme"], alternativeme=True),
			Parameter("forcePlatform", "Force chart on Woobull", ["wb", "woobull"], woobull=True)
		]
	}

	def __init__(self, tickerId, platform, bias):
		self.ticker = Ticker(tickerId)
		self.exchange = None
		self.parserBias = bias

		self.timeframes = []
		self.indicators = []
		self.chartStyle = []
		self.imageStyle = []
		self.filters = []
		self.numericalParameters = []

		self.platform = platform
		self.currentTimeframe = None
		self.hasExchange = False
		self.hasTimeframeRange = False

		self.requiresPro = False
		self.canCache = platform not in []

		self.errors = []
		self.errorIsFatal = False
		self.shouldFail = False

		self.__defaultParameters = {
			"Alternative.me": {
				"timeframes": [Parameter(None, None, None)],
				"indicators": [],
				"chartStyle": [],
				"imageStyle": [],
				"filters": []
			},
			"Woobull Charts": {
				"timeframes": [Parameter(None, None, None)],
				"indicators": [],
				"chartStyle": [],
				"imageStyle": [],
				"filters": []
			},
			"TradingLite": {
				"timeframes": [self.find_parameter_with_id(60, type="timeframes")],
				"indicators": [],
				"chartStyle": [],
				"imageStyle": [self.find_parameter_with_id("theme", name="dark theme", type="imageStyle")],
				"filters": []
			},
			"TradingView": {
				"timeframes": [self.find_parameter_with_id(60, type="timeframes")],
				"indicators": [],
				"chartStyle": [self.find_parameter_with_id("theme", name="dark theme", type="chartStyle"), self.find_parameter_with_id("candleStyle", name="candles", type="chartStyle")],
				"imageStyle": [],
				"filters": []
			},
			"Bookmap": {
				"timeframes": [self.find_parameter_with_id(60, type="timeframes")],
				"indicators": [],
				"chartStyle": [],
				"imageStyle": [],
				"filters": []
			},
			"GoCharting": {
				"timeframes": [self.find_parameter_with_id(60, type="timeframes")],
				"indicators": [],
				"chartStyle": [self.find_parameter_with_id("theme", name="dark theme", type="chartStyle"), self.find_parameter_with_id("candleStyle", name="candles", type="chartStyle")],
				"imageStyle": [],
				"filters": []
			},
			"Finviz": {
				"timeframes": [self.find_parameter_with_id(1440, type="timeframes")],
				"indicators": [],
				"chartStyle": [self.find_parameter_with_id("candleStyle", name="candles", type="chartStyle")],
				"imageStyle": [self.find_parameter_with_id("theme", name="light theme", type="imageStyle")],
				"filters": []
			},
			"Alpha Flow": {
				"timeframes": [],
				"indicators": [],
				"chartStyle": [],
				"imageStyle": [],
				"filters": []
			}
		}

		self.specialTickerTriggers = []
		if len(self.ticker.parts) > 1 and self.platform not in ["TradingView"]:
			self.set_error("Aggregated tickers are only available on TradingView.", isFatal=True)

	def __hash__(self):
		h1 = sorted([e.name for e in self.indicators])
		h2 = sorted([e.name for e in self.chartStyle])
		h3 = sorted([e.name for e in self.imageStyle])
		h4 = sorted([e.name for e in self.filters])
		return hash("{}{}{}{}{}{}{}{}{}{}".format(self.ticker, self.exchange, self.currentTimeframe, h1, h2, h3, h4, self.numericalParameters, self.platform, self.requiresPro))

	def process_ticker(self, defaults, bias):
		for i in range(len(self.ticker.parts)):
			tickerPart = self.ticker.parts[i]
			if type(tickerPart) is str: continue
			updatedTicker, updatedExchange = TickerParser.process_known_tickers(tickerPart, self.exchange, self.platform, defaults, bias)
			if updatedTicker is not None:
				self.ticker.parts[i] = updatedTicker
				if len(self.ticker.parts) == 1: self.exchange = updatedExchange
			else:
				self.shouldFail = True
		self.ticker.update_ticker_id()

		for trigger in self.specialTickerTriggers:
			if trigger == "longs":
				for i in range(len(self.ticker.parts)):
					part = self.ticker.parts[i]
					if type(part) is str: continue
					self.ticker.parts[i] = Ticker("{}LONGS".format(part.id), "{} Longs".format(part.name), part.base, part.base, part.symbol, hasParts=False)
				self.ticker.update_ticker_id()
			elif trigger == "shorts":
				for i in range(len(self.ticker.parts)):
					part = self.ticker.parts[i]
					if type(part) is str: continue
					self.ticker.parts[i] = Ticker("{}SHORTS".format(part.id), "{} Shorts".format(part.name), part.base, part.base, part.symbol, hasParts=False)
				self.ticker.update_ticker_id()
			elif trigger == "ls":
				self.ticker = Ticker("({}LONGS/({}LONGS+{}SHORTS))".format(self.ticker.id, self.ticker.id, self.ticker.id), "{} Longs/Shorts".format(self.ticker.name), None, "%", None)
			elif trigger == "sl":
				self.ticker = Ticker("({}SHORTS/({}LONGS+{}SHORTS))".format(self.ticker.id, self.ticker.id, self.ticker.id), "{} Shorts/Longs".format(self.ticker.name), None, "%", None)

	def add_parameter(self, argument, type):
		isSupported = None
		parsedParameter = None
		for param in ChartRequest.requestParameters[type]:
			if argument in param.parsablePhrases:
				parsedParameter = param
				isSupported = param.supports(self.platform)
				if isSupported:
					self.requiresPro = self.requiresPro or param.requiresPro
					break
		return isSupported, parsedParameter

	def add_timeframe(self, argument):
		timeframeSupported, parsedTimeframe = self.add_parameter(argument, "timeframes")
		if parsedTimeframe is not None and not self.has_parameter(parsedTimeframe.id, self.timeframes):
			if not timeframeSupported:
				outputMessage = "`{}` timeframe is not supported on {}.".format(argument, self.platform)
				return outputMessage, False
			self.timeframes.append(parsedTimeframe)
			return None, True
		return None, None

	def add_timeframe_range(self, argument):
		if len(argument.split("-")) != 2 or self.hasTimeframeRange: return None, False
		startEnd = [[argument.split("-")[0], 0], [argument.split("-")[1], 0]]
		isReversed = False

		for timeframe in self.requestParameters["timeframes"]:
			if timeframe.supports(self.platform):
				if startEnd[0][0] in timeframe.parsablePhrases and startEnd[0][1] == 0:
					startEnd[0][1] = timeframe.id
				if startEnd[1][0] in timeframe.parsablePhrases and startEnd[1][1] == 0:
					startEnd[1][1] = timeframe.id
					if startEnd[0][1] == 0: isReversed = True

		if isReversed: startEnd[0], startEnd[1] = startEnd[1], startEnd[0]

		if startEnd[0][1] < 60 and startEnd[1][1] > 60:
			if startEnd[0][0] in ["1"]:
				startEnd[0][1] = startEnd[0][1] * 60

		if startEnd[0][1] != 0 and startEnd[1][1] != 0:
			timeframes = []
			for timeframe in self.requestParameters["timeframes"]:
				if timeframe.supports(self.platform):
					if timeframe.id not in [3, 45, 120, 180, 360, 480, 720] or any(e in [3, 45, 120, 180, 360, 480, 720] for e in [startEnd[0][1], startEnd[1][1]]):
						if startEnd[0][1] <= timeframe.id <= startEnd[1][1]:
							timeframes.append(timeframe.id)
						elif timeframe.id > startEnd[1][1]: break
			if isReversed: timeframes = list(reversed(timeframes))
			timeframes = sorted(timeframes)

			for timeframe in timeframes:
				self.timeframes.append(self.find_parameter_with_id(timeframe, type="timeframes"))
			self.hasTimeframeRange = True
			return None, True

		return "`{}` range is not supported on {}.".format(argument, self.platform), False

	def add_exchange(self, argument):
		exchangeSupported, parsedExchange = TickerParser.find_exchange(argument, self.platform, self.parserBias)
		if parsedExchange is not None and not self.hasExchange:
			if not exchangeSupported:
				outputMessage = "`{}` exchange is not supported by {}.".format(parsedExchange.name, self.platform)
				return outputMessage, False
			self.exchange = parsedExchange
			self.hasExchange = True
			return None, True
		return None, None

	def add_indicator(self, argument):
		if argument in ["oscillator", "bands", "band", "ta"]: return None, False
		length = re.search("(\d+)$", argument)
		if length is not None and int(length.group()) > 0: argument = argument[:-len(length.group())]
		indicatorSupported, parsedIndicator = self.add_parameter(argument, "indicators")
		if parsedIndicator is not None and not self.has_parameter(parsedIndicator.id, self.indicators):
			if not indicatorSupported:
				outputMessage = "`{}` indicator is not supported on {}.".format(parsedIndicator.name, self.platform)
				return outputMessage, False
			self.indicators.append(parsedIndicator)
			self.numericalParameters.append(-1)
			if length is not None:
				if self.platform not in ["GoCharting"]:
					outputMessage = "Indicator lengths can only be changed on GoCharting."
					return outputMessage, False
				else:
					self.numericalParameters.append(int(length.group()))
			return None, True
		return None, None

	def add_chart_style(self, argument):
		chartStyleSupported, parsedChartStyle = self.add_parameter(argument, "chartStyle")
		if parsedChartStyle is not None and not self.has_parameter(parsedChartStyle.id, self.chartStyle):
			if not chartStyleSupported:
				outputMessage = "`{}` chart style is not supported on {}.".format(parsedChartStyle.name.title(), self.platform)
				return outputMessage, False
			self.chartStyle.append(parsedChartStyle)
			return None, True
		return None, None

	def add_image_style(self, argument):
		imageStyleSupported, parsedImageStyle = self.add_parameter(argument, "imageStyle")
		if parsedImageStyle is not None and not self.has_parameter(parsedImageStyle.id, self.imageStyle):
			if not imageStyleSupported:
				outputMessage = "`{}` chart style is not supported on {}.".format(parsedImageStyle.name.title(), self.platform)
				return outputMessage, False
			self.imageStyle.append(parsedImageStyle)
			return None, True
		return None, None

	def add_filters(self, argument):
		if argument in ["goods"]: return None, False
		filterSupported, parsedFilter = self.add_parameter(argument, "filters")
		if parsedFilter is not None and not self.has_parameter(parsedFilter.id, self.filters):
			if not filterSupported:
				outputMessage = "`{}` parameter is not supported on {}.".format(parsedFilter.name.title(), self.platform)
				return outputMessage, False
			self.filters.append(parsedFilter)
			return None, True
		return None, None

	def add_numerical_parameters(self, argument):
		try:
			numericalParameter = float(argument)
			if numericalParameter <= 0:
				outputMessage = "Only parameters greater than `0` are accepted."
				return outputMessage, False
			if self.platform not in ["GoCharting"]:
				outputMessage = "Indicator lengths can only be changed on GoCharting."
				return outputMessage, False
			self.numericalParameters.append(numericalParameter)
			return None, True
		except: return None, None

	def process_special_tickers(self, argument):
		noVolume = self.find_parameter_with_id("nv", type="chartStyle")

		if argument in ["dom", "dominance"]:
			if self.platform == "TradingView" and not self.ticker.id.endswith(".D"):
				for i in range(len(self.ticker.parts)):
					part = self.ticker.parts[i]
					if part.id in Ticker.separators: continue
					if part.id.endswith(("USD", "BTC")): part.id = part.id[:3]
					self.ticker.parts[i] = Ticker(part.id + ".D")
				if noVolume not in self.chartStyle: self.chartStyle.append(noVolume)
				return None, True
			return "Coin dominance charts are only available on TradingView.", False
		elif argument in ["mcap", "mc"]:
			if self.platform == "TradingView" and not self.ticker.id.startswith("CRYPTOCAP:"):
				for i in range(len(self.ticker.parts)):
					part = self.ticker.parts[i]
					if part.id in Ticker.separators: continue
					if part.id.endswith(("USD", "BTC")): part.id = part.id[:3]
					self.ticker.parts[i] = Ticker("CRYPTOCAP:" + part.id)
				self.ticker.update_ticker_id()
				if noVolume not in self.chartStyle: self.chartStyle.append(noVolume)
				return None, True
			return "Coin market capitalization charts are only available on TradingView.", False
		elif argument in ["longs", "long", "l"]:
			if self.platform == "TradingView" and not self.ticker.id.endswith(("LONGS", "SHORTS")):
				self.specialTickerTriggers.append("longs")
				if not self.hasExchange: self.exchange = TickerParser.find_exchange("bitfinex", self.platform, self.parserBias)[1]
				if noVolume not in self.chartStyle: self.chartStyle.append(noVolume)
				return None, True
			return "Bitfinex longs charts are only available on TradingView.", False
		elif argument in ["shorts", "short", "s"]:
			if self.platform == "TradingView" and not self.ticker.id.endswith(("LONGS", "SHORTS")):
				self.specialTickerTriggers.append("shorts")
				if not self.hasExchange: self.exchange = TickerParser.find_exchange("bitfinex", self.platform, self.parserBias)[1]
				if noVolume not in self.chartStyle: self.chartStyle.append(noVolume)
				return None, True
			return "Bitfinex shorts charts are only available on TradingView.", False
		elif argument in ["longs/shorts", "l/s", "ls"]:
			if self.platform == "TradingView" and len(self.ticker.parts) == 1:
				self.specialTickerTriggers.append("ls")
				if not self.hasExchange: self.exchange = TickerParser.find_exchange("bitfinex", self.platform, self.parserBias)[1]
				if noVolume not in self.chartStyle: self.chartStyle.append(noVolume)
				return None, True
			return "Bitfinex longs/shorts charts are only available on TradingView.", False
		elif argument in ["shorts/longs", "s/l", "sl"]:
			if self.platform == "TradingView" and len(self.ticker.parts) == 1:
				self.specialTickerTriggers.append("sl")
				if not self.hasExchange: self.exchange = TickerParser.find_exchange("bitfinex", self.platform, self.parserBias)[1]
				if noVolume not in self.chartStyle: self.chartStyle.append(noVolume)
				return None, True
			return "Bitfinex shorts/longs charts are only available on TradingView.", False

		return None, None

	def set_default_for(self, type):
		if type == "timeframes" and len(self.timeframes) == 0:
			for parameter in self.__defaultParameters[self.platform][type]:
				if not self.has_parameter(parameter.id, self.timeframes): self.timeframes.append(parameter)
		elif type == "indicators":
			for parameter in self.__defaultParameters[self.platform][type]:
				if not self.has_parameter(parameter.id, self.indicators): self.indicators.append(parameter)
		elif type == "chartStyle":
			for parameter in self.__defaultParameters[self.platform][type]:
				if not self.has_parameter(parameter.id, self.chartStyle): self.chartStyle.append(parameter)
		elif type == "imageStyle":
			for parameter in self.__defaultParameters[self.platform][type]:
				if not self.has_parameter(parameter.id, self.imageStyle): self.imageStyle.append(parameter)
		elif type == "filters":
			for parameter in self.__defaultParameters[self.platform][type]:
				if not self.has_parameter(parameter.id, self.filters): self.filters.append(parameter)

	def find_parameter_with_id(self, id, name=None, type=None):
		for t in (self.requestParameters.keys() if type is None else [type]):
			for parameter in self.requestParameters[t]:
				if id == parameter.id and (name is None or parameter.name == name):
					return parameter
		return None

	def is_parameter_present(self, id, argument):
		return self.has_parameter(id, self.timeframes + self.indicators + self.chartStyle + self.imageStyle + self.filters, argument)

	def has_parameter(self, id, list, argument=None):
		for e in list:
			if e.id == id and (argument is None or e.parsed[self.platform] == argument): return True
		return False

	def set_error(self, error, isFatal=False):
		if len(self.errors) > 0 and self.errors[0] is None: return
		self.errorIsFatal = isFatal
		self.errors.insert(0, error)
