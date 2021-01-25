import os
import sys
import time
import datetime
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
import math
import uuid
import pytz
import traceback
from urllib import request
import zmq
import zlib
import pickle

from engine.cache import Cache
from TickerParser import TickerParser, Ticker, supported

from pyvirtualdisplay import Display
from google.cloud import firestore, error_reporting

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import ui
from selenium.webdriver.support import expected_conditions as EC

from helpers.utils import Utils


database = firestore.Client()


class CronJobs(object):
	oldListings = {}
	accountProperties = {}

	zmqContext = zmq.Context.instance()

	accountsLink= None
	discordPropertiesUnregisteredUsersLink = None


	# -------------------------
	# Startup
	# -------------------------
	
	def __init__(self):
		self.logging = error_reporting.Client()
		self.cache = Cache(ttl=120)

		self.accountsLink = database.collection("accounts").on_snapshot(self.update_account_properties)
		self.discordPropertiesUnregisteredUsersLink = database.collection("discord/properties/users").order_by("marketAlerts").on_snapshot(self.update_unregistered_users_properties)


	# -------------------------
	# User management
	# -------------------------

	def update_account_properties(self, settings, changes, timestamp):
		"""Updates Alpha Account properties

		Parameters
		----------
		settings : [google.cloud.firestore_v1.document.DocumentSnapshot]
			complete document snapshot
		changes : [google.cloud.firestore_v1.watch.DocumentChange]
			database changes in the sent snapshot
		timestamp : int
			timestamp indicating time of change in the database
		"""

		try:
			for change in changes:
				properties = change.document.to_dict()
				accountId = change.document.id
				
				if change.type.name in ["ADDED", "MODIFIED"]:
					self.accountProperties[accountId] = properties
					if "userId" in self.accountProperties[accountId]["oauth"]["discord"]:
						userId = int(properties["oauth"]["discord"]["userId"])
						if userId in self.accountProperties:
							self.accountProperties.pop(userId)
				else:
					self.accountProperties.pop(accountId)

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def update_unregistered_users_properties(self, settings, changes, timestamp):
		"""Updates unregistered users properties

		Parameters
		----------
		settings : [google.cloud.firestore_v1.document.DocumentSnapshot]
			complete document snapshot
		changes : [google.cloud.firestore_v1.watch.DocumentChange]
			database changes in the sent snapshot
		timestamp : int
			timestamp indicating time of change in the database
		"""

		try:
			for change in changes:
				properties = change.document.to_dict()
				accountId = change.document.id

				if change.type.name in ["ADDED", "MODIFIED"]:
					if properties.get("connection") is not None: continue
					self.accountProperties[accountId] = properties
				else:
					self.accountProperties.pop(accountId)

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Job queue
	# -------------------------

	def run(self):
		while True:
			try:
				time.sleep(Utils.seconds_until_cycle())
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "1m" in timeframes:
					self.process_price_alerts()
					self.update_paper_limit_orders()

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

				if "15m" in timeframes:
					self.scrape_blogs()
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
			with ThreadPoolExecutor(max_workers=10) as pool:
				for accountId in list(self.accountProperties.keys()):
					marketAlerts = self.accountProperties[accountId].get("marketAlerts", []).copy()
					if len(marketAlerts) != 0:
						isRegistered = "customer" in self.accountProperties[accountId]
						authorId = self.accountProperties[accountId]["oauth"]["discord"].get("userId") if isRegistered else accountId
						if authorId is None: continue
						for key in marketAlerts:
							pool.submit(self.check_price_alert, authorId, accountId, isRegistered, marketAlerts.copy(), key)

					elif "customer" not in self.accountProperties[accountId] and "marketAlerts" in self.accountProperties[accountId]:
						database.document("discord/properties/users/{}".format(accountId)).update({"marketAlerts": firestore.DELETE_FIELD})

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def check_price_alert(self, authorId, accountId, isRegistered, marketAlerts, key):
		socket = CronJobs.zmqContext.socket(zmq.REQ)
		socket.connect("tcp://candle-server:6900")
		socket.setsockopt(zmq.LINGER, 3)
		poller = zmq.Poller()
		poller.register(socket, zmq.POLLIN)

		try:
			if self.cache.has(key):
				alert = self.cache.get(key)
			else:
				alert = database.document("details/marketAlerts/{}/{}".format(accountId, key)).get().to_dict()
				if alert is None: return
			self.cache.set(key, alert)

			alertRequest = pickle.loads(zlib.decompress(alert["request"]))
			ticker = alertRequest.get_ticker()
			exchange = alertRequest.get_exchange()

			if alertRequest.currentPlatform == "CCXT":
				levelText = Utils.format_price(exchange.properties, ticker.symbol, alert["level"])
			elif alertRequest.currentPlatform == "IEXC" or alertRequest.currentPlatform == "Quandl":
				levelText = "{:,.5f}".format(alert["level"])
			else:
				levelText = "{:,.0f}".format(alert["level"])

			socket.send_multipart([b"cronjob", b"candle", alert["request"]])
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

							marketAlerts.remove(key)
							database.document("details/marketAlerts/{}/{}".format(accountId, key)).delete()
							if isRegistered:
								database.document("accounts/{}".format(accountId)).set({"marketAlerts": marketAlerts}, merge=True)
							else:
								database.document("discord/properties/users/{}".format(accountId)).set({"marketAlerts": marketAlerts}, merge=True)

						else:
							print("{}: Price of {} ({}) hit {} {}.".format(accountId, ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote))
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
			platforms = ["TradingLite", "TradingView", "Bookmap", "GoCharting", "Alpha Flow", "CoinGecko", "CCXT", "IEXC", "Quandl", "Alpha Paper Trader", "Alpha Live Trader"]
			dataset = []
			tickerMap = {
				"traditional": [],
				"crypto": []
			}

			for platform in platforms:
				requests = database.collection("dataserver/statistics/{}".format(platform)).where("timestamp", ">", processingTimestamp - 86400 * 7).get()
				isCryptoPlatform = platform in ["TradingLite", "LLD", "CoinGecko", "CCXT", "Alpha Paper Trader", "Alpha Live Trader"]
				for e in requests:
					request = e.to_dict()
					if request["base"] in ["BTC.D", "BTC1!", "BLX", "XBT"]: request["base"] = "BTC"
					if request["base"] in ["ETH.D", "TOTAL2", "TOTAL", "OPTIONS"] or any([e in request["base"] for e in ["LONGS", "SHORTS"]]): continue
					if request["base"] in ["XAUUSD", "DXY"]: request["market"] = "traditional"

					exchange = None if platform not in supported.cryptoExchanges or request["exchange"] not in supported.cryptoExchanges[platform] else TickerParser.find_exchange(request["exchange"], platform)[1]
					matchingCryptoPair = TickerParser.find_ccxt_crypto_market(Ticker(request["base"].replace("PERP", "")), exchange, platform, {"exchange": None})[0]
					isInCcxtIndex = (matchingCryptoPair is not None and platform in ["CCXT", "Alpha Paper Trader"] and platform not in ["Alpha Flow"]) or (matchingCryptoPair is not None and len(matchingCryptoPair.base) * 0.8 <= len(request["base"]))

					request["market"] = request.get("market", "crypto" if isCryptoPlatform or isInCcxtIndex else "traditional")
					if request["market"] == "crypto" and isInCcxtIndex and matchingCryptoPair is not None and matchingCryptoPair.base != request["base"]: request["base"] = matchingCryptoPair.base
					dataset.append(request)

			for i in range(7, 0, -1):
				tickerMap["traditional"].append({})
				tickerMap["crypto"].append({})
				for request in dataset:
					if processingTimestamp - 86400 * i < request["timestamp"] <= processingTimestamp - 86400 * (i - 1):
						if request["base"] in tickerMap[request["market"]][-1]: tickerMap[request["market"]][-1][request["base"]] += 1
						else: tickerMap[request["market"]][-1][request["base"]] = 1

			sortedTickerMap = {
				"traditional": sorted(tickerMap["traditional"][-1].items(), key=lambda item: item[1]),
				"crypto": sorted(tickerMap["crypto"][-1].items(), key=lambda item: item[1])
			}

			maxScoreTraditional = sortedTickerMap["traditional"][-1][1]
			maxScoreCrypto = sortedTickerMap["crypto"][-1][1]

			topTraditionalTickers = [{"id": k, "rank": v / maxScoreTraditional * 100} for k, v in sortedTickerMap["traditional"][-20:]]
			topCryptoTickers = [{"id": k, "rank": v / maxScoreCrypto * 100} for k, v in sortedTickerMap["crypto"][-20:]]

			database.document("dataserver/statistics").set({
				"top": {
					"traditional": topTraditionalTickers,
					"crypto": topCryptoTickers
				},
				"upandcoming": {
					"traditional": [],
					"crypto": []
				}
			})

		except (KeyboardInterrupt, SystemExit): pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Data feeds
	# -------------------------

	def scrape_blogs(self):
		try:
			blogPosts = database.document("dataserver/blogs").get().to_dict()

			display = Display(visible=0, size=(1440, 960))
			display.start()

			options = Options()
			options.headless = False
			options.add_argument("--window-size=1440,960")
			options.add_argument('--use-gl')
			options.add_argument('--ignore-gpu-blacklist')
			options.add_argument('--disable-dev-shm-usage')
			options.add_argument("--hide-scrollbars")
			options.add_argument('--incognito')
			options.add_argument("--no-sandbox")
			chrome = webdriver.Chrome(options=options)
			wait = ui.WebDriverWait(chrome, 15)
			
			# TradingView
			try:
				chrome.get("https://www.tradingview.com/blog/en/")
				wait.until(EC.presence_of_all_elements_located((By.XPATH, "//div[@class='top-container']")))
				image = chrome.execute_script("return document.querySelector('.articles-grid').querySelectorAll('article')[0].querySelector('img').src")
				title = chrome.execute_script("return document.querySelector('.articles-grid').querySelectorAll('article')[0].querySelector('.title').innerText")
				category = chrome.execute_script("return document.querySelector('.articles-grid').querySelectorAll('article')[0].querySelector('.section').innerText").title()
				url = chrome.execute_script("return document.querySelector('.articles-grid').querySelectorAll('article')[0].querySelector('a').href")

				if blogPosts["TradingView"]["url"] != url:
					database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
						"title": title,
						"subtitle": "TradingView: {}".format(category),
						"description": None,
						"color": 2201331,
						"user": None,
						"channel": "738109593376260246",
						"url": url,
						"icon": "https://www.tradingview.com/favicon.ico",
						"image": image
					})

					blogPosts["TradingView"] = {
						"category": "TradingView: {}".format(category),
						"title": title,
						"image": image,
						"url": url
					}
			except: pass

			# BitMEX
			try:
				chrome.get("https://blog.bitmex.com")
				wait.until(EC.presence_of_all_elements_located((By.XPATH, "//div[@class='wpb_wrapper']")))
				image = chrome.execute_script("return document.querySelectorAll('.td-block-span12')[0].querySelector('img').src")
				title = chrome.execute_script("return document.querySelectorAll('.td-block-span12')[0].querySelector('.td-module-title').innerText")
				description = chrome.execute_script("return document.querySelectorAll('.td-block-span12')[0].querySelector('.td-excerpt').innerText")
				category = chrome.execute_script("return document.querySelectorAll('.td-block-span12')[0].querySelector('.td-post-category').innerText")
				url = chrome.execute_script("return document.querySelectorAll('.td-block-span12')[0].querySelector('.td-module-title').querySelector('a').href")

				if blogPosts["BitMEX"]["url"] != url:
					database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
						"title": title,
						"subtitle": "BitMEX: {}".format(category),
						"description": description,
						"color": 16515080,
						"user": None,
						"channel": "738427004969287781",
						"url": url,
						"icon": "https://www.bitmex.com/favicon.ico",
						"image": image
					})

					blogPosts["BitMEX"] = {
						"category": "BitMEX: {}".format(category),
						"title": title,
						"image": image,
						"url": url
					}
			except: pass

			# Binance
			try:
				chrome.get("https://www.binance.com/en/blog")
				wait.until(EC.presence_of_all_elements_located((By.XPATH, "//body")))
				image = chrome.execute_script("return document.querySelectorAll('.sc-153sum5-0')[0].querySelector('img').src")
				title = chrome.execute_script("return document.querySelectorAll('.sc-153sum5-0')[0].querySelector('.title').innerText")
				description = chrome.execute_script("return document.querySelectorAll('.sc-153sum5-0')[0].querySelector('.desc').innerText")
				url = chrome.execute_script("return document.querySelectorAll('.sc-153sum5-0')[0].querySelector('.read-btn').querySelector('a').href")

				if blogPosts["Binance"]["url"] != url:
					database.document("discord/properties/messages/{}".format(str(uuid.uuid4()))).set({
						"title": title,
						"subtitle": "Binance",
						"description": description,
						"color": 16306479,
						"user": None,
						"channel": "738427004969287781",
						"url": url,
						"icon": "https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fbinance.png?alt=media&token=5dd2b96c-0880-4b71-83f2-1afc88676133",
						"image": image
					})

					blogPosts["Binance"] = {
						"category": "Binance",
						"title": title,
						"image": image,
						"url": url
				}
			except: pass

			chrome.quit()
			display.stop()
			database.document("dataserver/blogs").set(blogPosts, merge=True)

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
