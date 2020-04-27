import time
import datetime
import pytz
import math

import colorsys
from ccxt.base import decimal_to_precision as dtp


class Utils(object):
	@staticmethod
	def format_price(exchange, symbol, price):
		precision = 8 if (exchange.markets[symbol]["precision"]["price"] is None if "price" in exchange.markets[symbol]["precision"] else True) else exchange.markets[symbol]["precision"]["price"]
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
	def convert_score(score):
		if 6 <= score <= 10: return ":chart_with_upwards_trend: Extremely bullish"
		elif 1 <= score <= 5: return ":chart_with_upwards_trend: Bullish"
		elif -5 <= score <= -1: return ":chart_with_downwards_trend: Bearish"
		elif -10 <= score <= -6: return ":chart_with_downwards_trend: Extremely bearish"
		else: return "Neutral"

	@staticmethod
	def recursive_fill(settings, template):
		for e in template:
			if type(template[e]) is dict:
				if e not in settings:
					settings[e] = template[e].copy()
				else:
					Utils.recursive_fill(settings[e], template[e])
			elif e not in settings:
				settings[e] = template[e]

	@staticmethod
	def shortcuts(raw, allowsShortcuts):
		initial = raw
		if allowsShortcuts:
			if raw in ["!help", "?help"]: raw = "a help"
			elif raw in ["!invite", "?invite"]: raw = "a invite"
			elif raw in ["mex", "mex xbt", "mex btc"]: raw = "p xbt"
			elif raw in ["mex eth"]: raw = "p ethusd mex"
			elif raw in ["mex xrp"]: raw = "p xrpusd mex"
			elif raw in ["mex ltc"]: raw = "p ltc mex"
			elif raw in ["mex bch"]: raw = "p bch mex"
			elif raw in ["mex eos"]: raw = "p eos mex"
			elif raw in ["mex trx"]: raw = "p trx mex"
			elif raw in ["mex ada"]: raw = "p ada mex"
			elif raw in ["stamp"]: raw = "p btc bitstamp"
			elif raw in ["stamp eth"]: raw = "p ethusd bitstamp"
			elif raw in ["stamp ltc"]: raw = "p ltc bitstamp"
			elif raw in ["stamp bch"]: raw = "p bch bitstamp"
			elif raw in ["stamp eos"]: raw = "p eos bitstamp"
			elif raw in ["stamp xrp"]: raw = "p xrp bitstamp"
			elif raw in ["stamp trx"]: raw = "p trx bitstamp"
			elif raw in ["stamp ada"]: raw = "p ada bitstamp"
			elif raw in ["finex"]: raw = "p btc bitfinex"
			elif raw in ["finex eth"]: raw = "p ethusd bitfinex"
			elif raw in ["finex ltc"]: raw = "p ltc bitfinex"
			elif raw in ["finex bch"]: raw = "p bch bitfinex"
			elif raw in ["finex eos"]: raw = "p eos bitfinex"
			elif raw in ["finex xrp"]: raw = "p xrp bitfinex"
			elif raw in ["finex trx"]: raw = "p trx bitfinex"
			elif raw in ["finex ada"]: raw = "p ada bitfinex"
			elif raw in ["coinbase"]: raw = "p btc cbp"
			elif raw in ["coinbase eth"]: raw = "p ethusd cbp"
			elif raw in ["coinbase ltc"]: raw = "p ltc cbp"
			elif raw in ["coinbase bch"]: raw = "p bch cbp"
			elif raw in ["coinbase zrx"]: raw = "p zrx cbp"
			elif raw in ["coinbase bat"]: raw = "p bat cbp"
			elif raw in ["coinbase zec"]: raw = "p zec cbp"
			elif raw.startswith("$") and not raw.startswith("$ "): raw = raw.replace("$", "mc ", 1)
			elif raw.startswith("!convert "): raw = raw[1:]

		shortcutUsed = initial != raw

		if raw in ["c internals", "c internal", "c int"]: raw = "c uvol-dvol w, tick, dvn-decn, pcc d line"
		elif raw in ["c btc vol"]: raw = "c bvol"
		elif raw in ["c mcap"]: raw = "c total nv"
		elif raw in ["c alt mcap"]: raw = "c total2 nv"
		elif raw in ["fut", "futs", "futures"]: raw = "p xbtm20, xbtu20"
		elif raw in ["funding", "fun"]: raw = "p xbt fun, eth mex fun, xrpusd mex fun"
		elif raw in ["funding xbt", "fun xbt", "funding xbtusd", "fun xbtusd", "funding btc", "fun btc", "funding btcusd", "fun btcusd", "xbt funding", "xbt fun", "xbtusd funding", "xbtusd fun", "btc funding", "btc fun", "btcusd funding", "btcusd fun"]: raw = "p xbt funding"
		elif raw in ["funding eth", "fun eth", "funding ethusd", "fun ethusd", "eth funding", "eth fun", "ethusd funding", "ethusd fun"]: raw = "p eth mex funding"
		elif raw in ["funding xrp", "fun xrp", "funding xrpusd", "fun xrpusd", "xrp funding", "xrp fun", "xrpusd funding", "xrpusd fun"]: raw = "p xrpusd mex funding"
		elif raw in ["oi", ".oi", "ov", ".ov"]: raw = "p xbt oi, eth mex oi, xrpusd mex oi"
		elif raw in ["oi xbt", "oi xbtusd", ".oi xbt", ".oi xbtusd", "ov xbt", "ov xbtusd", ".ov xbt", ".ov xbtusd"]: raw = "p xbt oi"
		elif raw in ["oi eth", "oi ethusd", ".oi eth", ".oi ethusd", "ov eth", "ov ethusd", ".ov eth", ".ov ethusd"]: raw = "p eth mex oi"
		elif raw in ["oi xrp", "oi xrpusd", ".oi xrp", ".oi xrpusd", "ov xrp", "ov xrpusd", ".ov xrp", ".ov xrpusd"]: raw = "p xrpusd oi"
		elif raw in ["prem", "prems", "premiums"]: raw = "p btc prems"
		elif raw in ["hmap"]: raw = "hmap change"
		elif raw in ["p greed index", "p gindex", "p gi", "p fear index", "p findex", "p fi", "p fear greed index", "p fgindex", "p fgi", "p greed fear index", "p gfindex", "p gfi"]: raw = "p am fgi"
		elif raw in ["c greed index", "c gindex", "c gi", "c fear index", "c findex", "c fi", "c fear greed index", "c fgindex", "c fgi", "c greed fear index", "c gfindex", "c gfi"]: raw = "c am fgi"
		elif raw in ["c nvtr", "c nvt", "c nvt ratio", "c nvtratio"]: raw = "c wc nvt"
		elif raw in ["c drbns", "c drbn", "c rbns", "c rbn", "c dribbon", "c difficulty ribbon", "c difficultyribbon"]: raw = "c wc drbn"
		elif raw.startswith("hmap, ") or raw.endswith(", hmap"): raw = raw.replace("hmap, ", "hmap map, ").replace(", hmap", ", hmap change")

		raw = raw.replace("line break", "break")

		return raw, shortcutUsed

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
		return ("1m", int(exchange.milliseconds() / 1000) - 60, 2)

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
