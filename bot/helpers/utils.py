import time
import datetime
import pytz

from ccxt.base import decimal_to_precision as dtp


class Utils(object):
	@staticmethod
	def format_price(exchange, symbol, level):
		precision = exchange.markets[symbol]["precision"]["price"]
		if exchange.id in ["bitmex"]: countingMode = dtp.TICK_SIZE
		elif exchange.id in ["bitfinex2"]: countingMode = dtp.SIGNIFICANT_DIGITS
		else: countingMode = dtp.DECIMAL_PLACES

		price = float(dtp.decimal_to_precision(level, precision=precision, counting_mode=countingMode, padding_mode=dtp.PAD_WITH_ZERO))
		return ("{:,.%df}" % Utils.num_of_decimal_places(exchange, price, precision)).format(price)

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
	def get_accepted_timeframes():
		acceptedTimeframes = []
		t = datetime.datetime.now().astimezone(pytz.utc)
		if t.second % 60 == 0: acceptedTimeframes.append("1m")
		else: return []
		if t.minute % 5 == 0: acceptedTimeframes.append("5m")
		if t.minute % 10 == 0: acceptedTimeframes.append("10m")
		if t.minute % 15 == 0: acceptedTimeframes.append("15m")
		if t.minute % 20 == 0: acceptedTimeframes.append("20m")
		if t.minute % 30 == 0: acceptedTimeframes.append("30m")
		if t.minute % 60 == 0: acceptedTimeframes.append("1H")
		if t.hour % 2 == 0 and t.minute == 0: acceptedTimeframes.append("2H")
		if t.hour % 3 == 0 and t.minute == 0: acceptedTimeframes.append("3H")
		if t.hour % 4 == 0 and t.minute == 0: acceptedTimeframes.append("4H")
		if t.hour % 6 == 0 and t.minute == 0: acceptedTimeframes.append("6H")
		if t.hour % 12 == 0 and t.minute == 0: acceptedTimeframes.append("12H")
		if t.hour % 24 == 0 and t.minute == 0: acceptedTimeframes.append("1D")
		return acceptedTimeframes

	@staticmethod
	def get_current_date():
		return datetime.datetime.now().astimezone(pytz.utc).strftime("%m. %d. %Y, %H:%M:%S")

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
			"trading": {
				"open_orders": [],
				"history": []
			},
			"paper_trading": {
				"free_balance": {
					"binance": {
						"USDT": 1000,
						"BTC": 0
					},
					"bittrex": {
						"USD": 1000,
						"BTC": 0
					}
				},
				"open_orders": [],
				"history": []
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
			"functions": {
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
	def shortcuts(r, isCommand, allowsShortcuts):
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
			elif r in ["funding", "fun", "funding xbt", "fun xbt", "funding xbtusd", "fun xbtusd", "funding btc", "fun btc", "funding btcusd", "fun btcusd"]: raw = "p xbt funding"
			elif r in ["funding", "fun", "funding eth", "fun eth", "funding ethusd", "fun ethusd"]: raw = "p ethusd mex funding"
			elif r in ["funding eth", "fun eth"]: raw = "p xbt funding"
			elif r in ["fut", "futs", "futures"]: raw = "p xbt futs"
			elif r in ["prem", "prems", "premiums"]: raw = "p xbt prems"
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

		if r in ["c internals", "c internal", "c int"]: raw = "c uvol-dvol w, tick, dvn-decn, pcc d line"
		elif r in ["c btc vol"]: raw = "c bvol"
		elif r in ["c mcap"]: raw = "c total nv"
		elif r in ["c alt mcap"]: raw = "c total2 nv"
		elif r in ["oi", "oi xbt"]: raw = "c xbt oi"
		elif r in ["oi eth"]: raw = "c eth oi"
		elif r in ["hmap"] or r.startswith("hmap, "): raw = raw.replace("hmap", "hmap price", 1)

		return raw, r != raw or isCommand, r != raw
