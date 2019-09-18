import os, os.path
import sys
import re
import random
import copy
import json
import time
import datetime
import pytz
import click
import requests
import urllib
from io import BytesIO
import threading
import argparse
import logging
import atexit

import discord
import asyncio
import ccxt
from PIL import Image

import google.oauth2.credentials
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from google.cloud import exceptions

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException
import selenium.webdriver.support.ui as ui
from selenium.webdriver.support import expected_conditions as EC

from bot.keys.keys import Keys as ApiKeys
from bot.helpers.utils import Utils
from bot.helpers.logger import Logger as l
from bot.helpers import constants

from bot.assistant.base import AlphaAssistant
from bot.engine.alerts import Alerts
from bot.engine.presets import Presets
from bot.engine.images import ImageProcessor
from bot.engine.coins import CoinParser
from bot.engine.coingecko import CoinGeckoLink
from bot.engine.trader import PaperTrader
from bot.engine.fusion import Fusion

try:
	# Firebase
	firebaseCredentials = credentials.Certificate("bot/keys/firebase credentials.json")
	firebase = firebase_admin.initialize_app(firebaseCredentials)
	db = firestore.client()

	# Google Assistant
	with open(os.path.join(click.get_app_dir("google-oauthlib-tool"), "credentials.json"), "r") as f:
		assistantCredentials = google.oauth2.credentials.Credentials(token=None, **json.load(f))
		http_request = google.auth.transport.requests.Request()
		assistantCredentials.refresh(http_request)
except KeyboardInterrupt: os._exit(1)
except Exception as e:
	exc_type, exc_obj, exc_tb = sys.exc_info()
	fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
	l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))
	os._exit(1)

# Google Assistant
grpc_channel = google.auth.transport.grpc.secure_authorized_channel(assistantCredentials, http_request, "embeddedassistant.googleapis.com")

# Command history
history = logging.getLogger("History")
history.setLevel(logging.DEBUG)
hfh = logging.FileHandler("command_history.log", mode="a")
hfh.setLevel(logging.DEBUG)
history.addHandler(hfh)

