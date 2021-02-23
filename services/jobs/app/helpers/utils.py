import time
import datetime
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
	def shortcuts(raw):
		if raw in ["!help", "?help"]: raw = "alpha help"
		elif raw in ["!invite", "?invite"]: raw = "alpha invite"
		elif raw in ["c internals", "c internal"]: raw = "c uvol-dvol w, tick, dvn-decn, pcc d line"
		elif raw in ["c btc vol"]: raw = "c bvol"
		elif raw in ["c mcap"]: raw = "c total nv"
		elif raw in ["c alt mcap"]: raw = "c total2 nv"
		elif raw in ["hmap"]: raw = "hmap change"
		elif raw in ["flow"]: raw = "flow options"
		elif raw in ["p gindex", "p gi", "p findex", "p fi", "p fgindex", "p fgi", "p gfindex", "p gfi"]: raw = "p am fgi"
		elif raw in ["c gindex", "c gi", "c findex", "c fi", "c fgindex", "c fgi", "c gfindex", "c gfi"]: raw = "c am fgi"
		elif raw in ["c nvtr", "c nvt", "c nvt ratio", "c nvtratio"]: raw = "c wc nvt"
		elif raw in ["c drbns", "c drbn", "c rbns", "c rbn", "c dribbon", "c difficultyribbon"]: raw = "c wc drbn"
		elif raw in ["p fut", "p futs", "p futures"]: raw = "p xbtz20, xbth21"

		raw = raw.replace("line break", "break")

		return raw

	@staticmethod
	def seconds_until_cycle():
		return (time.time() + 60) // 60 * 60 - time.time()

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