import sys
import urllib
import time

from TickerParser import TickerParser
from .parameter import PriceParameter as Parameter
from TickerParser import Ticker


class PriceRequestHandler(object):
	def __init__(self, accountId, authorId, tickerId, platforms, isPro=False, messageRequest=None, **kwargs):
		self.accountId = accountId
		self.authorId = authorId
		self.timestamp = time.time()
		self.hash = "P{}{}".format(int(time.time() * 1000), authorId)
		self.platforms = platforms
		self.defaults = {"exchange": None} if messageRequest is None else messageRequest.guildProperties["settings"]["charts"]["defaults"]
		self.parserBias = "traditional" if messageRequest is None else messageRequest.guildProperties["settings"]["messageProcessing"]["bias"]
		
		self.isDelayed = not isPro
		self.isMarketAlert = kwargs.get("isMarketAlert", False)

		self.currentPlatform = self.platforms[0]

		self.requests = {}
		for platform in self.platforms:
			self.requests[platform] = PriceRequest(tickerId, platform)

	def parse_argument(self, argument):
		for platform, request in self.requests.items():
			if request.errorIsFatal: continue

			# None None - No successeful parse
			# None True - Successful parse and add
			# "" False - Successful parse and error

			finalOutput = None

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

		outputMessage = None if currentMinimumErrors == 0 else (preferredRequestOrder[0].errors[0] if len(preferredRequestOrder) > 0 else "Requested quote is not available.")
		return outputMessage

	def set_current(self, platform=None, timeframe=None):
		if platform is not None: self.currentPlatform = platform
		if timeframe is not None:
			for platform in self.requests:
				self.requests[platform].currentTimeframe = timeframe

	def build_url(self, addMessageUrl=False):
		requestUrl, messageUrl = None, None
		if self.currentPlatform == "Alternative.me": requestUrl = "https://api.alternative.me/fng/?limit=2&format=json"
		elif self.currentPlatform == "LLD": pass
		elif self.currentPlatform == "CoinGecko": pass
		elif self.currentPlatform == "CCXT": pass
		elif self.currentPlatform == "IEXC": pass
		elif self.currentPlatform == "Quandl": pass

		return requestUrl, messageUrl

	def set_defaults(self):
		for platform, request in self.requests.items():
			if request.errorIsFatal: continue
			for type in request.requestParameters:
				request.set_default_for(type)

	def find_caveats(self):
		for platform, request in self.requests.items():
			if platform == "Alternative.me":
				if request.ticker.id not in ["FGI"]:
					request.set_error(None, isFatal=True)
				if self.isMarketAlert:
					if len(request.filters) > 1: request.set_error("Only one alert type can be specified at once.")
					elif len(request.numericalParameters) > 1: request.set_error("Only one alert trigger level can be specified at once.")
					elif len(request.numericalParameters) == 0: request.set_error("Alert trigger level was not provided.")
				else:
					if len(request.numericalParameters) > 0: request.set_error("Only Alpha Price Alerts accept numerical parameters.".format(request.ticker.id), isFatal=True)
			elif platform == "LLD":
				if request.ticker.id in ["MCAP"] and request.exchange is not None:
					request.set_error(None, isFatal=True)
				if self.isMarketAlert:
					if len(request.filters) > 1: request.set_error("Only one alert type can be specified at once.")
					elif len(request.numericalParameters) > 1: request.set_error("Only one alert trigger level can be specified at once.")
					elif len(request.numericalParameters) == 0: request.set_error("Alert trigger level was not provided.")
				else:
					if len(request.numericalParameters) > 0: request.set_error("Only Alpha Price Alerts accept numerical parameters.".format(request.ticker.id), isFatal=True)
			elif platform == "CoinGecko":
				if request.exchange is not None or (self.get_ticker_for("CCXT") is not None and self.get_ticker_for("CCXT").is_ranked_higher(request.ticker)):
					request.set_error(None, isFatal=True)
				if self.isMarketAlert:
					if len(request.filters) > 1: request.set_error("Only one alert type can be specified at once.")
					elif len(request.numericalParameters) > 1: request.set_error("Only one alert trigger level can be specified at once.")
					elif len(request.numericalParameters) == 0: request.set_error("Alert trigger level was not provided.")
				else:
					if len(request.numericalParameters) > 0: request.set_error("Only Alpha Price Alerts accept numerical parameters.".format(request.ticker.id), isFatal=True)
			elif platform == "CCXT":
				if request.exchange is None:
					request.set_error("Requested price for `{}` is not available.".format(request.ticker.id), isFatal=True)
				if self.isMarketAlert:
					if len(request.filters) > 1: request.set_error("Only one alert type can be specified at once.")
					elif len(request.numericalParameters) > 1: request.set_error("Only one alert trigger level can be specified at once.")
					elif len(request.numericalParameters) == 0: request.set_error("Alert trigger level was not provided.")
				else:
					if len(request.numericalParameters) > 0: request.set_error("Only Alpha Price Alerts accept numerical parameters.".format(request.ticker.id), isFatal=True)
			elif platform == "IEXC":
				if self.isMarketAlert:
					if len(request.filters) > 1: request.set_error("Only one alert type can be specified at once.")
					elif len(request.numericalParameters) > 1: request.set_error("Only one alert trigger level can be specified at once.")
					elif len(request.numericalParameters) == 0: request.set_error("Alert trigger level was not provided.")
				else:
					if len(request.numericalParameters) > 0: request.set_error("Only Alpha Price Alerts accept numerical parameters.".format(request.ticker.id), isFatal=True)
			elif platform == "Quandl":
				if self.isMarketAlert:
					if len(request.filters) > 1: request.set_error("Only one alert type can be specified at once.")
					elif len(request.numericalParameters) > 1: request.set_error("Only one alert trigger level can be specified at once.")
					elif len(request.numericalParameters) == 0: request.set_error("Alert trigger level was not provided.")
				else:
					if len(request.numericalParameters) > 0: request.set_error("Only Alpha Price Alerts accept numerical parameters.".format(request.ticker.id), isFatal=True)

	def requires_pro(self):
		return self.requests[self.currentPlatform].requiresPro

	def get_ticker(self): return self.get_ticker_for(self.currentPlatform)

	def get_exchange(self): return self.get_exchange_for(self.currentPlatform)

	def get_filters(self): return self.get_filters_for(self.currentPlatform)

	def get_numerical_parameters(self): return self.get_numerical_parameters_for(self.currentPlatform)

	def find_parameter_in_list(self, id, list, default=""): return self.find_parameter_in_list_for(id, list, self.currentPlatform, default)


	def get_ticker_for(self, platform):
		if platform not in self.requests: return None
		ticker = self.requests[platform].ticker

		if platform == "Alternative.me": pass
		elif platform == "LLD": pass
		elif platform == "CoinGecko": pass
		elif platform == "CCXT": pass
		elif platform == "IEXC": pass
		elif platform == "Quandl": pass

		return ticker

	def get_exchange_for(self, platform):
		if platform not in self.requests: return None
		exchange = self.requests[platform].exchange

		if platform == "Alternative.me": pass
		elif platform == "LLD": pass
		elif platform == "CoinGecko": pass
		elif platform == "CCXT": pass
		elif platform == "IEXC": pass
		elif platform == "Quandl": pass

		return exchange

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
		return "<Request: {}, {}>".format(self.get_ticker(), self.get_exchange())


