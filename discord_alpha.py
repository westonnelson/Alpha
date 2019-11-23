import os, os.path
import sys
import re
import random
import copy
import json
import time
import datetime
import pytz
import urllib
import threading
import argparse
import logging
import atexit

import discord
import dbl
import asyncio
import ccxt

from firebase_admin import initialize_app as initialize_firebase_app
from firebase_admin import credentials, firestore, storage
from google.cloud import exceptions

from bot.keys.keys import Keys as ApiKeys
from bot.assets import firebase_storage
from bot.helpers.utils import Utils
from bot.helpers.logger import Logger as l
from bot.helpers import constants

from bot.engine.assistant import Assistant
from bot.engine.alerts import Alerts
from bot.engine.presets import Presets
from bot.engine.images import ImageProcessor
from bot.engine.coins import CoinParser
from bot.engine.trader import PaperTrader
from bot.engine.fusion import Fusion

from bot.engine.connections.exchanges import Exchanges
from bot.engine.connections.coingecko import CoinGecko
from bot.engine.connections.alternativeme import Alternativeme

try:
	firebase = initialize_firebase_app(credentials.Certificate("bot/keys/firebase_credentials.json"), {'storageBucket': ApiKeys.get_firebase_bucket()})
	db = firestore.client()
	bucket = storage.bucket()
except Exception as e:
	exc_type, exc_obj, exc_tb = sys.exc_info()
	fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
	l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))
	os._exit(1)

# Command history
history = logging.getLogger("History")
history.setLevel(logging.DEBUG)
hfh = logging.FileHandler("command_history.log", mode="a")
hfh.setLevel(logging.DEBUG)
history.addHandler(hfh)

