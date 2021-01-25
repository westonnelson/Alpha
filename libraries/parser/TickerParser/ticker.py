class Ticker(object):
	separators = {"+", "-", "*", "/", "^", "(", ")"}

	def __init__(self, id, name=None, base=None, quote=None, symbol=None, hasParts=True, mcapRank=None, isReversed=False):
		self.id = id
		self.parts = []
		self.__hasParts = hasParts
		self.isAggregatedTicker = False

		self.name = id if name is None else name
		self.base = None
		self.quote = None
		self.symbol = None
		self.mcapRank = None
		self.isReversed = False
		self.__name = id if name is None else name
		self.__base = id if base is None else base
		self.__quote = quote
		self.__symbol = "{}/{}".format(self.__base, self.__quote) if symbol is None and self.__base is not None and self.__quote is not None else symbol
		self.__mcapRank = mcapRank
		self.__isReversed = isReversed

		self.update_ticker_parts(hasParts)
		self.update_properties()

	def update_ticker_parts(self, hasParts):
		if self.__hasParts: self.parts = Ticker.generate_ticker_parts(self.id)

	def update_ticker_id(self):
		self.id = ""
		for part in self.parts:
			if type(part) is str: self.id += part
			else: self.id += part.id
		self.update_properties()

	def update_properties(self):
		self.isAggregatedTicker = len(self.parts) > 1
		if self.isAggregatedTicker:
			self.base = None
			self.quote = None
			self.symbol = None
			self.mcapRank = None
			self.isReversed = None
		elif len(self.parts) == 1:
			self.name = self.parts[0].name
			self.base = self.parts[0].base
			self.quote = self.parts[0].quote
			self.symbol = self.parts[0].symbol
			self.mcapRank = self.parts[0].mcapRank
			self.isReversed = self.parts[0].isReversed
		else:
			self.name = self.__name
			self.base = self.__base
			self.quote = self.__quote
			self.symbol = self.__symbol
			self.mcapRank = self.__mcapRank
			self.isReversed = self.__isReversed

	def is_ranked_higher(self, other):
		if self.mcapRank is None or other.mcapRank is None: return False
		return self.mcapRank < other.mcapRank

	def __hash__(self):
		return hash("{}{}{}{}{}{}{}{}".format(self.id, self.isReversed, self.name, self.base, self.quote, self.symbol, self.mcapRank, [hash(e) for e in self.parts]))

	def __str__(self):
		return "{} [id: {}, {}/{}]".format(self.name, self.id, self.base, self.quote)

	def __eq__(self, other):
		if not isinstance(other, Ticker): return False
		return self.id == other.id and self.name == other.name and self.base == other.base and self.quote == other.quote and self.symbol == other.symbol

	@staticmethod
	def generate_ticker_parts(tickerId):
		if tickerId is None or (tickerId.startswith("'") and tickerId.endswith("'")) or (tickerId.startswith('"') and tickerId.endswith('"')) or (tickerId.startswith("‘") and tickerId.endswith("’")) or (tickerId.startswith("“") and tickerId.endswith("”")):
			return [Ticker(tickerId, hasParts=False)]
		else:
			parts = []
			currentPart = ""
			for char in tickerId:
				if char in Ticker.separators:
					if currentPart != "": parts.append(Ticker(currentPart, hasParts=False))
					parts.append(char)
					currentPart = ""
				else:
					currentPart += char
			parts.append(Ticker(currentPart, hasParts=False))

			return parts

	@staticmethod
	def generate_market_name(symbol, exchange):
		symbolInfo = exchange.properties.markets[symbol]
		marketPair = symbol.replace("-", "").split("/")
		marketName1 = "".join(marketPair)
		marketName2 = symbolInfo["id"].replace("_", "").replace("/", "").replace("-", "").upper()

		if any(e in marketName2 for e in ["XBT"]) or exchange.id in ["bitmex"]: return marketName2
		else: return marketName1
