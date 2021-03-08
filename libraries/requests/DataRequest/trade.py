import sys
import urllib
import time

from TickerParser import TickerParser
from .parameter import TradeParameter as Parameter
from TickerParser import Ticker


class TradeRequestHandler(object):
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
			self.requests[platform] = TradeRequest(tickerId, platform, self.parserBias)

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
		if self.currentPlatform == "Ichibot": pass

		return requestUrl, messageUrl

	def set_defaults(self):
		for platform, request in self.requests.items():
			if request.errorIsFatal: continue
			for type in request.requestParameters:
				request.set_default_for(type)

	def find_caveats(self):
		for platform, request in self.requests.items():
			if platform == "Alpha Paper Trader":
				if request.ticker.id is not None:
					if len(request.numericalParameters) == 0: request.set_error("Paper trade amount was not provided.")
					elif len(request.numericalParameters) > 2: request.set_error("Too many numerical arguments provided.")
				else:
					if len(request.numericalParameters) != 0: request.set_error("Numerical arguments can't be used with this command.")
			elif platform == "Ichibot":
				pass

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

		if platform == "Alpha Paper Trader": pass
		elif platform == "Ichibot": pass

		return ticker

	def get_exchange_for(self, platform):
		if platform not in self.requests: return None
		exchange = self.requests[platform].exchange

		if platform == "Alpha Paper Trader": pass
		elif platform == "Ichibot": pass

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


class TradeRequest(object):
	requestParameters = {
		"filters": [
			Parameter("isAmountPercent", "percentage amount", ["%"], paper=True),
			Parameter("isPricePercent", "percentage price", ["%"], paper=True),
			Parameter("isLimitOrder", "limit order", ["@", "at"], paper=True),
			Parameter("isReduceOnlyMode", "reduce only option", ["reduce"], paper=True),
			Parameter("autoDeleteOverride", "autodelete", ["del", "delete", "autodelete"], paper=True, ichibot=True)
		]
	}

	def __init__(self, tickerId, platform, bias):
		self.ticker = Ticker(tickerId)
		self.exchange = None
		self.parserBias = bias

		self.filters = []
		self.numericalParameters = []

		self.platform = platform
		self.hasExchange = False

		self.requiresPro = False
		self.canCache = False

		self.errors = []
		self.errorIsFatal = False

		self.__defaultParameters = {
			"Alpha Paper Trader": {
				"filters": []
			},
			"Ichibot": {
				"filters": []
			}
		}

		self.specialTickerTriggers = []
		if self.ticker.isAggregatedTicker and self.platform not in []:
			self.set_error("Aggregated tickers are not supported.", isFatal=True)

	def __hash__(self):
		h1 = sorted([e.name for e in self.filters])
		return hash("{}{}{}{}{}{}".format(self.ticker, self.exchange, h1, self.numericalParameters, self.platform, self.requiresPro))

	def process_ticker(self, defaults, bias):
		filters = [e.parsed[self.platform] for e in self.filters]

		for i in range(len(self.ticker.parts)):
			part = self.ticker.parts[i]
			if type(part) is str: continue
			updatedTicker, updatedExchange = TickerParser.process_known_tickers(part, self.exchange, self.platform, defaults, bias)
			if updatedTicker is not None:
				self.ticker.parts[i] = updatedTicker
				if not self.ticker.isAggregatedTicker: self.exchange = updatedExchange
			else:
				self.shouldFail = True
		self.ticker.update_ticker_id()

	def add_parameter(self, argument, type):
		isSupported = None
		parsedParameter = None
		for param in TradeRequest.requestParameters[type]:
			if argument in param.parsablePhrases:
				parsedParameter = param
				isSupported = param.supports(self.platform)
				if isSupported:
					self.requiresPro = self.requiresPro or param.requiresPro
					break
		return isSupported, parsedParameter

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