class Alpha(discord.AutoShardedClient):
	isBotReady = False
	lastMessageTimestamp = None
	dedicatedGuild = -1
	dblpy = None

	assistant = Assistant()
	alerts = Alerts()
	imageProcessor = ImageProcessor()
	coinParser = CoinParser()
	paperTrader = PaperTrader()
	fusion = Fusion()

	exchangeConnection = Exchanges()
	coinGeckoConnection = CoinGecko()
	alternativemeConnection = Alternativeme()

	statistics = {"alpha": 0, "alerts": 0, "c": 0, "p": 0, "v": 0, "d": 0, "hmap": 0, "mcap": 0, "mk": 0, "paper": 0}
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
			if side in ["unsupported"]: continue
			for id in constants.supportedExchanges[side]:
				if id not in self.coinParser.exchanges:
					self.rateLimited["p"][id] = {}
					self.rateLimited["d"][id] = {}
					self.rateLimited["v"][id] = {}
					try: self.coinParser.exchanges[id] = getattr(ccxt, id)()
					except: continue

		self.dblpy = dbl.DBLClient(client, ApiKeys.get_topgg_key())

		try:
			newOhlcvExchanges = []
			newOrderBookExchanges = []
			for id in ccxt.exchanges:
				if id in constants.supportedExchanges["unsupported"]: continue
				try:
					exchange = getattr(ccxt, id)()
					if exchange.has["fetchOrderBook"] and exchange.has["fetchOHLCV"] and hasattr(exchange, "timeframes"):
						if exchange.name not in [self.coinParser.exchanges[id].name for id in constants.supportedExchanges["ohlcv"]]:
							newOhlcvExchanges.append(id)
				except Exception as e:
					l.log("Exchage ID might have changed: {} ({})".format(id, e))
			if len(newOhlcvExchanges) != 0:
				l.log("New OHLCV supported exchanges: {}".format(newOhlcvExchanges))
			if len(newOrderBookExchanges) != 0:
				l.log("New OHLCV + orderbook supported exchanges: {}".format(newOrderBookExchanges))
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def on_ready(self):
		t = datetime.datetime.now().astimezone(pytz.utc)

		self.coinParser.refresh_coins()
		self.coinGeckoConnection.refresh_coingecko_datasets()
		self.fetch_settings(t)
		self.update_fusion_queue()

		try:
			priceData = self.coinParser.exchanges["bitmex"].fetch_ohlcv(
				"BTC/USD",
				timeframe="1d",
				since=(self.coinParser.exchanges["bitmex"].milliseconds() - 24 * 60 * 60 * 3 * 1000)
			)
			self.coinParser.lastBitcoinPrice = priceData[-1][4]
		except: pass

		await self.wait_for_chunked()
		if sys.platform == "linux":
			await self.update_guild_count()
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
				channel = client.get_channel(560884869899485233)
				alphaMessage = await channel.fetch_message(640502830062632960)
				onlineEmbed = discord.Embed(title=":white_check_mark: Alpha: online", color=constants.colors["deep purple"])
				await alphaMessage.edit(embed=onlineEmbed)
			except: pass

		await self.update_properties()
		await self.update_premium_message()
		await self.security_check()
		await self.send_alerts()
		await self.update_system_status(t)
		await self.update_price_status(t)

		self.isBotReady = True
		l.log("Status", "Alpha is online on {} servers ({:,} users)".format(len(client.guilds), len(client.users)))

	async def wait_for_chunked(self):
		for guild in client.guilds:
			if not guild.chunked: await asyncio.sleep(1)

	def cleanup(self):
		print("")
		l.log("Status", "timestamp: {}, description: Alpha bot is restarting".format(Utils.get_current_date()), post=sys.platform == "linux")

		try:
			for i in self.imageProcessor.screengrab:
				try: self.imageProcessor.screengrab[i].quit()
				except: continue
		except: pass

		try:
			if self.statistics["c"] > 0 and sys.platform == "linux":
				statisticsRef = db.document(u"alpha/statistics")
				for i in range(5):
					try:
						t = datetime.datetime.now().astimezone(pytz.utc)
						statisticsRef.set({"{}-{}".format(t.month, t.year): {"discord": self.statistics}}, merge=True)
						break
					except Exception as e:
						if i == 4: raise e
						else: time.sleep(5)
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def on_guild_join(self, guild):
		await self.update_guild_count()
		if guild.id in constants.bannedGuilds:
			l.log("Warning", "timestamp: {}, description: left a blocked server: {}".format(Utils.get_current_date(), message.guild.name))
			try: await guild.leave()
			except: pass

	async def on_guild_remove(self, guild):
		await self.update_guild_count()

	async def update_guild_count(self):
		try: await self.dblpy.post_guild_count()
		except: pass

	def fetch_settings(self, t):
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
				slice = "{}-{}".format(t.month, t.year)
				for data in statisticsData[slice]["discord"]:
					self.statistics[data] = statisticsData[slice]["discord"][data]
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Fatal Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))
			time.sleep(15)
			self.fetch_settings(t)

	def update_fusion_queue(self):
		try:
			instances = self.fusion.manage_load_distribution(self.coinParser.exchanges)
			if sys.platform == "linux":
				try: db.document(u'fusion/alpha').set({"distribution": instances}, merge=True)
				except: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_properties(self):
		try:
			alphaServer = client.get_guild(414498292655980583)
			role = discord.utils.get(alphaServer.roles, id=484387309303758848)

			if sys.platform == "linux":
				allUsers = [e.id for e in client.users]
				allGuilds = [e.id for e in client.guilds]

				batch = db.batch()
				i = 0
				for userId in list(self.userProperties):
					if userId not in allUsers:
						i += 1
						self.userProperties.pop(userId, None)
						batch.delete(db.document(u"alpha/settings/users/{}".format(userId)))
						if i == 500:
							batch.commit()
							i, batch = 0, None
							batch = db.batch()

				for guildId in list(self.guildProperties):
					if guildId not in allGuilds:
						i += 1
						self.guildProperties.pop(guildId, None)
						batch.delete(db.document(u"alpha/settings/servers/{}".format(userId)))
						if i == 500:
							batch.commit()
							i, batch = 0, None
							batch = db.batch()

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
								embed = discord.Embed(title="Your Alpha Premium subscription has expired", color=constants.colors["deep purple"])
								await recepient.send(embed=embed)
								await alphaServer.get_member(userId).remove_roles(role)
							except: pass
						elif self.userProperties[userId]["premium"]["timestamp"] - 259200 < time.time() and not self.userProperties[userId]["premium"]["hadWarning"]:
							recepient = client.get_user(userId)
							self.userProperties[userId]["premium"]["hadWarning"] = True
							fetchedSettingsRef.set(self.userProperties[userId], merge=True)
							if recepient is not None:
								embed = discord.Embed(title="Your Alpha Premium subscription expires on {}".format(self.userProperties[userId]["premium"]["date"]), color=constants.colors["deep purple"])
								try: await recepient.send(embed=embed)
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
									embed = discord.Embed(title="Alpha Premium subscription for your *{}* server has expired".format(guild.name), color=constants.colors["deep purple"])
									try: await member.send(embed=embed)
									except: pass
						elif self.guildProperties[guildId]["premium"]["timestamp"] - 259200 < time.time() and not self.guildProperties[guildId]["premium"]["hadWarning"]:
							guild = client.get_guild(guildId)
							self.guildProperties[guildId]["premium"]["hadWarning"] = True
							fetchedSettingsRef.set(self.guildProperties[guildId], merge=True)
							if guild is not None:
								for member in guild.members:
									if member.guild_permissions.administrator:
										embed = discord.Embed(title="Alpha Premium subscription for your *{}* server expires on {}".format(guild.name, self.guildProperties[guildId]["premium"]["date"]), color=constants.colors["deep purple"])
										try: await member.send(embed=embed)
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
					if (member.name.lower() in ["[alpha] maco", "maco <alpha dev>", "maco", "macoalgo", "alpha"] or False if member.nick is None else member.nick.lower() in ["[alpha] maco", "maco <alpha dev>", "maco", "macoalgo", "alpha"]) and member.id != 361916376069439490:
						if str(member.avatar_url) not in self.alphaSettings["avatarWhitelist"]:
							suspiciousUser = "**{}#{}** ({}): {}".format(member.name, member.discriminator, member.id, member.avatar_url)
							if suspiciousUser not in suspiciousUsers: suspiciousUsers.append(suspiciousUser)

			nicknamesMessage = ""
			suspiciousUsersMessage = ""
			if len(nicknames) > 0:
				nicknamesMessage = "These servers might be rebranding Alpha bot:\n● {}".format("\n● ".join(nicknames))
			if len(suspiciousUsers) > 0:
				suspiciousUsersMessage = "\n\nThese users might be impersonating MacoAlgo#9999:\n● {}".format("\n● ".join(suspiciousUsers))

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
						priceData = self.coinParser.exchanges["binance"].fetch_ohlcv(
							symbol,
							timeframe="1h",
							since=(self.coinParser.exchanges["binance"].milliseconds() - 60 * 60 * 2 * 1000)
						)
						prices[symbol] = priceData[-1][4]
						break
					except: await asyncio.sleep(self.coinParser.exchanges["binance"].rateLimit / 1000 * 2)

			if prices["BTC/USDT"] != 0 and prices["ETH/USDT"] != 0:
				premiumText = "__**A L P H A   P R E M I U M   F E A T U R E S**__\nWith Alpha premium you'll get access to price alerts, command presets, as well as increased rate limits.\n\n**Price alerts**\nPrice alerts allow you to make sure you don't miss a single move in the market. Alpha will notify you of a price move right through Discord, so you don't have to move between platforms.\n\n**Command presets**\nWith command presets you're able to make multiple requests at once, save your chart layouts or pull prices of your hodl porfolio.\n\n**Trading features _(coming soon)_**\nAlpha will allow you to make instant paper or live trades on crypto exchanges with just a single message.\n\n**Other perks**\nThe premium package also comes with raised limits allowing you to make virtually infinite number requests.\n\n**Dedicated VPS _(only when purchasing the server package)_**\nA dedicated virtual private server will deliver cutting edge performance of Alpha in your discord server even during high load.\n\n__**P R I C I N G**__\nAlpha premium can be purchased with crypto directly or via Patreon (<https://www.patreon.com/AlphaBotSystem>). All users and servers are eligible for one month free trial (dedicated VPS is not included). Please, contact <@!361916376069439490> for more details.\n\n**Individuals**\nSubscription for individuals costs $15/month or $135/year ({:,.4f} BTC/month and {:,.4f} BTC/year or {:,.4f} ETH/month and {:,.4f} ETH/year respectively)\n\n**Servers**\nSubscription for servers costs $100/month or $900/year ({:,.4f} BTC/month and {:,.4f} BTC/year or {:,.4f} ETH/month and {:,.4f} ETH/year respectively) and covers every user in the server.".format(15 / prices["BTC/USDT"], 135 / prices["BTC/USDT"], 15 / prices["ETH/USDT"], 135 / prices["ETH/USDT"], 100 / prices["BTC/USDT"], 900 / prices["BTC/USDT"], 100 / prices["ETH/USDT"], 900 / prices["ETH/USDT"])

				premiumChannel = client.get_channel(647165019578171432)
				try:
					premiumMessage = await premiumChannel.fetch_message(647521277963272192)
					await premiumMessage.edit(content=premiumText)
				except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_system_status(self, t):
		try:
			statisticsRef = db.document(u"alpha/statistics")
			statisticsRef.set({"{}-{}".format(t.month, t.year): {"discord": self.statistics}}, merge=True)

			numOfCharts = ":chart_with_upwards_trend: {:,} charts requested".format(self.statistics["c"] + self.statistics["hmap"])
			numOfAlerts = ":bell: {:,} alerts set".format(self.statistics["alerts"])
			numOfPrices = ":money_with_wings: {:,} prices pulled".format(self.statistics["d"] + self.statistics["p"] + self.statistics["v"])
			numOfDetails = ":tools: {:,} coin details looked up".format(self.statistics["mcap"] + self.statistics["mk"])
			numOfQuestions = ":crystal_ball: {:,} questions asked".format(self.statistics["alpha"])
			numOfServers = ":heart: Used in {:,} servers with {:,} members".format(len(client.guilds), len(client.users))

			req = urllib.request.Request("https://status.discordapp.com", headers={"User-Agent": "Mozilla/5.0"})
			webpage = str(urllib.request.urlopen(req).read())
			isDiscordWorking = "All Systems Operational" in webpage

			statisticsEmbed = discord.Embed(title="{}\n{}\n{}\n{}\n{}\n{}".format(numOfCharts, numOfAlerts, numOfPrices, numOfDetails, numOfQuestions, numOfServers), color=constants.colors["deep purple"])
			discordStatusEmbed = discord.Embed(title=":bellhop: Average ping: {:,.1f} milliseconds\n:satellite: Processing {:,.0f} messages per minute\n:signal_strength: Discord: {}".format(self.fusion.averagePing * 1000, self.fusion.averageMessages, "all systems operational" if isDiscordWorking else "degraded performance"), color=constants.colors["deep purple" if isDiscordWorking else "gray"])

			if sys.platform == "linux":
				channel = client.get_channel(560884869899485233)
				if self.statistics["c"] > 0:
					try:
						statsMessage = await channel.fetch_message(640502810244415532)
						await statsMessage.edit(embed=statisticsEmbed)
					except: pass
				try:
					statusMessage = await channel.fetch_message(640502825784180756)
					await statusMessage.edit(embed=discordStatusEmbed)
				except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_price_status(self, t):
		cycle = int(t.second / 15)
		fetchPairs = {
			0: (("bitmex", "MEX"), "BTC/USD", "ETH/USD"),
			1: (("binance", "BIN"), "BTC/USDT", "ETH/USDT"),
			2: (("bitmex", "MEX"), "BTC/USD", "ETH/USD"),
			3: (("binance", "BIN"), "BTC/USDT", "ETH/USDT")
		}

		price1 = None
		try:
			if fetchPairs[cycle][1] in self.rateLimited["p"][fetchPairs[cycle][0][0]]:
				price1 = self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][1]]
			else:
				priceData = self.coinParser.exchanges[fetchPairs[cycle][0][0]].fetch_ohlcv(
					fetchPairs[cycle][1],
					timeframe="1d",
					since=(self.coinParser.exchanges[fetchPairs[cycle][0][0]].milliseconds() - 24 * 60 * 60 * 3 * 1000)
				)
				price1 = [priceData[-1][4], priceData[-2][4]]
				self.coinParser.lastBitcoinPrice = price1[0]
				self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][1]] = price1
		except: pass

		price2 = None
		try:
			if fetchPairs[cycle][2] in self.rateLimited["p"][fetchPairs[cycle][0][0]]:
				price2 = self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][2]]
			else:
				priceData = self.coinParser.exchanges[fetchPairs[cycle][0][0]].fetch_ohlcv(
					fetchPairs[cycle][2],
					timeframe="1d",
					since=(self.coinParser.exchanges[fetchPairs[cycle][0][0]].milliseconds() - 24 * 60 * 60 * 3 * 1000)
				)
				price2 = [priceData[-1][4], priceData[-2][4]]
				self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][2]] = price2
		except: pass

		price1Text = " -" if price1 is None else "{:,.0f}".format(price1[0])
		price2Text = " -" if price2 is None else "{:,.0f}".format(price2[0])

		try: await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="{} ₿ {} Ξ {}".format(fetchPairs[cycle][0][1], price1Text, price2Text)))
		except: pass

		threading.Thread(target=self.clear_rate_limit_cache, args=(fetchPairs[cycle][0][0], fetchPairs[cycle][1], ["p"], self.coinParser.exchanges[fetchPairs[cycle][0][0]].rateLimit / 1000 * 2)).start()
		threading.Thread(target=self.clear_rate_limit_cache, args=(fetchPairs[cycle][0][0], fetchPairs[cycle][2], ["p"], self.coinParser.exchanges[fetchPairs[cycle][0][0]].rateLimit / 1000 * 2)).start()

	async def send_alerts(self):
		try:
			incomingAlertsChannel = client.get_channel(605419986164645889)
			if sys.platform == "linux" and incomingAlertsChannel is not None:
				alertMessages = await incomingAlertsChannel.history(limit=None).flatten()
				for message in reversed(alertMessages):
					userId, alertMessage = message.content.split(": ", 1)
					embed = discord.Embed(title=alertMessage, color=constants.colors["deep purple"])
					embed.set_author(name="Alert triggered", icon_url=firebase_storage.icon)

					try:
						alertUser = client.get_user(int(userId))
						await alertUser.send(embed=embed)
					except:
						try:
							outgoingAlertsChannel = client.get_channel(595954290409865226)
							outgoingAlertsChannel.send(content="<@!{}>!".format(alertUser.id), embed=embed)
						except Exception as e:
							exc_type, exc_obj, exc_tb = sys.exc_info()
							fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
							l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

				for message in reversed(alertMessages):
					try: await message.delete()
					except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	def server_ping(self):
		try:
			if sys.platform == "linux" and self.lastMessageTimestamp is not None:
				db.document(u'fusion/alpha').set({"lastUpdate": {"timestamp": self.lastMessageTimestamp[0], "time": self.lastMessageTimestamp[1].strftime("%m. %d. %Y, %H:%M")}}, merge=True)

			instances = db.collection(u'fusion').stream()
			for instance in instances:
				num = str(instance.id)
				if num.startswith("instance"):
					num = int(str(instance.id).split("-")[-1])
					instance = instance.to_dict()
					if instance["lastUpdate"]["timestamp"] + 360 < time.time():
						l.log("Warning", "timestamp: {}, description: Fusion instance {} is not responding".format(Utils.get_current_date(), num))
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_queue(self):
		while True:
			await asyncio.sleep(Utils.seconds_until_cycle())
			if not self.isBotReady: continue
			t = datetime.datetime.now().astimezone(pytz.utc)
			timeframes = Utils.get_accepted_timeframes(t)

			await self.update_price_status(t)
			await self.send_alerts()
			if "5m" in timeframes:
				self.server_ping()
				await self.update_system_status(t)
			if "1H" in timeframes:
				await self.update_premium_message()
				await self.security_check()
				await self.update_properties()
			if "4H" in timeframes:
				self.update_fusion_queue()
			if "1D" in timeframes:
				self.coinGeckoConnection.refresh_coingecko_datasets()
				if t.day == 1 and alphaSettings["lastStatsSnapshot"] != t.month:
					self.fusion.push_active_users(bucket, t)

	async def on_message(self, message):
		try:
			if message.channel.id == 595619229839917069: print("\n\n\n")
			guildId = message.guild.id if message.guild is not None else -1
			authorId = message.webhook_id if message.webhook_id is not None else message.author.id
			self.lastMessageTimestamp = (datetime.datetime.timestamp(pytz.utc.localize(message.created_at)), pytz.utc.localize(message.created_at))
			if self.dedicatedGuild != 0 and self.dedicatedGuild != guildId: return

			isSelf = message.author == client.user
			isUserBlocked = authorId in constants.blockedUsers if not message.author.bot else any(e in message.author.name.lower() for e in constants.blockedBotNames) or authorId in constants.blockedBots
			isChannelBlocked = message.channel.id in constants.blockedChannels or guildId in constants.blockedGuilds
			hasContent = message.clean_content != "" or isSelf

			if not self.isBotReady or isUserBlocked or isChannelBlocked or not hasContent: return

			isPersonalPremium = authorId in self.subscribedUsers
			isServerPremium = guildId in self.subscribedGuilds
			isPremium = isPersonalPremium or isServerPremium

			raw = " ".join(message.clean_content.lower().split())
			sentMessages = []
			shortcutsEnabled = True if guildId not in self.guildProperties else self.guildProperties[guildId]["settings"]["shortcuts"]
			presetUsed = False
			shortcutUsed = False
			limit = 30 if isPremium else 10
			hasMentions = len(message.mentions) != 0 or len(message.channel_mentions) != 0 or len(message.role_mentions) != 0 or "@everyone" in message.content or "@here" in message.content
			hasSendPermission = (True if message.guild is None else message.guild.me.permissions_in(message.channel).send_messages)

			if not raw.startswith("preset "):
				parsedPresets = []
				if message.author.id in self.userProperties: raw, presetUsed, parsedPresets = Presets.process_presets(raw, self.userProperties[message.author.id])
				if not presetUsed and guildId in self.guildProperties: raw, presetUsed, parsedPresets = Presets.process_presets(raw, self.guildProperties[guildId])

				if not presetUsed and guildId in self.usedPresetsCache:
					for preset in self.usedPresetsCache[guildId]:
						if preset["phrase"] == raw:
							if preset["phrase"] not in [p["phrase"] for p in parsedPresets]:
								parsedPresets = [preset]
								presetUsed = False
								break

				if isPremium:
					if presetUsed:
						if guildId != -1:
							if guildId not in self.usedPresetsCache: self.usedPresetsCache[guildId] = []
							for preset in parsedPresets:
								if preset not in self.usedPresetsCache[guildId]: self.usedPresetsCache[guildId].append(preset)
							self.usedPresetsCache[guildId] = self.usedPresetsCache[guildId][-3:]

						embed = discord.Embed(title="Running `{}` command from personal preset".format(raw), color=constants.colors["light blue"])
						try: sentMessages.append(await message.channel.send(embed=embed))
						except: pass
					elif len(parsedPresets) != 0:
						embed = discord.Embed(title="Do you want to add preset `{}` → `{}` to your account?".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"]), color=constants.colors["light blue"])
						try: addPresetMessage = await message.channel.send(embed=embed)
						except: pass

						def confirm_order(m):
							if m.author.id == message.author.id:
								response = ' '.join(m.clean_content.lower().split())
								if response.startswith(("y", "yes", "sure", "confirm", "execute")): return True
								elif response.startswith(("n", "no", "cancel", "discard", "reject")): raise Exception()

						try:
							this = await client.wait_for('message', timeout=60.0, check=confirm_order)
						except:
							embed = discord.Embed(title="Canceled", description="~~Do you want to add `{}` → `{}` to your account?~~".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"]), color=constants.colors["gray"])
							try: await addPresetMessage.edit(embed=embed)
							except: pass
							return
						else:
							raw = "preset add {} {}".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"])
				elif len(parsedPresets) != 0:
					try: await message.channel.send(content="Presets are available for premium members only. {}".format("Join our server to learn more: https://discord.gg/H9sS6WK" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more."))
					except Exception as e: await self.unknown_error(message, authorId, e)

			raw, shortcutUsed = Utils.shortcuts(raw, shortcutsEnabled)
			useMute = presetUsed or shortcutUsed
			isCommand = raw.startswith(("alpha ", "alert ", "alerts", "preset ", "c ", "p ", "d ", "v ", "hmap ", "mcap ", "mc ", "mk ", "convert ", "paper ")) and not isSelf

			if guildId != -1 and isCommand:
				if message.guild.name in self.alphaSettings["tosBlacklist"]:
					embed = discord.Embed(title="This server is violating Alpha terms of service", description="{}\n\nFor more info, join Alpha server".format(constants.termsOfService), color=0x000000)
					try:
						await message.channel.send(embed=embed)
						await message.channel.send(content="https://discord.gg/GQeDE85")
					except: pass
				# elif (True if guildId not in self.guildProperties else not self.guildProperties[guildId]["hasDoneSetup"]):
				# 	embed = discord.Embed(title="Thanks for adding Alpha to your server, we're thrilled to have you onboard. We think you're going to love what Alpha can do. Before you start using it, you must complete a short setup process. Type `alpha setup` to begin.", color=constants.colors["pink"])
				# 	try: await message.channel.send(embed=embed)
				# 	except Exception as e: await self.unknown_error(message, authorId, e)

			if raw.startswith("a "):
				if message.author.bot: return

				command = raw.split(" ", 1)[1]
				if command == "help":
					await self.help(message, authorId, raw, shortcutUsed)
					return
				elif command == "invite":
					try: await message.channel.send(embed=discord.Embed(title="https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372033", color=constants.colors["pink"]))
					except Exception as e: await self.unknown_error(message, authorId, e)
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
								serverSettings = Utils.updateServerSetting(serverSettings, "settings", sub="assistant", toVal=newVal)
								serverSettingsRef.set(serverSettings, merge=True)
								self.guildProperties[guildId] = copy.deepcopy(serverSettings)

								try: await message.channel.send(embed=discord.Embed(title="Google Assistant settings saved for *{}* server".format(message.guild.name), color=constants.colors["pink"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
						return
					elif command.startswith("shortcuts"):
						if message.author.guild_permissions.administrator:
							newVal = None
							if command == "shortcuts disable": newVal = False
							elif command == "shortcuts enable": newVal = True

							if newVal is not None:
								serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
								serverSettings = serverSettingsRef.get().to_dict()
								serverSettings = Utils.updateServerSetting(serverSettings, "settings", sub="shortcuts", toVal=newVal)
								serverSettingsRef.set(serverSettings, merge=True)
								self.guildProperties[guildId] = copy.deepcopy(serverSettings)

								try: await message.channel.send(embed=discord.Embed(title="Shortcuts settings saved for *{}* server".format(message.guild.name), color=constants.colors["pink"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
						return
					elif command.startswith("autodelete"):
						if message.author.guild_permissions.administrator:
							newVal = None
							if command == "autodelete disable": newVal = False
							elif command == "autodelete enable": newVal = True

							if newVal is not None:
								serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
								serverSettings = serverSettingsRef.get().to_dict()
								serverSettings = Utils.updateServerSetting(serverSettings, "settings", sub="autodelete", toVal=newVal)
								serverSettingsRef.set(serverSettings, merge=True)
								self.guildProperties[guildId] = copy.deepcopy(serverSettings)

								try: await message.channel.send(embed=discord.Embed(title="Autodelete settings saved for *{}* server".format(message.guild.name), color=constants.colors["pink"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
						return
				if authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
					if command.startswith("premium user"):
						subscription = raw.split("premium user ", 1)
						if len(subscription) == 2:
							parameters = subscription[1].split(" ", 1)
							if len(parameters) == 2:
								userId, plan = parameters
								trial = False
								if plan == "trial": plan, trial = 1, True

								allUsers = [e.id for e in client.users]
								if int(userId) not in allUsers:
									try: await message.channel.send(embed=discord.Embed(title="No users with this id found", color=constants.colors["gray"]))
									except: pass
									return
								recepient = client.get_user(int(userId))

								alphaServer = client.get_guild(414498292655980583)
								role = discord.utils.get(alphaServer.roles, id=484387309303758848)

								fetchedSettingsRef = db.document(u"alpha/settings/users/{}".format(int(userId)))
								fetchedSettings = fetchedSettingsRef.get().to_dict()
								fetchedSettings = Utils.createUserSettings(fetchedSettings)

								hadTrial = fetchedSettings["premium"]["hadTrial"]
								wasSubscribed = fetchedSettings["premium"]["subscribed"]
								if hadTrial and trial:
									if not wasSubscribed:
										try: await message.channel.send(embed=discord.Embed(title="This user already had a trial", color=constants.colors["gray"]))
										except: pass
									try: await message.delete()
									except: pass
									return

								lastTimestamp = fetchedSettings["premium"]["timestamp"]
								timestamp = (lastTimestamp if time.time() < lastTimestamp else time.time()) + 2635200 * int(plan)
								date = datetime.datetime.utcfromtimestamp(timestamp)
								fetchedSettings["premium"] = {"subscribed": True, "hadTrial": hadTrial or trial, "hadWarning": False, "timestamp": timestamp, "date": date.strftime("%m. %d. %Y"), "plan": int(plan)}

								fetchedSettingsRef.set(fetchedSettings, merge=True)
								self.userProperties[int(userId)] = copy.deepcopy(fetchedSettings)
								self.subscribedUsers.append(int(userId))
								try: await alphaServer.get_member(int(userId)).add_roles(role)
								except: pass

								if int(plan) > 0:
									if wasSubscribed:
										try:
											await recepient.send(embed=discord.Embed(title="Your Alpha Premium subscription was extended. Current expiry date: {}".format(fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
										except:
											outgoingAlertsChannel = client.get_channel(595954290409865226)
											try: await outgoingAlertsChannel.send(content="<@!{}>".format(userId), embed=discord.Embed(title="Your Alpha Premium subscription was extended. Current expiry date: {}".format(fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
											except: pass
									else:
										try:
											await recepient.send(embed=discord.Embed(title="Enjoy your Alpha Premium subscription. Current expiry date: {}".format(fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
										except:
											outgoingAlertsChannel = client.get_channel(595954290409865226)
											try: await outgoingAlertsChannel.send(content="<@!{}>".format(userId), embed=discord.Embed(title="Enjoy your Alpha Premium subscription. Current expiry date: {}".format(fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
											except: pass
								try: await message.delete()
								except: pass
						return
					elif command.startswith("premium server"):
						subscription = raw.split("premium server ", 1)
						if len(subscription) == 2:
							parameters = subscription[1].split(" ", 1)
							if len(parameters) == 2:
								guildId, plan = parameters
								trial = False
								if plan == "trial": plan, trial = 1, True

								allGuilds = [e.id for e in client.guilds]
								if int(guildId) not in allGuilds:
									try: await message.channel.send(embed=discord.Embed(title="No servers with this id found", color=constants.colors["gray"]))
									except: pass
									return

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
										try: await message.channel.send(embed=discord.Embed(title="This server already had a trial", color=constants.colors["gray"]))
										except: pass
									try: await message.delete()
									except: pass
									return

								lastTimestamp = fetchedSettings["premium"]["timestamp"]
								timestamp = (lastTimestamp if time.time() < lastTimestamp else time.time()) + 2635200 * int(plan)
								date = datetime.datetime.utcfromtimestamp(timestamp)
								fetchedSettings["premium"] = {"subscribed": True, "hadTrial": hadTrial or trial, "hadWarning": False, "timestamp": timestamp, "date": date.strftime("%m. %d. %Y"), "plan": int(plan)}

								fetchedSettingsRef.set(fetchedSettings, merge=True)
								self.guildProperties[int(guildId)] = copy.deepcopy(fetchedSettings)
								self.subscribedGuilds.append(int(guildId))

								if int(plan) > 0:
									if wasSubscribed:
										try:
											await recepient.send(embed=discord.Embed(title="Alpha Premium subscription for {} server was extended. Current expiry date: {}".format(setGuild.name, fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
										except:
											outgoingAlertsChannel = client.get_channel(595954290409865226)
											try: await outgoingAlertsChannel.send(content="<@!{}>".format(userId), embed=discord.Embed(title="Alpha Premium subscription for {} server was extended. Current expiry date: {}".format(setGuild.name, fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
											except: pass
									else:
										try:
											await recepient.send(embed=discord.Embed(title="Enjoy Alpha Premium subscription for {} server. Current expiry date: {}".format(setGuild.name, fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
										except:
											outgoingAlertsChannel = client.get_channel(595954290409865226)
											try: await outgoingAlertsChannel.send(content="<@!{}>".format(userId), embed=discord.Embed(title="Enjoy your Alpha Premium subscription. Current expiry date: {}".format(fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
											except: pass
								try: await message.delete()
								except: pass
						return
					elif command == "restart":
						self.isBotReady = False
						channel = client.get_channel(560884869899485233)
						try:
							await message.delete()
							alphaMessage = await channel.fetch_message(640502830062632960)
							onlineEmbed = discord.Embed(title=":warning: Alpha: restarting", color=constants.colors["gray"])
							await alphaMessage.edit(embed=onlineEmbed)
						except: pass
						l.log("Status", "A restart has been requested by {} at {}".format(message.author.name, Utils.get_current_date()))
						raise KeyboardInterrupt
					elif command == "reboot":
						self.isBotReady = False
						channel = client.get_channel(560884869899485233)
						try:
							await message.delete()
							alphaMessage = await channel.fetch_message(640502830062632960)
							onlineEmbed = discord.Embed(title=":warning: Alpha: restarting", color=constants.colors["gray"])
							await alphaMessage.edit(embed=onlineEmbed)
						except: pass
						l.log("Status", "A reboot has been requested by {} at {}".format(message.author.name, Utils.get_current_date()))
						if sys.platform == "linux": os.system("sudo reboot")
						return
					elif command in ["performance", "perf"]:
						try: await message.delete()
						except: pass
						queueLen1 = len(self.imageProcessor.screengrabLock[0])
						queueLen2 = len(self.imageProcessor.screengrabLock[1])
						queueLen3 = len(self.imageProcessor.screengrabLock[2])
						try: await message.channel.send(embed=discord.Embed(title="Screengrab queue 1: {}\nScreengrab queue 2: {}\nScreengrab queue 3: {}".format(queueLen1, queueLen2, queueLen3), color=constants.colors["pink"]))
						except: pass
						return
					else:
						await self.fusion.process_private_function(client, message, raw, self.coinParser.exchanges, guildId, self.coinParser.lastBitcoinPrice, db)
						return
			elif not isSelf and isCommand and hasSendPermission and not hasMentions:
				if message.content.lower().startswith(("alpha ", "alpha, ")):
					self.fusion.process_active_user(authorId, "alpha")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw): return

					self.statistics["alpha"] += 1
					rawCaps = " ".join(message.clean_content.split()).split(" ", 1)[1]
					if len(rawCaps) > 500: return
					if (self.guildProperties[guildId]["settings"]["assistant"] if guildId in self.guildProperties else True):
						try: await message.channel.trigger_typing()
						except: pass
					fallThrough, response = self.assistant.process_reply(raw, rawCaps, self.guildProperties[guildId]["settings"]["assistant"] if guildId in self.guildProperties else True)
					if fallThrough:
						if response == "help":
							await self.help(message, authorId, raw, shortcutUsed)
						elif response == "premium":
							try: await message.channel.send(content="Join our server to learn more: https://discord.gg/H9sS6WK" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more.")
							except Exception as e: await self.unknown_error(message, authorId, e)
						elif response == "invite":
							try: await message.channel.send(embed=discord.Embed(title="https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372033", color=constants.colors["pink"]))
							except Exception as e: await self.unknown_error(message, authorId, e)
						elif response == "status":
							req = urllib.request.Request("https://status.discordapp.com", headers={"User-Agent": "Mozilla/5.0"})
							webpage = str(urllib.request.urlopen(req).read())
							isDiscordWorking = "All Systems Operational" in webpage
							try: await message.channel.send(embed=discord.Embed(title=":bellhop: Average ping: {:,.1f} milliseconds\n:satellite: Processing {:,.0f} messages per minute\n:signal_strength: Discord: {}".format(self.fusion.averagePing * 1000, self.fusion.averageMessages, "all systems operational" if isDiscordWorking else "degraded performance"), color=constants.colors["deep purple" if isDiscordWorking else "gray"]))
							except Exception as e: await self.unknown_error(message, authorId, e)
						elif response == "vote":
							try: await message.channel.send(embed=discord.Embed(title="https://top.gg/bot/401328409499664394/vote", color=constants.colors["pink"]))
							except Exception as e: await self.unknown_error(message, authorId, e)
					elif response is not None:
						try: await message.channel.send(content=response)
						except: pass
				elif raw.startswith(("alert ", "alerts ")):
					self.fusion.process_active_user(authorId, "alerts")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw, mute=True, override=True): return

					if raw in ["alert help", "alerts help"]:
						embed = discord.Embed(title=":bell: Alerts", description="Price alerts allow you to make sure you don't miss a single move in the market. Alpha will notify you of a price move right through Discord, so you don't have to move between platforms.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Adding price alerts", value="```alert set <coin> <exchange> <price>```", inline=False)
						embed.add_field(name=":page_with_curl: Listing all your currently set alerts", value="```alert list```Alerts can be deleted by clicking the delete button below each alert.", inline=False)
						embed.add_field(name=":books: Examples", value="● `alert set btc bitmex 90000` sets a price alert for XBTUSD on BitMEX at 90,000 USD\n● `alert set ethbtc 0.025` sets a price alert for ETH on Binance at 0.025 BTC.", inline=False)
						embed.add_field(name=":notepad_spiral: Notes", value="Price alerts are currently only supported on Binance and BitMEX. Support for more exchanges is coming soon.", inline=False)
						embed.set_footer(text="Use \"alert help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					else:
						if isPremium:
							await self.support_message(message, authorId, raw, "alerts")
							slices = re.split(", alert | alert |, alerts | alerts |, ", raw.split(" ", 1)[1])
							if len(slices) > 5:
								await self.hold_up(message, authorId, isPremium)
								return
							for slice in slices:
								await self.alert(message, authorId, slice, useMute)
								self.statistics["alerts"] += 1
						else:
							try: await message.channel.send(content="Price alerts are available for premium members only. {}".format("Join our server to learn more: https://discord.gg/H9sS6WK" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more."))
							except Exception as e: await self.unknown_error(message, authorId, e)
				elif raw.startswith("preset "):
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw, mute=True, override=True): return

					if raw in ["preset help"]:
						embed = discord.Embed(title=":pushpin: Command presets", description="With command presets you're able to make multiple requests at once, save your chart layouts or pull prices of your hodl porfolio.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Adding presets", value="```preset add <name> <command>```A preset can be called by typing its name in the chat.", inline=False)
						embed.add_field(name=":page_with_curl: Listing all your presets", value="```preset list```Presets can be deleted by clicking the delete button below each preset.", inline=False)
						embed.add_field(name=":books: Examples", value="● `preset add btc4h c xbt 4h rsi macd, eth mex 4h rsi macd` creates a preset named `btc4h`. Calling it will produce 4h XBTUSD and ETHUSD BitMEX chart with RSI and MACD indicators.\n● `preset add bitcoin btc, xbt, btc bfx` creates a preset named `bitcoin`. Calling it will produce BTCUSD charts from Binance, BitMEX and Bitfinex respectively.", inline=False)
						embed.add_field(name=":notepad_spiral: Notes", value="Preset names must consist of a single phrase. Only up to 5 requests are allowed per preset.", inline=False)
						embed.set_footer(text="Use \"preset help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					else:
						if isPremium:
							await self.support_message(message, authorId, raw, "preset")
							slices = re.split(", preset | preset", raw.split(" ", 1)[1])
							if len(slices) > 5:
								await self.hold_up(message, authorId, isPremium)
								return
							for slice in slices:
								await self.presets(message, authorId, slice, guildId, useMute)
						else:
							try: await message.channel.send(content="Presets are available for premium members only. {}".format("Join our server to learn more: https://discord.gg/H9sS6WK" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more."))
							except Exception as e: await self.unknown_error(message, authorId, e)
				elif raw.startswith("c "):
					self.fusion.process_active_user(authorId, "c")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw): return

					if raw in ["c help"]:
						embed = discord.Embed(title=":chart_with_upwards_trend: Charts", description="Pull TradingView charts effortlessly with only a few keystrokes right in Discord.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```c <coin> <exchange> <timeframe(s)> <candle type> <indicators>```", inline=False)
						embed.add_field(name=":books: Examples", value="● `c btc` produces 1h BTCUSDT chart from Binance.\n● `c xbt log` produces 1h XBTUSD log chart from BitMEX.\n● `c ada 1-1h rsi srsi` produces 1m, 3m, 5m, 15m, 30m and 1h ADABTC charts from Binance with RSI, and Stoch RSI indicators.\n● `c $bnb 15m-1h bb mfi` produces 15m, 30m, & 1h BNBUSDT charts from Binance with Bollinger Bands, and MFI indicators.\n● `c etcusd btrx 1w-4h` 1w, 1d and 4h ETCUSD charts from Bittrex.", inline=False)
						embed.add_field(name=":notepad_spiral: Notes", value="Type `c parameters` for the complete indicator & timeframes list.", inline=False)
						embed.set_footer(text="Use \"c help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					elif raw in ["c parameters"]:
						availableIndicators = [
							"NV *(no volume)*", "ACCD *(Accumulation/Distribution)*", "ADR", "Aroon", "ATR", "Awesome *(Awesome Oscillator)*", "BB", "BBW", "CMF", "Chaikin *(Chaikin Oscillator)*", "Chande *(Chande Momentum Oscillator)*", "CI *(Choppiness Index)*", "CCI", "CRSI", "CC *(Correlation Coefficient)*", "DPO", "DM", "DONCH *(Donchian Channels)*", "DEMA", "EOM", "EFI", "EW *(Elliott Wave)*", "ENV *(Envelope)*", "Fisher *(Fisher Transform)*", "HV *(Historical Volatility)*", "HMA", "Ichimoku", "Keltner *(Keltner Channels)*", "KST", "LR *(Linear Regression)*", "MACD", "MOM", "MFI", "Moon *(Moon Phases)*", "MA", "EMA", "WMA", "OBV", "PSAR", "PPHL *(Pivot Points High Low)*", "PPS *(Pivot Points Standard)*", "PO *(Price Oscillator)*", "PVT", "ROC", "RSI", "RVI *(Relative Vigor Index)*", "VI (volatility index)", "SMIEI *(SMI Ergodic Indicator)*", "SMIEO *(SMI Ergodic Oscillator)*", "Stoch", "SRSI *(Stochastic RSI)*", "TEMA *(Triple EMA)*", "TRIX", "Ultimate *(Ultimate Oscillator)*", "VSTOP *(Volatility Stop)*", "VWAP", "VWMA", "WilliamsR", "WilliamsA *(Williams Alligator)*", "WF *(Williams Fractal)*", "ZZ *(Zig Zag)*"
						]
						embed = discord.Embed(title=":chains: Chart parameters", description="All available chart parameters you can use", color=constants.colors["light blue"])
						embed.add_field(name=":bar_chart: Indicators", value="{}".format(", ".join(availableIndicators)), inline=False)
						embed.add_field(name=":control_knobs: Timeframes", value="1/3/5/15/30-minute, 1/2/3/4-hour, daily, weekly and monthly", inline=False)
						embed.add_field(name=":scales: Exchanges", value=", ".join([(self.coinParser.exchanges[e].name if e in self.coinParser.exchanges else e.title()) for e in constants.supportedExchanges["charts"]]), inline=False)
						embed.add_field(name=":chart_with_downwards_trend: Candle types", value="Bars, Candles, Heikin Ashi, Line Break, Line, Area, Renko, Kagi, Point&Figure", inline=False)
						embed.add_field(name=":gear: Other parameters", value="Shorts, Longs, Log, White, Link", inline=False)
						embed.set_footer(text="Use \"c parameters\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					else:
						await self.support_message(message, authorId, raw, "c")
						slices = re.split(", c | c |, ", raw.split(" ", 1)[1])
						totalWeight = len(slices)
						if totalWeight > 5:
							await self.hold_up(message, authorId, isPremium)
							return
						for slice in slices:
							if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += 2
							else: self.rateLimited["u"][authorId] = 2

							if self.rateLimited["u"][authorId] >= limit:
								try: await message.channel.send(content="<@!{}>".format(authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
								self.rateLimited["u"][authorId] = limit
								totalWeight = limit
								break
							else:
								if slice in ["greed index", "gi", "fear index", "fi", "fear greed index", "fgi", "greed fear index", "gfi"]:
									chartMessages, weight = await self.fear_greed_index(message, authorId, slice, useMute)
								elif slice in ["nvtr", "nvt", "nvt ratio"]:
									chartMessages, weight = await self.woobull_chart(message, authorId, slice, "NVT", useMute)
								elif slice in ["drbns", "drbn", "rbns", "rbn", "difficulty ribbon"]:
									chartMessages, weight = await self.woobull_chart(message, authorId, slice, "Difficulty Ribbons", useMute)
								elif slice.startswith("tv "):
									chartMessages, weight = await self.tradingview_chart(message, authorId, slice[3:], useMute)
								elif slice.startswith("fv "):
									chartMessages, weight = await self.finviz_chart(message, authorId, slice[3: ], useMute)
								else:
									chartMessages, weight = await self.tradingview_chart(message, authorId, slice, useMute, canForward=False) # True

								sentMessages += chartMessages
								totalWeight += weight - 1

								if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += weight - 2
								else: self.rateLimited["u"][authorId] = weight - 2

						self.statistics["c"] += totalWeight
						await self.finish_request(message, authorId, raw, totalWeight, sentMessages)
				elif raw.startswith("p "):
					self.fusion.process_active_user(authorId, "p")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw): return

					if raw in ["p help"]:
						embed = discord.Embed(title=":money_with_wings: Prices", description="Market prices fro thousands of crypto tickers are available with a single command through Alpha on Discord.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```p <coin> <exchange>```", inline=False)
						embed.add_field(name=":books: Examples", value="● `p btc` will request BTCUSD price from CoinGecko.\n● `p ada bin` will request ADABTC price on Binance.\n● `p $bnb` will request BNBUSD price from CoinGecko.\n● `p etcusd btrx` will request ETCUSD price on Bittrex.", inline=False)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					elif raw not in ["p "]:
						await self.support_message(message, authorId, raw, "p")
						slices = re.split(", p | p |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, authorId, isPremium)
							return
						for slice in slices:
							if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += 1
							else: self.rateLimited["u"][authorId] = 1

							if self.rateLimited["u"][authorId] >= limit:
								try: await message.channel.send(content="<@!{}>".format(authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
								self.rateLimited["u"][authorId] = limit
								totalWeight = limit
								break
							else:
								await self.price(message, authorId, slice, isPremium, useMute)

						self.statistics["p"] += len(slices)
						await self.finish_request(message, authorId, raw, len(slices), [])
				elif raw.startswith("v "):
					self.fusion.process_active_user(authorId, "v")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw): return

					if raw in ["v help"]:
						embed = discord.Embed(title=":credit_card: 24-hour rolling volume", description="Rolling 24-hour volume can be requested for virtually any crypto ticker.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```v <coin> <exchange>```", inline=False)
						embed.add_field(name=":books: Examples", value="● `v btc` will request BTCUSD volume from CoinGecko.\n● `v ada bin` will request ADABTC volume on Binance.\n● `v $bnb` will request BNBUSD volume from CoinGecko.\n● `v etcusd btrx` will request ETCUSD volume on Bittrex.", inline=False)
						embed.set_footer(text="Use \"v help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					elif raw not in ["v "]:
						await self.support_message(message, authorId, raw, "v")
						slices = re.split(", v | v |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, authorId, isPremium)
							return
						for slice in slices:
							if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += 1
							else: self.rateLimited["u"][authorId] = 1

							if self.rateLimited["u"][authorId] >= limit:
								try: await message.channel.send(content="<@!{}>".format(authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
								self.rateLimited["u"][authorId] = limit
								totalWeight = limit
								break
							else:
								await self.volume(message, authorId, slice, useMute)

						self.statistics["v"] += len(slices)
						await self.finish_request(message, authorId, raw, len(slices), [])
				elif raw.startswith("d "):
					self.fusion.process_active_user(authorId, "d")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw): return

					if raw in ["d help"]:
						embed = discord.Embed(title=":book: Orderbook graphs", description="Orderbook snapshot graphs are available right through Alpha, saving you time having to log onto an exchange.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```d <coin> <exchange>```", inline=False)
						embed.add_field(name=":books: Examples", value="● `d btc` will request BTCUSDT orderbook snapshot on Binance.\n● `d ada` will request ADABTC orderbook snapshot on Binance.\n● `d $bnb` will request BNBUSDT orderbook snapshot on Binance.\n● `d etcusd btrx` will request ETCUSD orderbook snapshot from Bittrex.", inline=False)
						embed.set_footer(text="Use \"d help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					elif raw not in ["d "]:
						await self.support_message(message, authorId, raw, "d")
						slices = re.split(", d | d |, ", raw.split(" ", 1)[1])
						totalWeight = len(slices)
						if len(slices) > 5:
							await self.hold_up(message, authorId, isPremium)
							return
						for slice in slices:
							if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += 2
							else: self.rateLimited["u"][authorId] = 2

							if self.rateLimited["u"][authorId] >= limit:
								try: await message.channel.send(content="<@!{}>".format(authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
								self.rateLimited["u"][authorId] = limit
								totalWeight = limit
								break
							else:
								chartMessages, weight = await self.depth(message, authorId, slice, useMute)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += weight - 2
								else: self.rateLimited["u"][authorId] = weight - 2

						self.statistics["d"] += totalWeight
						await self.finish_request(message, authorId, raw, totalWeight, sentMessages)
				elif raw.startswith("hmap "):
					self.fusion.process_active_user(authorId, "hmap")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw): return

					if raw in ["hmap help"]:
						embed = discord.Embed(title=":fire: Heat maps", description="Pull various Bitgur heat maps right through Alpha on Discord. Market state information has never been so easily accessible before.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```hmap <type> <filters> <period>```", inline=False)
						embed.add_field(name=":books: Examples", value="● `hmap price` produces a cryptocurrency market heat map.\n● `hmap price tokens year` produces a cryptocurrency market heat map for tokens in the last year only.\n● `hmap exchanges` produces an exchange heat map.\n● `hmap category ai` produces a heat map of coins in the Data Storage/Analytics & AI category.\n● `hmap vol top10` produces a heat map showing top 10 coins by marketcap and their respected volatility.\n● `hmap unusual` produces a heat map showing coins, of which the trading volume has grown the most in last day.", inline=False)
						embed.add_field(name=":notepad_spiral: Notes", value="Type `hmap parameters` for the complete filter & timeframes list.", inline=False)
						embed.set_footer(text="Use \"hmap help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					elif raw in ["hmap parameters"]:
						availableCategories = [
							"crypto (Cryptocurrency)", "blockchain (Blockchain Platforms)", "commerce (Commerce & Advertising)", "commodities (Commodities)", "content (Content Management)", "ai (Data Storage/Analytics & AI)", "healthcare (Drugs & Healthcare)", "energy (Energy & Utilities)", "events (Events & Entertainment)", "financial (Financial Services)", "gambling (Gambling & Betting)", "gaming (Gaming & VR)", "identy (Identy & Reputation)", "legal (Legal)", "estate (Real Estate)", "social (Social Network)", "software (Software)", "logistics (Supply & Logistics)", "trading (Trading & Investing)",
						]
						embed = discord.Embed(title=":chains: Heat map parameters", description="All available heat map parameters you can use", color=constants.colors["light blue"])
						embed.add_field(name=":control_knobs: Timeframes", value="15-minute, 1-hour, daily, weekly, 1/3/6-month and 1-year", inline=False)
						embed.add_field(name=":scales: Filters", value="top10, top100, tokens, coins, gainers, loosers", inline=False)
						embed.add_field(name=":bar_chart: Categories", value="{}".format(", ".join(availableCategories)), inline=False)
						embed.set_footer(text="Use \"hmap parameters\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					else:
						await self.support_message(message, authorId, raw, "hmap")
						slices = re.split(", hmap | hmap |, ", raw)
						totalWeight = len(slices)
						if totalWeight > 5:
							await self.hold_up(message, authorId, isPremium)
							return
						for s in slices:
							slice = s if s.startswith("hmap") else "hmap " + s
							if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += 2
							else: self.rateLimited["u"][authorId] = 2

							if self.rateLimited["u"][authorId] >= limit:
								try: await message.channel.send(content="<@!{}>".format(authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
								self.rateLimited["u"][authorId] = limit
								totalWeight = limit
								break
							else:
								chartMessages, weight = await self.heatmap(message, authorId, slice, useMute)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += 2 - weight
								else: self.rateLimited["u"][authorId] = 2 - weight

						self.statistics["hmap"] += totalWeight
						await self.finish_request(message, authorId, raw, totalWeight, sentMessages)
				elif raw.startswith(("mcap ", "mc ")):
					self.fusion.process_active_user(authorId, "mcap")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw): return

					if raw in ["mcap help", "mc help"]:
						embed = discord.Embed(title=":tools: Coin details", description="All coin information from CoinGecko you can ask for accessible through a single command.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```mcap/mc <coin>```", inline=False)
						embed.add_field(name=":books: Examples", value="● `mc btc` will pull information about Bitcoin from CoinGecko.\n● `mc ada` will pull information about Cardano from CoinGecko.", inline=False)
						embed.set_footer(text="Use \"mcap help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					else:
						await self.support_message(message, authorId, raw, "mcap")
						slices = re.split(", mcap | mcap |, mc | mc |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, authorId, isPremium)
							return
						for slice in slices:
							if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += 1
							else: self.rateLimited["u"][authorId] = 1

							if self.rateLimited["u"][authorId] >= limit:
								try: await message.channel.send(content="<@!{}>".format(authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
								self.rateLimited["u"][authorId] = limit
								totalWeight = limit
								break
							else:
								await self.mcap(message, authorId, slice, useMute)

						self.statistics["mcap"] += len(slices)
						await self.finish_request(message, authorId, raw, len(slices), [])
				elif raw.startswith("mk "):
					self.fusion.process_active_user(authorId, "mk")
					if message.author.bot:
						if not await self.bot_verification(message, authorId, raw): return

					if raw in ["mk help"]:
						embed = discord.Embed(title=":page_facing_up: Market listings", description="A command for pulling a list of exchanges listing a particular market ticker.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```mk <coin>```", inline=False)
						embed.add_field(name=":books: Examples", value="● `mk btc` will list all exchanges listing BTC/USD market pair.\n● `mk ada` will list all exchanges listing ADA/BTC market pair.", inline=False)
						embed.set_footer(text="Use \"mk help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
					else:
						slices = re.split(", mk | mk |, ", raw.split(" ", 1)[1])
						if len(slices) > 5:
							await self.hold_up(message, authorId, isPremium)
							return
						for slice in slices:
							if authorId in self.rateLimited["u"]: self.rateLimited["u"][authorId] += 1
							else: self.rateLimited["u"][authorId] = 1

							if self.rateLimited["u"][authorId] >= limit:
								try: await message.channel.send(content="<@!{}>".format(authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, authorId, e)
								self.rateLimited["u"][authorId] = limit
								totalWeight = limit
								break
							else:
								await self.support_message(message, authorId, raw, "mk")
								await self.markets(message, authorId, slice, useMute)

						self.statistics["mk"] += len(slices)
						await self.finish_request(message, authorId, raw, len(slices), [])
			else:
				self.fusion.calculate_average_ping(message, authorId, client.cached_messages)
				if await self.fusion.invite_warning(message, authorId, raw, guildId): return
				if (self.guildProperties[guildId]["settings"]["assistant"] if guildId in self.guildProperties else True):
					response = self.assistant.funnyReplies(raw)
					if response is not None:
						self.statistics["alpha"] += 1
						try: message.channel.send(content=response)
						except: pass
				if not any(keyword in raw for keyword in constants.mutedMentionWords) and not message.author.bot and any(e in re.findall(r"[\w']+", raw) for e in constants.mentionWords) and guildId not in [414498292655980583, -1]:
					mentionMessage = "{}/{}: {}".format(message.guild.name, message.channel.name, message.clean_content)
					threading.Thread(target=self.fusion.webhook_send, args=("https://discordapp.com/api/webhooks/626866735567470626/QkGttlP9zowSyuKZn6SWb_RVejWbpjDk9yjTPYRxkT7ASg7KPCyZpD5GhaBidJQGau43", mentionMessage, "{}#{}".format(message.author.name, message.author.discriminator), message.author.avatar_url, False, message.attachments, message.embeds)).start()
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
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

	async def unknown_error(self, message, authorId, e, report=False):
		embed = discord.Embed(title="Looks like something went wrong.{}".format(" The issue was reported." if report else ""), color=constants.colors["gray"])
		embed.set_author(name="Something went wrong", icon_url=firebase_storage.icon_bw)
		try: await message.channel.send(embed=embed)
		except: pass

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
				elif reaction.emoji == '❌' and reaction.message.embeds[0]:
					titleText = reaction.message.embeds[0].title
					footerText = reaction.message.embeds[0].footer.text
					if " ● (id: " in footerText:
						alertId = footerText.split(" ● (id: ")[1][:-1]

						for id in constants.supportedExchanges["alerts"]:
							userAlerts = db.collection(u"alpha/alerts/{}".format(id)).stream()
							if userAlerts is not None:
								for alertAuthor in userAlerts:
									if int(alertAuthor.id) == user.id:
										deletedAlerts = []
										fetchedAlerts = alertAuthor.to_dict()
										for s in fetchedAlerts:
											for alert in fetchedAlerts[s]:
												if alert["id"] == alertId:
													deletedAlerts.append(alert)
													break

											if len(deletedAlerts) > 0:
												for alert in deletedAlerts:
													fetchedAlerts[s].remove(alert)
												alertsRef = db.document(u"alpha/alerts/{}/{}".format(id, user.id))
												try:
													batch = db.batch()
													batch.set(alertsRef, fetchedAlerts, merge=True)
													for i in range(1, self.fusion.numInstances + 1):
														batch.set(db.document(u'fusion/instance-{}'.format(i)), {"needsUpdate": True}, merge=True)
													batch.commit()
												except:
													await self.unknown_error(message, authorId, e)

												embed = discord.Embed(title="Alert deleted", color=constants.colors["gray"])
												embed.set_footer(text=footerText)
												try: await reaction.message.edit(embed=embed)
												except: pass
					elif " → `" in titleText and titleText.endswith("`"):
						presetName = titleText.split("`")[1]
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

						embed = discord.Embed(title="Preset deleted", color=constants.colors["gray"])
						embed.set_footer(text=footerText)
						try: await reaction.message.edit(embed=embed)
						except: pass

	async def finish_request(self, message, authorId, raw, weight, sentMessages):
		await asyncio.sleep(60)
		if authorId in self.rateLimited["u"]:
			self.rateLimited["u"][authorId] -= weight
			if self.rateLimited["u"][authorId] < 1: self.rateLimited["u"].pop(authorId, None)

		autodeleteEnabled = False
		if message.guild is not None:
			if message.guild.id in self.guildProperties:
				autodeleteEnabled = self.guildProperties[message.guild.id]["settings"]["autodelete"]

		if autodeleteEnabled:
			try: await message.delete()
			except: pass

		for chartMessage in sentMessages:
			try:
				if autodeleteEnabled: await chartMessage.delete()
				else: await chartMessage.remove_reaction("☑", message.channel.guild.me)
			except: pass

	def clear_rate_limit_cache(self, exchange, tickerId, commands, waitTime):
		time.sleep(waitTime)
		for command in commands:
			try: self.rateLimited[command][exchange].pop(tickerId, None)
			except: pass

	async def bot_verification(self, message, authorId, raw, mute=False, override=False):
		if override: return False
		if message.webhook_id is not None:
			if message.webhook_id not in constants.verifiedWebhooks:
				if not mute and message.guild.id != 414498292655980583:
					embed = discord.Embed(title="{} webhook is not verified with Alpha. To get it verified, please join Alpha server: https://discord.gg/GQeDE85".format(message.author.name), color=constants.colors["pink"])
					embed.set_author(name="Unverified webhook", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except: pass
				return False
		else:
			if message.author.id not in constants.verifiedBots:
				if not mute and message.guild.id != 414498292655980583:
					embed = discord.Embed(title="{}#{} bot is not verified with Alpha. To get it verified, please join Alpha server: https://discord.gg/GQeDE85".format(message.author.name, message.author.discriminator), color=constants.colors["pink"])
					embed.set_author(name="Unverified bot", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except: pass
				return False
		history.info("{} ({}): {}".format(Utils.get_current_date(), authorId, raw))
		print(authorId)
		return True

	async def help(self, message, authorId, raw, shortcutUsed):
		embed = discord.Embed(title=":wave: Introduction", description="Alpha bot is the world's most popular Discord bot for requesting charts, set price alerts, and more. Using Alpha is as simple as typing a short command into any Discord channel Alpha has access to.", color=constants.colors["light blue"])
		embed.add_field(name=":chart_with_upwards_trend: Charts", value="Easy access to TradingView charts.\nType `c help` to learn more.", inline=False)
		embed.add_field(name=":bell: Alerts", value="Setup price alerts for select crypto exchanges.\nType `alert help` to learn more.", inline=False)
		embed.add_field(name=":money_with_wings: Prices", value="Current cryptocurrency prices for thousands of tickers.\nType `p help` to learn more.", inline=False)
		embed.add_field(name=":book: Orderbook graphs", value="Orderbook snapshot charts of crypto market pairs.\nType `d help` to learn more.", inline=False)
		embed.add_field(name=":credit_card: 24-hour rolling volume", value="Request 24-hour rolling volume for virtually any crypto market pair.\nType `v help` to learn more.", inline=False)
		embed.add_field(name=":tools: Coin details", value="Detailed coin information from CoinGecko.\nType `mcap help` to learn more.", inline=False)
		embed.add_field(name=":fire: Heat maps", value="Check various heat maps from Bitgur.\nType `hmap help` to learn more.", inline=False)
		embed.add_field(name=":pushpin: Command presets", value="Create personal presets for easy access to things you use most.\nType `preset help` to learn more.", inline=False)
		embed.add_field(name=":crystal_ball: Assistant", value="Pull up Wikipedia articles, calculate math problems and get answers to many other question. Start a message with `alpha` and continue with your question.", inline=False)
		embed.set_footer(text="Use \"a help\" to pull up this list again.")
		try:
			if shortcutUsed:
				try: await message.author.send(embed=embed)
				except: await message.channel.send(embed=embed)
			else:
				await message.channel.send(embed=embed)
		except Exception as e: await self.unknown_error(message, authorId, e)

	async def support_message(self, message, authorId, raw, command):
		if random.randint(0, 50) == 1:
			c = command
			while c == command: c, textSet = random.choice(list(constants.supportMessages.items()))
			embed = discord.Embed(title=random.choice(textSet), color=constants.colors["light blue"])
			try: await message.channel.send(embed=embed)
			except: pass

	async def hold_up(self, message, authorId, isPremium):
		embed = discord.Embed(title="Only up to 5 requests are allowrd per command", color=constants.colors["gray"])
		embed.set_author(name="Too many requests", icon_url=firebase_storage.icon_bw)
		try: await message.channel.send(embed=embed)
		except Exception as e: await self.unknown_error(message, authorId, e)

	async def alert(self, message, authorId, raw, mute=False):
		try:
			sentMessages = []
			arguments = raw.split(" ")
			method = arguments[0]

			if method in ["set", "create", "add"]:
				if len(arguments) >= 3:
					tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker(arguments[1].upper(), "alerts")
					if isAggregatedSymbol:
						if not mute:
							embed = discord.Embed(title="Aggregated tickers aren't supported with the `alert` command", color=constants.colors["gray"])
							embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, authorId, e)
						return

					outputMessage, tickerId, arguments = self.alerts.process_alert_arguments(arguments, tickerId, exchange)
					if outputMessage is not None:
						if not mute:
							embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, authorId, e)
						return
					exchange, action, level, repeat = arguments

					outputMessage, details = self.coinParser.find_market_pair(tickerId, exchange, "alerts")
					if outputMessage is not None:
						if not mute:
							embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
							embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, authorId, e)
						return

					symbol, base, quote, marketPair, exchange = details

					try: await message.channel.trigger_typing()
					except: pass

					alertsRef = db.document(u"alpha/alerts/{}/{}".format(exchange, authorId))
					fetchedAlerts = alertsRef.get().to_dict()
					if fetchedAlerts is None: fetchedAlerts = {}

					sum = 0
					for key in fetchedAlerts: sum += len(fetchedAlerts[key])

					if sum >= 10:
						embed = discord.Embed(title="Only 10 alerts per exchange are allowed", color=constants.colors["gray"])
						embed.set_author(name="Maximum number of alerts reached", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
						return

					key = symbol.replace("/", "-")
					newAlert = {
						"id": "%013x" % random.randrange(10**15),
						"timestamp": time.time(),
						"time": Utils.get_current_date(),
						"channel": authorId,
						"action": action,
						"level": level,
						"repeat": repeat
					}
					levelText = Utils.format_price(self.coinParser.exchanges[exchange], symbol, level)

					if key not in fetchedAlerts: fetchedAlerts[key] = []
					for alert in fetchedAlerts[key]:
						if alert["action"] == action and alert["level"] == level:
							embed = discord.Embed(title="{} alert for {} ({}) at {} {} already exists".format(action.title(), base, self.coinParser.exchanges[exchange].name, levelText, quote), color=constants.colors["gray"])
							embed.set_author(name="Alert already exists", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, authorId, e)
							return

					fetchedAlerts[key].append(newAlert)

					try:
						batch = db.batch()
						batch.set(alertsRef, fetchedAlerts, merge=True)
						for i in range(1, self.fusion.numInstances + 1):
							batch.set(db.document(u'fusion/instance-{}'.format(i)), {"needsUpdate": True}, merge=True)
						batch.commit()
					except:
						await self.unknown_error(message, authorId, e)
						return

					embed = discord.Embed(title="{} alert set for {} ({}) at {} {}".format(action.title(), base, self.coinParser.exchanges[exchange].name, levelText, quote), color=constants.colors["deep purple"])
					embed.set_author(name="Alert successfully set", icon_url=firebase_storage.icon)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, authorId, e)

					threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
				else:
					embed = discord.Embed(title="Invalid command usage. Type `alert help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
			elif method in ["list", "all"]:
				if len(arguments) == 1:
					try: await message.channel.trigger_typing()
					except: pass

					alertsList = {}
					numberOfAlerts = 0
					for exchange in constants.supportedExchanges["alerts"]:
						userAlerts = db.collection(u"alpha/alerts/{}".format(exchange)).stream()
						if userAlerts is not None:
							for user in userAlerts:
								if int(user.id) == authorId:
									fetchedAlerts = user.to_dict()
									hasAlerts = False
									for s in fetchedAlerts:
										numberOfAlerts += len(fetchedAlerts[s])
										if exchange not in alertsList: alertsList[exchange] = {}
										if len(fetchedAlerts[s]) and s.replace("-", "/") not in alertsList: alertsList[exchange][s.replace("-", "/")] = fetchedAlerts[s]
									break

					if numberOfAlerts != 0:
						count = 0
						for exchange in sorted(alertsList.keys()):
							for symbol in sorted(alertsList[exchange].keys()):
								orderedAlertsList = {}
								for alert in alertsList[exchange][symbol]:
									orderedAlertsList[alert["level"]] = alert
								for i in sorted(orderedAlertsList.keys()):
									alert = orderedAlertsList[i]
									count += 1
									base = self.coinParser.exchanges[exchange].markets[symbol]["base"]
									quote = self.coinParser.exchanges[exchange].markets[symbol]["quote"]
									marketPair = self.coinParser.exchanges[exchange].markets[symbol]["id"].replace("_", "").replace("/", "").replace("-", "").upper()
									levelText = Utils.format_price(self.coinParser.exchanges[exchange], symbol, alert["level"])

									embed = discord.Embed(title="{} alert set for {} ({}) at {} {}".format(alert["action"].title(), marketPair, self.coinParser.exchanges[exchange].name, levelText, quote), color=constants.colors["deep purple"])
									embed.set_footer(text="Alert {}/{} ● (id: {})".format(count, numberOfAlerts, alert["id"]))
									try:
										alertMessage = await message.channel.send(embed=embed)
										await alertMessage.add_reaction('❌')
									except: pass
					else:
						embed = discord.Embed(title="You don't have any alerts set", color=constants.colors["gray"])
						embed.set_author(name="No alerts", icon_url=firebase_storage.icon)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
				else:
					embed = discord.Embed(title="Invalid command usage. Type `alert help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def presets(self, message, authorId, raw, guildId, mute=False):
		try:
			isServer = raw.startswith("server ") and message.author.guild_permissions.administrator
			offset = 1 if isServer else 0
			arguments = raw.split(" ", 2 + offset)
			method = arguments[0 + offset]

			if method in ["set", "create", "add"]:
				if len(arguments) == 3 + offset:
					try: await message.channel.trigger_typing()
					except: pass

					fetchedSettingsRef = db.document(u"alpha/settings/{}/{}".format("servers" if isServer else "users", guildId if isServer else authorId))
					fetchedSettings = fetchedSettingsRef.get().to_dict()
					fetchedSettings = Utils.createServerSetting(fetchedSettings) if isServer else Utils.createUserSettings(fetchedSettings)
					fetchedSettings, status = Presets.updatePresets(fetchedSettings, add=arguments[1 + offset].replace("`", ""), shortcut=arguments[2 + offset])
					statusTitle, statusMessage, statusColor = status
					fetchedSettingsRef.set(fetchedSettings, merge=True)
					if isServer: self.guildProperties[guildId] = copy.deepcopy(fetchedSettings)
					else: self.userProperties[authorId] = copy.deepcopy(fetchedSettings)

					embed = discord.Embed(title=statusMessage, color=constants.colors[statusColor])
					embed.set_author(name=statusTitle, icon_url=firebase_storage.icon)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
			elif method in ["list", "all"]:
				if len(arguments) == 1 + offset:
					try: await message.channel.trigger_typing()
					except: pass

					hasSettings = guildId in self.guildProperties if isServer else authorId in self.userProperties
					settings = {} if not hasSettings else (self.guildProperties[guildId] if isServer else self.userProperties[authorId])
					settings = Utils.createServerSetting(settings) if isServer else Utils.createUserSettings(settings)

					if len(settings["presets"]) > 0:
						allPresets = {}
						numberOfPresets = len(settings["presets"])
						for preset in settings["presets"]:
							allPresets[preset["phrase"]] = preset["shortcut"]

						for i, phrase in enumerate(sorted(allPresets.keys())):
							embed = discord.Embed(title="`{}`{} → `{}`".format(phrase, " (server-wide)" if isServer else "", allPresets[phrase]), color=constants.colors["deep purple"])
							embed.set_footer(text="Preset {}/{}".format(i + 1, numberOfPresets))
							try:
								presetMessage = await message.channel.send(embed=embed)
								await presetMessage.add_reaction('❌')
							except: pass
					else:
						embed = discord.Embed(title="You don't have any presets set", color=constants.colors["gray"])
						embed.set_author(name="No presets", icon_url=firebase_storage.icon)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
			elif len(arguments) <= 3 + offset:
				embed = discord.Embed(title="`{}` is not a valid argument. Type `preset help` to learn more.".format(method), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
				try: await message.channel.send(embed=embed)
				except Exception as e: await self.unknown_error(message, authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def tradingview_chart(self, message, authorId, raw, mute=False, canForward=False):
		try:
			sentMessages = []
			arguments = raw.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker(arguments[0].upper(), "charts", useCryptoParser=False)
			outputMessage, tickerId, arguments = self.imageProcessor.process_tradingview_arguments(arguments, tickerId, exchange, tickerParts)
			if outputMessage is not None:
				if not mute:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, authorId, e)
				if arguments is None:
					return ([], 0)
			timeframes, exchange, sendLink, isLog, barStyle, hideVolume, theme, indicators, isWide = arguments

			try: await message.channel.trigger_typing()
			except: pass

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
					embed = discord.Embed(title="One moment...", color=constants.colors["gray"])
					try:  waitMessage = await message.channel.send(embed=embed)
					except: pass
					await asyncio.sleep(0.5)

				try: await message.channel.trigger_typing()
				except: pass

				messageUrl = "https://www.tradingview.com/widgetembed/?symbol={}{}&hidesidetoolbar=0&symboledit=1&saveimage=1&withdateranges=1&enablepublishing=true&interval={}&theme={}&style={}&studies={}".format(urllib.parse.quote(exchange, safe=""), urllib.parse.quote(tickerId, safe=""), timeframe, theme, barStyle, "%1F".join(indicators))
				chartName = await self.imageProcessor.request_tradingview_chart(authorId, driverInstance, tickerId, timeframe, exchange, isLog, barStyle, hideVolume, theme, indicators, isWide)

				if waitMessage is not None:
					try: await waitMessage.delete()
					except: pass
				if chartName is None:
					try:
						if isCryptoTicker or not canForward:
							embed = discord.Embed(title="Requested chart for `{}` is not available".format(tickerId), color=constants.colors["gray"])
							embed.set_author(name="Chart not available", icon_url=firebase_storage.icon_bw)
							chartMessage = await message.channel.send(embed=embed)
							sentMessages.append(chartMessage)
							try: await chartMessage.add_reaction("☑")
							except: pass
						else:
							return await self.finviz_chart(message, authorId, raw, mute=True, isForwarded=True)
					except Exception as e: await self.unknown_error(message, authorId, e)
					return (sentMessages, len(sentMessages))

				try:
					embed = discord.Embed(title="{}".format(messageUrl), color=constants.colors["deep purple"])
					chartMessage = await message.channel.send(embed=embed if sendLink else None, file=discord.File("charts/" + chartName, chartName))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				except Exception as e: await self.unknown_error(message, authorId, e)

				self.imageProcessor.clean_cache()

			threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			self.rateLimited["c"] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def fear_greed_index(self, message, authorId, raw, mute=False):
		try:
			try: await message.channel.trigger_typing()
			except: pass

			sentMessages = []

			chartFile = self.alternativemeConnection.fear_greed_index_c(authorId)
			try:
				chartMessage = await message.channel.send(file=chartFile)
				sentMessages.append(chartMessage)
				await chartMessage.add_reaction("☑")
			except Exception as e: await self.unknown_error(message, authorId, e)

			threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
			return (sentMessages, len(sentMessages))
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			self.rateLimited["c"] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def woobull_chart(self, message, authorId, raw, chartType, mute=False):
		try:
			try: await message.channel.trigger_typing()
			except: pass

			sentMessages = []

			queueLength = [len(self.imageProcessor.screengrabLock[e]) for e in self.imageProcessor.screengrabLock]
			driverInstance = queueLength.index(min(queueLength))

			waitMessage = None
			if len(self.imageProcessor.screengrabLock[driverInstance]) > 2:
				embed = discord.Embed(title="One moment...", color=constants.colors["gray"])
				try:  waitMessage = await message.channel.send(embed=embed)
				except: pass
				await asyncio.sleep(0.5)

			chartName = await self.imageProcessor.request_woobull_chart(authorId, driverInstance, chartType)

			if waitMessage is not None:
				try: await waitMessage.delete()
				except: pass
			if chartName is None:
				try:
					embed = discord.Embed(title="Requested chart for `{}` is not available".format(chartType), color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=firebase_storage.icon_bw)
					chartMessage = await message.channel.send(embed=embed)
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				except Exception as e: await self.unknown_error(message, authorId, e)
				return (sentMessages, len(sentMessages))

			try:
				chartMessage = await message.channel.send(file=discord.File("charts/" + chartName, chartName))
				sentMessages.append(chartMessage)
				try: await chartMessage.add_reaction("☑")
				except: pass
			except Exception as e: await self.unknown_error(message, authorId, e)

			self.imageProcessor.clean_cache()

			threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def price(self, message, authorId, raw, isPremium, mute=False):
		try:
			sentMessages = []
			arguments = raw.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker(arguments[0].upper(), "ohlcv", exchangeFallthrough=True)
			if isAggregatedSymbol:
				if not mute:
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `p` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
				return

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, hasActions=True, command="p")
			if outputMessage is not None:
				if not mute:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
				return
			action, exchange = arguments
			exchangeFallthrough = (tickerId.endswith(("USD", "BTC")) and exchange == "")

			outputMessage, details = self.coinParser.find_market_pair(tickerId, exchange, "ohlcv", exchangeFallthrough=exchangeFallthrough)
			availableOnCoinGecko = (tickerId.lower() if details is None else details[1].lower()) in self.coinGeckoConnection.coinGeckoIndex
			useFallback = (outputMessage == "Ticker `{}` was not found".format(tickerId) or exchangeFallthrough) and availableOnCoinGecko
			if outputMessage is not None and not useFallback:
				if not mute:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
				return
			symbol, base, quote, marketPair, exchange = (tickerId, tickerId, "BTC", "{}BTC".format(tickerId), "CoinGecko") if useFallback and details is None else details
			if useFallback: exchange = "CoinGecko"
			coinThumbnail = firebase_storage.icon_bw

			try: await message.channel.trigger_typing()
			except: pass

			if action == "funding":
				try: sentMessages.append(await message.channel.send(embed=self.exchangeConnection.funding(self.coinParser.exchanges[exchange], marketPair, tickerId)))
				except Exception as e: await self.unknown_error(message, authorId, e)
			elif action == "oi":
				try: sentMessages.append(await message.channel.send(embed=self.exchangeConnection.open_interest(self.coinParser.exchanges[exchange], marketPair, tickerId, isPremium)))
				except Exception as e: await self.unknown_error(message, authorId, e)
			elif action == "premiums":
				try: coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
				except: pass
				try: sentMessages.append(await message.channel.send(embed=self.coinParser.premiums(marketPair, tickerId, coinThumbnail)))
				except Exception as e: await self.unknown_error(message, authorId, e)
			elif action == "ls":
				try: coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
				except: pass
				try: sentMessages.append(await message.channel.send(embed=self.coinParser.long_short_ratio(self.coinParser.exchanges["bitfinex2"], marketPair, tickerId, coinThumbnail, False)))
				except Exception as e: await self.unknown_error(message, authorId, e)
			elif action == "sl":
				try: coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
				except: pass
				try: sentMessages.append(await message.channel.send(embed=self.coinParser.long_short_ratio(self.coinParser.exchanges["bitfinex2"], marketPair, tickerId, coinThumbnail, True)))
				except Exception as e: await self.unknown_error(message, authorId, e)
			elif action == "dom":
				try: sentMessages.append(await message.channel.send(embed=self.coinGeckoConnection.coin_dominance(base)))
				except Exception as e: await self.unknown_error(message, authorId, e)
			elif action == "mcap":
				try: sentMessages.append(await message.channel.send(embed=self.coinGeckoConnection.total_market_cap()))
				except Exception as e: await self.unknown_error(message, authorId, e)
			elif action == "fgi":
				try: sentMessages.append(await message.channel.send(embed=self.alternativemeConnection.fear_greed_index_p()))
				except Exception as e: await self.unknown_error(message, authorId, e)
			else:
				if useFallback:
					try:
						cgData = self.coinGeckoConnection.coingecko.get_coin_by_id(id=self.coinGeckoConnection.coinGeckoIndex[base.lower()]["id"], localization="false", tickers=False, market_data=True, community_data=False, developer_data=False)
					except:
						embed = discord.Embed(title="Price data for {} from CoinGecko isn't available".format(marketPair), color=constants.colors["gray"])
						embed.set_author(name="Couldn't get price data", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
						return

					quote = quote if quote.lower() in cgData["market_data"]["current_price"] else "BTC"
					percentChange = cgData["market_data"]["price_change_percentage_24h_in_currency"][quote.lower()] if quote.lower() in cgData["market_data"]["price_change_percentage_24h_in_currency"] else 0
					percentChangeText = " *({:+.2f} %)*".format(percentChange)
					embedColor = constants.colors["amber" if percentChange == 0 else ("green" if percentChange > 0 else "red")]
					coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
					priceText = ("{:,.%df}" % (2 if quote == "USD" else 8)).format(cgData["market_data"]["current_price"][quote.lower()])
					usdConversion = None if quote == "USD" else "≈ ${:,.6f}".format(cgData["market_data"]["current_price"]["usd"])
				else:
					tf, limitTimestamp, candleOffset = Utils.get_highest_supported_timeframe(self.coinParser.exchanges[exchange], datetime.datetime.now().astimezone(pytz.utc))
					if symbol in self.rateLimited["p"][exchange] and symbol in self.rateLimited["v"][exchange]:
						price = self.rateLimited["p"][exchange][symbol]
						volume = self.rateLimited["v"][exchange][symbol]

						try: coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
						except: pass
					else:
						try:
							priceData = self.coinParser.exchanges[exchange].fetch_ohlcv(symbol, timeframe=tf.lower(), since=limitTimestamp, limit=500)
						except:
							embed = discord.Embed(title="Price data for {} on {} isn't available".format(marketPair, self.coinParser.exchanges[exchange].name), color=constants.colors["gray"])
							embed.set_author(name="Couldn't get price data", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, authorId, e)
							return

						try: coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
						except: pass

						price = [priceData[-1][4], priceData[0][1]] if len(priceData) < candleOffset else [priceData[-1][4], priceData[-candleOffset][1]]
						volume = sum([candle[5] for candle in priceData if int(candle[0] / 1000) >= int(self.coinParser.exchanges[exchange].milliseconds() / 1000) - 86400])
						if exchange == "bitmex" and symbol == "BTC/USD": self.coinParser.lastBitcoinPrice = price[0]
						self.rateLimited["p"][exchange][symbol] = price
						self.rateLimited["v"][exchange][symbol] = volume

					percentChange = 0 if tf == "1m" else (price[0] / price[1]) * 100 - 100
					percentChangeText = "" if tf == "1m" else " *({:+.2f} %)*".format(percentChange)
					embedColor = constants.colors["amber"] if tf == "1m" else constants.colors["amber" if percentChange == 0 else ("green" if percentChange > 0 else "red")]
					priceText = Utils.format_price(self.coinParser.exchanges[exchange], symbol, price[0])
					usdConversion = "≈ ${:,.6f}".format(price[0] * self.coinParser.lastBitcoinPrice) if quote == "BTC" else None

				embed = discord.Embed(title="{} {}{}".format(priceText, quote.replace("USDT", "USD").replace("USDC", "USD").replace("TUSD", "USD").replace("USDS", "USD").replace("PAX", "USD"), percentChangeText), description=usdConversion, color=embedColor)
				embed.set_author(name=marketPair, icon_url=coinThumbnail)
				embed.set_footer(text="Price on {}".format("CoinGecko" if exchange == "CoinGecko" else self.coinParser.exchanges[exchange].name))
				try: sentMessages.append(await message.channel.send(embed=embed))
				except Exception as e: await self.unknown_error(message, authorId, e)

				if not useFallback: threading.Thread(target=self.clear_rate_limit_cache, args=(exchange, symbol, ["p", "v"], self.coinParser.exchanges[exchange].rateLimit / 1000)).start()

			threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["p"][id] = {}
				self.rateLimited["v"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def volume(self, message, authorId, raw, mute=False):
		try:
			sentMessages = []
			arguments = raw.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker(arguments[0].upper(), "ohlcv", exchangeFallthrough=True)
			if isAggregatedSymbol:
				if not mute:
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `v` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
				return

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, command="v")
			if outputMessage is not None:
				if not mute:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
				return
			_, exchange = arguments
			exchangeFallthrough = (tickerId.endswith(("USD", "BTC")) and exchange == "")

			outputMessage, details = self.coinParser.find_market_pair(tickerId, exchange, "ohlcv", exchangeFallthrough=exchangeFallthrough)
			availableOnCoinGecko = (tickerId.lower() if details is None else details[1].lower()) in self.coinGeckoConnection.coinGeckoIndex
			useFallback = (outputMessage == "Ticker `{}` was not found".format(tickerId) or exchangeFallthrough) and availableOnCoinGecko
			if outputMessage is not None and not useFallback:
				if not mute:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
				return
			symbol, base, quote, marketPair, exchange = (tickerId, tickerId, "BTC", "{}BTC".format(tickerId), "CoinGecko") if useFallback and details is None else details
			if useFallback: exchange = "CoinGecko"
			coinThumbnail = firebase_storage.icon_bw

			try: await message.channel.trigger_typing()
			except: pass

			if useFallback:
				try:
					cgData = self.coinGeckoConnection.coingecko.get_coin_by_id(id=self.coinGeckoConnection.coinGeckoIndex[base.lower()]["id"], localization="false", tickers=False, market_data=True, community_data=False, developer_data=False)
				except:
					embed = discord.Embed(title="Volume data for {} from CoinGecko isn't available".format(marketPair), color=constants.colors["gray"])
					embed.set_author(name="Couldn't get volume data", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
					return

				coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
				base, quote = base if base.lower() in cgData["market_data"]["current_price"] else "BTC", "USD"
				volume = cgData["market_data"]["total_volume"][base.lower()]
				volumeUsd = cgData["market_data"]["total_volume"]["usd"]
			else:
				tf, limitTimestamp, candleOffset = Utils.get_highest_supported_timeframe(self.coinParser.exchanges[exchange], datetime.datetime.now().astimezone(pytz.utc))
				if symbol in self.rateLimited["p"][exchange] and symbol in self.rateLimited["v"][exchange]:
					price = self.rateLimited["p"][exchange][symbol]
					volume = self.rateLimited["v"][exchange][symbol]

					try: coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
					except: pass
				else:
					try:
						priceData = self.coinParser.exchanges[exchange].fetch_ohlcv(symbol, timeframe=tf.lower(), since=limitTimestamp, limit=500)
					except:
						embed = discord.Embed(title="Volume data for {} on {} isn't available".format(marketPair, self.coinParser.exchanges[exchange].name), color=constants.colors["gray"])
						embed.set_author(name="Couldn't get volume data", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, authorId, e)
						return

					try: coinThumbnail = self.coinGeckoConnection.coinGeckoIndex[base.lower()]["image"]
					except: pass

					price = [priceData[-1][4], priceData[0][1]] if len(priceData) < candleOffset else [priceData[-1][4], priceData[-candleOffset][1]]
					volume = sum([candle[5] for candle in priceData if int(candle[0] / 1000) >= int(self.coinParser.exchanges[exchange].milliseconds() / 1000) - 86400])
					if exchange == "bitmex" and symbol == "BTC/USD": self.coinParser.lastBitcoinPrice = price[0]
					self.rateLimited["p"][exchange][symbol] = price
					self.rateLimited["v"][exchange][symbol] = volume

				if exchange in ["bitmex"]: volume /= price[0]
				volumeUsd = int(volume * price[0])

			embed = discord.Embed(title="{:,.4f} {}".format(volume, base), description="≈ {:,} {}".format(volumeUsd, quote.replace("USDT", "USD").replace("USDC", "USD").replace("TUSD", "USD").replace("USDS", "USD").replace("PAX", "USD")), color=constants.colors["orange"])
			embed.set_author(name=marketPair, icon_url=coinThumbnail)
			embed.set_footer(text="Volume on {}".format("CoinGecko" if exchange == "CoinGecko" else self.coinParser.exchanges[exchange].name))
			try: sentMessages.append(await message.channel.send(embed=embed))
			except Exception as e: await self.unknown_error(message, authorId, e)

			if not useFallback: threading.Thread(target=self.clear_rate_limit_cache, args=(exchange, symbol, ["p", "v"], self.coinParser.exchanges[exchange].rateLimit / 1000)).start()

			threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["p"][id] = {}
				self.rateLimited["v"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def depth(self, message, authorId, raw, mute=False):
		try:
			sentMessages = []
			arguments = raw.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker(arguments[0].upper(), "ohlcv")
			if isAggregatedSymbol:
				if not mute:
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `d` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, authorId, e)
				return (sentMessages, len(sentMessages))

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, command="v")
			if outputMessage is not None:
				if not mute:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, authorId, e)
				return (sentMessages, len(sentMessages))
			_, exchange = arguments

			outputMessage, details = self.coinParser.find_market_pair(tickerId, exchange, "ohlcv")
			if outputMessage is not None:
				if not mute:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, authorId, e)
				return (sentMessages, len(sentMessages))

			symbol, base, quote, marketPair, exchange = details

			try: await message.channel.trigger_typing()
			except: pass

			if symbol in self.rateLimited["d"][exchange]:
				depthData = self.rateLimited["d"][exchange][symbol]
			else:
				try:
					depthData = self.coinParser.exchanges[exchange].fetch_order_book(symbol)
					self.rateLimited["d"][exchange][symbol] = depthData
				except:
					embed = discord.Embed(title="Orderbook data for {} isn't available".format(marketPair), color=constants.colors["gray"])
					embed.set_author(name="Couldn't get orderbook data", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
					self.rateLimited["d"][exchange] = {}
					return (sentMessages, len(sentMessages))

			chartName = self.imageProcessor.request_depth_chart(authorId, depthData, self.coinParser.exchanges[exchange].markets[symbol]["precision"]["price"])

			if chartName is None:
				try:
					embed = discord.Embed(title="Requested orderbook chart for `{}` is not available".format(marketPair), color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=firebase_storage.icon_bw)
					chartMessage = await message.channel.send(embed=embed)
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				except Exception as e: await self.unknown_error(message, authorId, e)
				return (sentMessages, len(sentMessages))

			try:
				chartMessage = await message.channel.send(file=discord.File("charts/" + chartName, chartName))
				sentMessages.append(chartMessage)
				try: await chartMessage.add_reaction("☑")
				except: pass
			except Exception as e: await self.unknown_error(message, authorId, e)

			threading.Thread(target=self.clear_rate_limit_cache, args=(exchange, symbol, ["d"], self.coinParser.exchanges[exchange].rateLimit / 1000)).start()

			self.imageProcessor.clean_cache()

			threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["d"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def heatmap(self, message, authorId, raw, mute=False):
		try:
			sentMessages = []
			arguments = raw.split(" ")

			outputMessage, arguments = self.imageProcessor.process_heatmap_arguments(arguments)
			if outputMessage is not None:
				if not mute:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, authorId, e)
				if arguments is None:
					return ([], 0)

			timeframes, chart, type, side, category = arguments

			try: await message.channel.trigger_typing()
			except: pass

			for timeframe in timeframes:
				queueLength = [len(self.imageProcessor.screengrabLock[e]) for e in self.imageProcessor.screengrabLock]
				driverInstance = queueLength.index(min(queueLength))

				waitMessage = None
				if len(self.imageProcessor.screengrabLock[driverInstance]) > 2:
					embed = discord.Embed(title="One moment...", color=constants.colors["gray"])
					try:  waitMessage = await message.channel.send(embed=embed)
					except: pass
					await asyncio.sleep(0.5)

				try: await message.channel.trigger_typing()
				except: pass

				chartName = await self.imageProcessor.request_heatmap_chart(authorId, driverInstance, timeframe, chart, type, side, category)

				if waitMessage is not None:
					try: await waitMessage.delete()
					except: pass

				if chartName is None:
					try:
						embed = discord.Embed(title="Requested heat map is not available".format(tickerId), color=constants.colors["gray"])
						embed.set_author(name="Heat map not available", icon_url=firebase_storage.icon_bw)
						chartMessage = await message.channel.send(embed=embed)
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					except Exception as e: await self.unknown_error(message, authorId, e)
					return (sentMessages, len(sentMessages))

				try:
					chartMessage = await message.channel.send(file=discord.File("charts/" + chartName, chartName))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				except Exception as e: await self.unknown_error(message, authorId, e)

				self.imageProcessor.clean_cache()

			threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))
			return ([], 0)

	async def mcap(self, message, authorId, raw, mute=False):
		try:
			sentMessages = []
			arguments = raw.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker(arguments[0].upper(), "ohlcv", defaultQuote="")
			if isAggregatedSymbol:
				if not mute:
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `mcap` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
				return

			conversion = ""
			if len(arguments) == 2: conversion = arguments[1].upper()
			elif len(arguments) > 2: return

			outputMessage, details = self.coinParser.find_mcap_pair(tickerId, conversion, exchange, "ohlcv")
			if outputMessage is not None:
				if not mute:
					try: int(base)
					except:
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
						try: sentMessages.append(await message.channel.send(embed=embed))
						except Exception as e: await self.unknown_error(message, authorId, e)
				return
			base, quote = details

			if base.lower() in self.coinGeckoConnection.coinGeckoIndex:
				try: await message.channel.trigger_typing()
				except: pass

				try:
					data = self.coinGeckoConnection.coingecko.get_coin_by_id(id=self.coinGeckoConnection.coinGeckoIndex[base.lower()]["id"], localization="false", tickers=False, market_data=True, community_data=True, developer_data=True)
				except:
					await self.unknown_error(message, authorId, e)
					return

				embed = discord.Embed(title="{} ({})".format(data["name"], base), description="Ranked #{} by market cap".format(data["market_data"]["market_cap_rank"]), color=constants.colors["lime"])
				embed.set_thumbnail(url=data["image"]["large"])

				if quote == "": quote = "USD"
				if quote.lower() not in data["market_data"]["current_price"]:
					embed = discord.Embed(title="Conversion to {} is not available".format(tickerId), color=constants.colors["gray"])
					embed.set_author(name="Conversion not available", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except: pass
					return

				usdPrice = ("${:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["usd"])).format(data["market_data"]["current_price"]["usd"])
				eurPrice = ("\n€{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["eur"])).format(data["market_data"]["current_price"]["eur"])
				btcPrice = ""
				ethPrice = ""
				bnbPrice = ""
				xrpPrice = ""
				basePrice = ""
				if base != "BTC" and "btc" in data["market_data"]["current_price"]:
					btcPrice = ("\n₿{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["btc"])).format(data["market_data"]["current_price"]["btc"])
				if base != "ETH" and "eth" in data["market_data"]["current_price"]:
					ethPrice = ("\nΞ{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["eth"])).format(data["market_data"]["current_price"]["eth"])
				if base != "BNB" and "bnb" in data["market_data"]["current_price"]:
					bnbPrice = ("\n{:,.%df} BNB" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["bnb"])).format(data["market_data"]["current_price"]["bnb"])
				if base != "XRP" and "xrp" in data["market_data"]["current_price"]:
					xrpPrice = ("\n{:,.%df} XRP" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["xrp"])).format(data["market_data"]["current_price"]["xrp"])
				if quote.lower() in data["market_data"]["current_price"] and quote not in ["USD", "EUR", "BTC", "ETH", "BNB", "XRP"]:
					basePrice = ("\n{:,.%df} {}" % Utils.add_decimal_zeros(data["market_data"]["current_price"][quote.lower()])).format(data["market_data"]["current_price"][quote.lower()], quote)
				embed.add_field(name="Price", value=(usdPrice + eurPrice + btcPrice + ethPrice + bnbPrice + xrpPrice + basePrice), inline=True)

				change1h = ""
				change24h = ""
				change7d = ""
				change30d = ""
				change1y = ""
				if quote.lower() in data["market_data"]["price_change_percentage_1h_in_currency"]:
					change1h = "Past hour: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_1h_in_currency"][quote.lower()])
				if quote.lower() in data["market_data"]["price_change_percentage_24h_in_currency"]:
					change24h = "\nPast day: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_24h_in_currency"][quote.lower()])
				if quote.lower() in data["market_data"]["price_change_percentage_7d_in_currency"]:
					change7d = "\nPast week: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_7d_in_currency"][quote.lower()])
				if quote.lower() in data["market_data"]["price_change_percentage_30d_in_currency"]:
					change30d = "\nPast month: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_30d_in_currency"][quote.lower()])
				if quote.lower() in data["market_data"]["price_change_percentage_1y_in_currency"]:
					change1y = "\nPast year: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_1y_in_currency"][quote.lower()])
				embed.add_field(name="Price Change", value=(change1h + change24h + change7d + change30d + change1y), inline=True)

				marketCap = ""
				totalVolume = ""
				totalSupply = ""
				circulatingSupply = ""
				if data["market_data"]["market_cap"] is not None:
					marketCap = "Market cap: {:,.0f} {}".format(data["market_data"]["market_cap"][quote.lower()], quote)
				if data["market_data"]["total_volume"] is not None:
					totalVolume = "\nTotal volume: {:,.0f} {}".format(data["market_data"]["total_volume"][quote.lower()], quote)
				if data["market_data"]["total_supply"] is not None:
					totalSupply = "\nTotal supply: {:,.0f}".format(data["market_data"]["total_supply"])
				if data["market_data"]["circulating_supply"] is not None:
					circulatingSupply = "\nCirculating supply: {:,.0f}".format(data["market_data"]["circulating_supply"])
				embed.add_field(name="Details", value=(marketCap + totalVolume + totalSupply + circulatingSupply), inline=False)

				embed.set_footer(text="Powered by CoinGecko API")

				try: sentMessages.append(await message.channel.send(embed=embed))
				except Exception as e: await self.unknown_error(message, authorId, e)

				threading.Thread(target=self.fusion.command_stream, args=([message] + sentMessages,)).start()
				return
			else:
				embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
				embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
				try: await message.channel.send(embed=embed)
				except Exception as e: await self.unknown_error(message, authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

	async def markets(self, message, authorId, raw, mute=False):
		try:
			arguments = raw.split(" ")

			tickerId, _, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker(arguments[0].upper(), "ohlcv")
			if isAggregatedSymbol:
				if not mute:
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `p` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, authorId, e)
				return

			try: await message.channel.trigger_typing()
			except: pass

			listings = self.coinParser.get_listings(tickerId, "", "ohlcv")
			if len(listings) != 0:
				embed = discord.Embed(color=constants.colors["deep purple"])
				embed.add_field(name="Found on {} exchanges".format(len(listings)), value="{}".format(", ".join(listings)), inline=False)
				embed.set_author(name="{} listings".format(tickerId), icon_url=firebase_storage.icon)
				try: await message.channel.send(embed=embed)
				except Exception as e: await self.unknown_error(message, authorId, e)
			else:
				embed = discord.Embed(title="`{}` is not listed on any exchange.".format(tickerId), color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=firebase_storage.icon_bw)
				try: await message.channel.send(embed=embed)
				except Exception as e: await self.unknown_error(message, authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, raw))

def handle_exit():
	client.loop.run_until_complete(client.dblpy.close())
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
