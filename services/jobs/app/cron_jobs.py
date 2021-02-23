import os
import signal
import time
import datetime
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
import asyncio
import math
import uuid
import pytz
import traceback
import zmq
import zlib
import pickle

from google.cloud import firestore, error_reporting

from DatabaseConnector import DatabaseConnector
from helpers.utils import Utils


database = firestore.Client()


class CronJobs(object):
	accountProperties = DatabaseConnector(mode="account")

	zmqContext = zmq.Context.instance()


	# -------------------------
	# Startup
	# -------------------------
	
	def __init__(self):
		self.isServiceAvailable = True
		signal.signal(signal.SIGINT, self.exit_gracefully)
		signal.signal(signal.SIGTERM, self.exit_gracefully)

		self.logging = error_reporting.Client()

	def exit_gracefully(self):
		print("[Startup]: Cron Job handler is exiting")
		self.isServiceAvailable = False


	# -------------------------
	# Job queue
	# -------------------------

	def run(self):
		while self.isServiceAvailable:
			try:
				time.sleep(Utils.seconds_until_cycle())
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "1m" in timeframes:
					self.process_price_alerts()
					# self.update_paper_limit_orders()

			except (KeyboardInterrupt, SystemExit): return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def job_queue(self):
		while True:
			try:
				time.sleep(Utils.seconds_until_cycle())
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "4H" in timeframes:
					self.update_popular_tickers()

			except (KeyboardInterrupt, SystemExit): return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Price Alerts
	# -------------------------

	def process_price_alerts(self):
		"""Sends out price alert notifications

		"""

		try:
			with ThreadPoolExecutor(max_workers=20) as pool:
				accounts = pool.submit(asyncio.run, self.accountProperties.keys()).result()
				users = database.document("details/marketAlerts").collections()
				for user in users:
					accountId = user.id
					authorId = pool.submit(asyncio.run, self.accountProperties.match(accountId)).result() if accountId in accounts else accountId
					if authorId is None: continue
					for alert in user.stream():
						pool.submit(self.check_price_alert, authorId, accountId, alert.id, alert.to_dict())

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def check_price_alert(self, authorId, accountId, key, alert):
		socket = CronJobs.zmqContext.socket(zmq.REQ)
		socket.connect("tcp://candle-server:6900")
		socket.setsockopt(zmq.LINGER, 3)
		poller = zmq.Poller()
		poller.register(socket, zmq.POLLIN)

		try:
			alertRequest = pickle.loads(zlib.decompress(alert["request"]))
			alertRequest.timestamp = time.time()
			ticker = alertRequest.get_ticker()
			exchange = alertRequest.get_exchange()

			if alertRequest.currentPlatform == "CCXT":
				levelText = Utils.format_price(exchange.properties, ticker.symbol, alert["level"])
			elif alertRequest.currentPlatform == "IEXC" or alertRequest.currentPlatform == "Quandl":
				levelText = "{:,.5f}".format(alert["level"])
			else:
				levelText = "{:,.0f}".format(alert["level"])

			if alert["timestamp"] < time.time() - 86400 * 30.5 * 6:
				if os.environ["PRODUCTION_MODE"]:
					database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
						"title": "Price alert for {} ({}) at {} {} expired.".format(ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote),
						"subtitle": "Alpha Price Alerts",
						"description": "Price alerts automatically cancel after 6 months. If you'd like to keep your alert, you'll have to schedule it again.",
						"color": 6765239,
						"user": authorId,
						"channel": alert["channel"]
					})

					database.document("details/marketAlerts/{}/{}".format(accountId, key)).delete()

				else:
					print("{}: price alert for {} ({}) at {} {} expired.".format(accountId, ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote))

			else:
				socket.send_multipart([b"cronjob", b"candle", zlib.compress(pickle.dumps(alertRequest, -1))])
				responses = poller.poll(30 * 1000)

				if len(responses) != 0:
					response = socket.recv()
					payload, responseText = pickle.loads(zlib.decompress(response))

					if payload is None:
						if responseText is not None:
							print("Alert request error", responseText)
							if os.environ["PRODUCTION_MODE"]: self.logging.report(responseText)
						return

					alertRequest.set_current(platform=payload["platform"])
					for candle in reversed(payload["candles"]):
						if candle[0] < alert["timestamp"]: break
						if (candle[3] <= alert["level"] and alert["placement"] == "below") or (alert["level"] <= candle[2] and alert["placement"] == "above"):
							if os.environ["PRODUCTION_MODE"]:
								database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
									"title": "Price of {} ({}) hit {} {}.".format(ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote),
									"subtitle": "Alpha Price Alerts",
									"description": None,
									"color": 6765239,
									"user": authorId,
									"channel": alert["channel"]
								})

								database.document("details/marketAlerts/{}/{}".format(accountId, key)).delete()

							else:
								print("{}: price of {} ({}) hit {} {}.".format(accountId, ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote))
							break

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
		socket.close()


	# -------------------------
	# Paper trading
	# -------------------------

	def update_paper_limit_orders(self):
		"""Process paper limit orders

		"""

		socket = CronJobs.zmqContext.socket(zmq.REQ)
		socket.connect("tcp://candle-server:6900")
		socket.setsockopt(zmq.LINGER, 3)
		poller = zmq.Poller()
		poller.register(socket, zmq.POLLIN)

		try:
			for accountId in self.accountProperties:
				if "customer" in self.accountProperties[accountId]:
					for exchange in self.accountProperties[accountId]["paperTrader"]:
						if exchange in ["globalLastReset", "globalResetCount"]: continue
						paper = self.accountProperties[accountId]["paperTrader"][exchange]

						for order in list(paper["openOrders"]):
							paperRequest = pickle.loads(zlib.decompress(order["request"]))
							ticker = paperRequest.get_ticker()
							exchange = paperRequest.get_exchange()

							if paperRequest.currentPlatform == "CCXT":
								levelText = Utils.format_price(exchange.properties, ticker.symbol, order["price"])
							elif paperRequest.currentPlatform == "IEXC" or paperRequest.currentPlatform == "Quandl":
								levelText = "{:,.5f}".format(order["price"])
							else:
								levelText = "{:,.0f}".format(order["price"])

							socket.send_multipart([b"cronjob", b"candle", order["request"]])
							responses = poller.poll(5 * 1000)

							if len(responses) != 0:
								response = socket.recv()
								payload, responseText = pickle.loads(zlib.decompress(response))

								if payload is None:
									if responseText is not None:
										print("Paper order request error", responseText)
										if os.environ["PRODUCTION_MODE"]: self.logging.report(responseText)
									return

								for candle in reversed(payload["candles"]):
									if candle[0] < order["timestamp"] / 1000: break
									if candle[3] < order["price"] < candle[2]:
										baseOrder = paper["balance"][ticker.base]
										quoteOrder = paper["balance"][ticker.quote]

										execAmount = order["amount"]
										isPricePercent, isLimitOrder, reduceOnly = order["parameters"]
										if reduceOnly and ((order["orderType"] == "buy" and baseOrder["amount"] >= 0) or (order["orderType"] == "sell" and baseOrder["amount"] <= 0)):
											order["status"] = "canceled"
											paper["openOrders"].remove(order)

										if exchange.id == "bitmex":
											averageEntry = (baseOrder["entry"] * baseOrder["amount"] + order["price"] * execAmount) / (baseOrder["amount"] + execAmount) if baseOrder["amount"] + execAmount != 0 else 0
											quoteValue = (abs(execAmount) * (-1 if reduceOnly else 1)) / (averageEntry if averageEntry != 0 else baseOrder["entry"]) / leverage
											roi = ((order["price"] - baseOrder["entry"]) * 0.000001 if ticker.symbol == "ETH/USD" else (1 / baseOrder["entry"] - 1 / order["price"])) * baseOrder["amount"] if baseOrder["entry"] != 0 else 0
											orderFee = execAmount * exchange.properties.markets[ticker.symbol]["maker" if isLimitOrder else "taker"]

											if order["orderType"] == "buy" or order["orderType"] == "sell":
												baseOrder["entry"] = averageEntry
												baseOrder["amount"] += execAmount
											elif order["orderType"] == "stop-buy" or order["orderType"] == "stop-sell":
												quoteOrder["amount"] += round(roi - (quoteValue + abs(orderFee) / order["price"]), 8)
												baseOrder["entry"] = averageEntry
												baseOrder["amount"] += execAmount
										else:
											if order["orderType"] == "buy":
												if reduceOnly: execAmount = min(abs(quoteOrder["amount"]), order["price"] * execAmount) / order["price"]
												orderFee = execAmount * exchange.properties.markets[ticker.symbol]["maker"]

												baseOrder["amount"] += execAmount - orderFee
											elif order["orderType"] == "sell":
												if reduceOnly: execAmount = min(abs(baseOrder["amount"]), execAmount)
												orderFee = execAmount * exchange.properties.markets[ticker.symbol]["maker"]

												quoteOrder["amount"] += (execAmount - orderFee) * order["price"]
											elif order["orderType"] == "stop-buy":
												if reduceOnly: execAmount = min(abs(quoteOrder["amount"]), order["price"] * execAmount) / order["price"]
												orderFee = execAmount * exchange.properties.markets[ticker.symbol]["taker"]

												baseOrder["amount"] += execAmount - orderFee
												quoteOrder["amount"] -= order["price"] * execAmount
											elif order["orderType"] == "stop-sell":
												if reduceOnly: execAmount = min(abs(baseOrder["amount"]), execAmount)
												orderFee = execAmount * exchange.properties.markets[ticker.symbol]["taker"]

												baseOrder["amount"] -= execAmount
												quoteOrder["amount"] += (execAmount - orderFee) * order["price"]

										paper["openOrders"].remove(order)
										order["status"] = "filled"
										paper["history"].append(order)
										database.document("accounts/{}".format(accountId)).set({"paperTrader": {exchange.id: paper}}, merge=True)

										if self.server.accountProperties[accountId]["oauth"]["discord"].get("userId") is not None:
											database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
												"title": "Paper {} order of {} {} on {} at {} was successfully executed.".format(order["orderType"].replace("-", " "), Utils.format_amount(exchange.properties, ticker.symbol, order["amount"]), order["base"], exchange.name, order["price"]),
												"subtitle": "Alpha Paper Trader",
												"description": None,
												"color": 6765239,
												"user": self.server.accountProperties[accountId]["oauth"]["discord"]["userId"],
												"channel": "611107823111372810"
											})

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Data updates
	# -------------------------

	def update_popular_tickers(self):
		if not os.environ["PRODUCTION_MODE"]: return
		try:
			processingTimestamp = time.time()
			platforms = ["TradingLite", "TradingView", "Bookmap", "GoCharting", "Alpha Flow", "CoinGecko", "CCXT", "IEXC", "Quandl", "Alpha Paper Trader"]
			dataset1d = []
			dataset8d = []
			topTickerMap = {
				"traditional": {},
				"crypto": {}
			}
			risingTickerMap = {
				"traditional": {},
				"crypto": {}
			}

			for platform in platforms:
				requests1d = database.collection("dataserver/statistics/{}".format(platform)).where("timestamp", ">=", processingTimestamp - 86400 * 1).get()
				requests8d = database.collection("dataserver/statistics/{}".format(platform)).where("timestamp", "<", processingTimestamp - 86400 * 1).where("timestamp", ">=", processingTimestamp - 86400 * 8).get()

				requests31d = database.collection("dataserver/statistics/{}".format(platform)).where("timestamp", "<", processingTimestamp - 86400 * 31).get()
				for e in requests31d:
					database.document("dataserver/statistics/{}/{}".format(platform, e.id)).delete()

				for e in requests1d:
					request = e.to_dict()
					if request["ticker"]["base"] in ["BTC", "BTC.D", "BTC1!", "BLX", "XBT", "XBTUSD", "BTCUSD", "BTCUSDT"]:
						request["ticker"]["base"] = "BTC" if request["ticker"]["bias"] == "crypto" else "BTCUSD"
					if request["ticker"]["base"] in ["ETH", "ETH.D", "ETHUSD", "ETHUSDT"]:
						request["ticker"]["base"] = "ETH" if request["ticker"]["bias"] == "crypto" else "ETHUSD"
					if request["ticker"]["base"] in ["TOTAL2", "TOTAL", "OPTIONS"] or any([e in request["ticker"]["base"] for e in ["LONGS", "SHORTS"]]):
						continue
					dataset1d.append(request)
				for e in requests8d:
					request = e.to_dict()
					if request["ticker"]["base"] in ["BTC", "BTC.D", "BTC1!", "BLX", "XBT", "XBTUSD", "BTCUSD", "BTCUSDT"]:
						request["ticker"]["base"] = "BTC" if request["ticker"]["bias"] == "crypto" else "BTCUSD"
					if request["ticker"]["base"] in ["ETH", "ETH.D", "ETHUSD", "ETHUSDT"]:
						request["ticker"]["base"] = "ETH" if request["ticker"]["bias"] == "crypto" else "ETHUSD"
					if request["ticker"]["base"] in ["TOTAL2", "TOTAL", "OPTIONS"] or any([e in request["ticker"]["base"] for e in ["LONGS", "SHORTS"]]):
						continue
					dataset8d.append(request)

			for request in dataset1d:
				topTickerMap[request["ticker"]["bias"]][request["ticker"]["base"]] = topTickerMap[request["ticker"]["bias"]].get(request["ticker"]["base"], 0) + 1

			for request in dataset8d:
				risingTickerMap[request["ticker"]["bias"]][request["ticker"]["base"]] = risingTickerMap[request["ticker"]["bias"]].get(request["ticker"]["base"], 0) + 1

			sortedTopTickerMap = {
				"traditional": sorted(topTickerMap["traditional"].items(), key=lambda item: item[1]),
				"crypto": sorted(topTickerMap["crypto"].items(), key=lambda item: item[1])
			}
			sortedRisingTickerMap = {
				"traditional": sorted([(base, topTickerMap["traditional"].get(base, 0) / (score / 7)) for base, score in risingTickerMap["traditional"].items() if score >= 7], key=lambda item: item[1]),
				"crypto": sorted([(base, topTickerMap["crypto"].get(base, 0) / (score / 7)) for base, score in risingTickerMap["crypto"].items() if score >= 7], key=lambda item: item[1])
			}

			maxScoreTopTraditional = sortedTopTickerMap["traditional"][-1][1]
			maxScoreTopCrypto = sortedTopTickerMap["crypto"][-1][1]

			topTraditionalTickers = [{"id": k, "rank": v / maxScoreTopTraditional * 100} for k, v in sortedTopTickerMap["traditional"][-20:]]
			topCryptoTickers = [{"id": k, "rank": v / maxScoreTopCrypto * 100} for k, v in sortedTopTickerMap["crypto"][-20:]]
			risingTraditionalTickers = [{"id": k, "rank": v} for k, v in sortedRisingTickerMap["traditional"][-20:]]
			risingCryptoTickers = [{"id": k, "rank": v} for k, v in sortedRisingTickerMap["crypto"][-20:]]

			database.document("dataserver/statistics").set({
				"top": {
					"traditional": topTraditionalTickers,
					"crypto": topCryptoTickers
				},
				"rising": {
					"traditional": risingTraditionalTickers,
					"crypto": risingCryptoTickers
				}
			})

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


if __name__ == "__main__":
	os.environ["PRODUCTION_MODE"] = os.environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in os.environ and os.environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Cron Jobs handler is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))

	cronJobs = CronJobs()
	Thread(target=cronJobs.job_queue, daemon=True).start()
	cronJobs.run()
