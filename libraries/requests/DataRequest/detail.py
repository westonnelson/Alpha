import sys
import urllib
import time

from TickerParser import TickerParser
from .parameter import DetailParameter as Parameter
from TickerParser import Ticker


class DetailRequestHandler(object):
	def __init__(self, accountId, authorId, tickerId, platforms, isPro=False, messageRequest=None, **kwargs):
		self.accountId = accountId
		self.authorId = authorId
		self.timestamp = time.time()
		self.hash = "P{}{}".format(int(time.time() * 1000), authorId)
		self.platforms = platforms
		self.defaults = {"exchange": None} if messageRequest is None else messageRequest.guildProperties["settings"]["charts"]["defaults"]
		self.parserBias = "traditional" if messageRequest is None else messageRequest.marketBias
		
		self.isDelayed = not isPro
		self.isMarketAlert = kwargs.get("isMarketAlert", False)

		self.currentPlatform = self.platforms[0]

		self.requests = {}
		for platform in self.platforms:
			self.requests[platform] = DetailRequest(tickerId, platform)

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
		if self.currentPlatform == "CoinGecko": pass
		elif self.currentPlatform == "IEXC": pass

		return requestUrl, messageUrl

	def set_defaults(self):
		for platform, request in self.requests.items():
			if request.errorIsFatal: continue
			for type in request.requestParameters:
				request.set_default_for(type)

	def find_caveats(self):
		for platform, request in self.requests.items():
			if platform == "CoinGecko":
				pass
			elif platform == "IEXC":
				pass

	def requires_pro(self):
		return self.requests[self.currentPlatform].requiresPro

	def get_ticker(self): return self.get_ticker_for(self.currentPlatform)

	def get_filters(self): return self.get_filters_for(self.currentPlatform)

	def find_parameter_in_list(self, id, list, default=""): return self.find_parameter_in_list_for(id, list, self.currentPlatform, default)


	def get_ticker_for(self, platform):
		if platform not in self.requests: return None
		ticker = self.requests[platform].ticker

		if platform == "CoinGecko": pass
		elif platform == "IEXC": pass

		return ticker

	def get_filters_for(self, platform):
		if platform not in self.requests: return []
		return self.requests[platform].filters

	def find_parameter_in_list_for(self, id, list, platform, default=""):
		for e in list:
			if e.id == id: return e.parsed[platform]
		return default

	def can_cache(self):
		return self.requests[self.currentPlatform].canCache

	def __str__(self):
		return "<Request: {}, {}>".format(self.get_ticker(), self.get_exchange())


class DetailRequest(object):
	requestParameters = {
		"filters": [
			Parameter("autoDeleteOverride", "autodelete", ["del", "delete", "autodelete"], coingecko=True, iexc=True)
		]
	}

	def __init__(self, tickerId, platform):
		self.ticker = Ticker(tickerId)
		self.filters = []

		self.platform = platform

		self.requiresPro = False
		self.canCache = platform not in []

		self.errors = []
		self.errorIsFatal = False

		self.__defaultParameters = {
			"CoinGecko": {
				"filters": []
			},
			"IEXC": {
				"filters": []
			}
		}

		self.specialTickerTriggers = []
		if self.ticker.isAggregatedTicker and self.platform not in []:
			self.set_error("Aggregated tickers are not supported.", isFatal=True)

	def __hash__(self):
		h1 = sorted([e.name for e in self.filters])
		return hash("{}{}{}{}".format(hash(self.ticker), h1, self.platform, self.requiresPro))

	def process_ticker(self, defaults, bias):
		filters = [e.parsed[self.platform] for e in self.filters]

		for i in range(len(self.ticker.parts)):
			part = self.ticker.parts[i]
			if type(part) is str: continue
			updatedTicker, updatedExchange = TickerParser.process_known_tickers(part, None, self.platform, defaults, bias)
			if updatedTicker is not None:
				self.ticker.parts[i] = updatedTicker
				if not self.ticker.isAggregatedTicker: self.exchange = updatedExchange
		self.ticker.update_ticker_id()

	def add_parameter(self, argument, type):
		isSupported = None
		parsedParameter = None
		for param in DetailRequest.requestParameters[type]:
			if argument in param.parsablePhrases:
				parsedParameter = param
				isSupported = param.supports(self.platform)
				if isSupported:
					self.requiresPro = self.requiresPro or param.requiresPro
					break
		return isSupported, parsedParameter

	def add_filters(self, argument):
		filterSupported, parsedFilter = self.add_parameter(argument, "filters")
		if parsedFilter is not None and not self.has_parameter(parsedFilter.id, self.filters):
			if not filterSupported:
				outputMessage = "`{}` parameter is not supported by {}.".format(parsedFilter.name.title(), self.platform)
				return outputMessage, False
			self.filters.append(parsedFilter)
			return None, True
		return None, None

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
