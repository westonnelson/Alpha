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
	registeredAccounts = {}

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
					self.update_accounts()
					self.process_price_alerts()
					self.process_paper_limit_orders()

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

	def update_accounts(self):
		try:
			loop = asyncio.get_event_loop()
			self.registeredAccounts = loop.run_until_complete(self.accountProperties.keys())
		except (KeyboardInterrupt, SystemExit): pass
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
			users = database.document("details/marketAlerts").collections()
			with ThreadPoolExecutor(max_workers=20) as pool:
				for user in users:
					accountId = user.id
					authorId = self.registeredAccounts.get(accountId, accountId)
					if authorId is None: continue
					for alert in user.stream():
						pool.submit(self.check_price_alert, authorId, accountId, alert.reference, alert.to_dict())

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def check_price_alert(self, authorId, accountId, reference, alert):
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
					reference.delete()

				else:
					print("{}: price alert for {} ({}) at {} {} expired".format(accountId, ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote))

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
									"user": None if alertRequest.find_parameter_in_list("public", alertRequest.get_filters(), default=False) else authorId,
									"channel": alert["channel"]
								})
								reference.delete()

							else:
								print("{}: price of {} ({}) hit {} {}".format(accountId, ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote))
							break

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
		socket.close()


	# -------------------------
	# Paper trading
	# -------------------------

	def process_paper_limit_orders(self):
		"""Process paper limit orders

		"""

		try:
			users = database.document("details/openPaperOrders").collections()
			with ThreadPoolExecutor(max_workers=20) as pool:
				for user in users:
					accountId = user.id
					authorId = self.registeredAccounts.get(accountId, accountId)
					if authorId is None: continue
					for order in user.stream():
						pool.submit(self.check_paper_order, authorId, accountId, order.reference, order.to_dict())

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def check_paper_order(self, authorId, accountId, reference, order):
		socket = CronJobs.zmqContext.socket(zmq.REQ)
		socket.connect("tcp://candle-server:6900")
		socket.setsockopt(zmq.LINGER, 3)
		poller = zmq.Poller()
		poller.register(socket, zmq.POLLIN)

		try:
			paperRequest = pickle.loads(zlib.decompress(order["request"]))
			paperRequest.timestamp = time.time()
			ticker = paperRequest.get_ticker()
			exchange = paperRequest.get_exchange()

			if paperRequest.currentPlatform == "CCXT":
				levelText = Utils.format_price(exchange.properties, ticker.symbol, order["price"])
			elif paperRequest.currentPlatform == "IEXC":
				levelText = "{:,.5f}".format(order["price"])
			else:
				levelText = "{:,.0f}".format(order["price"])

			if order["timestamp"] < time.time() - 86400 * 30.5 * 6:
				if os.environ["PRODUCTION_MODE"]:
					database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
						"title": "Paper {} order of {} {} on {} at {} {} expired.".format(order["orderType"].replace("-", " "), Utils.format_amount(exchange.properties, ticker.symbol, order["amount"]), ticker.base, paperRequest.currentPlatform if exchange is None else exchange.name, order["price"], ticker.quote),
						"subtitle": "Alpha Paper Trader",
						"description": "Paper orders automatically cancel after 6 months. If you'd like to keep your order, you'll have to set it again.",
						"color": 6765239,
						"user": authorId,
						"channel": order["channel"]
					})
					reference.delete()

				else:
					print("{}: paper {} order of {} {} on {} at {} expired".format(order["orderType"].replace("-", " "), Utils.format_amount(exchange.properties, ticker.symbol, order["amount"]), ticker.base, paperRequest.currentPlatform if exchange is None else exchange.name, order["price"], ticker.quote))

			else:
				socket.send_multipart([b"cronjob", b"candle", zlib.compress(pickle.dumps(paperRequest, -1))])
				responses = poller.poll(30 * 1000)

				if len(responses) != 0:
					response = socket.recv()
					payload, responseText = pickle.loads(zlib.decompress(response))

					if payload is None:
						if responseText is not None:
							print("Paper order request error", responseText)
							if os.environ["PRODUCTION_MODE"]: self.logging.report(responseText)
						return

					paperRequest.set_current(platform=payload["platform"])
					for candle in reversed(payload["candles"]):
						if candle[0] < order["timestamp"]: break
						if (candle[3] <= order["level"] and order["placement"] == "below") or (order["level"] <= candle[2] and order["placement"] == "above"):
							loop = asyncio.get_event_loop()
							accountProperties = loop.run_until_complete(self.accountProperties.get(accountId))

							if os.environ["PRODUCTION_MODE"]:
								if "paperTrader" in accountProperties:
									paper = accountProperties["paperTrader"]
									baseOrder = paper.get(ticker.base, 0)
									quoteOrder = paper.get(ticker.quote, 0)

									execAmount = order["amount"]
									isPricePercent, isLimitOrder, reduceOnly = order["parameters"]
									if reduceOnly and ((order["orderType"] == "buy" and baseOrder["amount"] >= 0) or (order["orderType"] == "sell" and baseOrder["amount"] <= 0)):
										order["status"] = "canceled"
										database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
											"title": "Paper {} order of {} {} on {} at {} {} expired.".format(order["orderType"].replace("-", " "), Utils.format_amount(exchange.properties, ticker.symbol, execAmount), ticker.base, paperRequest.currentPlatform if exchange is None else exchange.name, order["price"], ticker.quote),
											"subtitle": "Alpha Paper Trader",
											"description": "Paper orders automatically cancel after 6 months. If you'd like to keep your order, you'll have to set it again.",
											"color": 6765239,
											"user": authorId,
											"channel": order["channel"]
										})
										reference.delete()

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
									
										order["status"] = "filled"
										database.document("details/paperOrderHistory/{}/{}".format(accountId, str(uuid.uuid4()))).set(order)
										database.document("accounts/{}".format(accountId)).set({"paperTrader": {exchange.id: paper}}, merge=True)

										database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
											"title": "Paper {} order of {} {} on {} at {} {} was successfully executed.".format(order["orderType"].replace("-", " "), Utils.format_amount(exchange.properties, ticker.symbol, execAmount), ticker.base, paperRequest.currentPlatform if exchange is None else exchange.name, order["price"], ticker.quote),
											"subtitle": "Alpha Paper Trader",
											"description": None,
											"color": 6765239,
											"user": authorId,
											"channel": order["channel"]
										})
										reference.delete()

								else:
									print("{}: paper {} order of {} {} on {} at {} {} was successfully executed".format(order["orderType"].replace("-", " "), Utils.format_amount(exchange.properties, ticker.symbol, order["amount"]), ticker.base, paperRequest.currentPlatform if exchange is None else exchange.name, order["price"], ticker.quote))
							break

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
