import math
from ccxt.base import decimal_to_precision as dtp


class Utils(object):
	@staticmethod
	def format_price(exchange, symbol, price):
		precision = 8 if (exchange.markets[symbol]["precision"]["price"] is None if "price" in exchange.markets[symbol]["precision"] else True) else exchange.markets[symbol]["precision"]["price"]
		price = float(dtp.decimal_to_precision(price, rounding_mode=dtp.ROUND, precision=precision, counting_mode=exchange.precisionMode, padding_mode=dtp.PAD_WITH_ZERO))
		return ("{:,.%df}" % Utils.num_of_decimal_places(exchange, price, precision)).format(price)

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