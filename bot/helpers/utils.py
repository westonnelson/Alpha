import time
import datetime
import pytz
import math

from ccxt.base import decimal_to_precision as dtp


class Utils(object):
	@staticmethod
	def format_price(exchange, symbol, price):
		precision = exchange.markets[symbol]["precision"]["price"] if exchange.markets[symbol]["precision"]["price"] is not None else 8
		price = float(dtp.decimal_to_precision(price, rounding_mode=dtp.ROUND, precision=precision, counting_mode=exchange.precisionMode, padding_mode=dtp.PAD_WITH_ZERO))
		return ("{:,.%df}" % Utils.num_of_decimal_places(exchange, price, precision)).format(price)

	@staticmethod
	def format_amount(exchange, symbol, amount):
		precision = exchange.markets[symbol]["precision"]["amount"]
		amount = float(dtp.decimal_to_precision(amount, rounding_mode=dtp.TRUNCATE, precision=precision, counting_mode=exchange.precisionMode, padding_mode=dtp.NO_PADDING))
		return ("{:,.%df}" % Utils.num_of_decimal_places(exchange, amount, precision)).format(amount)

	@staticmethod
	def num_of_decimal_places(exchange, price, precision):
		if exchange.id in ["bitmex"]:
			s = str(precision)
			if "e" in s: return int(s.split("e-")[1])
			elif not '.' in s: return 0
			else: return len(s) - s.index('.') - 1
		elif exchange.id in ["bitfinex2"]:
			return precision - len(str(int(price)))
		else:
			return precision

	@staticmethod
	def add_decimal_zeros(number, digits=8):
		wholePart = str(int(number))
		return digits if wholePart == "0" else max(digits - len(wholePart), 0)

	@staticmethod
	def recursiveFill(settings, template):
		for e in template:
			if type(template[e]) is dict:
				if e not in settings:
					settings[e] = template[e].copy()
				else:
					Utils.recursiveFill(settings[e], template[e])
			elif e not in settings:
				settings[e] = template[e]

	@staticmethod
	def createUserSettings(settings):
		settingsTemplate = {
			"premium": {"subscribed": False, "hadTrial": False, "hadWarning": False, "timestamp": 0, "date": "", "plan": 0},
			"presets": [],
			"paper_trading": {
				"s_lastReset": 0, "s_numOfResets": 0,
				"binance": {"balance": {"USDT": {"amount": 1000}}, "open_orders": [], "history": []},
				#"coinbasepro": {"balance": {"USD": {"amount": 1000}}, "open_orders": [], "history": []},
				#"bittrex": {"balance": {"USD": {"amount": 1000}}, "open_orders": [], "history": []},
				#"poloniex": {"balance": {"USDT": {"amount": 1000}}, "open_orders": [], "history": []},
				#"kraken": {"balance": {"USD": {"amount": 1000}}, "open_orders": [], "history": []},
				#"huobipro": {"balance": {"USDT": {"amount": 1000}}, "open_orders": [], "history": []},
				#"bitmex": {"balance": {"BTC": {"amount": 0.1}}, "open_orders": [], "history": []}
			}
		}

		if settings is None: settings = {}
		Utils.recursiveFill(settings, settingsTemplate)

		return settings

	@staticmethod
	def createServerSetting(settings):
		settingsTemplate = {
			"premium": {"subscribed": False, "hadTrial": False, "hadWarning": False, "timestamp": 0, "date": "", "plan": 0},
			"presets": [],
			"hasDoneSetup": False,
			"settings": {
				"bias": "crypto",
				"tos": 0.0,
				"assistant": True,
				"shortcuts": True,
				"autodelete": False
			}
		}

		if settings is None: settings = {}
		Utils.recursiveFill(settings, settingsTemplate)

		return settings

	@staticmethod
	def updateServerSetting(raw, setting, sub=None, toVal=None):
		settings = Utils.createServerSetting(raw)

		if sub is not None:
			settings[setting][sub] = toVal
		else:
			settings[setting] = toVal

		return settings

	@staticmethod
	def updateForwarding(raw, group="general", add=None, remove=None):
		settings = Utils.createUserSettings(raw)

		if len(settings["forwarding"][group]) >= 10:
			return (settings, "You can only forward to 10 servers")

		if add in settings["forwarding"][group]:
			return (settings, "Server is already added")

		if add is not None:
			settings["forwarding"][group].append(add)
			return (settings, "Server successfully added")
		elif remove is not None:
			settings["forwarding"][group].remove(add)
			return (settings, "Server successfully removed")

		return settings, "Something went wrong..."

	@staticmethod
	def shortcuts(r, allowsShortcuts):
		raw = r

		if allowsShortcuts:
			if r in ["!help", "?help"]: raw = "a help"
			elif r in ["!invite", "?invite"]: raw = "a invite"
			elif r in ["mex", "mex xbt", "mex btc"]: raw = "p xbt"
			elif r in ["mex eth"]: raw = "p ethusd mex"
			elif r in ["mex ltc"]: raw = "p ltc mex"
			elif r in ["mex bch"]: raw = "p bch mex"
			elif r in ["mex eos"]: raw = "p eos mex"
			elif r in ["mex xrp"]: raw = "p xrp mex"
			elif r in ["mex trx"]: raw = "p trx mex"
			elif r in ["mex ada"]: raw = "p ada mex"
			elif r in ["finex"]: raw = "p btc bitfinex"
			elif r in ["finex eth"]: raw = "p ethusd bitfinex"
			elif r in ["finex ltc"]: raw = "p ltc bitfinex"
			elif r in ["finex bch"]: raw = "p bch bitfinex"
			elif r in ["finex eos"]: raw = "p eos bitfinex"
			elif r in ["finex xrp"]: raw = "p xrp bitfinex"
			elif r in ["finex trx"]: raw = "p trx bitfinex"
			elif r in ["finex ada"]: raw = "p ada bitfinex"
			elif r in ["coinbase"]: raw = "p btc cbp"
			elif r in ["coinbase eth"]: raw = "p ethusd cbp"
			elif r in ["coinbase ltc"]: raw = "p ltc cbp"
			elif r in ["coinbase bch"]: raw = "p bch cbp"
			elif r in ["coinbase zrx"]: raw = "p zrx cbp"
			elif r in ["coinbase bat"]: raw = "p bat cbp"
			elif r in ["coinbase zec"]: raw = "p zec cbp"
			elif r.startswith("$") and not r.startswith("$ "): raw = raw.replace("$", "mc ", 1)
			elif r.startswith("!convert "): raw = raw[1:]

		if r in ["c internals", "c internal", "c int"]: raw = "c uvol-dvol w, tick, dvn-decn, pcc d line"
		elif r in ["c btc vol"]: raw = "c bvol"
		elif r in ["c mcap"]: raw = "c total nv"
		elif r in ["p mcap"]: raw = "p btc 271f45c16070a"
		elif r in ["c alt mcap"]: raw = "c total2 nv"
		elif r in ["fut", "futs", "futures"]: raw = "p xbtz19, xbth20"
		elif r in ["funding", "fun"]: raw = "p xbt fun, eth mex fun"
		elif r in ["funding xbt", "fun xbt", "funding xbtusd", "fun xbtusd", "funding btc", "fun btc", "funding btcusd", "fun btcusd", "xbt funding", "xbt fun", "xbtusd funding", "xbtusd fun", "btc funding", "btc fun", "btcusd funding", "btcusd fun"]: raw = "p xbt funding"
		elif r in ["funding eth", "fun eth", "funding ethusd", "fun ethusd", "eth funding", "eth fun", "ethusd funding", "ethusd fun"]: raw = "p eth mex funding"
		elif r in ["oi", ".oi", "ov", ".ov"]: raw = "p xbt oi, eth mex oi"
		elif r in ["oi xbt", ".oi xbt", "ov xbt", ".ov xbt"]: raw = "p xbt oi"
		elif r in ["oi eth", ".oi eth", "ov eth", ".ov eth"]: raw = "p eth mex oi"
		elif r in ["prem", "prems", "premiums"]: raw = "p xbt prems"
		elif r in ["hmap"]: raw = "hmap price"
		elif r in ["p greed index", "p gi", "p fear index", "p fi", "p fear greed index", "p fgi", "p greed fear index", "p gfi"]: raw = "p btc 05d92bb00c1d5"
		elif r.startswith("hmap, ") or r.endswith(", hmap"): raw = raw.replace("hmap, ", "hmap price, ").replace(", hmap", ", hmap price")

		return raw, r != raw

	@staticmethod
	def seconds_until_cycle():
		n = datetime.datetime.now().astimezone(pytz.utc)
		return (15 - n.second % 15) - ((time.time() * 1000) % 1000) / 1000

	@staticmethod
	def get_highest_supported_timeframe(exchange, n):
		if exchange.timeframes is None: return ("1m", int(exchange.milliseconds() / 1000) - 60, 2)
		dailyOpen = (int(exchange.milliseconds() / 1000) - (n.second + n.minute * 60 + n.hour * 3600)) * 1000
		rolling24h = (int(exchange.milliseconds() / 1000) - 86400) * 1000
		availableTimeframes = ["5m", "10m", "15m", "20m", "30m", "1H", "2H", "3H", "4H", "6H", "8H", "12H", "1D"]
		for tf in availableTimeframes:
			if tf.lower() in exchange.timeframes:
				return tf, min(rolling24h, dailyOpen), math.ceil(int((exchange.milliseconds() - dailyOpen) / 1000) / Utils.get_frequency_time(tf))

	@staticmethod
	def get_accepted_timeframes(t):
		acceptedTimeframes = []
		for timeframe in ["1m", "5m", "10m", "15m", "20m", "30m", "1H", "2H", "3H", "4H", "6H", "8H", "12H", "1D"]:
			if t.second % 60 == 0 and (t.hour * 60 + t.minute) * 60 % Utils.get_frequency_time(timeframe) == 0:
				acceptedTimeframes.append(timeframe)
		return acceptedTimeframes

	@staticmethod
	def get_frequency_time(t):
		if t == "1D": return 86400
		elif t == "12H": return 43200
		elif t == "8H": return 28800
		elif t == "6H": return 21600
		elif t == "4H": return 14400
		elif t == "3H": return 10800
		elif t == "2H": return 7200
		elif t == "1H": return 3600
		elif t == "30m": return 1800
		elif t == "20m": return 1200
		elif t == "15m": return 900
		elif t == "10m": return 600
		elif t == "5m": return 300
		elif t == "1m": return 60

	@staticmethod
	def timestamp_to_date(timestamp):
		return datetime.datetime.utcfromtimestamp(timestamp).strftime("%m. %d. %Y, %H:%M")

	@staticmethod
	def get_current_date():
		return datetime.datetime.now().astimezone(pytz.utc).strftime("%m. %d. %Y, %H:%M:%S")
