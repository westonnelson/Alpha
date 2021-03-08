import time
import datetime
import requests
import json
import ccxt

from ccxt.base.decimal_to_precision import DECIMAL_PLACES
from . import supported


class Exchange(object):
	def __init__(self, id, name=None):
		self.id = id
		self.name = None
		self.properties = None
		self.isCrypto = False

		if id == "binancefutures":
			self.properties = ccxt.binance({'option': {'defaultMarket': 'futures'}})
			self.name = self.properties.name
			self.isCrypto = True
		elif id == "dexblue":
			self.properties = ProprietaryConnection(id)
			self.name = "dex.blue"
			self.isCrypto = True
		elif id == "uniswap":
			self.properties = ProprietaryConnection(id)
			self.name = "Uniswap"
			self.isCrypto = True
		elif id in supported.ccxtExchanges and id in ccxt.exchanges:
			self.properties = getattr(ccxt, id)()
			self.name = self.properties.name
			self.isCrypto = True
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
		if self.id == "dexblue":
			response = requests.get("https://api.dex.blue/rest/v1/listed").json()
			for market, data in response["data"]["markets"].items():
				symbol = "{}/{}".format(data["traded"], data["quote"])
				self.symbols.append(symbol)
				self.markets[symbol] = {"base": data["traded"], "quote": data["quote"], "id": market, "precision": {"price": None, "amount": None}}
			self.symbols.sort()
		elif self.id == "uniswap":
			payload = {"query": "{ pairs { id token0 { symbol } token1 { symbol decimals } } }"}
			response = requests.post("https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v2", data=json.dumps(payload)).json()
			for data in response["data"]["pairs"]:
				symbol = "{}/{}".format(data["token0"]["symbol"].upper(), data["token1"]["symbol"].upper())
				self.symbols.append(symbol)
				self.markets[symbol] = {"base": data["token0"]["symbol"], "quote": data["token1"]["symbol"], "id": data["id"], "precision": {"price": int(data["token1"]["decimals"]), "amount": None}}
			self.symbols.sort()

	def fetch_ohlcv(self, symbol, timeframe="1d", since=None, limit=500):
		if self.id == "dexblue":
			response = requests.get("https://api.dex.blue/rest/v1/market/{}/ticker".format(self.markets[symbol]["id"])).json()
			data = []
			if self.markets[symbol]["precision"]["price"] is None:
				if "." not in response["data"]["rate"]: self.markets[symbol]["precision"]["price"] = 0
				else: self.markets[symbol]["precision"]["price"] = len(response["data"]["rate"].split(".")[1])
			data.append([self.milliseconds(), float(response["data"]["rate"]) / (1.0 + float(response["data"]["change24h"]) / 100), float(response["data"]["high24h"]), float(response["data"]["low24h"]), float(response["data"]["rate"]), float(response["data"]["volumeQuote24h"])])
			return data
		elif self.id == "uniswap":
			payload = {"query": "{ pairs(where: {id: \"" + self.markets[symbol]["id"] + "\"}) { token1Price volumeToken1 } }"}
			response = requests.post("https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v2", data=json.dumps(payload)).json()
			data = []
			if len(response["data"]["pairs"]) == 1:
				data.append([self.milliseconds(), float(response["data"]["pairs"][0]["token1Price"]), float(response["data"]["pairs"][0]["token1Price"]), float(response["data"]["pairs"][0]["token1Price"]), float(response["data"]["pairs"][0]["token1Price"]), float(response["data"]["pairs"][0]["volumeToken1"])])
			return data
		return []

	def fetch_order_book(self, symbol):
		if self.id == "dexblue":
			response = requests.get("https://api.dex.blue/rest/v1/market/{}/orderbook?limit=100".format(self.markets[symbol]["id"])).json()
			timestamp = self.milliseconds()
			data = {
				"bids": [],
				"asks": [],
				"timestamp": timestamp,
				"datetime": datetime.datetime.utcfromtimestamp(timestamp / 1000).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
				"nonce": timestamp
			}
			for order in response["data"]:
				if order["direction"] == "BUY":
					data["bids"].append([float(order["rate"]), float(order["amount"]) / 10**18])
				else:
					data["asks"].append([float(order["rate"]), float(order["amount"]) / 10**18])
			return data
		elif self.id == "uniswap":
			return None
		return []

class ProprietaryExchange(object):
	def __init__(self, id):
		self.id = id
		self.symbols = []
		self.markets = {}
		self.timeframes = ["1m"]