class Alpha(discord.AutoShardedClient):
	isBotReady = False
	dedicatedGuild = -1

	alerts = Alerts()
	imageProcessor = ImageProcessor()
	coinParser = CoinParser()
	coinGeckoLink = CoinGeckoLink()
	paperTrader = PaperTrader()
	fusion = Fusion()

	statistics = {"c": 0, "alerts": 0, "p": 0, "v": 0, "d": 0, "hmap": 0, "mcap": 0, "mk": 0, "alpha": 0}
	rateLimited = {"c": {}, "p": {}, "d": {}, "v": {}, "u": {}}
	usedPresetsCache = {}

	alphaSettings = {}
	subscribedUsers = []
	subscribedGuilds = []
	userProperties = {}
	guildProperties = {}


	def prepare(self, for_guild=-1):
		atexit.register(self.cleanup)
		self.dedicatedGuild = for_guild

		for side in constants.supportedExchanges:
			for id in constants.supportedExchanges[side]:
				if id not in self.coinParser.exchanges:
					self.rateLimited["p"][id] = {}
					self.rateLimited["d"][id] = {}
					self.rateLimited["v"][id] = {}
					self.coinParser.exchanges[id] = getattr(ccxt, id)()
					if self.coinParser.exchanges[id].has["fetchOHLCV"] and hasattr(self.coinParser.exchanges[id], "timeframes"):
						if id not in constants.supportedExchanges["ohlcv"]:
							l.log("New OHLCV data supported exchange: {}".format(id))
					if self.coinParser.exchanges[id].has["fetchOrderBook"]:
						if id not in constants.supportedExchanges["orderbook"]:
							l.log("New orderbook supported exchange: {}".format(id))

	async def on_ready(self):
		self.coinParser.refresh_coins()
		self.coinGeckoLink.refresh_coingecko_coin_list()
		self.fetch_settings()
		self.update_fusion_queue()
		self.server_ping()

		try:
			rawData = self.coinParser.exchanges["bitmex"].fetch_ohlcv(
				"BTC/USD",
				timeframe="1d",
				since=(self.coinParser.exchanges["bitmex"].milliseconds() - 24 * 60 * 60 * 5 * 1000)
			)
			self.coinParser.lastBitcoinPrice = rawData[-1][4]
		except: pass

		await self.wait_for_chunked()
		if sys.platform == "linux": self.update_guild_count()
		try:
			faqAndRulesChannel = client.get_channel(601160698310950914)
			rulesAndTOSMessage = await faqAndRulesChannel.fetch_message(601160743236141067)
			faq1Message = await faqAndRulesChannel.fetch_message(601163022529986560)
			faq2Message = await faqAndRulesChannel.fetch_message(601163058831818762)
			faq3Message = await faqAndRulesChannel.fetch_message(601163075126689824)
			await rulesAndTOSMessage.edit(content=constants.rulesAndTOS)
			await faq1Message.edit(content=constants.faq1)
			await faq2Message.edit(content=constants.faq2)
			await faq3Message.edit(content=constants.faq3)
		except: pass

		await self.update_subscribers()
		await self.update_premium_message()
		await self.security_check()
		await self.send_alerts()
		await self.update_system_status()
		await self.update_price_status()

		self.isBotReady = True
		l.log("Alpha is online on {} servers ({:,} users)".format(len(client.guilds), len(set(client.get_all_members()))), post=False)

	async def wait_for_chunked(self):
		for guild in client.guilds:
			if not guild.chunked: await asyncio.sleep(1)

	def cleanup(self):
		print("")
		l.log("Status", "timestamp: {}, description: Alpha bot is restarting".format(Utils.get_current_date()), post=sys.platform == "linux")

		try:
			for i in self.imageProcessor.screengrab:
				self.imageProcessor.screengrab[i].quit()
		except: pass

		try:
			if self.statistics["c"] > 0 and sys.platform == "linux":
				statisticsRef = db.document(u"alpha/statistics")
				for i in range(5):
					try:
						statisticsRef.set(self.statistics, merge=True)
						break
					except Exception as e:
						if i == 4: raise e
						else: time.sleep(5)
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def on_guild_join(self, guild):
		self.update_guild_count()
		if guild.id in constants.bannedGuilds:
			l.log("Status", "timestamp: {}, description: left a blocked server: {}".format(Utils.get_current_date(), message.guild.name))
			try: await guild.leave()
			except: pass

	async def on_guild_remove(self, guild):
		self.update_guild_count()

	def update_guild_count(self):
		try:
			url = "https://discordbots.org/api/bots/{}/stats".format(client.user.id)
			headers = {"Authorization": ApiKeys.get_discordbots_key()}
			payload = {"server_count": len(client.guilds)}
			requests.post(url, data=payload, headers=headers)
		except: pass

	def fetch_settings(self):
		try:
			settingsRef = db.document(u"alpha/settings")
			self.alphaSettings = settingsRef.get().to_dict()

			allUserSettingsRef = db.collection(u"alpha/settings/users")
			allUserSettings = allUserSettingsRef.stream()
			for settings in allUserSettings:
				self.userProperties[int(settings.id)] = settings.to_dict()

			allServerSettingsRef = db.collection(u"alpha/settings/servers")
			allServerSettings = allServerSettingsRef.stream()
			for settings in allServerSettings:
				self.guildProperties[int(settings.id)] = settings.to_dict()

			statisticsRef = db.document(u"alpha/statistics")
			statisticsData = statisticsRef.get().to_dict()
			if statisticsData is not None:
				for data in statisticsData:
					self.statistics[data] = statisticsData[data]
		except Exception as e:
			l.log("Status", "timestamp: {}, description: could not reach Firebase: {}".format(Utils.get_current_date(), e))
			time.sleep(15)
			self.fetch_settings()

	def update_fusion_queue(self):
		try:
			instances = self.fusion.manage_load_distribution(self.coinParser.exchanges)
			if sys.platform == "linux":
				try: db.document(u"fusion/distribution").set(instances)
				except: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_subscribers(self):
		try:
			alphaServer = client.get_guild(414498292655980583)
			role = discord.utils.get(alphaServer.roles, id=484387309303758848)

			for userId in self.userProperties:
				if self.userProperties[userId]["premium"]["subscribed"]:
					if self.userProperties[userId]["premium"]["plan"] != 0:
						fetchedSettingsRef = db.document(u"alpha/settings/users/{}".format(userId))
						self.userProperties[userId] = Utils.createUserSettings(self.userProperties[userId])
						if self.userProperties[userId]["premium"]["timestamp"] < time.time():
							try: self.subscribedUsers.remove(userId)
							except: pass
							self.userProperties[userId]["premium"]["subscribed"] = False
							self.userProperties[userId]["premium"]["hadWarning"] = False
							fetchedSettingsRef.set(self.userProperties[userId], merge=True)
							try:
								recepient = client.get_user(userId)
								await recepient.send("Your Alpha Premium subscription has expired")
								await alphaServer.get_member(userId).remove_roles(role)
							except: pass
						elif self.userProperties[userId]["premium"]["timestamp"] - 259200 < time.time() and not self.userProperties[userId]["premium"]["hadWarning"]:
							recepient = client.get_user(userId)
							self.userProperties[userId]["premium"]["hadWarning"] = True
							fetchedSettingsRef.set(self.userProperties[userId], merge=True)
							if recepient is not None:
								try: await recepient.send("Your Alpha Premium subscription expires on {}".format(self.userProperties[userId]["premium"]["date"]))
								except: pass
						elif userId not in self.subscribedUsers:
							self.subscribedUsers.append(userId)
					elif userId not in self.subscribedUsers:
						self.subscribedUsers.append(userId)

			for guildId in self.guildProperties:
				if self.guildProperties[guildId]["premium"]["subscribed"]:
					if self.guildProperties[guildId]["premium"]["plan"] != 0:
						fetchedSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
						self.guildProperties[guildId] = Utils.createServerSetting(self.guildProperties[guildId])
						if self.guildProperties[guildId]["premium"]["timestamp"] < time.time():
							try: self.subscribedGuilds.remove(guildId)
							except: pass
							self.guildProperties[guildId]["premium"]["subscribed"] = False
							self.guildProperties[guildId]["premium"]["hadWarning"] = False
							fetchedSettingsRef.set(self.guildProperties[guildId], merge=True)
							guild = client.get_guild(guildId)
							for member in guild.members:
								if member.guild_permissions.administrator:
									try: await member.send("Alpha Premium subscription for your *{}* server has expired".format(guild.name))
									except: pass
						elif self.guildProperties[guildId]["premium"]["timestamp"] - 259200 < time.time() and not self.guildProperties[guildId]["premium"]["hadWarning"]:
							guild = client.get_guild(guildId)
							self.guildProperties[guildId]["premium"]["hadWarning"] = True
							fetchedSettingsRef.set(self.guildProperties[guildId], merge=True)
							if guild is not None:
								for member in guild.members:
									if member.guild_permissions.administrator:
										try: await member.send("Alpha Premium subscription for your *{}* server expires on {}".format(guild.name, self.guildProperties[guildId]["premium"]["date"]))
										except: pass
						elif guildId not in self.subscribedGuilds:
							self.subscribedGuilds.append(guildId)
					elif guildId not in self.subscribedGuilds:
						self.subscribedGuilds.append(guildId)
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def security_check(self):
		try:
			settingsRef = db.document(u"alpha/settings")
			self.alphaSettings = settingsRef.get().to_dict()
			strGuilds = []
			for guild in client.guilds:
				strGuilds.append(guild.name)
			guildsToRemove = []
			for key in ["tosBlacklist", "tosWhitelist"]:
				for guild in self.alphaSettings[key]:
					if guild not in strGuilds: guildsToRemove.append(guild)
				for guild in guildsToRemove:
					if guild in self.alphaSettings[key]: self.alphaSettings[key].pop(guild)

			suspiciousUsers = []
			nicknames = []
			for guild in client.guilds:
				if guild.me is not None:
					isBlacklisted = guild.name in self.alphaSettings["tosBlacklist"]
					isWhitelisted = guild.name in self.alphaSettings["tosWhitelist"]

					if guild.me.nick is not None:
						if isBlacklisted:
							if guild.me.nick == self.alphaSettings["tosBlacklist"][guild.name]:
								if guild.me.guild_permissions.change_nickname:
									try:
										await guild.me.edit(nick=None)
										self.alphaSettings["tosBlacklist"].pop(guild.name)
									except: pass
								continue
							else: self.alphaSettings["tosBlacklist"].pop(guild.name)
						if isWhitelisted:
							if guild.me.nick == self.alphaSettings["tosWhitelist"][guild.name]: continue
							else: self.alphaSettings["tosWhitelist"].pop(guild.name)

						for i in range(0, len(guild.me.nick.replace(" ", "")) - 2):
							slice = guild.me.nick.lower().replace(" ", "")[i:i+3]
							if slice in guild.name.lower() and slice not in ["the"]:
								nicknames.append("{}: **{}**".format(guild.name, guild.me.nick))
								break
					else:
						if isBlacklisted: self.alphaSettings["tosBlacklist"].pop(guild.name)
						if isWhitelisted: self.alphaSettings["tosWhitelist"].pop(guild.name)

				for member in guild.members:
					if (member.name == "maco" or False if member.nick is None else member.nick.lower() == "maco") and member.id != 361916376069439490:
						if str(member.avatar_url) not in self.alphaSettings["avatarWhitelist"]:
							suspiciousUser = "**{}#{}** ({}): {}".format(member.name, member.discriminator, member.id, member.avatar_url)
							if suspiciousUser not in suspiciousUsers: suspiciousUsers.append(suspiciousUser)

			nicknamesMessage = ""
			suspiciousUsersMessage = ""
			if len(nicknames) > 0:
				nicknamesMessage = "These servers might be rebranding Alpha bot:\n● {}".format("\n● ".join(nicknames))
			if len(suspiciousUsers) > 0:
				suspiciousUsersMessage = "\n\nThese users might be impersonating Maco#9999:\n● {}".format("\n● ".join(suspiciousUsers))

			securityMessage = "Nothig to review..." if nicknamesMessage == "" and suspiciousUsersMessage == "" else nicknamesMessage + suspiciousUsersMessage

			usageReviewChannel = client.get_channel(571786092077121536)
			try:
				usageReviewMessage = await usageReviewChannel.fetch_message(571795135617302528)
				await usageReviewMessage.edit(content=securityMessage)
			except: pass

			if sys.platform == "linux":
				try: settingsRef.set(self.alphaSettings, merge=True)
				except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_premium_message(self):
		try:
			prices = {"BTC/USDT": 0, "ETH/USDT": 0}
			for symbol in prices:
				for _ in range(10):
					try:
						rawData = self.coinParser.exchanges["binance"].fetch_ohlcv(
							symbol,
							timeframe="5m",
							since=(self.coinParser.exchanges["binance"].milliseconds() - 60 * 60 * 5 * 1000)
						)
						prices[symbol] = rawData[-2][4]
						break
					except: await asyncio.sleep(self.coinParser.exchanges["binance"].rateLimit / 1000 * 2)

			if prices["BTC/USDT"] != 0 and prices["ETH/USDT"] != 0:
				monthlyPremiumText = "__**F E A T U R E S**__\n\nWith Alpha premium you'll get access to price alerts, command presets, message forwarding service (currently not available), as well as increased rate limits.\n\n**Price alerts (beta)**\nWith price alerts, you are able to get instant notifications through Discord via direct messages whenever the price of supported coins crosses a certain level or when popular indicators reach a certain value.\n\n**Command presets**\nPresets allow you to quickly call commands you use most often, create indicator sets and more.\n\n**Other perks**\nThe package also comes with raised limits. Instead of 10 charts, you can request up to 30 charts per minute.\n\n__**P R I C I N G**__\nSubscription of $15/month or $150/year in crypto.\nCurrent Bitcoin pricing: {:,.8f} BTC/month or {:,.8f} BTC/year.\nCurrent Ethereum pricing: {:,.8f} ETH/month or {:,.8f} ETH/year.\n\nPlease, contact <@!361916376069439490> for more details. All users are eligible for one month free trial. For server-wide premium subscription, check <#560475744258490369>.".format(15 / prices["BTC/USDT"], 150 / prices["BTC/USDT"], 15 / prices["ETH/USDT"], 150 / prices["ETH/USDT"])
				annualPremiumText = "__**F E A T U R E S**__\n\nWith Alpha premium you'll get access to price alerts, command presets, message forwarding service (currently not available), a dedicated VPS, as well as increased rate limits. All premium features are available to all users across all servers with Alpha server-wide Premium\n\n**Personal and server-wide (coming soon) price alerts (beta)**\nWith price alerts, you are able to get instant notifications through Discord via direct messages whenever the price of supported coins crosses a certain level or when popular indicators reach a certain value. Server-wide price alers go a step further by alowing server owners to set price alerts for all users via a specified channel.\n\n**Personal and server-wide command presets**\nPresets allow you to quickly call commands you use most often, create indicator sets and more. Similarly to price alerts, server-wide command presets allow server owners to create presets available to all users in a server.\n\n**Dedicated VPS**\nA dedicated virtual private server will deliver cutting edge performance of Alpha in your discord server even during high load.\n\n**Other perks**\nThe package also comes with raised limits. Instead of 10 charts, you can request up to 30 charts per minute.\n\n__**P R I C I N G**__\nSubscription of $100/month or $1000/year in crypto. A dedicated VPS can be purchased separately for $50/month.\nCurrent Bitcoin pricing: {:,.8f} BTC/month or {:,.8f} BTC/year.\nCurrent Ethereum pricing: {:,.8f} ETH/month or {:,.8f} ETH/year.\n\nPlease, contact <@!361916376069439490> for more details. All servers are eligible for one month free trial (dedicated VPS is not included). For personal premium subscription, check <#509428086979297320>".format(100 / prices["BTC/USDT"], 1000 / prices["BTC/USDT"], 100 / prices["ETH/USDT"], 1000 / prices["ETH/USDT"])

				monthlyPremiumChannel = client.get_channel(509428086979297320)
				annualPremiumChannel = client.get_channel(560475744258490369)
				try:
					monthlyPremiumMessage = await monthlyPremiumChannel.fetch_message(569205237802336287)
					annualPremiumMessage = await annualPremiumChannel.fetch_message(569212387123789824)
					await monthlyPremiumMessage.edit(content=monthlyPremiumText)
					await annualPremiumMessage.edit(content=annualPremiumText)
				except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_system_status(self):
		try:
			statisticsRef = db.document(u"alpha/statistics")
			statisticsRef.set(self.statistics, merge=True)

			numOfCharts = "**{:,}** charts requested".format(self.statistics["c"] + self.statistics["hmap"])
			numOfPrices = "**{:,}** prices pulled".format(self.statistics["d"] + self.statistics["p"] + self.statistics["v"])
			numOfAlerts = "**{:,}** alerts set".format(self.statistics["alerts"])
			numOfDetails = "**{:,}** coin details looked up".format(self.statistics["mcap"] + self.statistics["mk"])
			numOfQuestions = "**{:,}** questions asked".format(self.statistics["alpha"])
			numOfServers = "Used in **{:,}** servers by **{:,}** users".format(len(client.guilds), len(set(client.get_all_members())))
			statsText = "{}\n{}\n{}\n{}\n{}\n{}".format(numOfCharts, numOfPrices, numOfAlerts, numOfDetails, numOfQuestions, numOfServers)

			req = urllib.request.Request("https://status.discordapp.com", headers={"User-Agent": "Mozilla/5.0"})
			webpage = str(urllib.request.urlopen(req).read())
			isDiscordWorking = "All Systems Operational" in webpage

			statusText = "Discord: **{}**\nAverage ping: **{:,.1f}** milliseconds\nProcessing **{:,.0f}** messages per minute".format("operational" if isDiscordWorking else "degraded performance", self.fusion.averagePing * 1000, self.fusion.averageMessages)
			alphaText = "Alpha: **online**"

			if sys.platform == "linux":
				channel = client.get_channel(560884869899485233)
				if self.statistics["c"] > 0 and sys.platform == "linux":
					try:
						statsMessage = await channel.fetch_message(615114371508731914)
						await statsMessage.edit(content=statsText)
					except: pass

				try:
					statusMessage = await channel.fetch_message(615119428899831878)
					await statusMessage.edit(content=statusText)
				except: pass

				try:
					alphaMessage = await channel.fetch_message(615137416780578819)
					await alphaMessage.edit(content=alphaText)
				except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_price_status(self):
		cycle = int(datetime.datetime.now().astimezone(pytz.utc).second/15)
		fetchPairs = {
			0: (("bitmex", "MEX"), ("BTCUSD", "BTC/USD"), ("ETHUSD", "ETH/USD")),
			1: (("binance", "BIN"), ("BTCUSD", "BTC/USDT"), ("ETHUSDT", "ETH/USDT")),
			2: (("bitmex", "MEX"), ("BTCUSD", "BTC/USD"), ("ETHUSD", "ETH/USD")),
			3: (("binance", "BIN"), ("BTCUSD", "BTC/USDT"), ("ETHUSDT", "ETH/USDT"))
		}

		price1 = None
		try:
			if fetchPairs[cycle][1][0] in self.rateLimited["p"][fetchPairs[cycle][0][0]]:
				price1 = self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][1][0]]
			else:
				rawData = self.coinParser.exchanges[fetchPairs[cycle][0][0]].fetch_ohlcv(
					fetchPairs[cycle][1][1],
					timeframe="1d",
					since=(self.coinParser.exchanges[fetchPairs[cycle][0][0]].milliseconds() - 24 * 60 * 60 * 5 * 1000)
				)
				price1 = (rawData[-1][4], rawData[-2][4])
				self.coinParser.lastBitcoinPrice = price1[0]
				self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][0][0]] = price1
		except: self.coinParser.refresh_coins()

		price2 = None
		try:
			if fetchPairs[cycle][2][0] in self.rateLimited["p"][fetchPairs[cycle][0][0]]:
				price2 = self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][2][0]]
			else:
				rawData = self.coinParser.exchanges[fetchPairs[cycle][0][0]].fetch_ohlcv(
					fetchPairs[cycle][2][1],
					timeframe="1d",
					since=(self.coinParser.exchanges[fetchPairs[cycle][0][0]].milliseconds() - 24 * 60 * 60 * 5 * 1000)
				)
				price2 = (rawData[-1][4], rawData[-2][4])
				self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][2][0]] = price2
		except: self.coinParser.refresh_coins()

		price1Text = " -" if price1 is None else "{:,.0f}".format(price1[0])
		price2Text = " -" if price2 is None else "{:,.0f}".format(price2[0])

		try: await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="{} ₿ {} Ξ {}".format(fetchPairs[cycle][0][1], price1Text, price2Text)))
		except: pass

		await asyncio.sleep(self.coinParser.exchanges[fetchPairs[cycle][0][0]].rateLimit / 1000 * 2)
		await asyncio.sleep(self.coinParser.exchanges[fetchPairs[cycle][0][0]].rateLimit / 1000 * 2)

		try:
			self.rateLimited["p"][fetchPairs[cycle][0][0]].pop(fetchPairs[cycle][1][0], None)
			self.rateLimited["p"][fetchPairs[cycle][0][0]].pop(fetchPairs[cycle][2][0], None)
		except: pass

	async def send_alerts(self):
		try:
			incomingAlertsChannel = client.get_channel(605419986164645889)
			if sys.platform == "linux" and incomingAlertsChannel is not None:
				alertMessages = await incomingAlertsChannel.history(limit=None).flatten()
				for message in reversed(alertMessages):
					userId, alertMessage = message.content.split(": ", 1)
					alertUser = client.get_user(int(userId))

					try:
						await alertUser.send(alertMessage)
					except:
						outgoingAlertsChannel = client.get_channel(595954290409865226)
						try: await outgoingAlertsChannel.send("<@!{}>! {}".format(alertUser.id, alertMessage))
						except: pass

					try: await message.delete()
					except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	def server_ping(self):
		try:
			if sys.platform == "linux":
				for i in range(5):
					try:
						db.document(u'fusion/alpha').set({"lastUpdate": {"timestamp": time.time(), "time": datetime.datetime.now().strftime("%m. %d. %Y, %H:%M")}}, merge=True)
						break
					except Exception as e:
						if i == 4:
							exc_type, exc_obj, exc_tb = sys.exc_info()
							fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
							l.log("Warning", "({}) debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))
						else: time.sleep(1)

			for i in range(5):
				try:
					instances = db.collection(u'fusion').stream()
					for instance in instances:
						num = str(instance.id)
						if num.startswith("instance"):
							num = int(str(instance.id).split("-")[-1])
							instance = instance.to_dict()
							if instance["lastUpdate"]["timestamp"] + 360 < time.time():
								l.log("Warning", "timestamp: {}, description: Fusion instance {} is not responding".format(Utils.get_current_date(), num))
					break
				except Exception as e:
					if i == 4:
						exc_type, exc_obj, exc_tb = sys.exc_info()
						fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
						l.log("Warning", "({}) debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))
					else: time.sleep(1)
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_queue(self):
		while True:
			n = datetime.datetime.now().astimezone(pytz.utc)
			await asyncio.sleep((15 - n.second % 15) - ((time.time() * 1000) % 1000) / 1000)
			if not self.isBotReady: continue
			timeframes = Utils.get_accepted_timeframes()

			await self.update_price_status()
			await self.send_alerts()
			if "5m" in timeframes:
				await self.update_system_status()
				self.server_ping()
			if "1H" in timeframes:
				await self.update_premium_message()
				await self.security_check()
				await self.update_subscribers()
			if "1D" in timeframes:
				self.coinGeckoLink.refresh_coingecko_coin_list()
				self.update_fusion_queue()

	async def on_message(self, message):
		try:
			guildId = message.guild.id if message.guild is not None else -1
			if self.dedicatedGuild != 0 and self.dedicatedGuild != guildId: return

			isSelf = message.author == client.user
			isUserBlocked = message.author.id in constants.blockedUsers if not message.author.bot else any(e in message.author.name.lower() for e in constants.blockedBotNames) or message.author.id in constants.blockedBots
			isChannelBlocked = message.channel.id in constants.blockedChannels or guildId in constants.blockedGuilds
			hasContent = message.clean_content != ""

			if not self.isBotReady or isUserBlocked or isChannelBlocked or not hasContent: return

			isPersonalPremium = message.author.id in self.subscribedUsers
			isServerPremium = guildId in self.subscribedGuilds
			isPremium = isPersonalPremium or isServerPremium

			raw = " ".join(message.clean_content.lower().split())
			sentMessages = []
			shortcutsEnabled = True
			presetUsed = False
			shortcutUsed = False
			limit = 30 if isPremium else 10
			hasMentions = len(message.mentions) != 0 or len(message.channel_mentions) != 0 or len(message.role_mentions) != 0 or "@everyone" in message.content or "@here" in message.content
			hasSendPermission = (True if message.guild is None else message.guild.me.permissions_in(message.channel).send_messages)

			if not raw.startswith("preset "):
				if isPremium:
					usedPresets = []
					if message.author.id in self.userProperties: raw, presetUsed, usedPresets = Presets.process_presets(raw, self.userProperties[message.author.id])
					if not presetUsed and guildId in self.guildProperties: raw, presetUsed, usedPresets = Presets.process_presets(raw, self.guildProperties[guildId])

					if guildId != -1:
						if guildId not in self.usedPresetsCache: self.usedPresetsCache[guildId] = []
						for preset in usedPresets:
							if preset not in self.usedPresetsCache[guildId]: self.usedPresetsCache[guildId].append(preset)
						self.usedPresetsCache[guildId] = self.usedPresetsCache[guildId][-3:]
				elif guildId in self.usedPresetsCache:
					presetUsed = False
					for preset in self.usedPresetsCache[guildId]:
						if preset["phrase"] == raw:
							presetUsed = True

			isCommand = raw.startswith(("alpha ", "alert ", "preset ", "c ", "p ", "d ", "v ", "hmap ", "mcap ", "mc", "mk ", "paper ")) or raw in ["hmap"]

			if presetUsed and isCommand:
				if isPremium:
					try:
						presetMessage = await message.channel.send("Running `{}` command from preset".format(raw))
						sentMessages.append(presetMessage)
					except: pass
				else:
					try: await message.channel.send("Presets are available for premium members only.\n\n{}".format(constants.premiumMessage))
					except: pass
					return

			if guildId != -1:
				if guildId in self.guildProperties: shortcutsEnabled = self.guildProperties[guildId]["functions"]["shortcuts"]

				if isCommand:
					if message.guild.name in self.alphaSettings["tosBlacklist"]:
						await message.channel.send("This server is violating terms of service:\n\n{}\n\nFor more info, join Alpha server:".format(constants.termsOfService))
						await message.channel.send("https://discord.gg/GQeDE85")

			raw, isCommand, shortcutUsed = Utils.shortcuts(raw, isCommand, shortcutsEnabled)

			useMute = isCommand and (presetUsed or shortcutUsed)

			if raw.startswith("a "):
				if message.author.bot: return

				command = raw.split(" ", 1)[1]
				if command == "help":
					await self.help(message, raw, shortcutUsed)
					return
				elif command == "invite":
					try: await message.channel.send("https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372033")
					except: await self.unknown_error(message)
					return
				if guildId != -1:
					if command.startswith("assistant"):
						if message.author.guild_permissions.administrator:
							newVal = None
							if command == "assistant disable": newVal = False
							elif command == "assistant enable": newVal = True

							if newVal is not None:
								serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
								serverSettings = serverSettingsRef.get().to_dict()
								serverSettings = Utils.updateServerSetting(serverSettings, "functions", sub="assistant", toVal=newVal)
								serverSettingsRef.set(serverSettings, merge=True)
								self.guildProperties[guildId] = copy.deepcopy(serverSettings)

								try: await message.channel.send("Google Assistant settings saved for **{}** server".format(message.guild.name))
								except: await self.unknown_error(message)
						return
					elif command.startswith("shortcuts"):
						if message.author.guild_permissions.administrator:
							newVal = None
							if command == "shortcuts disable": newVal = False
							elif command == "shortcuts enable": newVal = True

							if newVal is not None:
								serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
								serverSettings = serverSettingsRef.get().to_dict()
								serverSettings = Utils.updateServerSetting(serverSettings, "functions", sub="shortcuts", toVal=newVal)
								serverSettingsRef.set(serverSettings, merge=True)
								self.guildProperties[guildId] = copy.deepcopy(serverSettings)

								try: await message.channel.send("Shortcuts settings saved for **{}** server".format(message.guild.name))
								except: await self.unknown_error(message)
						return
					elif command.startswith("autodelete"):
						if message.author.guild_permissions.administrator:
							if message.guild.me.guild_permissions.manage_messages:
								newVal = None
								if command == "autodelete disable": newVal = False
								elif command == "autodelete enable": newVal = True

								if newVal is not None:
									serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
									serverSettings = serverSettingsRef.get().to_dict()
									serverSettings = Utils.updateServerSetting(serverSettings, "functions", sub="autodelete", toVal=newVal)
									serverSettingsRef.set(serverSettings, merge=True)
									self.guildProperties[guildId] = copy.deepcopy(serverSettings)

									try: await message.channel.send("Autodelete settings saved for **{}** server".format(message.guild.name))
									except: await self.unknown_error(message)
							else:
								try: await message.channel.send("To change autodelete settings, make sure Alpha has permission to manage messages")
								except: await self.unknown_error(message)
						return
				if message.author.id in [361916376069439490, 164073578696802305, 390170634891689984]:
					if command.startswith("premium user"):
						subscription = raw.split("premium user ", 1)
						if len(subscription) == 2:
							parameters = subscription[1].split(" ", 1)
							if len(parameters) == 2:
								userId, plan = parameters
								trial = False
								try:
									if plan == "trial": plan, trial = 1, True

									alphaServer = client.get_guild(414498292655980583)
									recepient = client.get_user(int(userId))
									role = discord.utils.get(alphaServer.roles, id=484387309303758848)

									fetchedSettingsRef = db.document(u"alpha/settings/users/{}".format(int(userId)))
									fetchedSettings = fetchedSettingsRef.get().to_dict()
									fetchedSettings = Utils.createUserSettings(fetchedSettings)

									hadTrial = fetchedSettings["premium"]["hadTrial"]
									wasSubscribed = fetchedSettings["premium"]["subscribed"]
									if hadTrial and trial:
										if not wasSubscribed:
											try:
												await message.channel.send("This user already had a trial")
												await recepient.send("Your Alpha Premium subscription trial has already expired")
											except: pass
										try: await message.delete()
										except: pass
										return

									lastTimestamp = fetchedSettings["premium"]["timestamp"]
									timestamp = (lastTimestamp if time.time() < lastTimestamp else time.time()) + 2635200 * int(plan)
									date = datetime.datetime.fromtimestamp(timestamp)
									fetchedSettings["premium"] = {"subscribed": True, "hadTrial": hadTrial or trial, "hadWarning": False, "timestamp": timestamp, "date": date.strftime("%m. %d. %Y"), "plan": int(plan)}

									fetchedSettingsRef.set(fetchedSettings, merge=True)
									self.userProperties[int(userId)] = copy.deepcopy(fetchedSettings)
									self.subscribedUsers.append(int(userId))
									try: await alphaServer.get_member(int(userId)).add_roles(role)
									except: pass

									if int(plan) > 0:
										if wasSubscribed:
											try:
												await recepient.send("Your Alpha Premium subscription was extended.\n*Current expiry date: {}*".format(fetchedSettings["premium"]["date"]))
											except:
												outgoingAlertsChannel = client.get_channel(595954290409865226)
												try: await outgoingAlertsChannel.send("<@!{}>, your Alpha Premium subscription was extended.\n*Current expiry date: {}*".format(userId, fetchedSettings["premium"]["date"]))
												except: pass
										else:
											try:
												await recepient.send("Enjoy your Alpha Premium subscription.\n*Current expiry date: {}*".format(fetchedSettings["premium"]["date"]))
											except:
												outgoingAlertsChannel = client.get_channel(595954290409865226)
												try: await outgoingAlertsChannel.send("<@!{}>, enjoy your Alpha Premium subscription.\n*Current expiry date: {}*".format(userId, fetchedSettings["premium"]["date"]))
												except: pass
									try: await message.delete()
									except: pass
								except Exception as e:
									exc_type, exc_obj, exc_tb = sys.exc_info()
									fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
									try: await message.channel.send("[Error]: timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
									except: pass
						return
					elif command.startswith("premium server"):
						subscription = raw.split("premium server ", 1)
						if len(subscription) == 2:
							parameters = subscription[1].split(" ", 1)
							if len(parameters) == 2:
								guildId, plan = parameters
								trial = False
								try:
									if plan == "trial": plan, trial = 1, True

									setGuild = client.get_guild(int(guildId))
									recepients = []
									for member in setGuild.members:
										if member.guild_permissions.administrator:
											recepients.append(member)

									fetchedSettingsRef = db.document(u"alpha/settings/servers/{}".format(int(guildId)))
									fetchedSettings = fetchedSettingsRef.get().to_dict()
									fetchedSettings = Utils.createServerSetting(fetchedSettings)

									hadTrial = fetchedSettings["premium"]["hadTrial"]
									wasSubscribed = fetchedSettings["premium"]["subscribed"]
									if hadTrial and trial:
										if not wasSubscribed:
											for recepient in recepients:
												try: await recepient.send("Alpha Premium subscription trial for *{}* server has already expired".format(setGuild.name))
												except: pass
										try: await message.delete()
										except: pass
										return

									lastTimestamp = fetchedSettings["premium"]["timestamp"]
									timestamp = (lastTimestamp if time.time() < lastTimestamp else time.time()) + 2635200 * int(plan)
									date = datetime.datetime.fromtimestamp(timestamp)
									fetchedSettings["premium"] = {"subscribed": True, "hadTrial": hadTrial or trial, "hadWarning": False, "timestamp": timestamp, "date": date.strftime("%m. %d. %Y"), "plan": int(plan)}

									fetchedSettingsRef.set(fetchedSettings, merge=True)
									self.guildProperties[int(guildId)] = copy.deepcopy(fetchedSettings)
									self.subscribedGuilds.append(int(guildId))

									if int(plan) > 0:
										if wasSubscribed:
											for recepient in recepients:
												try: await recepient.send("Alpha Premium subscription for **{}** server was extended.\n*Current expiry date: {}*".format(setGuild.name, fetchedSettings["premium"]["date"]))
												except: pass
										else:
											for recepient in recepients:
												try: await recepient.send("Enjoy Alpha Premium subscription for **{}** server.\n*Current expiry date: {}*".format(setGuild.name, fetchedSettings["premium"]["date"]))
												except: pass
									try: await message.delete()
									except: pass
								except Exception as e:
									exc_type, exc_obj, exc_tb = sys.exc_info()
									fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
									try: await message.channel.send("[Error]: timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
									except: pass
						return
					elif command == "restart":
						self.isBotReady = False
						channel = client.get_channel(560884869899485233)
						try:
							await message.delete()
							alphaMessage = await channel.fetch_message(615137416780578819)
							await alphaMessage.edit(content="Alpha: **restarting**")
						except: pass
						l.log("Status", "A restart has been requested by {}. Timestamp: {}".format(message.author.name, Utils.get_current_date()), post=False)
						raise KeyboardInterrupt
					elif command == "reboot":
						self.isBotReady = False
						channel = client.get_channel(560884869899485233)
						try:
							await message.delete()
							alphaMessage = await channel.fetch_message(615137416780578819)
							await alphaMessage.edit(content="Alpha: **restarting**")
						except: pass
						l.log("Status", "A reboot has been requested by {}. Timestamp: {}".format(message.author.name, Utils.get_current_date()), post=False)
						if sys.platform == "linux": os.system("sudo reboot")
						return
					else:
						await self.fusion.process_private_function(client, message, raw, self.coinParser.exchanges, guildId, self.coinParser.lastBitcoinPrice, db)
						return
			elif not isSelf and isCommand and hasSendPermission and not hasMentions:
				if message.content.startswith(("alpha ", "<@401328409499664394> ", "alpha, ", "<@401328409499664394>, ")):
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					command = raw.split(" ")[1]
					if command == "help":
						await self.help(message, raw, shortcutUsed)
					elif command == "premium":
						try: await message.channel.send(constants.premiumMessage)
						except: await self.unknown_error(message)
					elif command == "invite":
						try: await message.channel.send("https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372033")
						except: await self.unknown_error(message)
					elif command == "status":
						try: await message.channel.send("Average ping: **{:,.1f}** milliseconds\nProcessing **{:,.0f}** messages per minute".format(self.fusion.averagePing * 1000, self.fusion.averageMessages))
						except: await self.unknown_error(message)
					elif (self.guildProperties[guildId]["functions"]["assistant"] if guildId in self.guildProperties else True):
						self.statistics["alpha"] += 1
						rawCaps = message.content.split(" ", 1)[1]
						if len(rawCaps) > 500: return

						try: await message.channel.trigger_typing()
						except: pass

						if await self.funnyReplies(message, raw): return
						with AlphaAssistant("en-US", "nlc-bot-36685-nlc-bot-9w6rhy", "Alpha", False, grpc_channel, 60 * 3 + 5) as assistant:
							try: response, response_html = assistant.assist(text_query=rawCaps)
							except:
								await self.unknown_error(message)
								return
							if response	is not None and response != "":
								if "Here are some things you can ask for:" in response:
									await self.help(message, raw, shortcutUsed)
								elif any(trigger in response for trigger in constants.badPunTrigger):
									with open("bot/assets/jokes.json") as json_data:
										try: await message.channel.send("Here's a pun that might make you laugh :smile:\n{}".format(random.choice(json.load(json_data))))
										except: await self.unknown_error(message)
								else:
									for override in constants.messageOverrides:
										for trigger in constants.messageOverrides[override]:
											if raw == trigger:
												try: await message.channel.send(override)
												except: pass
												return
									try: await message.channel.send(response.replace("Google Assistant", "Alpha"))
									except: await self.unknown_error(message)
							else:
								try: await message.channel.send("I can't help you with that.")
								except: await self.unknown_error(message)
				elif raw.startswith(("alert ", "alerts ")):
					if message.author.bot:
						if not await self.bot_verification(message, raw, mute=True): return

					if raw in ["alert help", "alerts help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"Adding price alerts ```alert set <coin> <price>```",
							"Listing all set alerts ```alert list```",
							"List all your price alerts ```alert list```",
							"__**Examples:**__",
							"`alert set btc 90000` set alert for BTC/USDT on Binance when price hits $90,000",
							"`alert set xbt 1000` set alert for XBT/USD on BitMEX when price hits $1000",
							"`alert set ada 0.00000700` set alert for ADA/BTC on Binance when price hits 0.00000700 BTC"
							"__**Notes:**__",
							"Alerts only support Binance and BitMEX at this moment. More exchanges coming soon"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					else:
						if isPremium:
							slices = re.split(", alert | alert |, alerts | alerts |, ", raw.split(" ", 1)[1])
							if len(slices) > 5:
								await self.hold_up(message, isPremium)
								return
							for slice in slices:
								await self.alert(message, slice, useMute)
						else:
							try: await message.channel.send("Price alerts are available for premium members only.\n\n{}".format(constants.premiumMessage))
							except: pass
				elif raw.startswith("preset "):
					if message.author.bot:
						if not await self.bot_verification(message, raw, mute=True): return

					if raw in ["preset help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"Add a preset invoked by typing `<name>` ```preset add <name> <command>```",
							"List all presets ```preset list```",
							"__**Examples:**__",
							"`preset set btc15 c btc bfx 4h rsi srsi macd` get 4h Binance chart with RSI, SRSI, & MACD indicators when you type *btc15* in the chat",
							"__**Notes:**__",
							"Preset names have to be made out of a single word. They can then be used as commands on their own eg. `btc15` or within commands themselves eg. `c btc bfx myindicators`"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					else:
						if isPremium:
							slices = re.split(", preset | preset", raw.split(" ", 1)[1])
							if len(slices) > 5:
								await self.hold_up(message, isPremium)
								return
							for slice in slices:
								await self.presets(message, slice, guildId, useMute)
						else:
							try: await message.channel.send("Presets are available for premium members only.\n\n{}".format(constants.premiumMessage))
							except: pass
				elif raw.startswith("c "):
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					if raw in ["c help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"```c <coin> <exchange> <timeframe(s)> <candle type> <indicators>```",
							"__**Examples:**__",
							"`c btc` Binance BTC chart",
							"`c xbt log` BitMEX BTC log chart",
							"`c $coin link` get coin/USD(T) chart with links to interactive graphs",
							"`c btc 15m 1d 1w white` 15m, 1d, 1w Binance white theme charts",
							"`c btc bfx 4h rsi srsi macd` 4h Binance chart with RSI, SRSI, & MACD indicators",
							"`c xbt 1h-1w bb ic rsi` 1h, 4h, 1w BitMEX charts for XBT (perpetual BTC) with BB, IC, RSI indicators",
							"`c ada b 1-1h rsi srsi obv` 1m, 3m, 5m, 15m, 30m, 1h Binance charts for ADA/BTC with RSI, SRSI, OBV indicators",
							"`c $bnb bi 15m-1h bb ic mfi` 15m, 30m, & 1h Binance charts for BNB/USDT with BB, IC, and MFI indicators",
							"`c bnbusd b 15m-1h bb ic mfi` 15m, 30m, & 1h Binance charts for BNB/USDT",
							"`c etcusd btrx 1w-4h bb ic mfi` 1w, 1 day, & 4 hour Bittrex charts for ETC/USD",
							"`c $ltc cbp 1w-4h bb ic mfi` 1w, 1d, & 4h Coinbase Pro charts for LTC/USD",
							"Use `c <coin> shorts` or `c <coin> s` for shorts chart and `c <coin> longs` or `c <coin> l` for longs chart",
							"__**Notes:**__",
							"Type `c parameters` for the complete indicator & timeframes list"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					elif raw in ["c parameters", "c indicators", "c timeframes", "c exchanges"]:
						availableIndicators = [
							"**NV** (no volume indicator)", "**ACCD** (Accumulation/Distribution)", "**ADR**", "**Aroon**", "**ATR** (Average True Range)", "**Awesome** (Awesome Oscillator)",
							"**BB** (Bollinger Bands)", "**BBW** (Bollinger Bands Width)", "**CMF** (Chaikin Money Flow)", "**Chaikin** (Chaikin Oscillator)", "**Chande** (Chande Momentum Oscillator)",
							"**CI** (Choppiness Index)", "**CCI** (Commodity Channel Index)", "**CRSI** (ConnorsRSI)", "**CC** (Correlation Coefficient)", "**DPO** (Detrended Price Oscillator)",
							"**DM** (Directional Movement)", "**DONCH** (Donchian Channels)", "**DEMA** (Double EMA)", "**EOM** (Ease Of Movement)", "**EFI** (Elder's Force Index)",
							"**EW** (Elliott Wave)", "**ENV** (Envelope)", "**Fisher** (Fisher Transform)", "**HV** (Historical Volatility)", "**HMA** (Hull Moving Average)",
							"**Ichimoku** (Ichimoku Cloud)", "**Keltner** (Keltner Channels)", "**KST** (Know Sure Thing)", "**LR** (Linear Regression)", "**MACD**", "**MOM** (Momentum)",
							"**MFI** (Money Flow Index)", "**Moon** (Moon Phases)", "**MA** (Moving Average)", "**EMA** (Moving Average Exponentional)", "**WMA** (Moving Average Weighted)",
							"**OBV** (On Balance Volume)", "**PSAR** (Parabolic SAR)", "**PPHL** (Pivot Points High Low)", "**PPS** (Pivot Points Standard)", "**PO** (Price Oscillator)",
							"**PVT** (Price Volume Trend)", "**ROC** (Rate Of Change)", "**RSI** (Relative Strength Index)", "**VI** (Relative Vigor Index)", "**RVI** (Relative Volatility Index)",
							"**SMIEI** (SMI Ergodic Indicator)", "**SMIEO** (SMI Ergodic Oscillator)", "**Stoch** (Stochastic)", "**SRSI** (Stochastic RSI)", "**TEMA** (Triple EMA)", "**TRIX**",
							"**Ultimate** (Ultimate Oscillator)", "**VSTOP** (Volatility Stop)", "**VWAP**", "**VWMA** (Volume Weighted Moving Average)", "**WilliamsR** (Williams %R)", "**WilliamsA** (Williams Alligator)",
							"**WF** (Williams Fractal)", "**ZZ** (Zig Zag)"
						]
						try:
							await message.channel.send("Indicators: {}".format(", ".join(availableIndicators)))
							await message.channel.send("Timeframes: **1/3/5/15/30-minute**, **1/2/3/4-hour**, **daily** and **weekly**")
							await message.channel.send("Exchanges: Binance, Coinbase Pro, Bittrex, Poloniex, Kraken, BitMEX, Bitfinex, Bitflyer, OKCoin, Bithumb, Bitso, Bitstamp, BTCChina, Cobinhood, Coinfloor, Foxbit, Gemini, HitBTC, Huobi Pro, itBit, Mercado")
							await message.channel.send("Candle types: bars, candles, heikin ashi, line break, line, area, renko, kagi, point&figure")
							await message.channel.send("Additional parameters: log, white, link")
						except: await self.unknown_error(message)
					else:
						slices = re.split(", c | c |, ", raw.split(" ", 1)[1])
						totalWeight = len(slices)
						if totalWeight > 5:
							await self.hold_up(message, isPremium)
							return
						for slice in slices:
							if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += 2
							else: self.rateLimited["u"][message.author.id] = 2

							if self.rateLimited["u"][message.author.id] >= limit:
								try: await message.channel.send("<@!{}>, you reached a limit of **{} requests per minute**".format(message.author.id, limit))
								except: pass
								self.rateLimited["u"][message.author.id] = limit
								totalWeight = limit
								break
							else:
								await self.support_message(message, raw, isPremium)
								if slice in ["greed index", "gi", "fear index", "fi", "fear greed index", "fgi", "greed fear index", "gfi"]:
									chartMessages, weight = await self.fear_greed_index(message, slice, useMute)
								elif slice in ["nvt", "nvt signal"]:
									chartMessages, weight = await self.nvt_signal(message, slice, useMute)
								else:
									chartMessages, weight = await self.chart(message, slice, useMute)

								sentMessages += chartMessages
								totalWeight += weight - 1

								if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += weight - 2
								else: self.rateLimited["u"][message.author.id] = weight - 2

						self.statistics["c"] += totalWeight
						await self.finish_request(message, raw, totalWeight, sentMessages)
				elif raw.startswith("p "):
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					if raw in ["p help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"```p <coin> <exchange>```",
							"__**Examples:**__",
							"`p btc` Binance BTC price",
							"`p xbt` BitMEX BTC price",
							"`p $coin` get coin/USD(T) price",
							"`p ada` ADA/BTC price on Binance",
							"`p $xvg bfx` XVG/USDT price on Binance",
							"`p etcusd btrx` ETC/USD price on Bittrex"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					elif raw not in ["p "]:
						slices = re.split(", p | p |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, isPremium)
							return
						for slice in slices:
							if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += 1
							else: self.rateLimited["u"][message.author.id] = 1

							if self.rateLimited["u"][message.author.id] >= limit:
								try: await message.channel.send("<@!{}>, you reached a limit of **{} price requests per minute**".format(message.author.id, limit))
								except: pass
								self.rateLimited["u"][message.author.id] = limit
								totalWeight = limit
								break
							else:
								await self.support_message(message, raw, isPremium)
								if slice in ["greed index", "gi"]:
									try:
										r = requests.get("https://api.alternative.me/fng/?limit=1&format=json").json()
										greedIndex = r["data"][0]["value"]
										greedClassification = r["data"][0]["value_classification"]
										try: await message.channel.send("Current greed index is **{}** ({})".format(greedIndex, greedClassification))
										except: pass
									except:
										await self.unknown_error(message)
								else:
									await self.price(message, slice, isPremium, useMute)

						self.statistics["p"] += len(slices)
						await self.finish_request(message, raw, len(slices), [])
				elif raw.startswith("d "):
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					if raw in ["d help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"```d <coin> <exchange>```",
							"__**Examples:**__",
							"`d btc` Binance BTC orders",
							"`d xbt` BitMEX BTC orders",
							"`d $coin` get coin/USD(T) orders",
							"`d ada` ADA/BTC orders on Binance",
							"`d $xvg bfx` XVG/USDT orders on Binance",
							"`d etcusd btrx` ETC/USD orders on Bittrex"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					elif raw not in ["d "]:
						slices = re.split(", d | d |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, isPremium)
							return
						for slice in slices:
							if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += 1
							else: self.rateLimited["u"][message.author.id] = 1

							if self.rateLimited["u"][message.author.id] >= limit:
								try: await message.channel.send("<@!{}>, you reached a limit of **{} depth data requests per minute**".format(message.author.id, limit))
								except: pass
								self.rateLimited["u"][message.author.id] = limit
								totalWeight = limit
								break
							else:
								await self.support_message(message, raw, isPremium)
								await self.depth(message, slice, useMute)

						self.statistics["d"] += len(slices)
						await self.finish_request(message, raw, len(slices), [])
				elif raw.startswith("v "):
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					if raw in ["v help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"```v <coin> <exchange>```",
							"__**Examples:**__",
							"`v btc` Binance BTC daily volume",
							"`v xbt` BitMEX BTC daily volume",
							"`v $coin` get coin/USD(T) daily volume",
							"`v ada` ADA/BTC daily volume on Binance",
							"`v $xvg bfx` XVG/USDT daily volume on Binance",
							"`v etcusd btrx` ETC/USD daily volume on Bittrex"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					elif raw not in ["v "]:
						slices = re.split(", v | v |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, isPremium)
							return
						for slice in slices:
							if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += 1
							else: self.rateLimited["u"][message.author.id] = 1

							if self.rateLimited["u"][message.author.id] >= limit:
								try: await message.channel.send("<@!{}>, you reached a limit of **{} volume data requests per minute**".format(message.author.id, limit))
								except: pass
								self.rateLimited["u"][message.author.id] = limit
								totalWeight = limit
								break
							else:
								await self.support_message(message, raw, isPremium)
								await self.volume(message, slice, useMute)

						self.statistics["v"] += len(slices)
						await self.finish_request(message, raw, len(slices), [])
				elif raw.startswith("hmap "):
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					if raw in ["hmap help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"```hmap <type> <filters> <period>```",
							"__**Examples:**__",
							"`hmap price` cryptocurrency market heat map, color represent price change for the day, size represents market cap",
							"`hmap price top100` cryptocurrency market heat map for top 100 coins",
							"`hmap price tokens year` cryptocurrency market heat map for tokens in the last year only",
							"`hmap exchanges` exchanges map, color represent total trade volume change for the day, size represent trade volume",
							"`hmap trend loosers` unlike general market heat map in this report show only gainers or only loosers",
							"`hmap category ai` in this map you can see the coins in the Data Storage/Analytics & AI category.",
							"`hmap vol top10` heat map show top 10 coins by marketcap and their respected volatility",
							"`hmap unusual` map shows coins, the trade volume of which has grown the most in last day. Bitgur compares last 24h volume and average daily volume for the last week.",
							"__**Notes:**__",
							"Type `hmap parameters` for the complete filter & timeframes list"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					elif raw in ["hmap parameters"]:
						availableCategories = [
							"**crypto** (Cryptocurrency)", "**blockchain** (Blockchain Platforms)", "**commerce** (Commerce & Advertising)", "**commodities** (Commodities)",
							"**content** (Content Management)", "**ai** (Data Storage/Analytics & AI)", "**healthcare** (Drugs & Healthcare)", "**energy** (Energy & Utilities)",
							"**events** (Events & Entertainment)", "**financial** (Financial Services)", "**gambling** (Gambling & Betting)", "**gaming** (Gaming & VR)",
							"**identy** (Identy & Reputation)", "**legal** (Legal)", "**estate** (Real Estate)", "**social** (Social Network)", "**software** (Software)",
							"**logistics** (Supply & Logistics)", "**trading** (Trading & Investing)",
						]
						try:
							await message.channel.send("Timeframes: **15-minute**, **1-hour**, **daily** and **weekly**, **1/3/6-month**, **1-year**")
							await message.channel.send("Filters: **10** (top 10), **100** (top 100), **tokens**, **coins**, **gainers**, **loosers**")
							await message.channel.send("Categories: {}".format(", ".join(availableCategories)))
						except: await self.unknown_error(message)
					else:
						slices = re.split(", hmap | hmap |, ", raw)
						totalWeight = len(slices)
						if totalWeight > 5:
							await self.hold_up(message, isPremium)
							return
						for s in slices:
							slice = s if s.startswith("hmap") else "hmap " + s
							if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += 2
							else: self.rateLimited["u"][message.author.id] = 2

							if self.rateLimited["u"][message.author.id] >= limit:
								try: await message.channel.send("<@!{}>, you reached a limit of **{} chart requests per minute**".format(message.author.id, limit))
								except: pass
								self.rateLimited["u"][message.author.id] = limit
								totalWeight = limit
								break
							else:
								await self.support_message(message, raw, isPremium)
								chartMessages, weight = await self.heatmap(message, slice, useMute)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += 2 - weight
								else: self.rateLimited["u"][message.author.id] = 2 - weight

						self.statistics["hmap"] += totalWeight
						await self.finish_request(message, raw, totalWeight, sentMessages)
				elif raw.startswith(("mcap ", "mc ")):
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					if raw in ["mcap help", "mc help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"```mcap/mc <coin>```",
							"__**Examples:**__",
							"`mc btc` get information about Bitcoin/USD from CoinGecko",
							"`mc ada` get information about Cardano/USD from CoinGecko",
							"`mc trxbtc` get information about Tron/BTC from CoinGecko",
							"Note that only top 400 coins converted to USD or BTC are supported"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					else:
						slices = re.split(", mcap | mcap |, mc | mc |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, isPremium)
							return
						for slice in slices:
							if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += 1
							else: self.rateLimited["u"][message.author.id] = 1

							if self.rateLimited["u"][message.author.id] >= limit:
								try: await message.channel.send("<@!{}>, you reached a limit of **{} market data requests per minute**".format(message.author.id, limit))
								except: pass
								self.rateLimited["u"][message.author.id] = limit
								totalWeight = limit
								break
							else:
								await self.support_message(message, raw, isPremium)
								await self.mcap(message, slice, useMute)

						self.statistics["mcap"] += len(slices)
						await self.finish_request(message, raw, len(slices), [])
				elif raw.startswith("mk "):
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					if raw in ["mk help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"```mk <coin>```",
							"__**Examples:**__",
							"`mk btc` get all BTC/USD markets",
							"`mk trx` get all TRX/BTC markets",
							"`mk bnbusd` get all BNB/USD(T) markets",
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					else:
						slices = re.split(", mk | mk |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, isPremium)
							return
						for slice in slices:
							if message.author.id in self.rateLimited["u"]: self.rateLimited["u"][message.author.id] += 1
							else: self.rateLimited["u"][message.author.id] = 1

							if self.rateLimited["u"][message.author.id] >= limit:
								try: await message.channel.send("<@!{}>, you reached a limit of **{} coin listing requests per minute**".format(message.author.id, limit))
								except: pass
								self.rateLimited["u"][message.author.id] = limit
								totalWeight = limit
								break
							else:
								await self.support_message(message, raw, isPremium)
								await self.markets(message, slice, useMute)

						self.statistics["mk"] += len(slices)
						await self.finish_request(message, raw, len(slices), [])
				elif raw.startswith("paper ") and 601524236464553984 in [role.id for role in message.author.roles]:
					if message.author.bot:
						if not await self.bot_verification(message, raw): return

					if raw in ["paper help"]:
						helpCommandParts = [
							"__**Syntax:**__",
							"Execute orders ```paper <order type> <coin> <exchange> <amount@price>```",
							"Check your available balance ```paper balance <exchange> [all]```",
							"Get trading history ```paper history```",
							"Check all open orders ```paper orders```",
							"__**Examples:**__",
							"`paper buy btc 100%` buy BTC with 100% of available USD at market price",
							"`paper sell btc 0.01` sell 0.01 BTC at market price",
							"`paper buy btc 50%@5900` place a limit buy order with 50% of available USD at $5900",
							"`paper stop btc 100%@10%` place a stop loss to sell entire BTC position 10% below the current price",
							"`paper trailing stop btc 0.25@5%` place a 5% trailing stop loss to sell 0.25 BTC"
						]
						try: await message.channel.send("\n".join(helpCommandParts))
						except: await self.unknown_error(message)
					elif raw in ["paper leaderboard"]:
						await self.fetch_leaderboard(message, raw)
					elif raw.startswith("paper balance"):
						await self.fetch_paper_balance(message, raw)
					elif raw in ["paper history", "paper order history", "paper trade history", "paper history all", "paper order history all", "paper trade history all"]:
						await self.fetch_paper_orders(message, raw, "history")
					elif raw in ["paper orders", "paper open orders"]:
						await self.fetch_paper_orders(message, raw, "open_orders")
					else:
						slices = re.split(', paper | paper |, ', raw.split(" ", 1)[1])
						for slice in slices:
							await self.process_paper_trade(message, slice)
			else:
				self.fusion.calculate_average_ping(message, client.cached_messages)
				if await self.fusion.invite_warning(message, raw, guildId): return
				if (self.guildProperties[guildId]["functions"]["assistant"] if guildId in self.guildProperties else True):
					if await self.funnyReplies(message, raw):
						self.statistics["alpha"] += 1
						return
				if not any(keyword in raw for keyword in constants.mutedMentionWords) and not message.author.bot and any(e in re.findall(r"[\w']+", raw) for e in constants.mentionWords) and guildId not in [414498292655980583, -1]:
					mentionMessage = "{}/{}: {}".format(message.guild.name, message.channel.name, message.clean_content)
					t = threading.Thread(target=self.webhook_send, args=("https://discordapp.com/api/webhooks/565908326110724117/G1CcoCN5FueN5psTLLqWgIp1nd4sYbcDhi_aCbN0msEL-0ZT5vgGSFZP8wHIhUT0n5pN", mentionMessage, "{}#{}".format(message.author.name, message.author.discriminator), message.author.avatar_url, False, message.attachments, message.embeds))
					t.start()
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, report=True)
			self.rateLimited = {"c": {}, "p": {}, "d": {}, "v": {}, "u": {}}
			for side in constants.supportedExchanges:
				for id in constants.supportedExchanges[side]:
					if id not in self.rateLimited["p"] or id not in self.rateLimited["d"] or id not in self.rateLimited["v"]:
						self.rateLimited["p"][id] = {}
						self.rateLimited["d"][id] = {}
						self.rateLimited["v"][id] = {}

			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def unknown_error(self, message, id=None, report=False):
		try: await message.channel.send("Something went wrong{}".format(". The issue was reported" if report else ""))
		except: pass

	def webhook_send(self, url, content, username="Alpha", avatar_url=None, tts=False, files=None, embeds=None):
		if content != "": content += "\n"
		content += "\n".join([file.url for file in files])
		webhook = discord.Webhook.from_url(url, adapter=discord.RequestsWebhookAdapter())
		webhook.send(content=content, username=username, avatar_url=avatar_url, tts=tts, embeds=embeds)

	async def on_reaction_add(self, reaction, user):
		if user.id in [487714342301859854, 401328409499664394]: return
		if reaction.message.author.id in [487714342301859854, 401328409499664394]:
			users = await reaction.users().flatten()
			if reaction.message.author in users:
				if reaction.emoji == "☑":
					if reaction.message.guild is not None:
						guildPermissions = user.permissions_in(reaction.message.channel).manage_messages or user.id in [361916376069439490, 243053168823369728]
						if len(reaction.message.attachments) == 0:
							try: await reaction.message.delete()
							except: pass
						elif str(user.id) in reaction.message.attachments[0].filename or guildPermissions:
							try: await reaction.message.delete()
							except: pass
					else:
						try: await reaction.message.delete()
						except: pass
				elif reaction.emoji == '❌':
					if reaction.message.content.startswith("● Price alert"):
						alertId = reaction.message.content.split(" *(id: ")[1][:-2]

						for id in constants.supportedExchanges["alerts"]:
							userAlerts = db.collection(u"alpha/alerts/{}".format(id)).stream()
							if userAlerts is not None:
								for alertAuthor in userAlerts:
									if int(alertAuthor.id) == user.id:
										deletedAlerts = []
										allAlerts = alertAuthor.to_dict()
										for s in allAlerts:
											for alert in allAlerts[s]:
												if alert["id"] == alertId:
													deletedAlerts.append(alert)
													break

											if len(deletedAlerts) > 0:
												for alert in deletedAlerts:
													allAlerts[s].remove(alert)
												alertsRef = db.document(u"alpha/alerts/{}/{}".format(id, user.id))
												try:
													batch = db.batch()
													batch.set(alertsRef, allAlerts, merge=True)
													for i in range(1, self.fusion.numInstances + 1):
														batch.set(db.document(u'fusion/instance-{}'.format(i)), {"needsUpdate": True}, merge=True)
													batch.commit()
												except:
													await self.unknown_error(message)
												try: await reaction.message.delete()
												except: pass
												return
					elif reaction.message.content.endswith("`"):
						presetName = reaction.message.content.split("● ")[1].split(" `")[0]
						isServer = False
						if "(server-wide)" in presetName:
							presetName = presetName[:-13]
							isServer = True

						fetchedSettingsRef = db.document(u"alpha/settings/{}/{}".format("servers" if isServer else "users", reaction.message.guild.id if isServer else user.id))
						fetchedSettings = fetchedSettingsRef.get().to_dict()
						fetchedSettings = Utils.createServerSetting(fetchedSettings) if isServer else Utils.createUserSettings(fetchedSettings)
						fetchedSettings, statusMessage = Presets.updatePresets(fetchedSettings, remove=presetName)
						fetchedSettingsRef.set(fetchedSettings, merge=True)
						if isServer: self.guildProperties[reaction.message.guild.id] = copy.deepcopy(fetchedSettings)
						else: self.userProperties[user.id] = copy.deepcopy(fetchedSettings)

						try: await reaction.message.delete()
						except: pass

	async def finish_request(self, message, raw, weight, sentMessages):
		await asyncio.sleep(60)
		if message.author.id in self.rateLimited["u"]:
			self.rateLimited["u"][message.author.id] -= weight
			if self.rateLimited["u"][message.author.id] < 1: self.rateLimited["u"].pop(message.author.id, None)

		autodeleteEnabled = False
		if message.guild is not None:
			if message.guild.id in self.guildProperties:
				autodeleteEnabled = self.guildProperties[message.guild.id]["functions"]["autodelete"]

		if autodeleteEnabled:
			try: await message.delete()
			except: pass

		for chartMessage in sentMessages:
			try:
				if autodeleteEnabled: await chartMessage.delete()
				else: await chartMessage.remove_reaction("☑", message.channel.guild.me)
			except: pass

	async def bot_verification(self, message, raw, mute=False):
		if message.author.id not in constants.verifiedBots:
			if not mute and message.guild.id not in [414498292655980583, 592019306221535243]:
				if message.author.discriminator == "0000":
					try: await message.channel.send("**{}#{} is an unverified bot.**\nBot user making the request is likely a webhook, which cannot be verified and will not work with Alpha. For more info, join Alpha server: https://discord.gg/GQeDE85".format(message.author.name, message.author.discriminator))
					except: pass
				else:
					try: await message.channel.send("**{}#{} is an unverified bot.**\nTo get it verified, please join Alpha server: https://discord.gg/GQeDE85".format(message.author.name, message.author.discriminator))
					except: pass
			return False
		else:
			history.info("{} ({}): {}".format(Utils.get_current_date(), message.author.id, raw))
			return True

	async def help(self, message, raw, shortcutUsed):
		commands1 = [
			"TradingView charts with `c`, type `c help` to learn more.",
			"Setup price alerts for select coins with `alert`.",
			"Current coin price with `p`, type `p help` to learn more.",
			"Coin information from CoinGecko with `mc`, type `mc help` to learn more.",
			"Many other questions; start with `alpha` and continue with your question."
		]
		commands2 = [
			"Create presets with `preset` command, type `preset help` to learn more.",
			"Cross-server message forwarding with `f` command, type `f help` to learn more. (Coming soon for premium members)",
			"Depth information of a coin with `d`, type `d help` to learn more.",
			"24h coin volume with `v`, type `v help` to learn more.",
			"Check the current market state heat map with `hmap`, type `hmap help` to learn more.",
			"Request Bitcoin dominance chart with `c btc dom`.",
			"See what exchanges a coin is on with `mk`, type `mk help` to learn more.",
			"Request NVT ratio chart from *charts.woobull.com* with `nvt`.",
			"Request greed index chart with `c gi` or `p gi`."
		]
		commands3 = [
			"`a autodelete enable` and `a autodelete disable` to enable/disable automatic chart deletion after one minute.",
			"`a shortcuts disable` and `a shortcuts enable` to disable/enable price request shortcuts like `mex`.",
			"`a assistant disable` and `a assistant enable` to disable/enable Google Assistant integration."
		]
		try:
			if shortcutUsed:
				await message.author.send("__**Here are some things you can ask for:**__\n● {}".format("\n● ".join(commands1)))
				await message.author.send("__**Other useful commands:**__\n● {}".format("\n● ".join(commands2)))
				await message.author.send("__**For bot configuration (admins only):**__\n● {}".format("\n● ".join(commands3)))
			else:
				await message.channel.send("__**Here are some things you can ask for:**__\n● {}".format("\n● ".join(commands1)))
				await message.channel.send("__**Other useful commands:**__\n● {}".format("\n● ".join(commands2)))
				await message.channel.send("__**For bot configuration (admins only):**__\n● {}".format("\n● ".join(commands3)))
		except:
			try:
				await message.channel.send("__**Here are some things you can ask for:**__\n● {}".format("\n● ".join(commands1)))
				await message.channel.send("__**Other useful commands:**__\n● {}".format("\n● ".join(commands2)))
				await message.channel.send("__**For bot configuration (admins only):**__\n● {}".format("\n● ".join(commands3)))
			except:
				await self.unknown_error(message)

	async def support_message(self, message, raw, isPremium):
		if isPremium: return
		if random.randint(0, 250) == 1:
			try: await message.channel.send(constants.premiumMessage)
			except: pass

	async def hold_up(self, message, isPremium):
		if isPremium:
			try: await message.channel.send("You are requesting a lot of things at once. Having Alpha Premium does not mean you should spam, <@!{}>".format(message.author.id))
			except: self.unknown_error(message)
		else:
			try: await message.channel.send("You are requesting a lot of things at once. Maybe you should chill a little, <@!{}>".format(message.author.id))
			except: self.unknown_error(message)

	async def alert(self, message, raw, mute=False):
		try:
			arguments = raw.split(" ")
			method = arguments[0]

			if method in ["set", "create", "add"]:
				if len(arguments) >= 3:
					try: await message.channel.trigger_typing()
					except: pass

					tickerId, exchange, tickerParts, isAggregatedSymbol = self.coinParser.process_ticker(arguments[1].upper(), self.coinParser.coins["alerts"])
					if isAggregatedSymbol:
						try: await message.channel.send("Aggregated tickers aren't supported with the **alert** command")
						except: await self.unknown_error(message)
						return

					outputMessage, tickerId, arguments = self.alerts.process_alert_arguments(arguments, tickerId, exchange)
					if outputMessage is not None:
						if not mute:
							try: await message.channel.send(outputMessage)
							except: await self.unknown_error(message)
						return
					exchange, action, level, repeat = arguments

					outputMessage, details = self.coinParser.find_trading_pair_id(tickerId, exchange, "alerts", "fetchOHLCV")
					if outputMessage is not None:
						if not mute:
							try: await message.channel.send(outputMessage)
							except: await self.unknown_error(message)
						return

					symbol, exchange = details
					base = self.coinParser.exchanges[exchange].markets[symbol]["base"]
					quote = self.coinParser.exchanges[exchange].markets[symbol]["quote"]

					alertsRef = db.document(u"alpha/alerts/{}/{}".format(exchange, message.author.id))
					allAlerts = alertsRef.get().to_dict()
					if allAlerts is None: allAlerts = {}

					sum = 0
					for key in allAlerts: sum += len(allAlerts[key])

					if sum >= 10:
						try: await message.channel.send("Only 10 alerts per exchange are allowed")
						except: await self.unknown_error(message)
						return

					key = symbol.replace("/", "-")
					newAlert = {
						"id": "%013x" % random.randrange(10**15),
						"timestamp": time.time(),
						"time": Utils.get_current_date(),
						"channel": message.author.id,
						"action": action,
						"level": level,
						"repeat": repeat
					}

					if key not in allAlerts: allAlerts[key] = []
					for alert in allAlerts[key]:
						if alert["action"] == action and alert["level"] == level:
							try: await message.channel.send("This alert already exists")
							except: await self.unknown_error(message)
							return

					allAlerts[key].append(newAlert)

					try:
						batch = db.batch()
						batch.set(alertsRef, allAlerts, merge=True)
						for i in range(1, self.fusion.numInstances + 1):
							batch.set(db.document(u'fusion/instance-{}'.format(i)), {"needsUpdate": True}, merge=True)
						batch.commit()
					except:
						await self.unknown_error(message)
						return

					levelText = Utils.format_price(self.coinParser.exchanges[exchange], symbol, level)

					try: await message.channel.send("{} alert set for {} ({}) at **{} {}**".format(action.title(), base, self.coinParser.exchanges[exchange].name, levelText, quote))
					except: pass
				else:
					try: await message.channel.send("Invalid command usage. Type `alert help` to learn more")
					except: await self.unknown_error(message)
			elif method in ["list", "all"]:
				if len(arguments) == 1:
					try: await message.channel.send("__**Alerts:**__")
					except: pass

					alertsList = []
					for id in constants.supportedExchanges["alerts"]:
						userAlerts = db.collection(u"alpha/alerts/{}".format(id)).stream()
						if userAlerts is not None:
							for user in userAlerts:
								if int(user.id) == message.author.id:
									allAlerts = user.to_dict()
									hasAlerts = False
									for s in allAlerts:
										hasAlerts = hasAlerts or len(allAlerts[s]) > 0
										for alert in allAlerts[s]:
											symbol = s.replace("-", "/")
											base = self.coinParser.exchanges[id].markets[symbol]["base"]
											quote = self.coinParser.exchanges[id].markets[symbol]["quote"]
											coinPair = self.coinParser.exchanges[id].markets[symbol]["id"].replace("_", "").replace("/", "").replace("-", "").upper()
											levelText = Utils.format_price(self.coinParser.exchanges[id], symbol, alert["level"])

											try:
												alertMessage = await message.channel.send("● {} alert set for {} ({}) at **{} {}** *(id: {})*".format(alert["action"].title(), coinPair, self.coinParser.exchanges[id].name, levelText, quote, alert["id"]))
												await alertMessage.add_reaction('❌')
											except: pass
									break

					if not hasAlerts:
						try: await message.channel.send("You don't have any alerts set")
						except: await self.unknown_error(message)
				else:
					try: await message.channel.send("Invalid command usage. Type `alert help` to learn more")
					except: await self.unknown_error(message)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def presets(self, message, raw, guildId, mute=False):
		try:
			isServer = raw.startswith("server ") and message.author.guild_permissions.administrator
			offset = 1 if isServer else 0
			arguments = raw.split(" ", 2 + offset)
			method = arguments[0 + offset]

			if method in ["set", "create", "add"]:
				if len(arguments) == 3 + offset:
					try: await message.channel.trigger_typing()
					except: pass

					fetchedSettingsRef = db.document(u"alpha/settings/{}/{}".format("servers" if isServer else "users", guildId if isServer else message.author.id))
					fetchedSettings = fetchedSettingsRef.get().to_dict()
					fetchedSettings = Utils.createServerSetting(fetchedSettings) if isServer else Utils.createUserSettings(fetchedSettings)
					fetchedSettings, statusMessage = Presets.updatePresets(fetchedSettings, add=arguments[1 + offset].replace("`", ""), shortcut=arguments[2 + offset])
					fetchedSettingsRef.set(fetchedSettings, merge=True)
					if isServer: self.guildProperties[guildId] = copy.deepcopy(fetchedSettings)
					else: self.userProperties[message.author.id] = copy.deepcopy(fetchedSettings)

					try: await message.channel.send(statusMessage)
					except: await self.unknown_error(message)
			elif method in ["list", "all"]:
				if len(arguments) == 1 + offset:
					hasSettings = guildId in self.guildProperties if isServer else message.author.id in self.userProperties
					settings = {} if not hasSettings else (self.guildProperties[guildId] if isServer else self.userProperties[message.author.id])
					settings = Utils.createServerSetting(settings) if isServer else Utils.createUserSettings(settings)
					try: await message.channel.send("__**Presets:**__")
					except: await self.unknown_error(message)
					if len(settings["presets"]) > 0:
						for preset in settings["presets"]:
							try:
								presetMessage = await message.channel.send("● {}{} `{}`".format(preset["phrase"], " (server-wide)" if isServer else "", preset["shortcut"]))
								await presetMessage.add_reaction('❌')
							except: pass
					else:
						try: await message.channel.send("You don't have any presets set")
						except: await self.unknown_error(message)
			elif len(arguments) <= 3 + offset:
				try: await message.channel.send("Invalid argument: **{}**. \nType `preset help` to learn more".format(method))
				except: await self.unknown_error(message)
				return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def chart(self, message, raw, mute=False):
		try:
			sentMessages = []
			with message.channel.typing():
				arguments = raw.split(" ")

				tickerId, exchange, tickerParts, isAggregatedSymbol = self.coinParser.process_ticker(arguments[0].upper(), self.coinParser.coins["charts"], isOnlyCrypto=False)
				outputMessage, tickerId, arguments = self.imageProcessor.process_tradingview_arguments(arguments, tickerId, exchange, tickerParts)
				if outputMessage is not None:
					if not mute:
						try: await message.channel.send(outputMessage)
						except: await self.unknown_error(message)
					if arguments is None:
						return ([], 0)
				timeframes, exchange, sendLink, isLog, barStyle, hideVolume, theme, indicators, isWide = arguments

				if not isAggregatedSymbol:
					for i in range(3):
						if i == 2: self.coinParser.refresh_coins()
						if exchange == "" and tickerId not in ["XBTUSD"] and self.coinParser.exchanges["binance"].symbols is not None:
							if i == 2: self.coinParser.refresh_coins()
							for symbol in self.coinParser.exchanges["binance"].symbols:
								pair = symbol.split("/")
								if (tickerId.startswith(pair[0]) and (tickerId.replace(pair[0], "").endswith(pair[-1]) or tickerId.replace(pair[0], "").endswith(pair[-1].replace("USDT", "USD")) or (tickerId.replace(pair[0], "").endswith(pair[-1].replace("USDC", "USD").replace("TUSD", "USD").replace("USDS", "USD").replace("PAX", "USD")) and i != 0))) or (tickerId == pair[0] and len(pair) == 1):
									if tickerId.replace(pair[0], "") == "USD": tickerId = tickerId.replace("USD", "USDT")
									exchange = "BINANCE:"
									break

				for timeframe in timeframes:
					queueLength = [len(self.imageProcessor.screengrabLock[e]) for e in self.imageProcessor.screengrabLock]
					driverInstance = queueLength.index(min(queueLength))

					waitMessage = None
					if len(self.imageProcessor.screengrabLock[driverInstance]) > 2:
						try: waitMessage = await message.channel.send("One moment...")
						except: pass
						await asyncio.sleep(0.5)

					messageUrl = "https://www.tradingview.com/widgetembed/?symbol={}{}&hidesidetoolbar=0&symboledit=1&saveimage=1&withdateranges=1&enablepublishing=true&interval={}&theme={}&style={}&studies={}".format(urllib.parse.quote(exchange, safe=""), urllib.parse.quote(tickerId, safe=""), timeframe, theme, barStyle, "%1F".join(indicators))
					chartName = await self.imageProcessor.request_tradingview_chart(message.author.id, driverInstance, tickerId, timeframe, exchange, sendLink, isLog, barStyle, hideVolume, theme, indicators, isWide)

					if waitMessage is not None:
						try: await waitMessage.delete()
						except: pass
					if chartName is None:
						try:
							chartMessage = await message.channel.send("Requested chart for **{}** is not available".format(tickerId))
							sentMessages.append(chartMessage)
							try: await chartMessage.add_reaction("☑")
							except: pass
						except: await self.unknown_error(message)
						return (sentMessages, len(sentMessages))

					try:
						chartMessage = await message.channel.send(messageUrl if sendLink else "", file=discord.File("charts/" + chartName, chartName))
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					except: await self.unknown_error(message)

					chartsFolder = "./charts/"
					files = sorted(os.listdir(chartsFolder))
					numOfFiles = len([chartName for chartName in files if ".png" in chartName and os.path.isfile(os.path.join(chartsFolder, chartName))])
					if numOfFiles > 100:
						for i in range(numOfFiles - 100):
							os.remove(os.path.join(chartsFolder, files[i]))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, report=True)
			self.rateLimited["c"] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def fear_greed_index(self, message, raw, mute=False):
		try:
			sentMessages = []
			with message.channel.typing():
				chartName = self.imageProcessor.request_feargreedindex_chart(message.author.id)

				try:
					chartMessage = await message.channel.send(file=discord.File("charts/" + chartName, chartName))
					sentMessages.append(chartMessage)
					await chartMessage.add_reaction("☑")
				except: await self.unknown_error(message)

			return (sentMessages, len(sentMessages))
		except Exception as e:
			await self.unknown_error(message, report=True)
			self.rateLimited["c"] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def nvt_signal(self, message, raw, mute=False):
		try:
			sentMessages = []
			with message.channel.typing():
				queueLength = [len(self.imageProcessor.screengrabLock[e]) for e in self.imageProcessor.screengrabLock]
				driverInstance = queueLength.index(min(queueLength))

				waitMessage = None
				if len(self.imageProcessor.screengrabLock[driverInstance]) > 2:
					try: waitMessage = await message.channel.send("One moment...")
					except: pass
					await asyncio.sleep(0.5)

				chartName = await self.imageProcessor.request_nvtsignal_chart(message.author.id, driverInstance)

				if waitMessage is not None:
					try: await waitMessage.delete()
					except: pass
				if chartName is None:
					try:
						chartMessage = await message.channel.send("Requested chart for **NVT** is not available")
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					except: await self.unknown_error(message)
					return (sentMessages, len(sentMessages))

				try:
					chartMessage = await message.channel.send(file=discord.File("charts/" + chartName, chartName))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				except: await self.unknown_error(message)

				chartsFolder = "./charts/"
				files = sorted(os.listdir(chartsFolder))
				numOfFiles = len([chartName for chartName in files if ".png" in chartName and os.path.isfile(os.path.join(chartsFolder, chartName))])
				if numOfFiles > 100:
					for i in range(numOfFiles - 100):
						os.remove(os.path.join(chartsFolder, files[i]))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def price(self, message, raw, isPremium, mute=False):
		try:
			arguments = raw.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol = self.coinParser.process_ticker(arguments[0].upper(), self.coinParser.coins["ohlcv"])
			if isAggregatedSymbol:
				try: await message.channel.send("Aggregated tickers aren't supported with the **alert** command")
				except: await self.unknown_error(message)
				return

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, hasActions=True, command="p")
			if outputMessage is not None:
				if not mute:
					try: await message.channel.send(outputMessage)
					except: await self.unknown_error(message)
				return
			action, exchange = arguments

			try: await message.channel.trigger_typing()
			except: pass

			if action == "funding": await self.bitmex_funding(message, tickerId, mute)
			elif action == "futures": await self.bitmex_futures(message, tickerId, mute)
			elif action == "oi": await self.bitmex_open_interest(message, tickerId, isPremium, mute)
			elif action == "premiums": await self.bitcoin_premiums(message, tickerId, mute)
			elif action == "ls": await self.long_short_ratio(message, tickerId, False, mute)
			elif action == "sl": await self.long_short_ratio(message, tickerId, True, mute)
			else:
				outputMessage, details = self.coinParser.find_trading_pair_id(tickerId, exchange, "ohlcv", "fetchOHLCV")
				if outputMessage is not None:
					if outputMessage == "Ticker **{}** was not found".format(tickerId):
						fallback = tickerId[:-3] if tickerId.endswith(("BTC", "ETH", "USD")) else tickerId
						if fallback in self.coinGeckoLink.coingeckoDataset:
							try:
								data = self.coinGeckoLink.coingecko.get_coin_by_id(id=self.coinGeckoLink.coingeckoDataset[fallback.lower()], localization="false", tickers=False, market_data=True, community_data=False, developer_data=False)
							except:
								await self.unknown_error(message)
								return

							btcPrice = "${:,.8f}".format(data["market_data"]["current_price"]["btc"])
							usdPrice = "${:,.2f}".format(data["market_data"]["current_price"]["usd"])
							change24h = ""
							if "usd" in data["market_data"]["price_change_percentage_24h_in_currency"]:
								change24h = " *{:+.2f} %*".format(data["market_data"]["price_change_percentage_24h_in_currency"]["btc"])
							try: await message.channel.send("{} (CoinGecko fallback): **{} BTC** (${}) {}".format(tickerId, btcPrice, usdPrice, change24h))
							except: await self.unknown_error(message)
						else:
							try: await message.channel.send("**{}** not found".format(tickerId))
							except: await self.unknown_error(message)
					elif not mute:
						try: await message.channel.send(outputMessage)
						except: await self.unknown_error(message)
					return

				symbol, exchange = details
				base = self.coinParser.exchanges[exchange].markets[symbol]["base"]
				quote = self.coinParser.exchanges[exchange].markets[symbol]["quote"]
				coinPair = self.coinParser.exchanges[exchange].markets[symbol]["id"].replace("_", "").replace("/", "").replace("-", "").upper()

				if tickerId in self.rateLimited["p"][exchange] and tickerId in self.rateLimited["v"][exchange]:
					price = self.rateLimited["p"][exchange][tickerId]
					volume = self.rateLimited["v"][exchange][tickerId]
				else:
					try:
						rawData = self.coinParser.exchanges[exchange].fetch_ohlcv(
							symbol,
							timeframe="1d",
							since=(self.coinParser.exchanges[exchange].milliseconds() - 24 * 60 * 60 * 5 * 1000)
						)
						price = (rawData[-1][4], rawData[-2][4])
						volume = sum([candle[5] for candle in rawData])
						if exchange == "bitmex" and symbol == "BTC/USD": self.coinParser.lastBitcoinPrice = price[0]
						self.rateLimited["p"][exchange][tickerId] = price
						self.rateLimited["v"][exchange][tickerId] = volume
					except:
						try: await message.channel.send("Couldn't get price data")
						except: await self.unknown_error(message)
						self.rateLimited["p"][exchange] = {}
						self.rateLimited["v"][exchange] = {}
						return

				percentChange = price[0] / price[1] * 100 - 100
				priceText = Utils.format_price(self.coinParser.exchanges[exchange], symbol, price[0])
				usdConversion = " (${:,.2f})".format(price[0] * self.coinParser.lastBitcoinPrice) if quote == "BTC" else ""

				try: await message.channel.send("{} ({}): **{} {}**{} *{:+.2f} %*".format(coinPair, self.coinParser.exchanges[exchange].name, priceText, quote.replace("USDT", "USD").replace("USDC", "USD").replace("TUSD", "USD"), usdConversion, percentChange))
				except: await self.unknown_error(message)

				await asyncio.sleep(self.coinParser.exchanges[exchange].rateLimit / 1000)
				self.rateLimited["p"][exchange].pop(tickerId, None)
				self.rateLimited["v"][exchange].pop(tickerId, None)
				return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["p"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def bitmex_funding(self, message, tickerId, mute=False):
		tickerId = tickerId.replace("BTC", "XBT").replace("USDT", "USD")
		r = requests.get("https://www.bitmex.com/api/v1/instrument?symbol={}".format(tickerId)).json()
		if len(r) != 0:
			fundingDate = datetime.datetime.strptime(r[0]["fundingTimestamp"], "%Y-%m-%dT%H:%M:00.000Z")
			indicativeFundingTimestamp = datetime.datetime.timestamp(fundingDate) + 28800
			indicativeFundingDate = datetime.datetime.utcfromtimestamp(indicativeFundingTimestamp)
			deltaFunding = pytz.utc.localize(fundingDate) - datetime.datetime.now().astimezone(pytz.utc)
			deltaIndicative = pytz.utc.localize(indicativeFundingDate) - datetime.datetime.now().astimezone(pytz.utc)

			hours1, seconds1 = divmod(deltaFunding.days * 86400 + deltaFunding.seconds, 3600)
			minutes1 = int(seconds1 / 60)
			hoursFunding = "{:d} {} ".format(hours1, "hours" if hours1 > 1 else "hour") if hours1 > 0 else ""
			minutesFunding = "{:d} {}".format(minutes1 if hours1 > 0 or minutes1 > 0 else seconds1, "{}".format("minute" if minutes1 == 1 else "minutes") if hours1 > 0 or minutes1 > 0 else ("second" if seconds1 == 1 else "seconds"))
			deltaFundingText = "{}{}".format(hoursFunding, minutesFunding)

			hours2, seconds2 = divmod(deltaIndicative.days * 86400 + deltaIndicative.seconds, 3600)
			minutes2 = int(seconds2 / 60)
			hoursIndicative = "{:d} {} ".format(hours2, "hours" if hours2 > 1 else "hour") if hours2 > 0 else ""
			minutesIndicative = "{:d} {}".format(minutes2 if hours2 > 0 or minutes2 > 0 else seconds2, "{}".format("minute" if minutes2 == 1 else "minutes") if hours2 > 0 or minutes2 > 0 else ("second" if seconds2 == 1 else "seconds"))
			deltaIndicativeText = "{}{}".format(hoursIndicative, minutesIndicative)

			try: await message.channel.send("__{} (BitMEX):__\nFunding Rate: **{:+.4f} %** *(in {})*\nPredicted Rate: **{:+.4f} %** *(in {})*".format(tickerId, r[0]["fundingRate"] * 100, deltaFundingText, r[0]["indicativeFundingRate"] * 100, deltaIndicativeText))
			except: await self.unknown_error(message)
		else:
			try: await message.channel.send("**{}** not found".format(tickerId))
			except: await self.unknown_error(message)

	async def bitmex_futures(self, message, tickerId, mute=False):
		price = [(0, 0), (0, 0)]
		id = "bitmex"
		jobs = ["XBTZ19", "XBTU19"]
		for i in range(len(jobs)):
			tickerId = jobs[i]
			if tickerId in self.rateLimited["p"][id]:
				price = self.rateLimited["p"][id][tickerId]
			else:
				try:
					rawData = self.coinParser.exchanges[id].fetch_ohlcv(
						tickerId,
						timeframe="1d",
						since=(self.coinParser.exchanges[id].milliseconds() - 24 * 60 * 60 * 5 * 1000)
					)
					price[i] = (rawData[-1][4], rawData[-2][4])
					self.rateLimited["p"][id][tickerId] = price[i]
					self.rateLimited["v"][id][tickerId] = rawData[-1][5]
				except:
					try: await message.channel.send("Couldn't get price data")
					except: await self.unknown_error(message)
					self.rateLimited["p"][id] = {}
					self.rateLimited["v"][id] = {}
					return

		try: await message.channel.send("{} ({}): **{:.2f} USD** *{:+.2f} %*\n{} ({}): **{:.2f} USD** *{:+.2f} %*".format(jobs[0], id.title(), price[0][0], price[0][0] / price[0][1] * 100 - 100, jobs[1], id.title(), price[1][0], price[1][0] / price[1][1] * 100 - 100))
		except: await self.unknown_error(message)

		for i in range(len(jobs)):
			tickerId = jobs[i]
			await asyncio.sleep(self.coinParser.exchanges[id].rateLimit / 1000 * 2)
			self.rateLimited["p"][id].pop(tickerId, None)
			self.rateLimited["v"][id].pop(tickerId, None)

	async def bitcoin_premiums(self, message, tickerId, mute=False):
		price = [(0, 0), (0, 0), (0, 0), (0, 0), (0, 0)]
		jobs = ["bitmex", "bitfinex2", "coinbasepro", "binance", "huobipro"]
		prem = {
			"bitfinex2": "*no data*",
			"coinbasepro": "*no data*",
			"binance": "*no data*",
			"huobipro": "*no data*"
		}
		for i in range(len(jobs)):
			id = jobs[i]
			if tickerId in self.rateLimited["p"][id]:
				price[i] = self.rateLimited["p"][id][tickerId]
			else:
				try:
					rawData = self.coinParser.exchanges[id].fetch_ohlcv(
						"BTC/USD" if "BTC/USD" in self.coinParser.exchanges[id].symbols else "BTC/USDT",
						timeframe="1d",
						since=(self.coinParser.exchanges[id].milliseconds() - 24 * 60 * 60 * 5 * 1000)
					)
					price[i] = (rawData[-1][4], rawData[-2][4])
					self.rateLimited["p"][id][tickerId] = price[i]
					self.rateLimited["v"][id][tickerId] = rawData[-1][5]
				except:
					try: await message.channel.send("Couldn't get price data")
					except: await self.unknown_error(message)
					self.rateLimited["p"][id] = {}
					self.rateLimited["v"][id] = {}
					continue
			if id != "bitmex": prem[id] = "**{:+.2f} USD**".format(price[0][0] - price[i][0])

		try: await message.channel.send("__XBTUSD (BitMEX): **{:.1f} USD** *{:+.2f} %*__\nBTCUSD (Bitfinex): {}\nBTCUSD (Coinbase Pro): {}\nBTCUSDT (Binance): {}\nBTCUSD (Huobi Pro): {}".format(price[0][0], price[0][0] / price[0][1] * 100 - 100, prem["bitfinex2"], prem["coinbasepro"], prem["binance"], prem["huobipro"]))
		except: await self.unknown_error(message)

		for i in range(len(jobs)):
			id = jobs[i]
			await asyncio.sleep(self.coinParser.exchanges[id].rateLimit / 1000 * 2)
			self.rateLimited["p"][id].pop(tickerId, None)
			self.rateLimited["v"][id].pop(tickerId, None)

	async def long_short_ratio(self, message, tickerId, reverse=False, mute=False):
		if reverse:
			coin = tickerId
			tickerId = "{}SHORTS/({}LONGS+{}SHORTS)".format(tickerId, tickerId, tickerId)
			if tickerId in self.rateLimited["p"]["bitfinex2"]:
				ratio = self.rateLimited["p"]["bitfinex2"][tickerId]
			else:
				try:
					longs = self.coinParser.exchanges["bitfinex2"].publicGetStats1KeySizeSymbolLongLast({"key": "pos.size", "size": "1m", "symbol": "t{}".format(coin.replace("USDT", "USD")), "side": "short", "section": "last"})
					shorts = self.coinParser.exchanges["bitfinex2"].publicGetStats1KeySizeSymbolShortLast({"key": "pos.size", "size": "1m", "symbol": "t{}".format(coin.replace("USDT", "USD")), "side": "short", "section": "last"})
					ratio = shorts[1] / (longs[1] + shorts[1]) * 100
					self.rateLimited["p"]["bitfinex2"][tickerId] = ratio
				except:
					await self.unknown_error(message)
					self.rateLimited["p"]["bitfinex2"] = {}
					return

			try: await message.channel.send("{} (Bitfinex) shorts/longs ratio: **{:.1f} % / {:.1f} %**".format(coin, ratio, 100 - ratio))
			except: await self.unknown_error(message)

			await asyncio.sleep(self.coinParser.exchanges["bitfinex2"].rateLimit / 1000 * 4)
			self.rateLimited["p"]["bitfinex2"].pop(tickerId, None)
		else:
			coin = tickerId
			tickerId = "{}LONGS/({}LONGS+{}SHORTS)".format(tickerId, tickerId, tickerId)
			if tickerId in self.rateLimited["p"]["bitfinex2"]:
				ratio = self.rateLimited["p"]["bitfinex2"][tickerId]
			else:
				try:
					longs = self.coinParser.exchanges["bitfinex2"].publicGetStats1KeySizeSymbolLongLast({"key": "pos.size", "size": "1m", "symbol": "t{}".format(coin.replace("USDT", "USD")), "side": "long", "section": "last"})
					shorts = self.coinParser.exchanges["bitfinex2"].publicGetStats1KeySizeSymbolShortLast({"key": "pos.size", "size": "1m", "symbol": "t{}".format(coin.replace("USDT", "USD")), "side": "long", "section": "last"})
					ratio = longs[1] / (longs[1] + shorts[1]) * 100
					self.rateLimited["p"]["bitfinex2"][tickerId] = ratio
				except:
					await self.unknown_error(message)
					self.rateLimited["p"]["bitfinex2"] = {}
					return

			try: await message.channel.send("{} (Bitfinex) longs/shorts ratio: **{:.1f} % / {:.1f} %**".format(coin, ratio, 100 - ratio))
			except: await self.unknown_error(message)

			await asyncio.sleep(self.coinParser.exchanges["bitfinex2"].rateLimit / 1000 * 4)
			self.rateLimited["p"]["bitfinex2"].pop(tickerId, None)

	async def volume(self, message, raw, mute=False):
		try:
			arguments = raw.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol = self.coinParser.process_ticker(arguments[0].upper(), self.coinParser.coins["ohlcv"])
			if isAggregatedSymbol:
				try: await message.channel.send("Aggregated tickers aren't supported with the **alert** command")
				except: await self.unknown_error(message)
				return

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, command="v")
			if outputMessage is not None:
				if not mute:
					try: await message.channel.send(outputMessage)
					except: await self.unknown_error(message)
				return
			_, exchange = arguments

			try: await message.channel.trigger_typing()
			except: pass

			outputMessage, details = self.coinParser.find_trading_pair_id(tickerId, exchange, "ohlcv", "fetchOHLCV")
			if outputMessage is not None:
				if not mute:
					try: await message.channel.send(outputMessage)
					except: await self.unknown_error(message)
				return

			symbol, exchange = details
			base = self.coinParser.exchanges[exchange].markets[symbol]["base"]
			quote = self.coinParser.exchanges[exchange].markets[symbol]["quote"]
			coinPair = self.coinParser.exchanges[exchange].markets[symbol]["id"].replace("_", "").replace("/", "").replace("-", "").upper()

			if tickerId in self.rateLimited["p"][exchange] and tickerId in self.rateLimited["v"][exchange]:
				price = self.rateLimited["p"][exchange][tickerId]
				volume = self.rateLimited["v"][exchange][tickerId]
			else:
				try:
					rawData = self.coinParser.exchanges[exchange].fetch_ohlcv(
						symbol,
						timeframe="5m",
						since=(self.coinParser.exchanges[exchange].milliseconds() - 24 * 60 * 60 * 1 * 1000)
					)
					price = (rawData[-1][4], rawData[-2][4])
					volume = sum([candle[5] for candle in rawData])
					if exchange == "bitmex" and symbol == "BTC/USD": self.coinParser.lastBitcoinPrice = price[0]
					self.rateLimited["p"][exchange][tickerId] = price
					self.rateLimited["v"][exchange][tickerId] = volume
				except:
					try: await message.channel.send("Couldn't get price data")
					except: await self.unknown_error(message)
					self.rateLimited["p"][exchange] = {}
					self.rateLimited["v"][exchange] = {}
					return

			try: await message.channel.send("{} ({}): **{:,.1f} {}** ({:,} {})".format(coinPair, self.coinParser.exchanges[exchange].name, volume, base, int(volume * price[0]), quote.replace("USDT", "USD").replace("USDC", "USD").replace("TUSD", "USD").replace("USDS", "USD").replace("PAX", "USD")))
			except: await self.unknown_error(message)

			await asyncio.sleep(self.coinParser.exchanges[exchange].rateLimit / 1000)
			self.rateLimited["p"][exchange].pop(tickerId, None)
			self.rateLimited["v"][exchange].pop(tickerId, None)
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["v"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def depth(self, message, raw, mute=False):
		try:
			arguments = raw.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol = self.coinParser.process_ticker(arguments[0].upper(), self.coinParser.coins["ohlcv"])
			if isAggregatedSymbol:
				try: await message.channel.send("Aggregated tickers aren't supported with the **alert** command")
				except: await self.unknown_error(message)
				return

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, command="v")
			if outputMessage is not None:
				if not mute:
					try: await message.channel.send(outputMessage)
					except: await self.unknown_error(message)
				return
			_, exchange = arguments

			try: await message.channel.trigger_typing()
			except: pass

			outputMessage, details = self.coinParser.find_trading_pair_id(tickerId, exchange, "ohlcv", "fetchOHLCV")
			if outputMessage is not None:
				if not mute:
					try: await message.channel.send(outputMessage)
					except: await self.unknown_error(message)
				return

			symbol, exchange = details
			base = self.coinParser.exchanges[exchange].markets[symbol]["base"] if exchange != "bitmex" else (self.coinParser.exchanges[exchange].markets[symbol]["info"]["positionCurrency"] if symbol != "ETH/USD" else "cont.")
			quote = self.coinParser.exchanges[exchange].markets[symbol]["quote"]
			coinPair = self.coinParser.exchanges[exchange].markets[symbol]["id"].replace("_", "").replace("/", "").replace("-", "").upper()

			if tickerId in self.rateLimited["d"][exchange]:
				rawData = self.rateLimited["d"][exchange][tickerId]
			else:
				try:
					rawData = self.coinParser.exchanges[exchange].fetch_order_book(symbol)
					self.rateLimited["d"][exchange][tickerId] = rawData
				except:
					try: await message.channel.send("Couldn't get depth data")
					except: await self.unknown_error(message)
					self.rateLimited["d"][exchange] = {}
					return

			tempAllAsks = [e[1] for e in rawData["asks"]]
			tempAllBids = [e[1] for e in rawData["bids"]]

			if len(tempAllAsks) == 0 or len(tempAllBids) == 0:
				try: await message.channel.send("Depth data for **{}** is not available".format(coinPair))
				except: await self.unknown_error(message)
				self.rateLimited["d"][exchange] = {}
				return

			averageAskQty = sum(tempAllAsks) / len(tempAllAsks)
			averageBidQty = sum(tempAllBids) / len(tempAllBids)

			rawAsks = []
			wallSum = 0
			previousPrice = 0
			for e in rawData["asks"]:
				if wallSum < averageAskQty:
					wallSum += e[1]
				else:
					rawAsks.append((previousPrice, wallSum))
					wallSum = 0
				previousPrice = e[0]

			rawBids = []
			wallSum = 0
			previousPrice = 0
			for e in rawData["bids"]:
				if wallSum < averageBidQty:
					wallSum += e[1]
				else:
					rawBids.append((e[0], wallSum))
					wallSum = 0
				previousPrice = e[0]

			orderedAsks = sorted([e[1] for e in rawAsks])
			askPrices = sorted([e[0] for e in rawAsks])
			orderedBids = sorted([e[1] for e in rawBids])
			bidPrices = sorted([e[0] for e in rawBids])

			maxQtyLen = max(
				len("{:,} {}".format(round(orderedAsks[-1], 1), quote)),
				len("{:,} {}".format(round(orderedBids[-1], 1), quote))
			)
			maxPriceLen = max(len("{:.4f}".format(askPrices[-1])), len("{:.4f}".format(bidPrices[-1])))

			asks = "{} ({}) asks: ```diff".format(coinPair, self.coinParser.exchanges[exchange].name)
			for e in reversed(rawAsks):
				if e[1] in orderedAsks[-(20 if len(orderedAsks) >= 20 else len(orderedAsks)):]:
					wall = "{:,} {}".format(round(e[1], 1), base)
					wallPrice = "{:.4f}".format(e[0])
					wallSpaces = " " * (maxQtyLen - len(wall))
					priceSpaces = " " * (maxPriceLen - len(wallPrice))
					asks += "\n- {}{} @ {}{} {}".format(wallSpaces, wall, priceSpaces, wallPrice if quote in ["USD", "USDT", "TUSD", "USDC"] else "{:.8f}".format(e[0]), quote)
			asks += "```"

			bids = "{} ({}) bids: ```diff".format(coinPair, self.coinParser.exchanges[exchange].name)
			for e in rawBids:
				if e[1] in orderedBids[-(20 if len(orderedBids) >= 20 else len(orderedBids)):]:
					wall = "{:,} {}".format(round(e[1], 1), base)
					wallPrice = "{:.4f}".format(e[0])
					wallSpaces = " " * (maxQtyLen - len(wall))
					priceSpaces = " " * (maxPriceLen - len(wallPrice))
					bids += "\n+ {}{} @ {}{} {}".format(wallSpaces, wall, priceSpaces, wallPrice if quote in ["USD", "USDT", "TUSD", "USDC"] else "{:.8f}".format(e[0]), quote)
			bids += "```"

			try:
				await message.channel.send(asks)
				await message.channel.send(bids)
			except: await self.unknown_error(message)

			await asyncio.sleep(self.coinParser.exchanges[exchange].rateLimit / 1000)
			self.rateLimited["d"][exchange].pop(tickerId, None)
			self.rateLimited["d"][exchange].pop(tickerId, None)
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["d"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def heatmap(self, message, raw, mute=False):
		try:
			sentMessages = []
			with message.channel.typing():
				arguments = raw.split(" ")

				outputMessage, arguments = self.imageProcessor.process_heatmap_arguments(arguments)
				if outputMessage is not None:
					if not mute:
						try: await message.channel.send(outputMessage)
						except: await self.unknown_error(message)
					if arguments is None:
						return ([], 0)

				timeframes, chart, type, side, category = arguments

				for timeframe in timeframes:
					queueLength = [len(self.imageProcessor.screengrabLock[e]) for e in self.imageProcessor.screengrabLock]
					driverInstance = queueLength.index(min(queueLength))

					waitMessage = None
					if len(self.imageProcessor.screengrabLock[driverInstance]) > 2:
						try: waitMessage = await message.channel.send("One moment...")
						except: pass
						await asyncio.sleep(0.5)

					chartName = await self.imageProcessor.request_heatmap_chart(message.author.id, driverInstance, timeframe, chart, type, side, category)

					if waitMessage is not None:
						try: await waitMessage.delete()
						except: pass
					if chartName is None:
						try:
							chartMessage = await message.channel.send("Couldn't get the requested heat map")
							sentMessages.append(chartMessage)
							try: await chartMessage.add_reaction("☑")
							except: pass
						except: await self.unknown_error(message)
						return (sentMessages, len(sentMessages))

					try:
						chartMessage = await message.channel.send(file=discord.File("charts/" + chartName, chartName))
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					except: await self.unknown_error(message)

					chartsFolder = "./charts/"
					files = sorted(os.listdir(chartsFolder))
					numOfFiles = len([chartName for chartName in files if ".png" in chartName and os.path.isfile(os.path.join(chartsFolder, chartName))])
					if numOfFiles > 100:
						for i in range(numOfFiles - 100):
							os.remove(os.path.join(chartsFolder, files[i]))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def mcap(self, message, raw, mute=False):
		try:
			arguments = raw.split(" ")

			tickerId = arguments[0].upper()
			conversion = ""
			if len(arguments) == 2:
				conversion = arguments[1]
			elif len(arguments) > 2:
				return

			if tickerId.startswith("$"):
				tickerId = tickerId.replace("$", "") + "USD"

			if len(tickerId.replace("-", "+").replace("/", "+").replace("*", "+").split("+")[:-1]) > 1:
				try: await message.channel.send("Aggregated tickers aren't supported with the **mcap** command")
				except: await self.unknown_error(message)
				return

			if tickerId in ["XBT", "XBTUSD"]:
				tickerId = "BTC"
			elif tickerId.endswith(("Z19", "U19")):
				tickerId = tickerId.replace("Z19", "USD").replace("U19", "USD").replace("XBT", "BTC")

			try: await message.channel.trigger_typing()
			except: pass

			for id in self.coinParser.exchanges:
				if conversion != "": break
				if self.coinParser.exchanges[id].symbols is not None:
					for symbol in self.coinParser.exchanges[id].symbols:
						pair = symbol.split("/")
						pair[-1] = pair[-1].replace("USDT", "USD").replace("USDC", "USD").replace("TUSD", "USD").replace("USDS", "USD").replace("PAX", "USD")
						if tickerId.startswith(pair[0]) and tickerId.replace(pair[0], "").endswith(pair[-1]):
							tickerId = pair[0]
							conversion = pair[-1].lower()
							break

			if tickerId.lower() in self.coinGeckoLink.coingeckoDataset:
				try:
					data = self.coinGeckoLink.coingecko.get_coin_by_id(id=self.coinGeckoLink.coingeckoDataset[tickerId.lower()], localization="false", tickers=False, market_data=True, community_data=True, developer_data=True)
				except:
					await self.unknown_error(message)
					return

				embed = discord.Embed(title="{} ({})".format(data["name"], tickerId), description="Ranked #{} by market cap".format(data["market_data"]["market_cap_rank"]), color=0xD949B7)
				embed.set_thumbnail(url=data["image"]["large"])

				if conversion == "": conversion = "usd"
				if conversion not in data["market_data"]["current_price"]:
					try: await message.channel.send("Conversion to **{}** is not available".format(conversion.upper()))
					except: await self.unknown_error(message)
					return

				usdPrice = ("${:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["usd"])).format(data["market_data"]["current_price"]["usd"])
				eurPrice = ("\n€{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["eur"])).format(data["market_data"]["current_price"]["eur"])
				btcPrice = ""
				ethPrice = ""
				bnbPrice = ""
				xrpPrice = ""
				basePrice = ""
				if tickerId != "BTC" and "btc" in data["market_data"]["current_price"]:
					btcPrice = ("\n₿{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["btc"])).format(data["market_data"]["current_price"]["btc"])
				if tickerId != "ETH" and "eth" in data["market_data"]["current_price"]:
					ethPrice = ("\nΞ{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["eth"])).format(data["market_data"]["current_price"]["eth"])
				if tickerId != "BNB" and "bnb" in data["market_data"]["current_price"]:
					bnbPrice = ("\n{:,.%df} BNB" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["bnb"])).format(data["market_data"]["current_price"]["bnb"])
				if tickerId != "XRP" and "xrp" in data["market_data"]["current_price"]:
					xrpPrice = ("\n{:,.%df} XRP" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["xrp"])).format(data["market_data"]["current_price"]["xrp"])
				if conversion in data["market_data"]["current_price"] and conversion.upper() not in ["USD", "EUR", "BTC", "ETH", "BNB", "XRP"]:
					basePrice = ("\n{:,.%df} {}" % Utils.add_decimal_zeros(data["market_data"]["current_price"][conversion])).format(data["market_data"]["current_price"][conversion], conversion.upper())
				embed.add_field(name="Price", value=(usdPrice + eurPrice + btcPrice + ethPrice + bnbPrice + xrpPrice + basePrice), inline=True)

				change1h = ""
				change24h = ""
				change7d = ""
				change30d = ""
				change1y = ""
				if conversion in data["market_data"]["price_change_percentage_1h_in_currency"]:
					change1h = "Past hour: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_1h_in_currency"][conversion])
				if conversion in data["market_data"]["price_change_percentage_24h_in_currency"]:
					change24h = "\nPast day: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_24h_in_currency"][conversion])
				if conversion in data["market_data"]["price_change_percentage_7d_in_currency"]:
					change7d = "\nPast week: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_7d_in_currency"][conversion])
				if conversion in data["market_data"]["price_change_percentage_30d_in_currency"]:
					change30d = "\nPast month: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_30d_in_currency"][conversion])
				if conversion in data["market_data"]["price_change_percentage_1y_in_currency"]:
					change1y = "\nPast year: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_1y_in_currency"][conversion])
				embed.add_field(name="Price Change", value=(change1h + change24h + change7d + change30d + change1y), inline=True)

				marketCap = ""
				totalVolume = ""
				totalSupply = ""
				circulatingSupply = ""
				if data["market_data"]["market_cap"] is not None:
					marketCap = "Market cap: {:,.0f} {}".format(data["market_data"]["market_cap"][conversion], conversion.upper())
				if data["market_data"]["total_volume"] is not None:
					totalVolume = "\nTotal volume: {:,.0f} {}".format(data["market_data"]["total_volume"][conversion], conversion.upper())
				if data["market_data"]["total_supply"] is not None:
					totalSupply = "\nTotal supply: {:,.0f}".format(data["market_data"]["total_supply"])
				if data["market_data"]["circulating_supply"] is not None:
					circulatingSupply = "\nCirculating supply: {:,.0f}".format(data["market_data"]["circulating_supply"])
				embed.add_field(name="Details", value=(marketCap + totalVolume + totalSupply + circulatingSupply), inline=False)

				embed.set_footer(text="Powered by CoinGecko API")

				try: await message.channel.send(embed=embed)
				except: await self.unknown_error(message)
			elif not mute:
				try: int(tickerId)
				except:
					try: await message.channel.send("Coin information from CoinGecko for **{}** is not available".format(tickerId))
					except: await self.unknown_error(message)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def markets(self, message, raw, mute=False):
		try:
			arguments = raw.split(" ")

			tickerId = arguments[0].upper()
			if tickerId.startswith("$"):
				tickerId = tickerId.replace("$", "") + "USD"

			if len(tickerId.replace("-", "+").replace("/", "+").replace("*", "+").split("+")[:-1]) > 1:
				try: await message.channel.send("Aggregated tickers aren't supported with the **mk** command")
				except: await self.unknown_error(message)
				return

			if tickerId in ["XBT", "XBTUSD", "BTC"]:
				tickerId = "BTCUSD"
			elif tickerId.endswith(("Z19", "U19")):
				tickerId = tickerId.replace("BTC", "XBT")
			elif tickerId in self.coinParser.coins["all"]: tickerId = tickerId + "BTC"

			listings = []
			for id in self.coinParser.exchanges:
				if self.coinParser.exchanges[id].symbols is not None:
					for symbol in self.coinParser.exchanges[id].symbols:
						pair = symbol.split("/")
						if (tickerId.startswith(pair[0]) and (tickerId.replace(pair[0], "").endswith(pair[-1]) or tickerId.replace(pair[0], "").endswith(pair[-1].replace("USDT", "USD").replace("USDC", "USD").replace("TUSD", "USD").replace("USDS", "USD").replace("PAX", "USD")))) or (tickerId == pair[0] and len(pair) == 1):
							if self.coinParser.exchanges[id].name not in listings:
								listings.append(self.coinParser.exchanges[id].name)
							break
			try:
				if len(listings) != 0: await message.channel.send("__**{}**__ is listed on the following exchanges: **{}**.".format(tickerId, "**, **".join(listings)))
				else: await message.channel.send("__**{}**__ is not listed on any exchange.".format(tickerId))
			except: await self.unknown_error(message)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def funnyReplies(self, message, raw):
		for response in constants.funnyReplies:
			for trigger in constants.funnyReplies[response]:
				if raw == trigger:
					try: await message.channel.send(response)
					except: pass
					return True
		return False

def handle_exit():
	client.loop.run_until_complete(client.logout())
	for t in asyncio.Task.all_tasks(loop=client.loop):
		if t.done():
			try: t.exception()
			except asyncio.InvalidStateError: pass
			except asyncio.TimeoutError: pass
			except asyncio.CancelledError: pass
			continue
		t.cancel()
		try:
			client.loop.run_until_complete(asyncio.wait_for(t, 5, loop=client.loop))
			t.exception()
		except asyncio.InvalidStateError: pass
		except asyncio.TimeoutError: pass
		except asyncio.CancelledError: pass

if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument("--guild", default=0, type=int, help="Dedicated guild ID", nargs="?", required=False)
	options = parser.parse_args()

	client = Alpha() if options.guild == -1 else Alpha(shard_count=1)
	client.prepare(for_guild=options.guild)

	while True:
		client.loop.create_task(client.update_queue())
		try:
			if sys.platform == "linux":
				client.loop.run_until_complete(client.start(ApiKeys.get_discord_token()))
			else:
				client.loop.run_until_complete(client.start(ApiKeys.get_discord_token(mode="debug")))
		except KeyboardInterrupt:
			handle_exit()
			client.loop.close()
			break
		except (Exception, SystemExit):
			handle_exit()

		client = Alpha(loop=client.loop) if options.guild == -1 else Alpha(loop=client.loop, shard_count=1)