class PriceRequest(object):
	requestParameters = {
		"filters": [
			Parameter("lld", "funding", ["fun", "fund", "funding"], lld="funding"),
			Parameter("lld", "open interest", ["oi", "openinterest", "ov", "openvalue"], lld="oi"),
			Parameter("lld", "longs/shorts ratio", ["ls", "l/s", "longs/shorts", "long/short"], lld="ls"),
			Parameter("lld", "shorts/longs ratio", ["sl", "s/l", "shorts/longs", "short/long"], lld="sl"),
			Parameter("lld", "dominance", ["dom", "dominance"], lld="dom"),
			Parameter("autoDeleteOverride", "autodelete", ["del", "delete", "autodelete"], coingecko=True, ccxt=True, iexc=True, quandl=True, alternativeme=True, lld=True)
		]
	}

	def __init__(self, tickerId, platform):
		self.ticker = Ticker(tickerId)
		self.exchange = None
		self.filters = []
		self.numericalParameters = []

		self.platform = platform
		self.hasExchange = False

		self.requiresPro = False
		self.canCache = platform not in []

		self.errors = []
		self.errorIsFatal = False

		self.__defaultParameters = {
			"Alternative.me": {
				"filters": []
			},
			"LLD": {
				"filters": []
			},
			"CoinGecko": {
				"filters": []
			},
			"CCXT": {
				"filters": []
			},
			"IEXC": {
				"filters": []
			},
			"Quandl": {
				"filters": []
			}
		}

		self.specialTickerTriggers = []
		if self.ticker.isAggregatedTicker and self.platform not in []:
			self.set_error("Aggregated tickers are not supported.", isFatal=True)

	def __hash__(self):
		h1 = sorted([e.name for e in self.filters])
		return hash("{}{}{}{}{}{}".format(hash(self.ticker), hash(self.exchange), h1, self.numericalParameters, self.platform, self.requiresPro))

	def process_ticker(self, defaults, bias):
		filters = [e.parsed[self.platform] for e in self.filters]
		if any([e in filters for e in ["funding", "oi"]]):
			if not self.hasExchange: self.exchange = TickerParser.find_exchange("bitmex", self.platform)[1]
		elif any([e in filters for e in ["ls", "sl"]]):
			if not self.hasExchange: self.exchange = TickerParser.find_exchange("bitfinex", self.platform)[1]

		for i in range(len(self.ticker.parts)):
			part = self.ticker.parts[i]
			if type(part) is str: continue
			updatedTicker, updatedExchange = TickerParser.process_known_tickers(part, self.exchange, self.platform, defaults, bias)
			if updatedTicker is not None:
				self.ticker.parts[i] = updatedTicker
				if not self.ticker.isAggregatedTicker: self.exchange = updatedExchange
		self.ticker.update_ticker_id()

	def add_parameter(self, argument, type):
		isSupported = None
		parsedParameter = None
		for param in PriceRequest.requestParameters[type]:
			if argument in param.parsablePhrases:
				parsedParameter = param
				isSupported = param.supports(self.platform)
				if isSupported:
					self.requiresPro = self.requiresPro or param.requiresPro
					break
		return isSupported, parsedParameter

	def add_exchange(self, argument):
		exchangeSupported, parsedExchange = TickerParser.find_exchange(argument, self.platform)
		if parsedExchange is not None and not self.hasExchange:
			if not exchangeSupported:
				outputMessage = "`{}` exchange is not supported by {}.".format(parsedExchange.name, self.platform)
				return outputMessage, False
			self.exchange = parsedExchange
			self.hasExchange = True
			return None, True
		return None, None

	def add_filters(self, argument):
		filterSupported, parsedFilter = self.add_parameter(argument, "filters")
		if parsedFilter is not None and not self.has_parameter(parsedFilter.id, self.filters):
			if not filterSupported:
				outputMessage = "`{}` parameter is not supported by {}.".format(parsedFilter.name.title(), self.platform)
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
			self.numericalParameters.append(numericalParameter)
			return None, True
		except: return None, None

	def process_special_tickers(self, argument):
		return None, None

	def set_default_for(self, type):
		if type == "filters":
			for parameter in self.__defaultParameters[self.platform][type]:
				if not self.has_parameter(parameter.id, self.filters): self.filters.append(parameter)

	def find_parameter_with_id(self, id, name=None, type=None):
		for t in (self.requestParameters.keys() if type is None else [type]):
			for parameter in self.requestParameters[t]:
				if id == parameter.id and (name is None or parameter.name == name):
					return parameter
		return None

	def is_parameter_present(self, id, argument):
		return self.has_parameter(id, self.filters, argument)

	def has_parameter(self, id, list, argument=None):
		for e in list:
			if e.id == id and (argument is None or e.parsed[self.platform] == argument): return True
		return False

	def set_error(self, error, isFatal=False):
		if len(self.errors) > 0 and self.errors[0] is None: return
		self.errorIsFatal = isFatal
		self.errors.insert(0, error)
