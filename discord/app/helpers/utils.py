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
		if exchange.id in ["bitmex", "ftx"]:
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
	def shortcuts(raw, allowsShortcuts):
		initial = raw
		if allowsShortcuts:
			if raw in ["!help", "?help"]: raw = "alpha help"
			elif raw in ["!invite", "?invite"]: raw = "alpha invite"
			elif raw in ["mex"]: raw = "p xbt, eth mex, xrp mex, bch mex, ltc mex"
			elif raw in ["mex xbt", "mex btc"]: raw = "p xbt"
			elif raw in ["mex eth"]: raw = "p eth mex"
			elif raw in ["mex xrp"]: raw = "p xrp mex"
			elif raw in ["mex bch"]: raw = "p bch mex"
			elif raw in ["mex ltc"]: raw = "p ltc mex"
			elif raw in ["mex eos"]: raw = "p eos mex"
			elif raw in ["mex trx"]: raw = "p trx mex"
			elif raw in ["mex ada"]: raw = "p ada mex"
			elif raw in ["fut", "futs", "futures"]: raw = "p xbtz20, xbth21"
			elif raw in ["funding", "fun"]: raw = "p xbt fun, eth mex fun, xrp mex fun, bch mex fun, ltc mex fun"
			elif raw in ["oi", "ov"]: raw = "p xbt oi, eth mex oi, xrp mex oi, bch mex oi, ltc mex oi"
			elif raw in ["prem", "prems", "premiums"]: raw = "p btc prems"

		shortcutUsed = initial != raw

		if raw in ["c internals", "c internal", "c int"]: raw = "c uvol-dvol w, tick, dvn-decn, pcc d line"
		elif raw in ["c btc vol"]: raw = "c bvol"
		elif raw in ["c mcap"]: raw = "c total nv"
		elif raw in ["c alt mcap"]: raw = "c total2 nv"
		elif raw in ["funding xbt", "fun xbt", "funding xbtusd", "fun xbtusd", "funding btc", "fun btc", "funding btcusd", "fun btcusd", "xbt funding", "xbt fun", "xbtusd funding", "xbtusd fun", "btc funding", "btc fun", "btcusd funding", "btcusd fun"]: raw = "p xbt funding"
		elif raw in ["funding eth", "fun eth", "funding ethusd", "fun ethusd", "eth funding", "eth fun", "ethusd funding", "ethusd fun"]: raw = "p eth mex funding"
		elif raw in ["funding xrp", "fun xrp", "funding xrpusd", "fun xrpusd", "xrp funding", "xrp fun", "xrpusd funding", "xrpusd fun"]: raw = "p xrp mex funding"
		elif raw in ["funding bch", "fun bch", "funding bchusd", "fun bchusd", "bch funding", "bch fun", "bchusd funding", "bchusd fun"]: raw = "p bch mex funding"
		elif raw in ["funding ltc", "fun ltc", "funding ltcusd", "fun ltcusd", "ltc funding", "ltc fun", "ltcusd funding", "ltcusd fun"]: raw = "p ltc mex funding"
		elif raw in ["oi xbt", "oi xbtusd", "ov xbt", "ov xbtusd"]: raw = "p xbt oi"
		elif raw in ["oi eth", "oi ethusd", "ov eth", "ov ethusd"]: raw = "p eth mex oi"
		elif raw in ["oi xrp", "oi xrpusd", "ov xrp", "ov xrpusd"]: raw = "p xrp mex oi"
		elif raw in ["oi bch", "oi bchusd", "ov bch", "ov bchusd"]: raw = "p bch mex oi"
		elif raw in ["oi ltc", "oi ltcusd", "ov ltc", "ov ltcusd"]: raw = "p ltc mex oi"
		elif raw in ["hmap"]: raw = "hmap change"
		elif raw in ["flow"]: raw = "flow options"
		elif raw in ["p gindex", "p gi", "p findex", "p fi", "p fgindex", "p fgi", "p gfindex", "p gfi"]: raw = "p am fgi"
		elif raw in ["c gindex", "c gi", "c findex", "c fi", "c fgindex", "c fgi", "c gfindex", "c gfi"]: raw = "c am fgi"
		elif raw in ["c nvtr", "c nvt", "c nvt ratio", "c nvtratio"]: raw = "c wc nvt"
		elif raw in ["c drbns", "c drbn", "c rbns", "c rbn", "c dribbon", "c difficultyribbon"]: raw = "c wc drbn"

		raw = raw.replace("line break", "break")

		return raw, shortcutUsed

	@staticmethod
	def seconds_until_cycle(every=15, offset=0):
		n = datetime.datetime.now().astimezone(pytz.utc)
		return (every - (n.second + offset) % every) - ((time.time() * 1000) % 1000) / 1000

	@staticmethod
	def get_accepted_timeframes(t):
		acceptedTimeframes = []
		for timeframe in ["1m", "2m", "3m", "5m", "10m", "15m", "20m", "30m", "1H", "2H", "3H", "4H", "6H", "8H", "12H", "1D"]:
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
		elif t == "3m": return 180
		elif t == "2m": return 120
		elif t == "1m": return 60

	@staticmethod
	def timestamp_to_date(timestamp):
		return datetime.datetime.utcfromtimestamp(timestamp).strftime("%m. %d. %Y, %H:%M")

	@staticmethod
	def get_current_date():
		return datetime.datetime.now().astimezone(pytz.utc).strftime("%m. %d. %Y, %H:%M:%S")
