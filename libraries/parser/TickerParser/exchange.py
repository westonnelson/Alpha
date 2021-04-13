import time
import datetime
import requests
import json
import ccxt

from ccxt.base.decimal_to_precision import DECIMAL_PLACES
from . import supported


class Exchange(object):
	def __init__(self, id, marketType, name=None, region=None):
		self.id = id
		self.name = None
		self.region = region
		self.properties = None
		self.type = marketType

		if id == "binancefutures":
			self.properties = ccxt.binance({'option': {'defaultMarket': 'futures'}})
			self.name = "Binance Futures"
			self.type = "crypto"
		elif id == "uniswap":
			self.properties = ProprietaryConnection(id)
			self.name = "Uniswap"
			self.type = "crypto"
		elif id in supported.ccxtExchanges and id in ccxt.exchanges:
			self.properties = getattr(ccxt, id)()
			self.name = self.properties.name
			self.type = "crypto"
		else:
			self.properties = ProprietaryExchange(id)
			self.name = id.title() if name is None else name

	def __hash__(self):
		return hash(self.id)

	def __str__(self):
		return "{} [id: {}]".format(self.name, self.id)

class ProprietaryConnection(object):
	def __init__(self, id):
		self.id = id
		self.symbols = []
		self.markets = {}
		self.timeframes = ["1d"]

		self.precisionMode = DECIMAL_PLACES

	def milliseconds(self):
		return int(time.time() * 1000)

	def load_markets(self):
		if self.id == "uniswap":
			payload = {"query": "{ pairs { id token0 { symbol } token1 { symbol decimals } } }"}
			response = requests.post("https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v2", data=json.dumps(payload)).json()
			for data in response["data"]["pairs"]:
				symbol = "{}/{}".format(data["token0"]["symbol"].upper(), data["token1"]["symbol"].upper())
				self.symbols.append(symbol)
				self.markets[symbol] = {"base": data["token0"]["symbol"], "quote": data["token1"]["symbol"], "id": data["id"], "precision": {"price": int(data["token1"]["decimals"]), "amount": None}}
			self.symbols.sort()

	def fetch_ohlcv(self, symbol, timeframe="1d", since=None, limit=500):
		if self.id == "uniswap":
			payload = {"query": "{ pairs(where: {id: \"" + self.markets[symbol]["id"] + "\"}) { token1Price volumeToken1 } }"}
			response = requests.post("https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v2", data=json.dumps(payload)).json()
			data = []
			if len(response["data"]["pairs"]) == 1:
				data.append([self.milliseconds(), float(response["data"]["pairs"][0]["token1Price"]), float(response["data"]["pairs"][0]["token1Price"]), float(response["data"]["pairs"][0]["token1Price"]), float(response["data"]["pairs"][0]["token1Price"]), float(response["data"]["pairs"][0]["volumeToken1"])])
			return data
		return []

	def fetch_order_book(self, symbol):
		if self.id == "uniswap":
			return None
		return []

class ProprietaryExchange(object):
	def __init__(self, id):
		self.id = id
		self.symbols = []
		self.markets = {}
		self.timeframes = ["1m"]