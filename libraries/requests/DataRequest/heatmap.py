import sys
import urllib
import time

from TickerParser import TickerParser
from .parameter import HeatmapParameter as Parameter


class HeatmapRequestHandler(object):
	def __init__(self, accountId, authorId, platforms, isPro=False, messageRequest=None, **kwargs):
		self.accountId = accountId
		self.authorId = authorId
		self.timestamp = time.time()
		self.hash = "HMAP{}{}".format(int(time.time() * 1000), authorId)
		self.platforms = platforms
		self.parserBias = "traditional" if messageRequest is None else messageRequest.marketBias
		
		self.isDelayed = not isPro

		self.currentPlatform = self.platforms[0]

		self.requests = {}
		for platform in self.platforms:
			self.requests[platform] = HeatmapRequest(platform)

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

			outputMessage, success = request.add_heatmap_style(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_image_style(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			outputMessage, success = request.add_filters(argument)
			if outputMessage is not None: finalOutput = outputMessage
			elif success is not None and success: continue

			# outputMessage, success = request.add_numerical_parameters(argument)
			# if outputMessage is not None: finalOutput = outputMessage
			# elif success is not None and success: continue

			if finalOutput is None:
				request.set_error("`{}` is not a valid argument.".format(argument), isFatal=True)
			else:
				request.set_error(finalOutput)

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

		outputMessage = None if currentMinimumErrors == 0 else (preferredRequestOrder[0].errors[0] if len(preferredRequestOrder) > 0 else "Requested heat map is not available.")
		return outputMessage

	def set_current(self, platform=None, timeframe=None):
		if platform is not None: self.currentPlatform = platform
		if timeframe is not None:
			for platform in self.requests:
				self.requests[platform].currentTimeframe = timeframe

	def build_url(self, addMessageUrl=False):
		requestUrl, messageUrl = None, None
		if self.currentPlatform == "Finviz":
			filters = self.get_filters()
			if self.get_current_timeframe() != "": style = self.get_current_timeframe()
			else: style = self.find_parameter_in_list("style", filters)
			type = self.find_parameter_in_list("type", filters)
			requestUrl = "https://www.finviz.com/map.ashx?{}{}".format(type, style)
			if addMessageUrl: messageUrl = requestUrl
		elif self.currentPlatform == "Bitgur":
			filters = self.get_filters()
			heatmap = self.find_parameter_in_list("heatmap", filters)
			side = self.find_parameter_in_list("side", filters)
			type = self.find_parameter_in_list("type", filters)
			category = self.find_parameter_in_list("category", filters)
			requestUrl = "https://bitgur.com/map/{}{}{}{}{}".format("" if heatmap == "coins/" else heatmap, side, self.get_current_timeframe(), type, category)
			if addMessageUrl: messageUrl = requestUrl

		return requestUrl, messageUrl

	def set_defaults(self):
		for platform, request in self.requests.items():
			if request.errorIsFatal: continue
			for type in request.requestParameters:
				request.set_default_for(type)

	def find_caveats(self):
		for platform, request in self.requests.items():
			if platform == "Finviz":
				style = self.find_parameter_in_list_for("style", request.filters, platform)
				type = self.find_parameter_in_list_for("type", request.filters, platform)
				if len(request.timeframes) == 0:
					if style == "":
						request.timeframes = [request.find_parameter_with_id(1440, type="timeframes")]
					else:
						request.timeframes = [Parameter(None, None, None, finviz="")]
				elif len(request.timeframes) != 0 and style != "":
					request.set_error("Timeframe cannot be used with selected heat map style.")
				if type == "t=etf" and style not in ["&st=ytd", "&st=relvol"]:
					request.set_error("Heat map of `exchange traded funds` with select parameters is not available.")
			elif platform == "Bitgur":
				for _ in range(8):
					heatmap = self.find_parameter_in_list_for("heatmap", request.filters, platform)
					side = self.find_parameter_in_list_for("side", request.filters, platform)
					type = self.find_parameter_in_list_for("type", request.filters, platform)
					category = self.find_parameter_in_list_for("category", request.filters, platform)
					# Timeframes are not supported on some heat map types
					if heatmap in ["exchanges/", "volatility/", "unusual_volume/"]:
						if len(request.timeframes) != 0:
							if request.timeframes[0].id is not None: request.set_error("Timeframes are not supported on the {} heat map.".format(heatmap[:-1])); break
						else:
							request.timeframes = [Parameter(None, None, None, bitgur="")]; continue
					elif len(request.timeframes) == 0:
						request.timeframes = [request.find_parameter_with_id(1440, type="timeframes")]; continue

					# Category heat map checks
					if heatmap in ["category/"]:
						if category == "": request.set_error("Missing category."); break
					elif category != "": request.filters.append(request.find_parameter_with_id("heatmap", name="category", type="filters")); continue

					if heatmap in ["coins/", "trend/"]:
						if category == "" and type != "": request.filters.append(request.find_parameter_with_id("category", name="mcap", type="filters")); continue

					if heatmap in ["exchanges/", "category/"]:
						if type != "": request.set_error("Types are not supported on the {} heat map.".format(heatmap[:-1])); break
					elif type == "":
						request.filters.append(request.find_parameter_with_id("type", name="all", type="filters")); continue

					if heatmap in ["coins/", "exchanges/", "category/", "volatility/", "unusual_volume/"]:
						if side != "": request.set_error("Top gainers/loosers are not supported on the {} heat map.".format(heatmap[:-1])); break
					elif side == "":
						request.filters.append(request.find_parameter_with_id("side", name="gainers", type="filters")); continue

					# Add default heat map style
					if heatmap == "":
						if side != "": request.filters.append(request.find_parameter_with_id("heatmap", name="trend", type="filters")); continue
						else: request.filters.append(request.find_parameter_with_id("heatmap", name="change", type="filters")); continue

					break
	
	def requires_pro(self):
		return self.requests[self.currentPlatform].requiresPro

	def get_current_timeframe(self): return self.get_current_timeframe_for(self.currentPlatform)

	def get_timeframes(self): return self.get_timeframes_for(self.currentPlatform)

	def get_heatmap_style(self): return self.get_heatmap_style_for(self.currentPlatform)

	def get_image_style(self): return self.get_image_style_for(self.currentPlatform)

	def get_filters(self): return self.get_filters_for(self.currentPlatform)

	def get_numerical_parameters(self): return self.get_numerical_parameters_for(self.currentPlatform)

	def find_parameter_in_list(self, id, list, default=""): return self.find_parameter_in_list_for(id, list, self.currentPlatform, default)

	def get_current_timeframe_for(self, platform):
		if platform not in self.requests: return None
		timeframes = [e.parsed[platform] for e in self.requests[platform].timeframes]
		return self.requests[platform].currentTimeframe

	def get_timeframes_for(self, platform):
		if platform not in self.requests: return []
		timeframes = [e.parsed[platform] for e in self.requests[platform].timeframes]
		return timeframes

	def get_heatmap_style_for(self, platform):
		if platform not in self.requests: return []
		return "".join([e.parsed[platform] for e in self.requests[platform].heatmapStyle])

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
		return "<Request: {}, (style: {})>".format(self.get_timeframes(), self.get_heatmap_style())


class HeatmapRequest(object):
	requestParameters = {
		"timeframes": [
			Parameter(1, "1m", ["1", "1m", "1min", "1mins", "1minute", "1minutes", "min"]),
			Parameter(2, "2m", ["2", "2m", "2min", "2mins", "2minute", "2minutes"]),
			Parameter(3, "3m", ["3", "3m", "3min", "3mins", "3minute", "3minutes"]),
			Parameter(5, "5m", ["5", "5m", "5min", "5mins", "5minute", "5minutes"]),
			Parameter(10, "10m", ["10", "10m", "10min", "10mins", "10minute", "10minutes"]),
			Parameter(15, "15m", ["15", "15m", "15min", "15mins", "15minute", "15minutes"], bitgur="last_minute15/"),
			Parameter(20, "20m", ["20", "20m", "20min", "20mins", "20minute", "20minutes"]),
			Parameter(30, "30m", ["30", "30m", "30min", "30mins", "30minute", "30minutes"]),
			Parameter(45, "45m", ["45", "45m", "45min", "45mins", "45minute", "45minutes"]),
			Parameter(60, "1H", ["60", "60m", "60min", "60mins", "60minute", "60minutes", "1", "1h", "1hr", "1hour", "1hours", "hourly", "hour", "hr", "h"], bitgur="last_hour/"),
			Parameter(120, "2H", ["120", "120m", "120min", "120mins", "120minute", "120minutes", "2", "2h", "2hr", "2hrs", "2hour", "2hours"]),
			Parameter(180, "3H", ["180", "180m", "180min", "180mins", "180minute", "180minutes", "3", "3h", "3hr", "3hrs", "3hour", "3hours"]),
			Parameter(240, "4H", ["240", "240m", "240min", "240mins", "240minute", "240minutes", "4", "4h", "4hr", "4hrs", "4hour", "4hours"]),
			Parameter(360, "6H", ["360", "360m", "360min", "360mins", "360minute", "360minutes", "6", "6h", "6hr", "6hrs", "6hour", "6hours"]),
			Parameter(480, "8H", ["480", "480m", "480min", "480mins", "480minute", "480minutes", "8", "8h", "8hr", "8hrs", "8hour", "8hours"]),
			Parameter(720, "12H", ["720", "720m", "720min", "720mins", "720minute", "720minutes", "12", "12h", "12hr", "12hrs", "12hour", "12hours"]),
			Parameter(1440, "1D", ["24", "24h", "24hr", "24hrs", "24hour", "24hours", "d", "day", "1", "1d", "1day", "daily", "1440", "1440m", "1440min", "1440mins", "1440minute", "1440minutes"], finviz="", bitgur="last_day/"),
			Parameter(2880, "2D", ["48", "48h", "48hr", "48hrs", "48hour", "48hours", "2", "2d", "2day", "2880", "2880m", "2880min", "2880mins", "2880minute", "2880minutes"]),
			Parameter(3420, "3D", ["72", "72h", "72hr", "72hrs", "72hour", "72hours", "3", "3d", "3day", "3420", "3420m", "3420min", "3420mins", "3420minute", "3420minutes"]),
			Parameter(10080, "1W", ["7", "7d", "7day", "7days", "w", "week", "1w", "1week", "weekly"], finviz="&st=w1", bitgur="last_week/"),
			Parameter(20160, "2W", ["14", "14d", "14day", "14days", "2w", "2week"]),
			Parameter(43829, "1M", ["30d", "30day", "30days", "1", "1m", "m", "mo", "month", "1mo", "1month", "monthly"], finviz="&st=w4", bitgur="last_month/"),
			Parameter(87658, "2M", ["2", "2m", "2m", "2mo", "2month", "2months"]),
			Parameter(131487, "3M", ["3", "3m", "3m", "3mo", "3month", "3months"], finviz="&st=w13"),
			Parameter(175316, "4M", ["4", "4m", "4m", "4mo", "4month", "4months"]),
			Parameter(262974, "6M", ["6", "6m", "5m", "6mo", "6month", "6months"], finviz="&st=w26", bitgur="last_month6/"),
			Parameter(525949, "1Y", ["12", "12m", "12mo", "12month", "12months", "year", "yearly", "1year", "1y", "y", "annual", "annually"], finviz="&st=w52", bitgur="last_year/"),
			Parameter(1051898, "2Y", ["24", "24m", "24mo", "24month", "24months", "2year", "2y"]),
			Parameter(1577847, "3Y", ["36", "36m", "36mo", "36month", "36months", "3year", "3y"]),
			Parameter(2103796, "4Y", ["48", "48m", "48mo", "48month", "48months", "4year", "4y"])
		],
		"heatmapStyle": [

		],
		"imageStyle": [
			Parameter("force", "force", ["--force"], bitgur="force")
		],
		"filters": [
			Parameter("style", "year to date performance", ["ytd"], finviz="&st=ytd"),
			Parameter("style", "relative volume", ["relative", "volume", "relvol", "rvol"], finviz="&st=relvol"),
			Parameter("style", "P/E", ["pe"], finviz="&st=pe"),
			Parameter("style", "forward P/E", ["fpe"], finviz="&st=fpe"),
			Parameter("style", "PEG", ["peg"], finviz="&st=peg"),
			Parameter("style", "P/S", ["ps"], finviz="&st=ps"),
			Parameter("style", "P/B", ["pb"], finviz="&st=pb"),
			Parameter("style", "dividend yield", ["div", "dividend", "yield"], finviz="&st=div"),
			Parameter("style", "EPS growth past 5 years", ["eps", "growth" "eps5y"], finviz="&st=eps5y"),
			Parameter("style", "float short", ["float", "short", "fs"], finviz="&st=short"),
			Parameter("style", "analysts recomendation", ["analysts", "recomendation", "recom", "rec", "ar"], finviz="&st=rec"),
			Parameter("style", "earnings day performance", ["earnings", "day", "performance", "edp", "earnperf", "edperf"], finviz="&st=earnperf"),
			Parameter("style", "earnings date", ["earnings", "earn", "date", "performance", "ep", "earndate", "edate", "eperf"], finviz="&st=earndate"),
			Parameter("heatmap", "change", ["change"], bitgur="coins/"),
			Parameter("heatmap", "volatility", ["volatility", "vol", "v"], bitgur="volatility/"),
			Parameter("heatmap", "exchanges", ["exchanges", "exchange", "exc", "e"], bitgur="exchanges/"),
			Parameter("heatmap", "trend", ["trend", "tre", "t"], bitgur="trend/"),
			Parameter("heatmap", "category", ["category", "cat", "c"], bitgur="category/"),
			Parameter("heatmap", "unusual", ["unusual", "volume", "unu", "unv", "uvol", "u"], bitgur="unusual_volume/"),
			Parameter("side", "gainers", ["gainers", "gainer", "gain", "g"], bitgur="gainers/"),
			Parameter("side", "loosers", ["loosers", "looser", "loss", "l"], bitgur="loosers/"),
			Parameter("type", "top100", ["top100", "100top", "100"], bitgur="top100/"),
			Parameter("type", "top10", ["top10", "10top", "10"], bitgur="top10/"),
			Parameter("type", "coins", ["coins", "coin"], bitgur="crypto/"),
			Parameter("type", "token", ["token", "tokens"], bitgur="token/"),
			Parameter("type", "all", ["full", "all", "every", "everything"], finviz="t=sec_all", bitgur="all/"),
			Parameter("type", "s&p500", ["sp500", "s&p500", "sp"], finviz="t=sec"),
			Parameter("type", "full", ["map", "geo", "world"], finviz="t=geo"),
			Parameter("type", "exchange traded funds", ["etfs", "etf"], finviz="t=etf"),
			Parameter("category", "mcap", [], bitgur="cap"),
			Parameter("category", "cryptocurrency", ["cryptocurrency", "crypto"], bitgur="cryptocurrency"),
			Parameter("category", "blockchain platforms", ["blockchain", "platforms"], bitgur="blockchain_platforms"),
			Parameter("category", "commerce and advertising", ["commerce", "advertising"], bitgur="commerce_and_advertising"),
			Parameter("category", "commodities", ["commodities"], bitgur="commodities"),
			Parameter("category", "content management", ["content", "management"], bitgur="content_management"),
			Parameter("category", "data storage and AI", ["data", "storage", "analytics", "ai"], bitgur="data_storage_analytics_and_ai"),
			Parameter("category", "drugs and healthcare", ["drugs", "healthcare"], bitgur="drugs_and_healthcare"),
			Parameter("category", "energy and utilities", ["energy", "utilities"], bitgur="energy_and_utilities"),
			Parameter("category", "events and entertainment", ["events", "entertainment"], bitgur="events_and_entertainment"),
			Parameter("category", "financial services", ["financial", "services"], bitgur="financial_services"),
			Parameter("category", "gambling and betting", ["gambling", "betting"], bitgur="gambling_and_betting"),
			Parameter("category", "gaming and VR", ["gaming", "vr"], bitgur="gaming_and_vr"),
			Parameter("category", "identy and reputation", ["identy", "reputation"], bitgur="identy_and_reputation"),
			Parameter("category", "legal", ["legal"], bitgur="legal"),
			Parameter("category", "real estate", ["real", "estate"], bitgur="real_estate"),
			Parameter("category", "social network", ["social", "network"], bitgur="social_network"),
			Parameter("category", "software", ["software"], bitgur="software"),
			Parameter("category", "supply and logistics", ["supply", "logistics"], bitgur="supply_and_logistics"),
			Parameter("category", "trading and investing", ["trading", "investing"], bitgur="trading_and_investing"),
			Parameter("autoDeleteOverride", "autodelete", ["del", "delete", "autodelete"], finviz=True, bitgur=True)
		]
	}

	def __init__(self, platform):
		self.timeframes = []
		self.heatmapStyle = []
		self.imageStyle = []
		self.filters = []
		self.numericalParameters = []

		self.platform = platform
		self.currentTimeframe = None
		self.hasTimeframeRange = False

		self.requiresPro = False
		self.canCache = platform not in []

		self.errors = []
		self.errorIsFatal = False

		self.__defaultParameters = {
			"Finviz": {
				"timeframes": [],
				"heatmapStyle": [],
				"imageStyle": [],
				"filters": [self.find_parameter_with_id("type", name="s&p500", type="filters")]
			},
			"Bitgur": {
				"timeframes": [],
				"heatmapStyle": [],
				"imageStyle": [],
				"filters": []
			}
		}

	def __hash__(self):
		h1 = sorted([e.name for e in self.heatmapStyle])
		h2 = sorted([e.name for e in self.imageStyle])
		h3 = sorted([e.name for e in self.filters])
		return hash("{}{}{}{}{}{}{}".format(self.currentTimeframe, h1, h2, h3, self.numericalParameters, self.platform, self.requiresPro))

	def add_parameter(self, argument, type):
		isSupported = None
		parsedParameter = None
		for param in HeatmapRequest.requestParameters[type]:
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

	def add_heatmap_style(self, argument):
		heatmapStyleSupported, parsedHeatmapStyle = self.add_parameter(argument, "heatmapStyle")
		if parsedHeatmapStyle is not None and not self.has_parameter(parsedHeatmapStyle.id, self.heatmapStyle):
			if not heatmapStyleSupported:
				outputMessage = "`{}` heat map style is not supported on {}.".format(parsedHeatmapStyle.name.title(), self.platform)
				return outputMessage, False
			self.heatmapStyle.append(parsedHeatmapStyle)
			return None, True
		return None, None

	def add_image_style(self, argument):
		imageStyleSupported, parsedImageStyle = self.add_parameter(argument, "imageStyle")
		if parsedImageStyle is not None and not self.has_parameter(parsedImageStyle.id, self.imageStyle):
			if not imageStyleSupported:
				outputMessage = "`{}` heat map style is not supported on {}.".format(parsedImageStyle.name.title(), self.platform)
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
			if self.platform not in []:
				outputMessage = "Price arguments are not supported by {}.".format(self.platform)
				return outputMessage, False
			self.numericalParameters.append(numericalParameter)
			return None, True
		except: return None, None

	def set_default_for(self, type):
		if type == "timeframes" and len(self.timeframes) == 0:
			for parameter in self.__defaultParameters[self.platform][type]:
				if not self.has_parameter(parameter.id, self.timeframes): self.timeframes.append(parameter)
		elif type == "heatmapStyle":
			for parameter in self.__defaultParameters[self.platform][type]:
				if not self.has_parameter(parameter.id, self.heatmapStyle): self.heatmapStyle.append(parameter)
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
		return self.has_parameter(id, self.timeframes + self.heatmapStyle + self.imageStyle + self.filters, argument)

	def has_parameter(self, id, list, argument=None):
		for e in list:
			if e.id == id and (argument is None or e.parsed[self.platform] == argument): return True
		return False

	def set_error(self, error, isFatal=False):
		if len(self.errors) > 0 and self.errors[0] is None: return
		self.errorIsFatal = isFatal
		self.errors.insert(0, error)
