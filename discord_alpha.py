import os
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

from bot.keys.f802e1fba977727845e8872c1743a714 import Keys as ApiKeys
from bot.assets import firebase_storage
from bot.helpers.utils import Utils
from bot.helpers.logger import Logger as l
from bot.helpers import constants

from bot.engine.assistant import Assistant
from bot.engine.alerts import Alerts
from bot.engine.coins import CoinParser
from bot.engine.fusion import Fusion
from bot.engine.images import ImageProcessor
from bot.engine.presets import Presets
from bot.engine.processor import Processor
from bot.engine.trader import PaperTrader

from bot.engine.connections.exchanges import Exchanges
from bot.engine.connections.coingecko import CoinGecko
from bot.engine.connections.alternativeme import Alternativeme
from bot.engine.connections.coindar import Coindar

from bot.engine.constructs.cryptography import EncryptionHandler
from bot.engine.constructs.message import MessageRequest

try:
	firebase = initialize_firebase_app(credentials.Certificate("bot/keys/bf12e1515c25c7d8c0352f1413ab9a15.json"), {'storageBucket': ApiKeys.get_firebase_bucket()})
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
	coindar = Coindar()

	encryptionHandler = EncryptionHandler()

	statistics = {"alpha": 0, "alerts": 0, "c": 0, "p": 0, "v": 0, "d": 0, "hmap": 0, "mcap": 0, "news": 0, "mk": 0, "convert": 0, "paper": 0, "live": 0}
	rateLimited = {"c": {}, "p": {}, "d": {}, "v": {}, "u": {}}
	usedPresetsCache = {}

	alphaSettings = {}
	subscribedUsers = {}
	subscribedGuilds = {}
	userProperties = {}
	guildProperties = {}

	alphaServerMembers = []
	maliciousUsers = {}


	def prepare(self, for_guild=-1):
		t = datetime.datetime.now().astimezone(pytz.utc)

		atexit.register(self.cleanup)
		self.dedicatedGuild = for_guild

		newExchanges = list(set(ccxt.exchanges).symmetric_difference({'_1btcxe', 'acx', 'adara', 'anxpro', 'bcex', 'bequant', 'bibox', 'bigone', 'binance', 'binanceje', 'binanceus', 'bit2c', 'bitbank', 'bitbay', 'bitfinex', 'bitfinex2', 'bitflyer', 'bitforex', 'bithumb', 'bitkk', 'bitlish', 'bitmart', 'bitmax', 'bitmex', 'bitso', 'bitstamp', 'bitstamp1', 'bittrex', 'bitz', 'bl3p', 'bleutrade', 'braziliex', 'btcalpha', 'btcbox', 'btcchina', 'btcmarkets', 'btctradeim', 'btctradeua', 'btcturk', 'buda', 'bw', 'bytetrade', 'cex', 'chilebit', 'cobinhood', 'coinbase', 'coinbaseprime', 'coinbasepro', 'coincheck', 'coinegg', 'coinex', 'coinfalcon', 'coinfloor', 'coingi', 'coinmarketcap', 'coinmate', 'coinone', 'coinspot', 'coolcoin', 'coss', 'crex24', 'deribit', 'digifinex', 'dsx', 'exmo', 'exx', 'fcoin', 'fcoinjp', 'flowbtc', 'foxbit', 'ftx', 'fybse', 'gateio', 'gemini', 'hitbtc', 'hitbtc2', 'huobipro', 'huobiru', 'ice3x', 'idex', 'independentreserve', 'indodax', 'itbit', 'kkex', 'kraken', 'kucoin', 'kuna', 'lakebtc', 'latoken', 'lbank', 'liquid', 'livecoin', 'luno', 'lykke', 'mercado', 'mixcoins', 'oceanex', 'okcoincny', 'okcoinusd', 'okex', 'okex3', 'paymium', 'poloniex', 'rightbtc', 'southxchange', 'stex', 'stronghold', 'surbitcoin', 'theocean', 'therock', 'tidebit', 'tidex', 'timex', 'upbit', 'vaultoro', 'vbtc', 'whitebit', 'xbtce', 'yobit', 'zaif', 'zb'}))
		if len(newExchanges) != 0: l.log("New OHLCV supported exchanges: {}".format(newExchanges))

		for side in constants.supportedExchanges:
			for id in constants.supportedExchanges[side]:
				if id not in CoinParser.exchanges:
					self.rateLimited["p"][id] = {}
					self.rateLimited["d"][id] = {}
					self.rateLimited["v"][id] = {}
					try: CoinParser.exchanges[id] = getattr(ccxt, id)()
					except: continue

		CoinParser.refresh_coin_pair_index()
		self.fetch_settings(t)
		self.update_fusion_queue()

		self.dblpy = dbl.DBLClient(client, ApiKeys.get_topgg_key())

	async def on_ready(self):
		t = datetime.datetime.now().astimezone(pytz.utc)

		try:
			priceData = CoinParser.exchanges["bitmex"].fetch_ohlcv(
				"BTC/USD",
				timeframe="1d",
				since=(CoinParser.exchanges["bitmex"].milliseconds() - 24 * 60 * 60 * 3 * 1000)
			)
			CoinParser.lastBitcoinPrice = priceData[-1][4]
		except: pass

		await self.update_system_status(t)
		await self.update_price_status(t)
		if sys.platform == "linux":
			await self.update_guild_count()
			await self.update_static_messages()

		await self.wait_for_chunked()

		self.alphaServer = client.get_guild(414498292655980583)
		self.premiumRoles = {
			0: discord.utils.get(self.alphaServer.roles, id=651042597472698368),
			1: discord.utils.get(self.alphaServer.roles, id=601518889469345810),
			2: discord.utils.get(self.alphaServer.roles, id=601519642070089748),
			3: discord.utils.get(self.alphaServer.roles, id=484387309303758848)
		}

		await self.update_properties()
		await self.security_check()
		await self.send_alerts()

		self.isBotReady = True
		l.log("Alerts", "Alpha is online on {} servers ({:,} users)".format(len(client.guilds), len(client.users)), color=0x00BCD4)

	async def wait_for_chunked(self):
		for guild in client.guilds:
			if not guild.chunked: await asyncio.sleep(1)

	def cleanup(self):
		print("")
		l.log("Alerts", "timestamp: {}, description: Alpha bot is restarting".format(Utils.get_current_date()), post=sys.platform == "linux", color=0x3F51B5)

		try:
			for i in self.imageProcessor.screengrab:
				try: self.imageProcessor.screengrab[i].quit()
				except: continue
			self.imageProcessor.display.stop()
		except: pass

		try:
			if self.statistics["c"] > 0 and sys.platform == "linux":
				statisticsRef = db.document(u"alpha/statistics")
				for i in range(5):
					try:
						t = datetime.datetime.now().astimezone(pytz.utc)
						statisticsRef.set({"{}-{:02d}".format(t.year, t.month): {"discord": self.statistics}}, merge=True)
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

	async def update_static_messages(self):
		try:
			# Alpha Premium messages
			premiumChannel = client.get_channel(669917049895518208)
			bronzeMessage = await premiumChannel.fetch_message(670704460057673728)
			silverMessage = await premiumChannel.fetch_message(670704462075133999)
			goldMessage = await premiumChannel.fetch_message(670704465321394180)
			await bronzeMessage.edit(embed=discord.Embed(title="Alpha Bronze is a great introduction to Alpha's premium features. Bronze members get increased request limits, command presets, up to ten price alerts at a time, and access to paper trader.", description="Learn more about Alpha Bronze on [our website](https://www.alphabotsystem.com/pricing/bronze/)", color=0xFFEA00))
			await silverMessage.edit(embed=discord.Embed(title="Alpha Silver gives you everything Bronze does and more. Not only do Silver members get more pending alerts, they also get access to Alpha's live trader and access to our custom Silver level indicator suite.", description="Learn more about Alpha Silver on [our website](https://www.alphabotsystem.com/pricing/silver/)", color=0xFFC400))
			await goldMessage.edit(embed=discord.Embed(title="Alpha Gold is the perfect choice for serious traders. Gold members enjoy unlimited trading through Discord, increased limits, and get access to our full suite of custom indicators.", description="Learn more about Alpha Gold on [our website](https://www.alphabotsystem.com/pricing/gold/)", color=0xFF9100))

			# Rules and ToS
			faqAndRulesChannel = client.get_channel(601160698310950914)
			rulesAndTOSMessage = await faqAndRulesChannel.fetch_message(601160743236141067)
			faq1Message = await faqAndRulesChannel.fetch_message(601163022529986560)
			faq2Message = await faqAndRulesChannel.fetch_message(601163058831818762)
			faq3Message = await faqAndRulesChannel.fetch_message(601163075126689824)
			await rulesAndTOSMessage.edit(content=constants.rulesAndTOS)
			await faq1Message.edit(content=constants.faq1)
			await faq2Message.edit(content=constants.faq2)
			await faq3Message.edit(content=constants.faq3)

			# Alpha status
			alphaMessage = await client.get_channel(560884869899485233).fetch_message(640502830062632960)
			await alphaMessage.edit(embed=discord.Embed(title=":white_check_mark: Alpha: online", color=constants.colors["deep purple"]))
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

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
				slice = "{}-{:02d}".format(t.year, t.month)
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
			instances = self.fusion.manage_load_distribution(CoinParser.exchanges)
			if sys.platform == "linux":
				try: db.document(u'fusion/alpha').set({"distribution": instances}, merge=True)
				except: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_properties(self):
		try:
			self.alphaServerMembers = [e.id for e in self.alphaServer.members]

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
							self.subscribedUsers.pop(userId, None)
							self.userProperties[userId]["premium"]["subscribed"] = False
							self.userProperties[userId]["premium"]["hadWarning"] = False
							fetchedSettingsRef.set(self.userProperties[userId], merge=True)

							recepient = client.get_user(userId)
							embed = discord.Embed(title="Your Alpha Premium subscription has expired", color=constants.colors["deep purple"])
							try: await recepient.send(embed=embed)
							except: pass
							try: await self.alphaServer.get_member(userId).remove_roles(self.premiumRoles[0])
							except: pass
							try: await self.alphaServer.get_member(userId).remove_roles(self.premiumRoles[self.userProperties[userId]["premium"]["plan"]])
							except: pass
						elif self.userProperties[userId]["premium"]["timestamp"] - 259200 < time.time() and not self.userProperties[userId]["premium"]["hadWarning"]:
							recepient = client.get_user(userId)
							self.userProperties[userId]["premium"]["hadWarning"] = True
							fetchedSettingsRef.set(self.userProperties[userId], merge=True)
							if recepient is not None:
								embed = discord.Embed(title="Your Alpha Premium subscription expires on {}".format(self.userProperties[userId]["premium"]["date"]), color=constants.colors["deep purple"])
								try: await recepient.send(embed=embed)
								except: pass
						else:
							self.subscribedUsers[userId] = self.userProperties[userId]["premium"]["plan"]
					else:
						self.subscribedUsers[userId] = 3

			for guildId in self.guildProperties:
				if self.guildProperties[guildId]["premium"]["subscribed"]:
					if self.guildProperties[guildId]["premium"]["plan"] != 0:
						fetchedSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
						self.guildProperties[guildId] = Utils.createServerSetting(self.guildProperties[guildId])
						if self.guildProperties[guildId]["premium"]["timestamp"] < time.time():
							self.subscribedGuilds.pop(guildId, None)
							self.guildProperties[guildId]["premium"]["subscribed"] = False
							self.guildProperties[guildId]["premium"]["hadWarning"] = False
							fetchedSettingsRef.set(self.guildProperties[guildId], merge=True)
							guild = client.get_guild(guildId)
							for member in guild.members:
								if member.guild_permissions.administrator:
									embed = discord.Embed(title="Alpha Premium subscription for *{}* server has expired".format(guild.name), color=constants.colors["deep purple"])
									try: await member.send(embed=embed)
									except: pass
						elif self.guildProperties[guildId]["premium"]["timestamp"] - 259200 < time.time() and not self.guildProperties[guildId]["premium"]["hadWarning"]:
							guild = client.get_guild(guildId)
							self.guildProperties[guildId]["premium"]["hadWarning"] = True
							fetchedSettingsRef.set(self.guildProperties[guildId], merge=True)
							if guild is not None:
								for member in guild.members:
									if member.guild_permissions.administrator:
										embed = discord.Embed(title="Alpha Premium subscription for *{}* server expires on {}".format(guild.name, self.guildProperties[guildId]["premium"]["date"]), color=constants.colors["deep purple"])
										try: await member.send(embed=embed)
										except: pass
						else:
							self.subscribedGuilds[guildId] = self.guildProperties[guildId]["premium"]["plan"]
					else:
						self.subscribedGuilds[guildId] = 3
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def security_check(self):
		try:
			settingsRef = db.document(u"alpha/settings")
			self.alphaSettings = settingsRef.get().to_dict()

			strGuilds = [e.name for e in client.guilds]

			guildsToRemove = []
			for key in ["tosBlacklist", "tosWhitelist"]:
				for guild in self.alphaSettings[key]:
					if guild not in strGuilds: guildsToRemove.append(guild)
				for guild in guildsToRemove:
					if guild in self.alphaSettings[key]: self.alphaSettings[key].pop(guild)

			suspiciousUsers = {"ids": [], "username": [], "nickname": [], "oldWhitelist": list(self.alphaSettings["avatarWhitelist"]), "oldBlacklist": list(self.alphaSettings["avatarBlacklist"])}
			botNicknames = []
			for guild in client.guilds:
				if guild.id in constants.bannedGuilds:
					l.log("Warning", "timestamp: {}, description: left a blocked server: {}".format(Utils.get_current_date(), guild.name))
					try: await guild.leave()
					except: pass

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
								botNicknames.append("```{}: {}```".format(guild.name, guild.me.nick))
								break
					else:
						if isBlacklisted: self.alphaSettings["tosBlacklist"].pop(guild.name)
						if isWhitelisted: self.alphaSettings["tosWhitelist"].pop(guild.name)

				for member in guild.members:
					if str(member.avatar_url) in self.alphaSettings["avatarBlacklist"]:
						if guild.id not in self.maliciousUsers: self.maliciousUsers[guild.id] = [[], 0]
						self.maliciousUsers[guild.id][0].append(member.id)
						if str(member.avatar_url) in suspiciousUsers["oldBlacklist"]: suspiciousUsers["oldBlacklist"].remove(str(member.avatar_url))
					else:
						if str(member.avatar_url) in ["https://cdn.discordapp.com/embed/avatars/0.png", "https://cdn.discordapp.com/embed/avatars/1.png", "https://cdn.discordapp.com/embed/avatars/2.png", "https://cdn.discordapp.com/embed/avatars/3.png", "https://cdn.discordapp.com/embed/avatars/4.png"]: continue

						if member.id not in [401328409499664394, 361916376069439490, 164073578696802305, 390170634891689984] and member.id not in suspiciousUsers["ids"]:
							if member.name.lower() in ["[alpha] maco", "maco <alpha dev>", "macoalgo", "alpha", "[alpha] mal", "notmaliciousupload", "[alpha] tom", "tom (cryptocurrencyfacts)"]:
								if str(member.avatar_url) in suspiciousUsers["oldWhitelist"]: suspiciousUsers["oldWhitelist"].remove(str(member.avatar_url))
								if str(member.avatar_url) in suspiciousUsers["oldBlacklist"]: suspiciousUsers["oldBlacklist"].remove(str(member.avatar_url))
								if str(member.avatar_url) not in self.alphaSettings["avatarWhitelist"]:
									suspiciousUsers["username"].append("{}: {}".format(member.id, str(member.avatar_url)))
									suspiciousUsers["ids"].append(member.id)
							if member.nick is not None:
								if member.nick.lower() in ["[alpha] maco", "maco <alpha dev>", "maco", "macoalgo", "alpha", "[alpha] mal", "notmaliciousupload", "[alpha] tom", "tom (cryptocurrencyfacts)"]:
									if str(member.avatar_url) in suspiciousUsers["oldWhitelist"]: suspiciousUsers["oldWhitelist"].remove(str(member.avatar_url))
									if str(member.avatar_url) in suspiciousUsers["oldBlacklist"]: suspiciousUsers["oldBlacklist"].remove(str(member.avatar_url))
									if str(member.avatar_url) not in self.alphaSettings["avatarWhitelist"]:
										suspiciousUsers["nickname"].append("{}: {}".format(member.id, str(member.avatar_url)))
										suspiciousUsers["ids"].append(member.id)

			for oldAvatar in suspiciousUsers["oldWhitelist"]: self.alphaSettings["avatarWhitelist"].remove(oldAvatar)
			for oldAvatar in suspiciousUsers["oldBlacklist"]: self.alphaSettings["avatarBlacklist"].remove(oldAvatar)

			botNicknamesText = "No bot nicknames to review..."
			suspiciousUserNamesTest = "No usernames to review..."
			suspiciousUserNicknamesText = "No user nicknames to review..."
			if len(botNicknames) > 0: botNicknamesText = "These servers might be rebranding Alpha bot:{}".format("".join(botNicknames))
			if len(suspiciousUsers["username"]) > 0: suspiciousUserNamesTest = "These users might be impersonating Alpha bot or staff:\n{}".format("\n".join(suspiciousUsers["username"]))
			if len(suspiciousUsers["nickname"]) > 0: suspiciousUserNicknamesText = "These users might be impersonating Alpha bot or staff via nicknames:\n{}".format("\n".join(suspiciousUsers["nickname"]))

			usageReviewChannel = client.get_channel(571786092077121536)
			try:
				botNicknamesMessage = await usageReviewChannel.fetch_message(650084120063508501)
				suspiciousUserNamesMessage = await usageReviewChannel.fetch_message(650084126711349289)
				suspiciousUserNicknamesMessage = await usageReviewChannel.fetch_message(650084149096480831)
				await botNicknamesMessage.edit(content=botNicknamesText)
				await suspiciousUserNamesMessage.edit(content=suspiciousUserNamesTest)
				await suspiciousUserNicknamesMessage.edit(content=suspiciousUserNicknamesText)
			except: pass

			if sys.platform == "linux":
				try: settingsRef.set(self.alphaSettings, merge=True)
				except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_system_status(self, t):
		try:
			statisticsRef = db.document(u"alpha/statistics")
			statisticsRef.set({"{}-{:02d}".format(t.year, t.month): {"discord": self.statistics}}, merge=True)

			numOfCharts = ":chart_with_upwards_trend: {:,} charts requested".format(self.statistics["c"] + self.statistics["hmap"])
			numOfAlerts = ":bell: {:,} alerts set".format(self.statistics["alerts"])
			numOfPrices = ":money_with_wings: {:,} prices pulled".format(self.statistics["d"] + self.statistics["p"] + self.statistics["v"])
			numOfDetails = ":tools: {:,} coin details looked up".format(self.statistics["mcap"] + self.statistics["mk"] + self.statistics["convert"])
			numOfTrades = ":dart: {:,} trades executed".format(self.statistics["paper"] + self.statistics["live"])
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
				priceData = CoinParser.exchanges[fetchPairs[cycle][0][0]].fetch_ohlcv(
					fetchPairs[cycle][1],
					timeframe="1d",
					since=(CoinParser.exchanges[fetchPairs[cycle][0][0]].milliseconds() - 24 * 60 * 60 * 3 * 1000)
				)
				price1 = [priceData[-1][4], priceData[-2][4]]
				CoinParser.lastBitcoinPrice = price1[0]
				self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][1]] = price1
		except: pass

		price2 = None
		try:
			if fetchPairs[cycle][2] in self.rateLimited["p"][fetchPairs[cycle][0][0]]:
				price2 = self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][2]]
			else:
				priceData = CoinParser.exchanges[fetchPairs[cycle][0][0]].fetch_ohlcv(
					fetchPairs[cycle][2],
					timeframe="1d",
					since=(CoinParser.exchanges[fetchPairs[cycle][0][0]].milliseconds() - 24 * 60 * 60 * 3 * 1000)
				)
				price2 = [priceData[-1][4], priceData[-2][4]]
				self.rateLimited["p"][fetchPairs[cycle][0][0]][fetchPairs[cycle][2]] = price2
		except: pass

		price1Text = " -" if price1 is None else "{:,.0f}".format(price1[0])
		price2Text = " -" if price2 is None else "{:,.0f}".format(price2[0])

		try: await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="{} ₿ {} Ξ {}".format(fetchPairs[cycle][0][1], price1Text, price2Text)))
		except: pass

		threading.Thread(target=self.clear_rate_limit_cache, args=(fetchPairs[cycle][0][0], fetchPairs[cycle][1], ["p"], CoinParser.exchanges[fetchPairs[cycle][0][0]].rateLimit / 1000 * 2)).start()
		threading.Thread(target=self.clear_rate_limit_cache, args=(fetchPairs[cycle][0][0], fetchPairs[cycle][2], ["p"], CoinParser.exchanges[fetchPairs[cycle][0][0]].rateLimit / 1000 * 2)).start()

	async def send_alerts(self):
		if sys.platform == "linux":
			try:
				try: alertMessages = await client.get_channel(605419986164645889).history(limit=None).flatten()
				except: return
				for message in reversed(alertMessages):
					userId, alertMessage = message.content.split(": ", 1)
					embed = discord.Embed(title=alertMessage, color=constants.colors["deep purple"])
					embed.set_author(name="Price alert triggered", icon_url=firebase_storage.icon)

					try:
						alertUser = client.get_user(int(userId))
						await alertUser.send(embed=embed)
					except:
						await client.get_channel(595954290409865226).send(content="<@!{}>!".format(alertUser.id), embed=embed)

					try: await message.delete()
					except: pass
			except asyncio.CancelledError: pass
			except Exception as e:
				exc_type, exc_obj, exc_tb = sys.exc_info()
				fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
				l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def process_data_streams(self):
		dataStream = {"raw": [656788876932677632, 656789037192839180, 656789117262233600], "id": [656222852152033332, 656645038650032139, 656650448215867403], "timeframe": ["240", "240", "60"], "type": ["crypto", "forex", "equities"]}

		if sys.platform == "linux":
			for i in range(3):
				streamChannel = client.get_channel(dataStream["raw"][i])
				try:
					try: streamMessages = await streamChannel.history(limit=10).flatten() # None
					except: continue
					for message in reversed(streamMessages):
						if "`" in message.content:
							ticker, trendScore, momentumScore, volatilityScore, volumeScore = message.clean_content.lower().replace("`", "").split(", ")[:5]
							exchange, tickerId = ticker.split(":")
							chartName, chartMessage = None, None

							if dataStream["type"][i] == "crypto": tickerId, parameters = tickerId.upper(), [exchange, dataStream["timeframe"][i]]
							else: tickerId, parameters = "{}:{}".format(exchange.upper(), tickerId.upper()), [dataStream["timeframe"][i]]
							outputMessage, request = Processor.process_chart_arguments(401328409499664394, parameters, tickerId, command="c", defaultPlatforms=["TradingLite", "TradingView"])
							if outputMessage is not None:
								l.log("Warning", "timestamp: {}, failed to process data stream chart: {}".format(Utils.get_current_date(), outputMessage))
							else:
								request.set_current(timeframe=request.get_timeframes()[0])
								chartName, chartMessage = await self.imageProcessor.request_chart(401328409499664394, request)
								if chartName is None:
									l.log("Warning", "timestamp: {}, failed to fetch data stream chart: {}".format(Utils.get_current_date(), chartMessage))
							file = None if chartName is None else discord.File("charts/" + chartName, chartName)

							embed = discord.Embed(title=tickerId.upper(), color=constants.colors["deep purple"])
							embed.add_field(name="Trend", value="{}".format(Utils.convert_score(int(trendScore))), inline=True)
							embed.add_field(name="Momentum", value="{}".format(Utils.convert_score(int(momentumScore))), inline=True)
							embed.add_field(name="Volatility", value="{}".format(Utils.convert_score(int(volatilityScore))), inline=True)
							embed.add_field(name="Volume", value="{}".format(Utils.convert_score(int(volumeScore))), inline=True)
						else:
							embed = discord.Embed(title=message.clean_content, color=constants.colors["light blue"])
							file = None

						try:
							outgoingChannel = client.get_channel(dataStream["id"][i])
							await outgoingChannel.send(embed=embed, file=file)
						except Exception as e:
							exc_type, exc_obj, exc_tb = sys.exc_info()
							fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
							l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

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
				await self.security_check()
				await self.update_properties()
			if "4H" in timeframes:
				self.update_fusion_queue()
			if "1D" in timeframes:
				self.coinGeckoConnection.refresh_coingecko_datasets()
				if t.day == 1 and self.alphaSettings["lastStatsSnapshot"] != t.month:
					self.fusion.push_active_users(bucket, t)
			await self.process_data_streams()

	async def on_message(self, message):
		try:
			self.lastMessageTimestamp = (datetime.datetime.timestamp(pytz.utc.localize(message.created_at)), pytz.utc.localize(message.created_at))
			messageRequest = MessageRequest(" ".join(message.clean_content.lower().split()), message.webhook_id if message.webhook_id is not None else message.author.id, message.guild.id if message.guild is not None else -1)
			sentMessages = []

			if self.dedicatedGuild != 0 and self.dedicatedGuild != messageRequest.guildId: return

			isSelf = message.author == client.user
			isUserBlocked = (messageRequest.authorId in constants.blockedBots if message.webhook_id is None else any(e in message.author.name.lower() for e in constants.blockedBotNames)) if message.author.bot else messageRequest.authorId in constants.blockedUsers
			isChannelBlocked = message.channel.id in constants.blockedChannels or messageRequest.guildId in constants.blockedGuilds
			hasContent = message.clean_content != "" or isSelf

			if not self.isBotReady or isUserBlocked or isChannelBlocked or not hasContent: return

			messageRequest.personalPremium = self.subscribedUsers[messageRequest.authorId] if messageRequest.authorId in self.subscribedUsers else 0
			messageRequest.serverPremium = self.subscribedGuilds[messageRequest.authorId] if messageRequest.guildId in self.subscribedGuilds else 0
			shortcutsEnabled = True if messageRequest.guildId not in self.guildProperties else self.guildProperties[messageRequest.guildId]["settings"]["shortcuts"]
			hasSendPermission = True if message.guild is None else message.guild.me.permissions_in(message.channel).send_messages

			if not messageRequest.content.startswith("preset "):
				parsedPresets = []
				if message.author.id in self.userProperties: messageRequest.content, messageRequest.presetUsed, parsedPresets = Presets.process_presets(messageRequest.content, self.userProperties[message.author.id])
				if not messageRequest.presetUsed and messageRequest.guildId in self.guildProperties: messageRequest.content, messageRequest.presetUsed, parsedPresets = Presets.process_presets(messageRequest.content, self.guildProperties[messageRequest.guildId])

				if not messageRequest.presetUsed and messageRequest.guildId in self.usedPresetsCache:
					for preset in self.usedPresetsCache[messageRequest.guildId]:
						if preset["phrase"] == messageRequest.content:
							if preset["phrase"] not in [p["phrase"] for p in parsedPresets]:
								parsedPresets = [preset]
								messageRequest.presetUsed = False
								break

				if messageRequest.is_bronze():
					if messageRequest.presetUsed:
						if messageRequest.guildId != -1:
							if messageRequest.guildId not in self.usedPresetsCache: self.usedPresetsCache[messageRequest.guildId] = []
							for preset in parsedPresets:
								if preset not in self.usedPresetsCache[messageRequest.guildId]: self.usedPresetsCache[messageRequest.guildId].append(preset)
							self.usedPresetsCache[messageRequest.guildId] = self.usedPresetsCache[messageRequest.guildId][-3:]

						embed = discord.Embed(title="Running `{}` command from personal preset".format(messageRequest.content), color=constants.colors["light blue"])
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
							messageRequest.content = "preset add {} {}".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"])
				elif len(parsedPresets) != 0:
					try: await message.channel.send(content="Presets are available to premium members only. {}".format("Join our server to learn more: https://discord.gg/GQeDE85" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more."))
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

			messageRequest.content, messageRequest.shortcutUsed = Utils.shortcuts(messageRequest.content, shortcutsEnabled)
			isCommand = messageRequest.content.startswith(tuple(constants.commandWakephrases)) and not isSelf

			if messageRequest.guildId != -1:
				if messageRequest.guildId in self.maliciousUsers:
					if any([e.id in self.maliciousUsers[messageRequest.guildId][0] for e in message.guild.members]) and time.time() + 60 < self.maliciousUsers[messageRequest.guildId][1]:
						self.maliciousUsers[messageRequest.guildId][1] = time.time()
						embed = discord.Embed(title="This Discord server has one or more members disguising as Alpha bot or one of the team members. Server admins are advised to take action.", description="Users flagged for impersonation are: {}".format(", ".join(["<@!{}>".format(e.id) for e in maliciousUsers])), color=0x000000)

				if isCommand:
					if len(self.alphaSettings["tosBlacklist"]) != 0:
						if message.guild.name in self.alphaSettings["tosBlacklist"]:
							embed = discord.Embed(title="This Discord server is violating Alpha terms of service. The inability to comply will result in termination of all Alpha related services.", color=0x000000)
							embed.add_field(name="Terms of service", value="[Read now](https://www.alphabotsystem.com/terms-of-service/)", inline=True)
							embed.add_field(name="Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
					elif (True if messageRequest.guildId not in self.guildProperties else not self.guildProperties[messageRequest.guildId]["hasDoneSetup"]) and messageRequest.content != "alpha setup" and message.channel.id == 479927662337458176:
						embed = discord.Embed(title="Thanks for adding Alpha to your server, we're thrilled to have you onboard. We think you're going to love everything Alpha can do. Before you start using it, you must complete a short setup process. Type `alpha setup` to begin.", color=constants.colors["pink"])
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

			if messageRequest.content.startswith("a "):
				if message.author.bot: return

				command = messageRequest.content.split(" ", 1)[1]
				if command == "help":
					await self.help(message, messageRequest)
					return
				elif command == "invite":
					try: await message.channel.send(embed=discord.Embed(title="https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372033", color=constants.colors["pink"]))
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return
				if messageRequest.guildId != -1:
					if command.startswith("assistant"):
						if message.author.guild_permissions.administrator:
							newVal = None
							if command == "assistant disable": newVal = False
							elif command == "assistant enable": newVal = True

							if newVal is not None:
								serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(messageRequest.guildId))
								serverSettings = serverSettingsRef.get().to_dict()
								serverSettings = Utils.updateServerSetting(serverSettings, "settings", sub="assistant", toVal=newVal)
								serverSettingsRef.set(serverSettings, merge=True)
								self.guildProperties[messageRequest.guildId] = copy.deepcopy(serverSettings)

								try: await message.channel.send(embed=discord.Embed(title="Assistant settings saved for *{}* server".format(message.guild.name), color=constants.colors["pink"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return
					elif command.startswith("shortcuts"):
						if message.author.guild_permissions.administrator:
							newVal = None
							if command == "shortcuts disable": newVal = False
							elif command == "shortcuts enable": newVal = True

							if newVal is not None:
								serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(messageRequest.guildId))
								serverSettings = serverSettingsRef.get().to_dict()
								serverSettings = Utils.updateServerSetting(serverSettings, "settings", sub="shortcuts", toVal=newVal)
								serverSettingsRef.set(serverSettings, merge=True)
								self.guildProperties[messageRequest.guildId] = copy.deepcopy(serverSettings)

								try: await message.channel.send(embed=discord.Embed(title="Shortcuts settings saved for *{}* server".format(message.guild.name), color=constants.colors["pink"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return
					elif command.startswith("autodelete"):
						if message.author.guild_permissions.administrator:
							newVal = None
							if command == "autodelete disable": newVal = False
							elif command == "autodelete enable": newVal = True

							if newVal is not None:
								serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(messageRequest.guildId))
								serverSettings = serverSettingsRef.get().to_dict()
								serverSettings = Utils.updateServerSetting(serverSettings, "settings", sub="autodelete", toVal=newVal)
								serverSettingsRef.set(serverSettings, merge=True)
								self.guildProperties[messageRequest.guildId] = copy.deepcopy(serverSettings)

								try: await message.channel.send(embed=discord.Embed(title="Autodelete settings saved for *{}* server".format(message.guild.name), color=constants.colors["pink"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return
				if messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
					if command.startswith("premium user"):
						subscription = messageRequest.content.split("premium user ", 1)
						if len(subscription) == 2:
							parameters = subscription[1].split(" ", 2)
							if len(parameters) == 3:
								if parameters[2] == "trial": userId, plan, duration, trial = int(parameters[0]), int(parameters[1]), 1, True
								else: userId, plan, duration, trial = int(parameters[0]), int(parameters[1]), int(parameters[2]), False

								recepient = client.get_user(userId)
								if recepient is None:
									try: await message.channel.send(embed=discord.Embed(title="No users with this id found", color=constants.colors["gray"]))
									except: pass
									return

								fetchedSettingsRef = db.document(u"alpha/settings/users/{}".format(userId))
								fetchedSettings = fetchedSettingsRef.get().to_dict()
								fetchedSettings = Utils.createUserSettings(fetchedSettings)

								hadTrial = fetchedSettings["premium"]["hadTrial"]
								wasSubscribed = fetchedSettings["premium"]["subscribed"]
								lastTimestamp = fetchedSettings["premium"]["timestamp"]

								if hadTrial and trial:
									if wasSubscribed:
										try: await message.channel.send(embed=discord.Embed(title="This user already has the trial.", color=constants.colors["gray"]))
										except: pass
									else:
										try: await message.channel.send(embed=discord.Embed(title="This user already had a trial.", color=constants.colors["gray"]))
										except: pass
									try: await message.delete()
									except: pass
									return

								timestamp = (lastTimestamp if time.time() < lastTimestamp else time.time()) + 2635200 * duration
								date = datetime.datetime.utcfromtimestamp(timestamp)
								fetchedSettings["premium"] = {"subscribed": True, "hadTrial": hadTrial or trial, "hadWarning": False, "timestamp": timestamp, "date": date.strftime("%m. %d. %Y"), "plan": plan}
								fetchedSettingsRef.set(fetchedSettings, merge=True)
								self.userProperties[userId] = copy.deepcopy(fetchedSettings)
								self.subscribedUsers[userId] = plan if plan != 0 else 3

								try: await self.alphaServer.get_member(userId).add_roles(self.premiumRoles[0], self.premiumRoles[plan])
								except: pass

								if plan != 0:
									if wasSubscribed:
										embed = discord.Embed(title="Your Alpha Premium subscription was extended. Current expiry date: {}".format(fetchedSettings["premium"]["date"]), color=constants.colors["pink"])
										try: await recepient.send(embed=embed)
										except: await client.get_channel(595954290409865226).send(content="<@!{}>".format(userId), embed=embed)
									else:
										embed = discord.Embed(title="Enjoy your Alpha Premium subscription. Current expiry date: {}".format(fetchedSettings["premium"]["date"]), color=constants.colors["pink"])
										try: await recepient.send(embed=embed)
										except: await client.get_channel(595954290409865226).send(content="<@!{}>".format(userId), embed=embed)
									await client.get_channel(606035811087155200).send(embed=discord.Embed(title="User {} (id: {}) subscribed to Alpha Premium until {}".format(str(recepient), userId, fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
								else:
									await client.get_channel(606035811087155200).send(embed=discord.Embed(title="User {} (id: {}) was given Alpha Premium with no end date".format(str(recepient), userId), color=constants.colors["pink"]))

								await message.delete()
						return
					elif command.startswith("premium server"):
						subscription = messageRequest.content.split("premium server ", 1)
						if len(subscription) == 2:
							parameters = subscription[1].split(" ", 2)
							if len(parameters) == 3:
								if parameters[2] == "trial": guildId, plan, duration, trial = int(parameters[0]), int(parameters[1]), 1, True
								else: guildId, plan, duration, trial = int(parameters[0]), int(parameters[1]), int(parameters[2]), False

								setGuild = client.get_guild(guildId)
								if setGuild is None:
									try: await message.channel.send(embed=discord.Embed(title="No servers with this id found", color=constants.colors["gray"]))
									except: pass
									return

								fetchedSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
								fetchedSettings = fetchedSettingsRef.get().to_dict()
								fetchedSettings = Utils.createServerSetting(fetchedSettings)

								hadTrial = fetchedSettings["premium"]["hadTrial"]
								wasSubscribed = fetchedSettings["premium"]["subscribed"]
								lastTimestamp = fetchedSettings["premium"]["timestamp"]

								if hadTrial and trial:
									if wasSubscribed:
										try: await message.channel.send(embed=discord.Embed(title="This server already has the trial.", color=constants.colors["gray"]))
										except: pass
									else:
										try: await message.channel.send(embed=discord.Embed(title="This server already had a trial.", color=constants.colors["gray"]))
										except: pass
									try: await message.delete()
									except: pass
									return

								timestamp = (lastTimestamp if time.time() < lastTimestamp else time.time()) + 2635200 * duration
								date = datetime.datetime.utcfromtimestamp(timestamp)
								fetchedSettings["premium"] = {"subscribed": True, "hadTrial": hadTrial or trial, "hadWarning": False, "timestamp": timestamp, "date": date.strftime("%m. %d. %Y"), "plan": plan}
								fetchedSettingsRef.set(fetchedSettings, merge=True)
								self.guildProperties[guildId] = copy.deepcopy(fetchedSettings)
								self.subscribedGuilds[guildId] = plan if plan != 0 else 3

								recepients = []
								for member in setGuild.members:
									if member.guild_permissions.administrator:
										recepients.append(member)

								if plan > 0:
									if wasSubscribed:
										embed = discord.Embed(title="Alpha Premium subscription for {} server was extended. Current expiry date: {}".format(setGuild.name, fetchedSettings["premium"]["date"]), color=constants.colors["pink"])
										try:
											for recepient in recepients: await recepient.send(embed=embed)
										except: await client.get_channel(595954290409865226).send(content=", ".join(["<@!{}>".format(e.id) for e in recepients]), embed=embed)
									else:
										embed = discord.Embed(title="Enjoy Alpha Premium subscription for {} server. Current expiry date: {}".format(setGuild.name, fetchedSettings["premium"]["date"]), color=constants.colors["pink"])
										try:
											for recepient in recepients: await recepient.send(embed=embed)
										except: await client.get_channel(595954290409865226).send(content=", ".join(["<@!{}>".format(e.id) for e in recepients]), embed=embed)
									await client.get_channel(606035811087155200).send(embed=discord.Embed(title="{} server (id: {}) subscribed to Alpha Premium until {}".format(setGuild.name, guildId, fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
								else:
									await client.get_channel(606035811087155200).send(embed=discord.Embed(title="{} server (id: {}) was given Alpha Premium with no end date".format(setGuild.name, guildId), color=constants.colors["pink"]))

								await message.delete()
						return
					elif command == "restart":
						self.isBotReady = False
						try:
							await message.delete()
							alphaMessage = await client.get_channel(560884869899485233).fetch_message(640502830062632960)
							await alphaMessage.edit(embed=discord.Embed(title=":warning: Alpha: restarting", color=constants.colors["gray"]))
						except: pass
						l.log("Alerts", "A restart has been requested by {} at {}".format(message.author.name, Utils.get_current_date()), color=0x9C27B0)
						raise KeyboardInterrupt
					elif command == "reboot":
						self.isBotReady = False
						try:
							await message.delete()
							alphaMessage = await client.get_channel(560884869899485233).fetch_message(640502830062632960)
							await alphaMessage.edit(embed=discord.Embed(title=":warning: Alpha: restarting", color=constants.colors["gray"]))
						except: pass
						l.log("Alerts", "A reboot has been requested by {} at {}".format(message.author.name, Utils.get_current_date()), color=0xE91E63)
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
						await self.fusion.process_private_function(client, message, messageRequest, CoinParser.exchanges, CoinParser.lastBitcoinPrice, db)
						return
			elif not isSelf and isCommand and hasSendPermission:
				if messageRequest.content.startswith(("alpha ", "alpha, ", "@alpha ", "@alpha, ")):
					self.fusion.process_active_user(messageRequest.authorId, "alpha")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					self.statistics["alpha"] += 1
					rawCaps = " ".join(message.clean_content.split()).split(" ", 1)[1]
					if len(rawCaps) > 500: return
					if (self.guildProperties[messageRequest.guildId]["settings"]["assistant"] if messageRequest.guildId in self.guildProperties else True):
						try: await message.channel.trigger_typing()
						except: pass
					fallThrough, response = self.assistant.process_reply(messageRequest.content, rawCaps, self.guildProperties[messageRequest.guildId]["settings"]["assistant"] if messageRequest.guildId in self.guildProperties else True)
					if fallThrough:
						if response == "help":
							await self.help(message, messageRequest)
						elif response == "premium":
							try: await message.channel.send(content="Join our server to learn more: https://discord.gg/H9sS6WK" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more.")
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						elif response == "invite":
							try: await message.channel.send(embed=discord.Embed(title="https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372033", color=constants.colors["deep purple"]))
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						elif response == "status":
							req = urllib.request.Request("https://status.discordapp.com", headers={"User-Agent": "Mozilla/5.0"})
							webpage = str(urllib.request.urlopen(req).read())
							isDiscordWorking = "All Systems Operational" in webpage
							try: await message.channel.send(embed=discord.Embed(title=":bellhop: Average ping: {:,.1f} milliseconds\n:satellite: Processing {:,.0f} messages per minute\n:signal_strength: Discord: {}".format(self.fusion.averagePing * 1000, self.fusion.averageMessages, "all systems operational" if isDiscordWorking else "degraded performance"), color=constants.colors["deep purple" if isDiscordWorking else "gray"]))
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						elif response == "vote":
							try: await message.channel.send(embed=discord.Embed(title="https://top.gg/bot/401328409499664394/vote", color=constants.colors["deep purple"]))
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						elif response == "referrals":
							embed = discord.Embed(title="Alpha referral links", color=constants.colors["deep purple"])
							embed.add_field(name="Binance", value="Get 10% kickback on all commissions when trading on Binance by [signing up here](https://www.binance.com/en/register?ref=PJF2KLMW)", inline=False)
							embed.add_field(name="Bitmex", value="Get 10% fee discount for the first 6 months when trading on BitMEX by [signing up here](https://www.bitmex.com/register/Cz9JxF)", inline=False)
							embed.add_field(name="TradingView", value="Get $30 after purchasing a paid plan on TradingView by [signing up here](https://www.tradingview.com/gopro/?share_your_love=AlphaBotSystem)", inline=False)
							embed.add_field(name="FTX", value="Get a 5% fee discount on all your trades on FTX until Jan 1st 2020 by [signing up here](https://ftx.com/#a=Alpha)", inline=False)
							embed.add_field(name="Coinbase", value="Get $13 on Coinbase after [signing up here](https://www.coinbase.com/join/conrad_78)", inline=False)
							embed.add_field(name="Deribit", value="Get 10% fee discount for the first 6 months when trading on Deribit by [signing up here](https://www.deribit.com/reg-8980.6502)", inline=False)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						elif response == "setup":
							await self.setup(message, messageRequest)
					elif response is not None:
						try: await message.channel.send(content=response)
						except: pass
				elif messageRequest.content.startswith(("alert ", "alerts ")):
					self.fusion.process_active_user(messageRequest.authorId, "alerts")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest, override=True): return

					if messageRequest.content in ["alert help", "alerts help"]:
						embed = discord.Embed(title=":bell: Price Alerts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/price-alerts/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Adding price alerts", value="```alert set <coin> <exchange> <price>```", inline=False)
						embed.add_field(name=":page_with_curl: Listing all your currently set alerts", value="```alert list```Alerts can be deleted by clicking the red cross emoji below each alert.", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"alert help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						if messageRequest.is_bronze():
							requestSlices = re.split(", alert | alert |, alerts | alerts |, ", messageRequest.content.split(" ", 1)[1])
							if len(requestSlices) > messageRequest.get_limit() / 2:
								await self.hold_up(message, messageRequest)
								return
							for requestSlice in requestSlices:
								await self.alert(message, messageRequest, requestSlice)
								self.statistics["alerts"] += 1
							await self.support_message(message, "alerts")
						else:
							try: await message.channel.send(content="Price alerts are available to premium members only. {}".format("Join our server to learn more: https://discord.gg/GQeDE85" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more."))
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				elif messageRequest.content.startswith("preset "):
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest, override=True): return

					if messageRequest.content == "preset help":
						embed = discord.Embed(title=":pushpin: Command Presets", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/command-presets/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Adding presets", value="```preset add <name> <command>```A preset can be called by typing its name in the chat.", inline=False)
						embed.add_field(name=":page_with_curl: Listing all your presets", value="```preset list```Presets can be deleted by clicking the red cross emoji below each preset.", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"preset help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						if messageRequest.is_bronze():
							requestSlices = re.split(", preset | preset", messageRequest.content.split(" ", 1)[1])
							if len(requestSlices) > messageRequest.get_limit() / 2:
								await self.hold_up(message, messageRequest)
								return
							for requestSlice in requestSlices:
								await self.presets(message, messageRequest, requestSlice)
							await self.support_message(message, "preset")
						else:
							try: await message.channel.send(content="Presets are available to premium members only. {}".format("Join our server to learn more: https://discord.gg/GQeDE85" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more."))
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				elif messageRequest.content.startswith("c "):
					self.fusion.process_active_user(messageRequest.authorId, "c")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "c help":
						embed = discord.Embed(title=":chart_with_upwards_trend: Charts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/charts/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```c <ticker id> <exchange> <timeframe(s)> <candle type> <indicators>```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"c help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					elif messageRequest.content == "c parameters":
						availableIndicators = [
							"NV *(no volume)*", "ACCD *(Accumulation/Distribution)*", "ADR", "Aroon", "ATR", "Awesome *(Awesome Oscillator)*", "BB", "BBW", "CMF", "Chaikin *(Chaikin Oscillator)*", "Chande *(Chande Momentum Oscillator)*", "CI *(Choppiness Index)*", "CCI", "CRSI", "CC *(Correlation Coefficient)*", "DPO", "DM", "DONCH *(Donchian Channels)*", "DEMA", "EOM", "EFI", "EW *(Elliott Wave)*", "ENV *(Envelope)*", "Fisher *(Fisher Transform)*", "HV *(Historical Volatility)*", "HMA", "Ichimoku", "Keltner *(Keltner Channels)*", "KST", "LR *(Linear Regression)*", "MACD", "MOM", "MFI", "Moon *(Moon Phases)*", "MA", "EMA", "WMA", "OBV", "PSAR", "PPHL *(Pivot Points High Low)*", "PPS *(Pivot Points Standard)*", "PO *(Price Oscillator)*", "PVT", "ROC", "RSI", "RVI *(Relative Vigor Index)*", "VI (volatility index)", "SMIEI *(SMI Ergodic Indicator)*", "SMIEO *(SMI Ergodic Oscillator)*", "Stoch", "SRSI *(Stochastic RSI)*", "TEMA *(Triple EMA)*", "TRIX", "Ultimate *(Ultimate Oscillator)*", "VSTOP *(Volatility Stop)*", "VWAP", "VWMA", "WilliamsR", "WilliamsA *(Williams Alligator)*", "WF *(Williams Fractal)*", "ZZ *(Zig Zag)*"
						]
						embed = discord.Embed(title=":chains: Chart parameters", description="All available chart parameters you can use.", color=constants.colors["light blue"])
						embed.add_field(name=":bar_chart: Indicators", value="{}".format(", ".join(availableIndicators)), inline=False)
						embed.add_field(name=":control_knobs: Timeframes", value="1/3/5/15/30-minute, 1/2/3/4-hour, Daily, Weekly and Monthly", inline=False)
						embed.add_field(name=":scales: Exchanges", value=", ".join([(CoinParser.exchanges[e].name if e in CoinParser.exchanges else e.title()) for e in constants.supportedExchanges["TradingView"]]), inline=False)
						embed.add_field(name=":chart_with_downwards_trend: Candle types", value="Bars, Candles, Heikin Ashi, Line Break, Line, Area, Renko, Kagi, Point&Figure", inline=False)
						embed.add_field(name=":gear: Other parameters", value="Shorts, Longs, Log, White, Link", inline=False)
						embed.set_footer(text="Use \"c parameters\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						requestSlices = re.split(", c | c |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 2
							else: self.rateLimited["u"][messageRequest.authorId] = 2

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("am "): platform, requestSlice = "Alternative.me", requestSlice[3:]
								elif requestSlice.startswith("wc "): platform, requestSlice = "Woobull Charts", requestSlice[3:]
								elif requestSlice.startswith("tl "): platform, requestSlice = "TradingLite", requestSlice[3:]
								elif requestSlice.startswith("tv "): platform, requestSlice = "TradingView", requestSlice[3:]
								elif requestSlice.startswith("fv "): platform, requestSlice = "Finviz", requestSlice[3:]

								chartMessages, weight = await self.chart(message, messageRequest, requestSlice, platform)

								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += weight - 2
								else: self.rateLimited["u"][messageRequest.authorId] = weight - 2
						await self.support_message(message, "c")

						self.statistics["c"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("p "):
					self.fusion.process_active_user(messageRequest.authorId, "p")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "p help":
						embed = discord.Embed(title=":money_with_wings: Prices", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/prices/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```p <ticker id> <exchange>```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"p help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					elif messageRequest.content not in ["p "]:
						requestSlices = re.split(", p | p |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 1
							else: self.rateLimited["u"][messageRequest.authorId] = 1

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								await self.price(message, messageRequest, requestSlice)
						await self.support_message(message, "p")

						self.statistics["p"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("v "):
					self.fusion.process_active_user(messageRequest.authorId, "v")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "v help":
						embed = discord.Embed(title=":credit_card: 24-hour Rolling Volume", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/rolling-volume/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```v <ticker id> <exchange>```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"v help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					elif messageRequest.content not in ["v "]:
						requestSlices = re.split(", v | v |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 1
							else: self.rateLimited["u"][messageRequest.authorId] = 1

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								await self.volume(message, messageRequest, requestSlice)
						await self.support_message(message, "v")

						self.statistics["v"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("d "):
					self.fusion.process_active_user(messageRequest.authorId, "d")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "d help":
						embed = discord.Embed(title=":book: Orderbook Visualizations", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/orderbook-visualizations/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```d <ticker id> <exchange>```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"d help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					elif messageRequest.content not in ["d "]:
						requestSlices = re.split(", d | d |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 2
							else: self.rateLimited["u"][messageRequest.authorId] = 2

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								chartMessages, weight = await self.depth(message, messageRequest, requestSlice)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += weight - 2
								else: self.rateLimited["u"][messageRequest.authorId] = weight - 2
						await self.support_message(message, "d")

						self.statistics["d"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("hmap "):
					self.fusion.process_active_user(messageRequest.authorId, "hmap")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "hmap help":
						embed = discord.Embed(title=":fire: Heat Maps", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/heat-maps/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```hmap <type> <filters> <period>```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"hmap help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					elif messageRequest.content == "hmap parameters":
						availableCategories = [
							"Crypto (Cryptocurrency)", "Blockchain (Blockchain Platforms)", "Commerce (Commerce & Advertising)", "Commodities (Commodities)", "Content (Content Management)", "Ai (Data Storage/Analytics & Ai)", "Healthcare (Drugs & Healthcare)", "Energy (Energy & Utilities)", "Events (Events & Entertainment)", "Financial (Financial Services)", "Gambling (Gambling & Betting)", "Gaming (Gaming & Vr)", "Identy (Identy & Reputation)", "Legal (Legal)", "Estate (Real Estate)", "Social (Social Network)", "Software (Software)", "Logistics (Supply & Logistics)", "Trading (Trading & Investing)",
						]
						embed = discord.Embed(title=":chains: Heat map parameters", description="All available heat map parameters you can use.", color=constants.colors["light blue"])
						embed.add_field(name=":control_knobs: Timeframes", value="15-minute, 1-hour, Daily, Weekly, 1/3/6-month and 1-year", inline=False)
						embed.add_field(name=":scales: Filters", value="Top10, Top100, Tokens, Coins, Gainers, Loosers", inline=False)
						embed.add_field(name=":bar_chart: Categories", value="{}".format(", ".join(availableCategories)), inline=False)
						embed.set_footer(text="Use \"hmap parameters\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						requestSlices = re.split(", hmap | hmap |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 2
							else: self.rateLimited["u"][messageRequest.authorId] = 2

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("bg "): platform, requestSlice = "Bitgur", requestSlice[3:]
								elif requestSlice.startswith("tl "): platform, requestSlice = "TradingLite", requestSlice[3:]
								elif requestSlice.startswith("fv "): platform, requestSlice = "Finviz", requestSlice[3:]

								chartMessages, weight = await self.heatmap(message, messageRequest, requestSlice, platform)

								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 2 - weight
								else: self.rateLimited["u"][messageRequest.authorId] = 2 - weight
						await self.support_message(message, "hmap")

						self.statistics["hmap"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith(("mcap ", "mc ")):
					self.fusion.process_active_user(messageRequest.authorId, "mcap")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content in ["mcap help", "mc help"]:
						embed = discord.Embed(title=":tools: Cryptocurrency details", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/cryptocurrency-details/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```mcap/mc <ticker id>```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"mc help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						requestSlices = re.split(", mcap | mcap |, mc | mc |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 1
							else: self.rateLimited["u"][messageRequest.authorId] = 1

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								await self.mcap(message, messageRequest, requestSlice)
						await self.support_message(message, "mcap")

						self.statistics["mcap"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("n ") and messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
					self.fusion.process_active_user(messageRequest.authorId, "n")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "n help":
						embed = discord.Embed(title=":newspaper: News", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/news/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```n <ticker id>```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"n help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					elif messageRequest.content == "n parameters":
						embed = discord.Embed(title=":chains: News parameters", description="All available news parameters you can use.", color=constants.colors["light blue"])
						embed.add_field(name=":scales: Filters", value="General, AMA, Announcement, Airdrop, Brand, Burn, Conference, Contest, Exchange, Hard fork, ICO, Regulation, Meetup, Partnership, Release, Soft fork, Swap, Test, Update, Report", inline=False)
						embed.set_footer(text="Use \"n parameters\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						requestSlices = re.split(", n | n |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 1
							else: self.rateLimited["u"][messageRequest.authorId] = 1

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								break
							else:
								await self.news(message, messageRequest, requestSlice)
						await self.support_message(message, "n")

						self.statistics["news"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("mk "):
					self.fusion.process_active_user(messageRequest.authorId, "mk")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "mk help":
						embed = discord.Embed(title=":page_facing_up: Market listings", description="A command for pulling a list of exchanges listing a particular market ticker.", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```mk <coin>```", inline=False)
						embed.add_field(name=":books: Examples", value="● `mk btc` will list all exchanges listing BTCUSD market pair.\n● `mk ada` will list all exchanges listing ADABTC market pair.", inline=False)
						embed.set_footer(text="Use \"mk help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						requestSlices = re.split(", mk | mk |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 1
							else: self.rateLimited["u"][messageRequest.authorId] = 1

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								await self.markets(message, messageRequest, requestSlice)
						await self.support_message(message, "mk")

						self.statistics["mk"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("convert "):
					self.fusion.process_active_user(messageRequest.authorId, "convert")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "convert help":
						embed = discord.Embed(title=":yen: Cryptocurrency Conversions", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/cryptocurrency-conversions/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Syntax", value="```convert <amount> <from> [to, in...] <to>```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"convert help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						requestSlices = re.split(", convert | convert |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 1
							else: self.rateLimited["u"][messageRequest.authorId] = 1

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								try: await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a minute.", color=constants.colors["gray"]))
								except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								await self.convert(message, messageRequest, requestSlice)
						await self.support_message(message, "convert")

						self.statistics["convert"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("paper ") and message.channel.id in [611107823111372810, 479927662337458176]:
					self.fusion.process_active_user(messageRequest.authorId, "paper")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "paper help":
						embed = discord.Embed(title=":joystick: Paper trader", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/paper-trader/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Placing orders", value="```paper <order type> <coin> <exchange> <amount>/<amount@price>```If no price is provided, market price will be used. Both amount and price can be specified in percentages.", inline=False)
						embed.add_field(name=":moneybag: Checking your available paper balance", value="```paper balance <exchange> [all]```If no exchange is provided, all will be listed. Paper balances smaller than 0.001 BTC are hidden by defauly. Use `all` parameter to show all available balances.", inline=False)
						embed.add_field(name=":package: Checking your trading history", value="```paper history <exchange> [all]```Only the last 50 trades are saved.", inline=False)
						embed.add_field(name=":outbox_tray: Checking your open or pending orders", value="```paper orders <exchange> [all]```", inline=False)
						embed.add_field(name=":track_previous: Reset your paper trader balances", value="```paper reset```You can only reset your paper trader balances once every seven days.", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"paper help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					elif messageRequest.content == "paper leaderboard":
						await self.fetch_leaderboard(message, messageRequest)
					elif messageRequest.content.startswith(("paper balance", "paper bal")):
						await self.fetch_paper_balance(message, messageRequest)
					elif messageRequest.content.startswith("paper history"):
						await self.fetch_paper_orders(message, messageRequest, "history")
					elif messageRequest.content.startswith("paper orders"):
						await self.fetch_paper_orders(message, messageRequest, "open_orders")
					elif messageRequest.content.startswith("paper reset"):
						await self.reset_paper_balance(message, messageRequest)
					else:
						requestSlices = re.split(', paper | paper |, ', messageRequest.content.split(" ", 1)[1])
						for requestSlice in requestSlices:
							await self.process_paper_trade(message, messageRequest, requestSlice)
						await self.support_message(message, "paper")
				elif messageRequest.content.startswith("stream ") and messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "stream help":
						embed = discord.Embed(title=":abacus: Data Streams", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/data-streams/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Stream setup", value="```stream set <type>```", inline=False)
						embed.add_field(name=":pencil2: Delete data stream", value="```stream delete```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"convert help\" to pull up this list again.")
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						if messageRequest.is_bronze_server():
							requestSlices = re.split(", stream | stream |, ", messageRequest.content.split(" ", 1)[1])
							if len(requestSlices) > messageRequest.get_limit() / 2:
								await self.hold_up(message, messageRequest)
								return
							for requestSlice in requestSlices:
								await self.data_stream(message, messageRequest, requestSlice)
								self.statistics["alerts"] += 1
							await self.support_message(message, "alerts")
						else:
							try: await message.channel.send(content="Data streams are available to premium servers only. {}".format("Join our server to learn more: https://discord.gg/GQeDE85" if message.guild.id != 414498292655980583 else "Check <#509428086979297320> or <#560475744258490369> to learn more."))
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif messageRequest.content == "brekkeven" and messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
				self.fusion.process_active_user(messageRequest.authorId, "brekkeven")
				if message.author.bot:
					if not await self.bot_verification(message, messageRequest, override=True): return

				await self.brekkeven(message, messageRequest)
				await self.support_message(message)
			else:
				self.fusion.calculate_average_ping(message, client.cached_messages)
				if await self.fusion.invite_warning(message, messageRequest): return
				if (self.guildProperties[messageRequest.guildId]["settings"]["assistant"] if messageRequest.guildId in self.guildProperties else True):
					response = self.assistant.funnyReplies(messageRequest.content)
					if response is not None:
						self.statistics["alpha"] += 1
						try: message.channel.send(content=response)
						except: pass
					return
				if not any(e in messageRequest.content for e in constants.mutedMentionWords) and not message.author.bot and any(e in messageRequest.content for e in constants.mentionWords) and messageRequest.guildId not in [414498292655980583, -1]:
					mentionMessage = "{}/{}: {}".format(message.guild.name, message.channel.name, message.clean_content)
					threading.Thread(target=self.fusion.webhook_send, args=(ApiKeys.get_log_webhook(mode="mentions"), mentionMessage, "{}#{}".format(message.author.name, message.author.discriminator), message.author.avatar_url, False, message.attachments, message.embeds)).start()
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			self.rateLimited = {"c": {}, "p": {}, "d": {}, "v": {}, "u": {}}
			for side in constants.supportedExchanges:
				for id in constants.supportedExchanges[side]:
					if id not in self.rateLimited["p"] or id not in self.rateLimited["d"] or id not in self.rateLimited["v"]:
						self.rateLimited["p"][id] = {}
						self.rateLimited["d"][id] = {}
						self.rateLimited["v"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, message.clean_content))

	async def unknown_error(self, message, authorId, e, report=False):
		if sys.platform != "linux" and not report:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			print("[Quiet Error]: debug info: {}, {}, line {}, description: {}".format(exc_type, fname, exc_tb.tb_lineno, e))
		embed = discord.Embed(title="Looks like something went wrong.{}".format(" The issue was reported." if report else ""), color=constants.colors["gray"])
		embed.set_author(name="Something went wrong", icon_url=firebase_storage.icon_bw)
		try: await message.channel.send(embed=embed)
		except: pass

	async def on_reaction_add(self, reaction, user):
		if user.id in [487714342301859854, 401328409499664394]: return
		if reaction.message.author.id in [487714342301859854, 401328409499664394]:
			try: users = await reaction.users().flatten()
			except: return
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
													await self.unknown_error(message, messageRequest.authorId, e)

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

	async def finish_request(self, message, messageRequest, weight, sentMessages):
		await asyncio.sleep(60)
		if messageRequest.authorId in self.rateLimited["u"]:
			self.rateLimited["u"][messageRequest.authorId] -= weight
			if self.rateLimited["u"][messageRequest.authorId] < 1: self.rateLimited["u"].pop(messageRequest.authorId, None)

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

	async def bot_verification(self, message, messageRequest, override=False):
		if override: return False
		if message.webhook_id is not None:
			if message.webhook_id not in constants.verifiedWebhooks:
				if not messageRequest.is_muted() and message.guild.id != 414498292655980583:
					embed = discord.Embed(title="{} webhook is not verified with Alpha. To get it verified, please join Alpha server: https://discord.gg/GQeDE85".format(message.author.name), color=constants.colors["pink"])
					embed.set_author(name="Unverified webhook", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except: pass
				return False
		else:
			if message.author.id not in constants.verifiedBots:
				if not messageRequest.is_muted() and message.guild.id != 414498292655980583:
					embed = discord.Embed(title="{}#{} bot is not verified with Alpha. To get it verified, please join Alpha server: https://discord.gg/GQeDE85".format(message.author.name, message.author.discriminator), color=constants.colors["pink"])
					embed.set_author(name="Unverified bot", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except: pass
				return False
		history.info("{} ({}): {}".format(Utils.get_current_date(), messageRequest.authorId, messageRequest.content))
		return True

	async def help(self, message, messageRequest):
		embed = discord.Embed(title=":wave: Introduction", description="Alpha bot is the world's most popular Discord bot for requesting charts, set price alerts, and more. Using Alpha is as simple as typing a short command into any Discord channel Alpha has access to. A full guide is available on (our website)[https://www.alphabotsystem.com/alpha-bot/features/]", color=constants.colors["light blue"])
		embed.add_field(name=":chart_with_upwards_trend: Charts", value="Easy access to TradingView charts.\nType `c help` to learn more.", inline=False)
		embed.add_field(name=":bell: Alerts", value="Setup price alerts for select crypto exchanges.\nType `alert help` to learn more.", inline=False)
		embed.add_field(name=":money_with_wings: Prices", value="Current cryptocurrency prices for thousands of tickers.\nType `p help` to learn more.", inline=False)
		embed.add_field(name=":book: Orderbook visualizations", value="Orderbook snapshot charts of crypto market pairs.\nType `d help` to learn more.", inline=False)
		embed.add_field(name=":credit_card: 24-hour rolling volume", value="Request 24-hour rolling volume for virtually any crypto market pair.\nType `v help` to learn more.", inline=False)
		embed.add_field(name=":tools: Cryptocurrency coin details", value="Detailed coin information from CoinGecko.\nType `mcap help` to learn more.", inline=False)
		# embed.add_field(name=":newspaper: News", value="See latest news and upcoming events in the crypto space.\nType `n help` to learn mode.", inline=False)
		embed.add_field(name=":yen: Cryptocurrency conversions", value="An easy way to convert between different currencies or units.\nType `convert help` to learn more.", inline=False)
		embed.add_field(name=":fire: Heat maps", value="Check various heat maps from Bitgur.\nType `hmap help` to learn more.", inline=False)
		embed.add_field(name=":pushpin: Command presets", value="Create personal presets for easy access to things you use most.\nType `preset help` to learn more.", inline=False)
		embed.add_field(name=":crystal_ball: Assistant", value="Pull up Wikipedia articles, calculate math problems and get answers to many other question. Start a message with `alpha` and continue with your question.", inline=False)
		embed.add_field(name=":link: Official Alpha website", value="[alphabotsystem.com](https://www.alphabotsystem.com)", inline=True)
		embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
		embed.add_field(name=":link: Official Alpha Twitter", value="[@AlphaBotSystem](https://twitter.com/AlphaBotSystem)", inline=True)
		embed.set_footer(text="Use \"a help\" to pull up this list again.")
		try:
			if messageRequest.shortcutUsed:
				try: await message.author.send(embed=embed)
				except: await message.channel.send(embed=embed)
			else:
				await message.channel.send(embed=embed)
		except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

	async def support_message(self, message, command=None):
		if random.randint(0, 25) == 1:
			c = command
			while c == command: c, textSet = random.choice(list(constants.supportMessages.items()))
			embed = discord.Embed(title=random.choice(textSet), color=constants.colors["light blue"])
			try: await message.channel.send(embed=embed)
			except: pass

	async def hold_up(self, message, messageRequest):
		embed = discord.Embed(title="Only up to {:d} requests are allowed per command.".format(int(messageRequest.get_limit() / 2)), color=constants.colors["gray"])
		embed.set_author(name="Too many requests", icon_url=firebase_storage.icon_bw)
		try: await message.channel.send(embed=embed)
		except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

	async def setup(self, message, messageRequest):
		try:
			embed = discord.Embed(title=":wrench: Setup", color=constants.colors["pink"])
			embed.add_field(name=":scroll: Terms of service", value="By using Alpha, you agree to current Alpha ToS ([available here](https://www.alphabotsystem.com/terms-of-service/)) and every potential upcoming new version. For important updates, please join the [official Alpha server](https://discord.gg/GQeDE85).", inline=False)
			embed.add_field(name=":eye: Access", value="Alpha has access to {} channels. For a complete list, type `a channel list`. If you don't intend on using the bot in some of the channels, restrict its access by disabling its *read messages* permission. All messages flowing through those channels are processed, but not stored nor analyzed for sentiment, trade or similar data. Alpha stores anonymous statistical information. For transparency, our message handling system is [open-source](https://github.com/alphabotsystem/Alpha).".format(len(message.guild.channels)), inline=False)
			embed.add_field(name=":satellite: Dedicated channels", value="You can dedicate specific channels for specific features.", inline=False)
			embed.add_field(name=":control_knobs: Functionality settings", value="You can enable or disable certain Alpha features.", inline=False)
			try: await message.channel.send(embed=embed)
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, messageRequest.content))

	async def alert(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")
			method = arguments[0]

			if method in ["set", "create", "add"]:
				if len(arguments) >= 3:
					tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker_depricated(arguments[1].upper(), "alerts")
					if isAggregatedSymbol:
						if not messageRequest.is_muted():
							embed = discord.Embed(title="Aggregated tickers aren't supported with the `alert` command", color=constants.colors["gray"])
							embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

					outputMessage, tickerId, arguments = self.alerts.process_alert_arguments(arguments, tickerId, exchange)
					if outputMessage is not None:
						if not messageRequest.is_muted():
							embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return
					exchange, action, level, repeat = arguments

					outputMessage, details = self.coinParser.find_market_pair_depricated(tickerId, exchange, "alerts")
					if outputMessage is not None:
						if not messageRequest.is_muted():
							embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
							embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

					symbol, base, quote, marketPair, exchange = details

					try: await message.channel.trigger_typing()
					except: pass

					alertsRef = db.document(u"alpha/alerts/{}/{}".format(exchange, messageRequest.authorId))
					fetchedAlerts = alertsRef.get().to_dict()
					if fetchedAlerts is None: fetchedAlerts = {}

					sum = 0
					for key in fetchedAlerts: sum += len(fetchedAlerts[key])

					if sum >= 10:
						embed = discord.Embed(title="Only 10 alerts per exchange are allowed", color=constants.colors["gray"])
						embed.set_author(name="Maximum number of alerts reached", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

					key = symbol.replace("/", "-")
					newAlert = {
						"id": "%013x" % random.randrange(10**15),
						"timestamp": time.time(),
						"time": Utils.get_current_date(),
						"channel": messageRequest.authorId,
						"action": action,
						"level": level,
						"repeat": repeat
					}
					levelText = Utils.format_price(CoinParser.exchanges[exchange], symbol, level)

					if key not in fetchedAlerts: fetchedAlerts[key] = []
					for alert in fetchedAlerts[key]:
						if alert["action"] == action and alert["level"] == level:
							embed = discord.Embed(title="{} alert for {} ({}) at {} {} already exists.".format(action.title(), base, CoinParser.exchanges[exchange].name, levelText, quote), color=constants.colors["gray"])
							embed.set_author(name="Alert already exists", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
							return

					fetchedAlerts[key].append(newAlert)

					try:
						batch = db.batch()
						batch.set(alertsRef, fetchedAlerts, merge=True)
						for i in range(1, self.fusion.numInstances + 1):
							batch.set(db.document(u'fusion/instance-{}'.format(i)), {"needsUpdate": True}, merge=True)
						batch.commit()
					except:
						await self.unknown_error(message, messageRequest.authorId, e)
						return

					embed = discord.Embed(title="{} alert set for {} ({}) at {} {}.".format(action.title(), base, CoinParser.exchanges[exchange].name, levelText, quote), description=(None if messageRequest.authorId in self.alphaServerMembers else "Alpha will be unable to deliver this alert in case your DMs are disabled. Please join Alpha Discord server for a guaranteed delivery: https://discord.gg/GQeDE85"), color=constants.colors["deep purple"])
					embed.set_author(name="Alert successfully set", icon_url=firebase_storage.icon)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				else:
					embed = discord.Embed(title="Invalid command usage. Type `alert help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
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
								if int(user.id) == messageRequest.authorId:
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
									base = CoinParser.exchanges[exchange].markets[symbol]["base"]
									quote = CoinParser.exchanges[exchange].markets[symbol]["quote"]
									marketPair = CoinParser.exchanges[exchange].markets[symbol]["id"].replace("_", "").replace("/", "").replace("-", "").upper()
									levelText = Utils.format_price(CoinParser.exchanges[exchange], symbol, alert["level"])

									embed = discord.Embed(title="{} alert set for {} ({}) at {} {}".format(alert["action"].title(), marketPair, CoinParser.exchanges[exchange].name, levelText, quote), color=constants.colors["deep purple"])
									embed.set_footer(text="Alert {}/{} ● (id: {})".format(count, numberOfAlerts, alert["id"]))
									try:
										alertMessage = await message.channel.send(embed=embed)
										await alertMessage.add_reaction('❌')
									except: pass
					else:
						embed = discord.Embed(title="You don't have any alerts set", color=constants.colors["gray"])
						embed.set_author(name="No alerts", icon_url=firebase_storage.icon)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				else:
					embed = discord.Embed(title="Invalid command usage. Type `alert help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def presets(self, message, messageRequest, requestSlice):
		try:
			isServer = requestSlice.startswith("server ") and message.author.guild_permissions.administrator
			offset = 1 if isServer else 0
			arguments = requestSlice.split(" ", 2 + offset)
			method = arguments[0 + offset]

			if method in ["set", "create", "add"]:
				if len(arguments) == 3 + offset:
					try: await message.channel.trigger_typing()
					except: pass

					fetchedSettingsRef = db.document(u"alpha/settings/{}/{}".format("servers" if isServer else "users", messageRequest.guildId if isServer else messageRequest.authorId))
					fetchedSettings = fetchedSettingsRef.get().to_dict()
					fetchedSettings = Utils.createServerSetting(fetchedSettings) if isServer else Utils.createUserSettings(fetchedSettings)
					fetchedSettings, status = Presets.updatePresets(fetchedSettings, add=arguments[1 + offset].replace("`", ""), shortcut=arguments[2 + offset])
					statusTitle, statusMessage, statusColor = status
					fetchedSettingsRef.set(fetchedSettings, merge=True)
					if isServer: self.guildProperties[messageRequest.guildId] = copy.deepcopy(fetchedSettings)
					else: self.userProperties[messageRequest.authorId] = copy.deepcopy(fetchedSettings)

					embed = discord.Embed(title=statusMessage, color=constants.colors[statusColor])
					embed.set_author(name=statusTitle, icon_url=firebase_storage.icon)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif method in ["list", "all"]:
				if len(arguments) == 1 + offset:
					try: await message.channel.trigger_typing()
					except: pass

					hasSettings = messageRequest.guildId in self.guildProperties if isServer else messageRequest.authorId in self.userProperties
					settings = {} if not hasSettings else (self.guildProperties[messageRequest.guildId] if isServer else self.userProperties[messageRequest.authorId])
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
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif len(arguments) <= 3 + offset:
				embed = discord.Embed(title="`{}` is not a valid argument. Type `preset help` to learn more.".format(method), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
				try: await message.channel.send(embed=embed)
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def chart(self, message, messageRequest, requestSlice, platform):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_chart_arguments(messageRequest.authorId, arguments[1:], tickerId=arguments[0].upper(), platform=platform, command="c", defaultPlatforms=["Alternative.me", "Woobull Charts", "TradingLite", "TradingView", "Finviz"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return ([], 0)

			for timeframe in request.get_timeframes():
				try: await message.channel.trigger_typing()
				except: pass

				request.set_current(timeframe=timeframe)
				chartName, chartMessage = await self.imageProcessor.request_chart(messageRequest.authorId, request)

				if chartName is None:
					try:
						errorMessage = "Requested chart for `{}` is not available.".format(request.get_tickerId()) if chartMessage is None else chartMessage
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Chart not available", icon_url=firebase_storage.icon_bw)
						chartMessage = await message.channel.send(embed=embed)
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return (sentMessages, len(sentMessages))

				try:
					embed = discord.Embed(title="{}".format(chartMessage), color=constants.colors["deep purple"])
					chartMessage = await message.channel.send(embed=embed if chartMessage else None, file=discord.File("charts/" + chartName, chartName))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

			self.imageProcessor.clean_cache()
			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			self.rateLimited["c"] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
			return ([], 0)

	async def price(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker_depricated(arguments[0].upper(), "ohlcv", exchangeFallthrough=True)
			if isAggregatedSymbol:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `p` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, hasActions=True, command="p")
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			action, exchange = arguments
			exchangeFallthrough = (tickerId.endswith(("USD", "BTC")) and exchange == "")

			try: await message.channel.trigger_typing()
			except: pass

			outputMessage, details = self.coinParser.find_market_pair_depricated(tickerId, exchange, "ohlcv", exchangeFallthrough=exchangeFallthrough)
			availableOnCoinGecko = (tickerId.lower() if details is None else details[1].lower()) in CoinGecko.coinGeckoIndex
			useFallback = (outputMessage == "Ticker `{}` was not found".format(tickerId) or exchangeFallthrough) and availableOnCoinGecko
			if outputMessage is not None and not useFallback:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			symbol, base, quote, marketPair, exchange = (tickerId, tickerId, "BTC", "{}BTC".format(tickerId), "CoinGecko") if useFallback and details is None else details
			if useFallback: exchange = "CoinGecko"
			coinThumbnail = firebase_storage.icon_bw

			if action == "funding":
				try: sentMessages.append(await message.channel.send(embed=self.exchangeConnection.funding(CoinParser.exchanges[exchange], marketPair, tickerId)))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif action == "oi":
				try: sentMessages.append(await message.channel.send(embed=self.exchangeConnection.open_interest(CoinParser.exchanges[exchange], marketPair, tickerId)))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif action == "premiums":
				try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
				except: pass
				try: sentMessages.append(await message.channel.send(embed=self.coinParser.premiums(marketPair, tickerId, coinThumbnail)))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif action == "ls":
				try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
				except: pass
				try: sentMessages.append(await message.channel.send(embed=self.coinParser.long_short_ratio(CoinParser.exchanges["bitfinex2"], marketPair, tickerId, coinThumbnail, False)))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif action == "sl":
				try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
				except: pass
				try: sentMessages.append(await message.channel.send(embed=self.coinParser.long_short_ratio(CoinParser.exchanges["bitfinex2"], marketPair, tickerId, coinThumbnail, True)))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif action == "dom":
				try: sentMessages.append(await message.channel.send(embed=self.coinGeckoConnection.coin_dominance(base)))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif action == "mcap":
				try: sentMessages.append(await message.channel.send(embed=self.coinGeckoConnection.total_market_cap()))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			elif action == "Alternative.me":
				try: sentMessages.append(await message.channel.send(embed=Alternativeme.fear_greed_index()))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			else:
				if useFallback:
					try:
						cgData = self.coinGeckoConnection.coingecko.get_coin_by_id(id=CoinGecko.coinGeckoIndex[base.lower()]["id"], localization="false", tickers=False, market_data=True, community_data=False, developer_data=False)
					except:
						embed = discord.Embed(title="Price data for {} from CoinGecko isn't available.".format(marketPair), color=constants.colors["gray"])
						embed.set_author(name="Couldn't get price data", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

					quote = quote if quote.lower() in cgData["market_data"]["current_price"] else "BTC"
					percentChange = cgData["market_data"]["price_change_percentage_24h_in_currency"][quote.lower()] if quote.lower() in cgData["market_data"]["price_change_percentage_24h_in_currency"] else 0
					percentChangeText = " *({:+.2f} %)*".format(percentChange)
					embedColor = constants.colors["amber" if percentChange == 0 else ("green" if percentChange > 0 else "red")]
					coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
					priceText = ("{:,.%df}" % (2 if quote == "USD" else 8)).format(cgData["market_data"]["current_price"][quote.lower()])
					usdConversion = None if quote == "USD" else "≈ ${:,.6f}".format(cgData["market_data"]["current_price"]["usd"])
				else:
					tf, limitTimestamp, candleOffset = Utils.get_highest_supported_timeframe(CoinParser.exchanges[exchange], datetime.datetime.now().astimezone(pytz.utc))
					if symbol in self.rateLimited["p"][exchange] and symbol in self.rateLimited["v"][exchange]:
						price = self.rateLimited["p"][exchange][symbol]
						volume = self.rateLimited["v"][exchange][symbol]

						try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
						except: pass
					else:
						try:
							priceData = CoinParser.exchanges[exchange].fetch_ohlcv(symbol, timeframe=tf.lower(), since=limitTimestamp, limit=300)
							if len(priceData) == 0: raise Exception()
						except:
							embed = discord.Embed(title="Price data for {} on {} isn't available.".format(marketPair, CoinParser.exchanges[exchange].name), color=constants.colors["gray"])
							embed.set_author(name="Couldn't get price data", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
							return

						try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
						except: pass

						price = [priceData[-1][4], priceData[0][1]] if len(priceData) < candleOffset else [priceData[-1][4], priceData[-candleOffset][1]]
						volume = sum([candle[5] for candle in priceData if int(candle[0] / 1000) >= int(CoinParser.exchanges[exchange].milliseconds() / 1000) - 86400])
						if exchange == "bitmex" and symbol == "BTC/USD": self.coinParser.lastBitcoinPrice = price[0]
						self.rateLimited["p"][exchange][symbol] = price
						self.rateLimited["v"][exchange][symbol] = volume

					percentChange = 0 if tf == "1m" else (price[0] / price[1]) * 100 - 100
					percentChangeText = "" if tf == "1m" else " *({:+.2f} %)*".format(percentChange)
					embedColor = constants.colors["amber"] if tf == "1m" else constants.colors["amber" if percentChange == 0 else ("green" if percentChange > 0 else "red")]
					priceText = Utils.format_price(CoinParser.exchanges[exchange], symbol, price[0])
					usdConversion = "≈ ${:,.6f}".format(price[0] * self.coinParser.lastBitcoinPrice) if quote == "BTC" else None

				embed = discord.Embed(title="{} {}{}".format(priceText, quote.replace("USDT", "USD").replace("USDC", "USD").replace("TUSD", "USD").replace("USDS", "USD").replace("PAX", "USD"), percentChangeText), description=usdConversion, color=embedColor)
				embed.set_author(name=marketPair, icon_url=coinThumbnail)
				embed.set_footer(text="Price on {}".format("CoinGecko" if exchange == "CoinGecko" else CoinParser.exchanges[exchange].name))
				try: sentMessages.append(await message.channel.send(embed=embed))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

				if not useFallback: threading.Thread(target=self.clear_rate_limit_cache, args=(exchange, symbol, ["p", "v"], CoinParser.exchanges[exchange].rateLimit / 1000)).start()
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["p"][id] = {}
				self.rateLimited["v"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def volume(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker_depricated(arguments[0].upper(), "ohlcv", exchangeFallthrough=True)
			if isAggregatedSymbol:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `v` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, command="v")
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			_, exchange = arguments
			exchangeFallthrough = (tickerId.endswith(("USD", "BTC")) and exchange == "")

			try: await message.channel.trigger_typing()
			except: pass

			outputMessage, details = self.coinParser.find_market_pair_depricated(tickerId, exchange, "ohlcv", exchangeFallthrough=exchangeFallthrough)
			availableOnCoinGecko = (tickerId.lower() if details is None else details[1].lower()) in CoinGecko.coinGeckoIndex
			useFallback = (outputMessage == "Ticker `{}` was not found".format(tickerId) or exchangeFallthrough) and availableOnCoinGecko
			if outputMessage is not None and not useFallback:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			symbol, base, quote, marketPair, exchange = (tickerId, tickerId, "BTC", "{}BTC".format(tickerId), "CoinGecko") if useFallback and details is None else details
			if useFallback: exchange = "CoinGecko"
			coinThumbnail = firebase_storage.icon_bw

			if useFallback:
				try:
					cgData = self.coinGeckoConnection.coingecko.get_coin_by_id(id=CoinGecko.coinGeckoIndex[base.lower()]["id"], localization="false", tickers=False, market_data=True, community_data=False, developer_data=False)
				except:
					embed = discord.Embed(title="Volume data for {} from CoinGecko isn't available.".format(marketPair), color=constants.colors["gray"])
					embed.set_author(name="Couldn't get volume data", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return

				coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
				base, quote = base if base.lower() in cgData["market_data"]["current_price"] else "BTC", "USD"
				volume = cgData["market_data"]["total_volume"][base.lower()]
				volumeUsd = cgData["market_data"]["total_volume"]["usd"]
			else:
				tf, limitTimestamp, candleOffset = Utils.get_highest_supported_timeframe(CoinParser.exchanges[exchange], datetime.datetime.now().astimezone(pytz.utc))
				if symbol in self.rateLimited["p"][exchange] and symbol in self.rateLimited["v"][exchange]:
					price = self.rateLimited["p"][exchange][symbol]
					volume = self.rateLimited["v"][exchange][symbol]

					try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
					except: pass
				else:
					try:
						priceData = CoinParser.exchanges[exchange].fetch_ohlcv(symbol, timeframe=tf.lower(), since=limitTimestamp, limit=300)
						if len(priceData) == 0: raise Exception()
					except:
						embed = discord.Embed(title="Volume data for {} on {} isn't available.".format(marketPair, CoinParser.exchanges[exchange].name), color=constants.colors["gray"])
						embed.set_author(name="Couldn't get volume data", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

					try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
					except: pass

					price = [priceData[-1][4], priceData[0][1]] if len(priceData) < candleOffset else [priceData[-1][4], priceData[-candleOffset][1]]
					volume = sum([candle[5] for candle in priceData if int(candle[0] / 1000) >= int(CoinParser.exchanges[exchange].milliseconds() / 1000) - 86400])
					if exchange == "bitmex" and symbol == "BTC/USD": self.coinParser.lastBitcoinPrice = price[0]
					self.rateLimited["p"][exchange][symbol] = price
					self.rateLimited["v"][exchange][symbol] = volume

				if exchange in ["bitmex"]: volume /= price[0]
				volumeUsd = int(volume * price[0])

			embed = discord.Embed(title="{:,.4f} {}".format(volume, base), description="≈ {:,} {}".format(volumeUsd, quote.replace("USDT", "USD").replace("USDC", "USD").replace("TUSD", "USD").replace("USDS", "USD").replace("PAX", "USD")), color=constants.colors["orange"])
			embed.set_author(name=marketPair, icon_url=coinThumbnail)
			embed.set_footer(text="Volume on {}".format("CoinGecko" if exchange == "CoinGecko" else CoinParser.exchanges[exchange].name))
			try: sentMessages.append(await message.channel.send(embed=embed))
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

			if not useFallback: threading.Thread(target=self.clear_rate_limit_cache, args=(exchange, symbol, ["p", "v"], CoinParser.exchanges[exchange].rateLimit / 1000)).start()
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["p"][id] = {}
				self.rateLimited["v"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def depth(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker_depricated(arguments[0].upper(), "ohlcv")
			if isAggregatedSymbol:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `d` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return (sentMessages, len(sentMessages))

			outputMessage, tickerId, arguments = self.coinParser.process_coin_data_arguments(arguments, tickerId, exchange, command="v")
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return (sentMessages, len(sentMessages))
			_, exchange = arguments

			try: await message.channel.trigger_typing()
			except: pass

			outputMessage, details = self.coinParser.find_market_pair_depricated(tickerId, exchange, "ohlcv")
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return (sentMessages, len(sentMessages))

			symbol, base, quote, marketPair, exchange = details

			if symbol in self.rateLimited["d"][exchange]:
				depthData = self.rateLimited["d"][exchange][symbol]
			else:
				try:
					depthData = CoinParser.exchanges[exchange].fetch_order_book(symbol)
					self.rateLimited["d"][exchange][symbol] = depthData
				except:
					embed = discord.Embed(title="Orderbook data for {} isn't available.".format(marketPair), color=constants.colors["gray"])
					embed.set_author(name="Couldn't get orderbook data", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					self.rateLimited["d"][exchange] = {}
					return (sentMessages, len(sentMessages))

			chartName, chartMessage = self.imageProcessor.request_alpha_depth_chart(messageRequest.authorId, depthData, CoinParser.exchanges[exchange].markets[symbol]["precision"]["price"])

			if chartName is None:
				try:
					embed = discord.Embed(title="Requested orderbook chart for `{}` is not available.".format(marketPair), color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=firebase_storage.icon_bw)
					chartMessage = await message.channel.send(embed=embed)
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return (sentMessages, len(sentMessages))

			try:
				chartMessage = await message.channel.send(file=discord.File("charts/" + chartName, chartName))
				sentMessages.append(chartMessage)
				try: await chartMessage.add_reaction("☑")
				except: pass
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

			threading.Thread(target=self.clear_rate_limit_cache, args=(exchange, symbol, ["d"], CoinParser.exchanges[exchange].rateLimit / 1000)).start()

			self.imageProcessor.clean_cache()
			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			for id in constants.supportedExchanges["ohlcv"]:
				self.rateLimited["d"][id] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
			return ([], 0)

	async def heatmap(self, message, messageRequest, requestSlice, platform):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_chart_arguments(messageRequest.authorId, arguments, platform=platform, command="hmap", defaultPlatforms=["Bitgur"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: sentMessages.append(await message.channel.send(embed=embed))
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return ([], 0)

			for timeframe in request.get_timeframes():
				try: await message.channel.trigger_typing()
				except: pass

				request.set_current(timeframe=timeframe)
				chartName, chartMessage = await self.imageProcessor.request_chart(messageRequest.authorId, request)

				if chartName is None:
					try:
						embed = discord.Embed(title="Requested heat map is not available.", color=constants.colors["gray"])
						embed.set_author(name="Heat map not available", icon_url=firebase_storage.icon_bw)
						chartMessage = await message.channel.send(embed=embed)
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return (sentMessages, len(sentMessages))

				try:
					embed = discord.Embed(title="{}".format(chartMessage), color=constants.colors["deep purple"])
					chartMessage = await message.channel.send(embed=embed if chartMessage else None, file=discord.File("charts/" + chartName, chartName))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

			self.imageProcessor.clean_cache()
			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			self.rateLimited["c"] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
			return ([], 0)

	async def mcap(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker_depricated(arguments[0].upper(), "ohlcv", defaultQuote="")
			if isAggregatedSymbol:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `mcap` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return

			conversion = ""
			if len(arguments) == 2: conversion = arguments[1].upper()
			elif len(arguments) > 2: return

			outputMessage, details = self.coinParser.find_mcap_pair(tickerId, conversion, exchange, "ohlcv")
			if outputMessage is not None:
				if not messageRequest.is_muted():
					try: int(base)
					except:
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
						try: sentMessages.append(await message.channel.send(embed=embed))
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			base, quote = details

			if base.lower() in CoinGecko.coinGeckoIndex:
				try: await message.channel.trigger_typing()
				except: pass

				try:
					data = self.coinGeckoConnection.coingecko.get_coin_by_id(id=CoinGecko.coinGeckoIndex[base.lower()]["id"], localization="false", tickers=False, market_data=True, community_data=True, developer_data=True)
				except Exception as e:
					await self.unknown_error(message, messageRequest.authorId, e)
					return

				embed = discord.Embed(title="{} ({})".format(data["name"], base), description="Ranked #{} by market cap".format(data["market_data"]["market_cap_rank"]), color=constants.colors["lime"])
				embed.set_thumbnail(url=data["image"]["large"])

				if quote == "": quote = "USD"
				if quote.lower() not in data["market_data"]["current_price"]:
					embed = discord.Embed(title="Conversion to {} is not available.".format(tickerId), color=constants.colors["gray"])
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
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			else:
				embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
				embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
				try: sentMessages.append(await message.channel.send(embed=embed))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def news(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, arguments = self.coindar.process_news_arguments(arguments)
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			tickerId, tags = arguments

			try: await message.channel.trigger_typing()
			except: pass

			try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
			except: coinThumbnail = firebase_storage.icon_bw

			try:
				sentMessages.append(await message.channel.send(embed=self.coindar.upcoming_news(tickerId, coinThumbnail, tags)))
			except Exception as e:
				embed = discord.Embed(title="News data from Coindar isn't available.", color=constants.colors["gray"])
				embed.set_author(name="Couldn't get news data", icon_url=firebase_storage.icon_bw)
				try: await message.channel.send(embed=embed)
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def markets(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			tickerId, _, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker_depricated(arguments[0].upper(), "ohlcv")
			if isAggregatedSymbol:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Aggregated tickers aren't supported with the `p` command", color=constants.colors["gray"])
					embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return

			try: await message.channel.trigger_typing()
			except: pass

			listings = self.coinParser.get_listings(tickerId, "", "ohlcv")
			if len(listings) == 0:
				embed = discord.Embed(title="`{}` is not listed on any exchange.".format(tickerId), color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=firebase_storage.icon_bw)
				try: sentMessages.append(await message.channel.send(embed=embed))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return

			embed = discord.Embed(color=constants.colors["deep purple"])
			embed.add_field(name="Found on {} exchanges".format(len(listings)), value="{}".format(", ".join(listings)), inline=False)
			embed.set_author(name="{} listings".format(tickerId), icon_url=firebase_storage.icon)
			try: sentMessages.append(await message.channel.send(embed=embed))
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def convert(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = self.coinGeckoConnection.argument_cleanup(requestSlice).split(" ")

			outputMessage, arguments = self.coinGeckoConnection.process_converter_arguments(arguments)
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			amount, base, quote = arguments

			isBaseInIndex = base.lower() in CoinGecko.exchangeRates or base.lower() in CoinGecko.coinGeckoIndex
			isQuoteInIndex = quote.lower() in CoinGecko.exchangeRates or quote.lower() in CoinGecko.coinGeckoIndex

			if not isBaseInIndex or not isQuoteInIndex:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Ticker `{}` does not exist".format(quote if isBaseInIndex else base), color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return

			try: await message.channel.trigger_typing()
			except: pass

			self.coinGeckoConnection.refresh_coingecko_datasets()
			convertedValue = self.coinGeckoConnection.convert(base, quote, amount)

			embed = discord.Embed(title="{} {} ≈ {:,.8f} {}".format(amount, base, round(convertedValue, 8), quote), color=constants.colors["deep purple"])
			embed.set_author(name="Conversion", icon_url=firebase_storage.icon)
			embed.set_footer(text="Prices on CoinGecko")
			try: sentMessages.append(await message.channel.send(embed=embed))
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def fetch_leaderboard(self, message, messageRequest):
		try:
			leaderboardRaw = []
			allUserIds = [m.id for m in message.guild.members]
			for userId in self.userProperties:
				if userId not in allUserIds or (len(self.userProperties[userId]["paper_trading"]["history"]) == 0 and len(self.userProperties[userId]["paper_trading"]["open_orders"]) == 0): continue
				balances = self.userProperties[userId]["paper_trading"]["free_balance"]

				totalBtc = 0
				for id in constants.supportedExchanges["trading"]:
					priceData = self.rateLimited["p"][id]
					btcPrice = priceData["BTC" + self.paperTrader.dollarQuote[id]]["last"]

					if id in balances:
						for base in balances[id]:
							btcValue = None
							if base + "/BTC" in priceData:
								totalBtc += balances[id][base] * priceData[base + "/BTC"]["last"]
							elif base in ["BTC"]:
								totalBtc += balances[id][base]
							elif base in ["USDT", "TUSD", "USDC", "USDS", "PAX", "USD"]:
								totalBtc += balances[id][base] / btcPrice

				totalUsd = totalBtc * btcPrice
				if totalUsd <= len(constants.supportedExchanges["trading"]) * 1000: continue
				leaderboardRaw.append((userId, totalUsd))

			leaderboardRaw = sorted(leaderboardRaw, key = lambda x : x[1], reverse = True)

			leaderboard = []
			for place in leaderboardRaw[:10]:
				user = client.get_user(place[0])
				leaderboard.append("● {}#{}: ${:,.2f}".format(user.name, user.discriminator, place[1]))

			if len(leaderboard) > 0:
				try: await message.channel.send("__**Paper trader leaderboard ({}):**__\n{}".format(message.guild.name, "\n".join(leaderboard)))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			else:
				try: await message.channel.send("__**Paper trader leaderboard ({}):**__\nSo empty...".format(message.author.name, message.author.discriminator))
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, "paper leaderboard"))

	async def fetch_paper_balance(self, message, messageRequest, requestSlice):
		try:
			if messageRequest.authorId not in self.userProperties: self.userProperties[messageRequest.authorId] = {}
			self.userProperties[messageRequest.authorId] = Utils.createUserSettings(self.userProperties[messageRequest.authorId])

			arguments = requestSlice.split(" ")[2:]
			allBalances = False
			exchanges = []

			if len(arguments) > 0:
				for i, argument in enumerate(arguments):
					updated, newExchange = CoinParser.find_exchange(argument, "ohlcv")
					if updated: exchanges.append(newExchange)
					elif argument in ["all"]: allBalances = True
					else:
						if not messageRequest.is_muted():
							embed = discord.Embed(title="`{}` is not a valid argument. Type `paper help` to learn more.".format(argument), color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

			exchanges = sorted(list(set(exchanges)))
			if len(exchanges) == 0:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="You must provide at least one exchange. Type `paper help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				return
			for id in exchanges:
				if id not in constants.supportedExchanges["trading"]:
					if not messageRequest.is_muted():
						embed = discord.Embed(title="{} exchange is not yet supported. Type `paper help` to learn more.".format(CoinParser.exchanges[id].name), color=constants.colors["gray"])
						embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return

			try: await message.channel.trigger_typing()
			except: pass

			self.coinGeckoConnection.refresh_coingecko_datasets()

			numOfResets = self.userProperties[messageRequest.authorId]["paper_trading"]["s_numOfResets"]
			lastReset = self.userProperties[messageRequest.authorId]["paper_trading"]["s_lastReset"]
			paperDescription = "Trading since {} with {} balance {}".format(Utils.timestamp_to_date(lastReset), numOfResets, "reset" if numOfResets == 1 else "resets") if lastReset > 0 else None
			embed = discord.Embed(title=":joystick: Paper trader balance", description=paperDescription, color=constants.colors["deep purple"])

			lastExchangeIndex = 0
			fieldIndex = 0
			for exchange in self.userProperties[messageRequest.authorId]["paper_trading"]:
				if exchange in exchanges:
					balances = self.userProperties[messageRequest.authorId]["paper_trading"][exchange]["balance"]
					lastExchangeIndex = fieldIndex
					fieldIndex += 1

					totalValue = 0
					numberOfAssets = 0

					for base in sorted(balances.keys()):
						isFiat, _ = self.coinGeckoConnection.checkIfFiat(base, other=CoinGecko.fiatConversionTickers)
						if not isFiat:
							tickerId, isCryptoTicker = self.coinParser.process_ticker_depricated(base, "trading")[0]
							outputMessage, details = self.coinParser.find_market_pair_depricated(tickerId, exchange, "trading")
							if outputMessage is not None:
								await self.unknown_error(message, messageRequest.authorId, e, report=True)
								l.log("Warning", "base {} could not be found on {}".format(base, CoinParser.exchanges[exchange].name))
								return
							_, base, quote, marketPair, exchange = details

						coinName = CoinGecko.coinGeckoIndex[base.lower()]["name"] if base.lower() in CoinGecko.coinGeckoIndex else base
						amount = balances[base]["amount"]

						if exchange in ["bitmex"]:
							if base == "BTC":
								valueText = "{:,.4f} XBT".format(amount)
								convertedValueText = "≈ {:,.6f} USD".format(amount * self.coinParser.lastBitcoinPrice)
								totalValue += amount * self.coinParser.lastBitcoinPrice
								btcValue = -1
							else:
								coinName = "{} position".format(marketPair)
								valueText = "{:,.0f} contracts".format(amount)
								convertedValueText = "≈ {:,.4f} XBT".format(amount / self.coinParser.lastBitcoinPrice)
								totalValue += amount * self.coinParser.lastBitcoinPrice
								btcValue = -1
						else:
							if isFiat:
								valueText = "{:,.8f} {}".format(amount, base)
								convertedValueText = "Stable in fiat value"
								totalValue += amount
								btcValue = self.coinGeckoConnection.convert(base, "BTC", amount)
							elif base == "BTC":
								valueText = "{:,.8f} {}".format(amount, base)
								convertedValueText = "≈ {:,.6f} {}".format(self.coinGeckoConnection.convert(base, quote, amount), quote)
								totalValue += self.coinGeckoConnection.convert(base, "USD", amount)
								btcValue = self.coinGeckoConnection.convert(base, "BTC", amount)
							else:
								valueText = "{:,.8f} {}".format(amount, base)
								convertedValueText = ("{:,.%df} {}" % (6 if quote.lower() in CoinGecko.fiatConversionTickers else 8)).format(self.coinGeckoConnection.convert(base, quote, amount), quote)
								totalValue += self.coinGeckoConnection.convert(base, "USD", amount)
								btcValue = self.coinGeckoConnection.convert(base, "BTC", amount)

						if (btcValue > 0.001 or btcValue == -1) or (amount > 0 and allBalances):
							embed.add_field(name="{}:\n{}".format(coinName, valueText), value=convertedValueText, inline=True)
							fieldIndex += 1
							numberOfAssets += 1

					embed.insert_field_at(lastExchangeIndex, name="__{}__".format(CoinParser.exchanges[exchange].name), value="Holding {} {}. Estimated total value: ${:,.2f} ({:+,.2f} % ROI)".format(numberOfAssets, "assets" if numberOfAssets > 1 else "asset", totalValue, (totalValue / self.paperTrader.startingBalance[exchange] - 1) * 100), inline=False)

			try: await message.channel.send(embed=embed)
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def reset_paper_balance(self, message, messageRequest, requestSlice):
		if messageRequest.authorId not in self.userProperties: self.userProperties[messageRequest.authorId] = {}
		self.userProperties[messageRequest.authorId] = Utils.createUserSettings(self.userProperties[messageRequest.authorId])

		if self.userProperties[messageRequest.authorId]["paper_trading"]["s_lastReset"] + 604800 < time.time():
			embed = discord.Embed(title="Do you really want to reset your paper balance? This cannot be undone.", color=constants.colors["pink"])
			embed.set_author(name="Paper balance reset", icon_url=firebase_storage.icon)
			try: resetBalanceMessage = await message.channel.send(embed=embed)
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			else:
				def confirm_order(m):
					if m.author.id == messageRequest.authorId:
						response = ' '.join(m.clean_content.lower().split())
						if response.startswith(("y", "yes", "sure", "confirm", "execute")): return True
						elif response.startswith(("n", "no", "cancel", "discard", "reject")): raise Exception()

				try:
					this = await client.wait_for('message', timeout=60.0, check=confirm_order)
				except:
					embed = discord.Embed(title="Reset paper balance", description="~~Do you really want to reset your paper balance? This cannot be undone.~~", color=constants.colors["gray"])
					try: await resetBalanceMessage.edit(embed=embed)
					except: pass

					return
				else:
					numOfResets = self.userProperties[messageRequest.authorId]["paper_trading"]["s_numOfResets"]
					self.userProperties[messageRequest.authorId].pop("paper_trading", None)
					self.userProperties[messageRequest.authorId] = Utils.createUserSettings(self.userProperties[messageRequest.authorId])
					self.userProperties[messageRequest.authorId]["paper_trading"]["s_numOfResets"] = numOfResets + 1
					self.userProperties[messageRequest.authorId]["paper_trading"]["s_lastReset"] = time.time()

					try:
						userPropertiesRef = db.document(u'alpha/settings/users/{}'.format(messageRequest.authorId))
						for i in range(5):
							try:
								userPropertiesRef.set(self.userProperties[messageRequest.authorId], merge=True)
								break
							except Exception as e:
								if i == 4: raise e
								else: await asyncio.sleep(2)
					except Exception as e:
						embed = discord.Embed(title="Paper balance was reset. While the request was executed, changes will be uploaded to our database later. The issue was reported", color=constants.colors["gray"])
						embed.set_author(name="Paper balance failed to execute completely", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

						exc_type, exc_obj, exc_tb = sys.exc_info()
						fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
						l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
					else:
						embed = discord.Embed(title="Paper balance was reset successfully", color=constants.colors["deep purple"])
						embed.set_author(name="Paper balance reset", icon_url=firebase_storage.icon)
						try: orderConfirmationMessage = await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
		else:
			embed = discord.Embed(title="Paper balance can only be reset once every seven days.", color=constants.colors["gray"])
			embed.set_author(name="Paper balance reset", icon_url=firebase_storage.icon_bw)
			try: await message.channel.send(embed=embed)
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

	async def fetch_paper_orders(self, message, messageRequest, requestSlice, sort):
		try:
			if messageRequest.authorId not in self.userProperties: self.userProperties[messageRequest.authorId] = {}
			self.userProperties[messageRequest.authorId] = Utils.createUserSettings(self.userProperties[messageRequest.authorId])

			arguments = requestSlice.split(" ")[2:]
			exchanges = []

			if len(arguments) > 0:
				for i, argument in enumerate(arguments):
					updated, newExchange = CoinParser.find_exchange(argument, "ohlcv")
					if updated: exchanges.append(newExchange)
					else:
						if not messageRequest.is_muted():
							embed = discord.Embed(title="`{}` is not a valid argument. Type `paper help` to learn more.".format(argument), color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

			try: await message.channel.trigger_typing()
			except: pass

			for exchange in self.userProperties[messageRequest.authorId]["paper_trading"]:
				if exchange in exchanges:
					orders = self.userProperties[messageRequest.authorId]["paper_trading"][exchange][sort]

					if sort == "history":
						if len(orders) == 0:
							embed = discord.Embed(title=":joystick: No paper trader history", color=constants.colors["deep purple"])
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						else:
							embed = discord.Embed(title=":joystick: Paper trader order history", color=constants.colors["deep purple"])

							for order in orders:
								quoteText = order["quote"]
								side = ""
								if order["orderType"] == "buy":
									side = "Bought"
								elif order["orderType"] == "sell":
									side = "Sold"
								elif order["orderType"].startswith("stop"):
									side = "Stop loss hit"
								elif order["orderType"].startswith("trailing-stop"):
									side = "Trailing stop hit"
									quoteText = "%"

								embed.add_field(name="{} {} {} at {} {}".format(side, order["amount"], order["base"], order["price"], quoteText), value="{} ● (id: {})".format(Utils.timestamp_to_date(order["timestamp"]), order["id"]), inline=True)

							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					else:
						numOfOrders = len(orders)
						if numOfOrders == 0:
							embed = discord.Embed(title=":joystick: No open paper orders", color=constants.colors["deep purple"])
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						else:
							for i, order in enumerate(orders):
								quoteText = order["quote"]
								side = order["orderType"].replace("-", " ").capitalize()
								if order["orderType"].startswith("trailing-stop"):
									quoteText = "%"

								embed = discord.Embed(title="{} {} {} at {} {}".format(side, order["amount"], order["base"], order["price"], quoteText), color=constants.colors["deep purple"])
								embed.set_footer(text="Order {}/{} ● {} ● (id: {})".format(i + 1, numOfOrders, CoinParser.exchanges[order["exchange"]].name, order["id"]))
								try:
									orderMessage = await message.channel.send(embed=embed)
									await orderMessage.add_reaction('❌')
								except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def process_paper_trade(self, message, messageRequest, requestSlice):
		try:
			arguments = self.paperTrader.argument_cleanup(requestSlice).split(" ")
			orderType = arguments[0]

			if orderType in ["buy", "sell", "stop-sell", "trailing-stop-sell"] and 2 <= len(arguments) <= 5:
				tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = self.coinParser.process_ticker_depricated(arguments[1].upper(), "ohlcv")
				if isAggregatedSymbol:
					if not messageRequest.is_muted():
						embed = discord.Embed(title="Aggregated tickers aren't supported with the `paper` command", color=constants.colors["gray"])
						embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return

				outputMessage, tickerId, arguments = self.coinParser.process_trader_arguments(arguments, orderType, tickerId, exchange)
				if outputMessage is not None:
					if not messageRequest.is_muted():
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return
				execPrice, execAmount, isAmountPercent, isPricePercent, reduceOnly, exchange = arguments

				outputMessage, details = self.coinParser.find_market_pair_depricated(tickerId, exchange, "trading")
				if outputMessage is not None:
					if not messageRequest.is_muted():
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return

				try: await message.channel.trigger_typing()
				except: pass

				symbol, base, quote, marketPair, exchange = details
				coinThumbnail = firebase_storage.icon_bw
				baseText = base if exchange != "bitmex" else "contracts"

				if symbol in self.rateLimited["p"][exchange] and symbol in self.rateLimited["v"][exchange]:
					price = self.rateLimited["p"][exchange][symbol]
					volume = self.rateLimited["v"][exchange][symbol]

					try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
					except: pass
				else:
					tf, limitTimestamp, candleOffset = Utils.get_highest_supported_timeframe(CoinParser.exchanges[exchange], datetime.datetime.now().astimezone(pytz.utc))
					try:
						priceData = CoinParser.exchanges[exchange].fetch_ohlcv(symbol, timeframe=tf.lower(), since=limitTimestamp, limit=300)
						if len(priceData) == 0: raise Exception()
					except:
						embed = discord.Embed(title="Price data for {} on {} isn't available.".format(marketPair, CoinParser.exchanges[exchange].name), color=constants.colors["gray"])
						embed.set_author(name="Couldn't get price data", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

					try: coinThumbnail = CoinGecko.coinGeckoIndex[base.lower()]["image"]
					except: pass

					price = [priceData[-1][4], priceData[0][1]] if len(priceData) < candleOffset else [priceData[-1][4], priceData[-candleOffset][1]]
					volume = sum([candle[5] for candle in priceData if int(candle[0] / 1000) >= int(CoinParser.exchanges[exchange].milliseconds() / 1000) - 86400])
					if exchange == "bitmex" and symbol == "BTC/USD": self.coinParser.lastBitcoinPrice = price[0]
					self.rateLimited["p"][exchange][symbol] = price
					self.rateLimited["v"][exchange][symbol] = volume

				if execPrice == -1: execPrice = price[0]

				if messageRequest.authorId not in self.userProperties: self.userProperties[messageRequest.authorId] = {}
				self.userProperties[messageRequest.authorId] = Utils.createUserSettings(self.userProperties[messageRequest.authorId])
				outputTitle, outputMessage, details = self.paperTrader.process_trade(self.userProperties[messageRequest.authorId], CoinParser.exchanges[exchange], symbol, orderType, price[0], execPrice, execAmount, isPricePercent, isAmountPercent, reduceOnly)
				if outputMessage is not None:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name=outputTitle, icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return
				paper, execPrice, execPriceText, execAmount, execAmountText, isLimitOrder = details
				self.userProperties[messageRequest.authorId] = paper

				confirmationText = "Do you want to place a paper {} order of {} {} on {} at {} {}?".format(orderType.replace("-", " "), execAmountText, baseText, CoinParser.exchanges[exchange].name, execPriceText, quote)
				newOrder = {
					"id": "%013x" % random.randrange(10**15),
					"orderType": orderType,
					"base": base,
					"quote": quote,
					"exchange": exchange,
					"amount": execAmount,
					"price": execPrice,
					"timestamp": time.time()
				}
				if orderType == "trailing-stop-sell":
					newOrder["highest"] = execPrice
					quote = "%"

				try:
					embed = discord.Embed(title=confirmationText, color=constants.colors["pink"])
					embed.set_author(name="Confirm paper order", icon_url=firebase_storage.icon)
					try: orderConfirmationMessage = await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				except:
					await self.unknown_error(message, messageRequest.authorId, e)
					return

				def confirm_order(m):
					if m.author.id == messageRequest.authorId:
						response = ' '.join(m.clean_content.lower().split())
						if response.startswith(("y", "yes", "sure", "confirm", "execute")): return True
						elif response.startswith(("n", "no", "cancel", "discard", "reject")): raise Exception()

				try:
					this = await client.wait_for('message', timeout=60.0, check=confirm_order)
				except:
					embed = discord.Embed(title="Canceled", description="~~{}~~".format(confirmationText), color=constants.colors["gray"])
					try: await orderConfirmationMessage.edit(embed=embed)
					except: pass
					return
				else:
					try: await message.channel.trigger_typing()
					except: pass

					paper = self.paperTrader.post_trade(self.userProperties[messageRequest.authorId], CoinParser.exchanges[exchange], symbol, orderType, price[0], execPrice, execAmount, isLimitOrder, isPricePercent, isAmountPercent, reduceOnly)
					if paper is None:
						await self.unknown_error(message, messageRequest.authorId, e, report=True)
						return
					self.userProperties[messageRequest.authorId] = paper
					if self.userProperties[messageRequest.authorId]["paper_trading"]["s_lastReset"] == 0: self.userProperties[messageRequest.authorId]["paper_trading"]["s_lastReset"] = time.time()

					try:
						userPropertiesRef = db.document(u'alpha/settings/users/{}'.format(messageRequest.authorId))
						for i in range(5):
							try:
								userPropertiesRef.set(self.userProperties[messageRequest.authorId], merge=True)
								break
							except Exception as e:
								if i == 4: raise e
								else: await asyncio.sleep(2)
					except Exception as e:
						embed = discord.Embed(title="Order was not pushed to the database. While the order was executed, it will be uploaded to our database later. The issue was reported", color=constants.colors["gray"])
						embed.set_author(name="Order failed to execute completely", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

						exc_type, exc_obj, exc_tb = sys.exc_info()
						fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
						l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
					else:
						successMessage = "Paper {} order of {} {} on {} at {} {} was successfully {}".format(orderType.replace("-", " "), execAmountText, baseText, CoinParser.exchanges[exchange].name, execPriceText, quote, "executed" if price[0] == execPrice else "placed")
						embed = discord.Embed(title=successMessage, color=constants.colors["deep purple"])
						embed.set_author(name="Confirm paper order", icon_url=firebase_storage.icon)
						try: orderConfirmationMessage = await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
			else:
				embed = discord.Embed(title="Invalid command usage. Type `paper help` to learn more.", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
				try: await message.channel.send(embed=embed)
				except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def data_stream(self, message, messageRequest, requestSlice):
		try:
			arguments = requestSlice.split(" ", 2)
			method = arguments[0]

			if method in ["set", "create", "add"]:

			elif method in ["delete", "remove"]:

		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def brekkeven(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []

			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

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
	modeOverride = parser.add_mutually_exclusive_group(required=False)
	modeOverride.add_argument('--override', '-O', dest='modeOverride', help="Force run in a different mode", action='store_true')
	parser.set_defaults(modeOverride=False)
	options = parser.parse_args()

	mode = ("debug" if sys.platform == "linux" else "production") if options.modeOverride else ("production" if sys.platform == "linux" else "debug")
	if options.modeOverride:
		print("[Info]: Running in {} mode.".format(mode))

	client = Alpha() if options.guild == 0 else Alpha(shard_count=1)
	client.prepare(for_guild=options.guild)

	while True:
		client.loop.create_task(client.update_queue())
		try:
			client.loop.run_until_complete(client.start(ApiKeys.get_discord_token(mode=mode)))
		except KeyboardInterrupt:
			handle_exit()
			client.loop.close()
			break
		except (Exception, SystemExit):
			handle_exit()

		client = Alpha(loop=client.loop) if options.guild == -1 else Alpha(loop=client.loop, shard_count=1)
