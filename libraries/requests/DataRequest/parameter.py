class ChartParameter(object):
	def __init__(self, id, name, parsablePhrases, tradinglite=None, tradingview=None, bookmap=None, gocharting=None, finviz=None, alternativeme=None, woobull=None, alphaflow=None, requiresPro=False, dynamic=None):
		self.id = id
		self.name = name
		self.parsablePhrases = parsablePhrases
		self.requiresPro = requiresPro
		self.parsed = {
			"Alternative.me": alternativeme,
			"Woobull Charts": woobull,
			"TradingLite": tradinglite,
			"TradingView": tradingview,
			"Bookmap": bookmap,
			"GoCharting": gocharting,
			"Finviz": finviz,
			"Alpha Flow": alphaflow
		}
		self.dynamic = dynamic

	def supports(self, platform):
		return self.parsed[platform] is not None

	def __supported_platforms(self, findSupported):
		supported = []
		for platform in self.parsed:
			if (self.supports(platform) and findSupported) or (not self.supports(platform) and not findSupported): supported.append(platform)
		return supported

	def supported_platforms(self):
		return self.__supportedPlatforms(True)

	def unsupported_platforms(self):
		return self.__supported_platforms(False)

	def __str__(self):
		return "{} [id: {}]".format(self.name, self.id)

class HeatmapParameter(object):
	def __init__(self, id, name, parsablePhrases, finviz=None, bitgur=None, requiresPro=False):
		self.id = id
		self.name = name
		self.parsablePhrases = parsablePhrases
		self.requiresPro = requiresPro
		self.parsed = {
			"Bitgur": bitgur,
			"Finviz": finviz
		}

	def supports(self, platform):
		return self.parsed[platform] is not None

	def __supported_platforms(self, findSupported):
		supported = []
		for platform in self.parsed:
			if (self.supports(platform) and findSupported) or (not self.supports(platform) and not findSupported): supported.append(platform)
		return supported

	def supported_platforms(self):
		return self.__supportedPlatforms(True)

	def unsupported_platforms(self):
		return self.__supported_platforms(False)

	def __str__(self):
		return "{} [id: {}]".format(self.name, self.id)

class PriceParameter(object):
	def __init__(self, id, name, parsablePhrases, coingecko=None, ccxt=None, iexc=None, quandl=None, alternativeme=None, lld=None, requiresPro=False):
		self.id = id
		self.name = name
		self.parsablePhrases = parsablePhrases
		self.requiresPro = requiresPro
		self.parsed = {
			"Alternative.me": alternativeme,
			"LLD": lld,
			"CoinGecko": coingecko,
			"CCXT": ccxt,
			"IEXC": iexc,
			"Quandl": quandl
		}

	def supports(self, platform):
		return self.parsed[platform] is not None

	def __supported_platforms(self, findSupported):
		supported = []
		for platform in self.parsed:
			if (self.supports(platform) and findSupported) or (not self.supports(platform) and not findSupported): supported.append(platform)
		return supported

	def supported_platforms(self):
		return self.__supportedPlatforms(True)

	def unsupported_platforms(self):
		return self.__supported_platforms(False)

	def __str__(self):
		return "{} [id: {}]".format(self.name, self.id)

class DetailParameter(object):
	def __init__(self, id, name, parsablePhrases, coingecko=None, iexc=None, requiresPro=False):
		self.id = id
		self.name = name
		self.parsablePhrases = parsablePhrases
		self.requiresPro = requiresPro
		self.parsed = {
			"CoinGecko": coingecko,
			"IEXC": iexc
		}

	def supports(self, platform):
		return self.parsed[platform] is not None

	def __supported_platforms(self, findSupported):
		supported = []
		for platform in self.parsed:
			if (self.supports(platform) and findSupported) or (not self.supports(platform) and not findSupported): supported.append(platform)
		return supported

	def supported_platforms(self):
		return self.__supportedPlatforms(True)

	def unsupported_platforms(self):
		return self.__supported_platforms(False)

	def __str__(self):
		return "{} [id: {}]".format(self.name, self.id)

class TradeParameter(object):
	def __init__(self, id, name, parsablePhrases, ichibot=None, requiresPro=False):
		self.id = id
		self.name = name
		self.parsablePhrases = parsablePhrases
		self.requiresPro = requiresPro
		self.parsed = {
			"Ichibot": ichibot
		}

	def supports(self, platform):
		return self.parsed[platform] is not None

	def __supported_platforms(self, findSupported):
		supported = []
		for platform in self.parsed:
			if (self.supports(platform) and findSupported) or (not self.supports(platform) and not findSupported): supported.append(platform)
		return supported

	def supported_platforms(self):
		return self.__supportedPlatforms(True)

	def unsupported_platforms(self):
		return self.__supported_platforms(False)

	def __str__(self):
		return "{} [id: {}]".format(self.name, self.id)
