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
import argparse
import logging
import atexit
import asyncio
import concurrent

import discord
import ccxt
import dbl

from firebase_admin import initialize_app as initialize_firebase_app
from firebase_admin import credentials, firestore, storage
from google.cloud import exceptions

from bot.keys.f802e1fba977727845e8872c1743a714 import Keys as ApiKeys
from bot.assets import firebase_storage
from bot.helpers.utils import Utils
from bot.helpers.logger import Logger as l
from bot.helpers import constants

from bot.engine.assistant import Assistant
from bot.engine.fusion import Fusion
from bot.engine.parser import Parser
from bot.engine.presets import Presets
from bot.engine.processor import Processor
from bot.engine.trader import PaperTrader

from bot.engine.connections.coingecko import CoinGecko
from bot.engine.connections.coindar import Coindar
from bot.engine.constructs.cryptography import EncryptionHandler
from bot.engine.constructs.message import MessageRequest
from bot.engine.constructs.exchange import Exchange


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
	executor = None
	dblpy = None

	assistant = Assistant()
	parser = Parser()
	paperTrader = PaperTrader()
	processor = Processor()
	fusion = Fusion()

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


	async def prepare(self, for_guild=-1):
		t = datetime.datetime.now().astimezone(pytz.utc)

		atexit.register(self.cleanup)
		self.dedicatedGuild = for_guild
		self.executor = concurrent.futures.ThreadPoolExecutor()

		newExchanges = list(set(ccxt.exchanges).symmetric_difference(constants.ccxtSupportedExchanges))
		if len(newExchanges) != 0: l.log("New OHLCV supported exchanges: {}".format(newExchanges))

		for type in constants.supportedExchanges:
			for id in constants.supportedExchanges[type]:
				if id not in Parser.exchanges:
					self.rateLimited["d"][id] = {}
					Parser.exchanges[id] = Exchange(id, type)

		print("[Startup]: exchange initialization complete")
		await client.loop.run_in_executor(self.executor, self.parser.refresh_coingecko_datasets)
		await client.loop.run_in_executor(self.executor, Parser.refresh_ccxt_index)
		print("[Startup]: coin index refresh complete")
		await self.fetch_settings(t)
		print("[Startup]: settings processing complete")
		await self.update_fusion_queue()
		print("[Startup]: fusion queue updated")

		self.dblpy = dbl.DBLClient(client, ApiKeys.get_topgg_key())

	async def on_ready(self):
		t = datetime.datetime.now().astimezone(pytz.utc)

		await self.update_system_status(t)
		print("[Startup]: system status check complete")
		if sys.platform == "linux":
			await self.update_guild_count()
			await self.update_static_messages()

		print("[Startup]: waiting for quild chuning")
		await self.wait_for_chunked()
		print("[Startup]: all quilds chunked")

		self.alphaServer = client.get_guild(414498292655980583)
		self.premiumRoles = {
			0: discord.utils.get(self.alphaServer.roles, id=651042597472698368),
			1: discord.utils.get(self.alphaServer.roles, id=601518889469345810),
			2: discord.utils.get(self.alphaServer.roles, id=601519642070089748),
			3: discord.utils.get(self.alphaServer.roles, id=484387309303758848),
			"indicators": discord.utils.get(self.alphaServer.roles, id=650353024954531840)
		}

		await self.update_properties()
		print("[Startup]: user and guild properties updated")
		await self.security_check()
		print("[Startup]: security check complete")
		await self.send_alerts()
		print("[Startup]: all pending alerts sent")
		await self.update_price_status(t)

		self.isBotReady = True
		l.log("Alerts", "Alpha is online, present in {} servers with {:,} members. Running with {}.".format(len(client.guilds), len(client.users), "1 shard" if client.shard_ids is None else "{} shards".format(len(client.shard_ids))), color=0x00BCD4)

	async def wait_for_chunked(self):
		for guild in client.guilds:
			if not guild.chunked: await asyncio.sleep(1)

	def cleanup(self):
		print("")
		l.log("Alerts", "timestamp: {}, description: Alpha bot is restarting".format(Utils.get_current_date()), post=sys.platform == "linux", color=0x3F51B5)

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

	async def on_member_join(self, member):
		if member.guild.id == 414498292655980583:
			self.alphaServerMembers = [e.id for e in self.alphaServer.members]
			await self.fusion.check_for_spam(member)

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
			await bronzeMessage.edit(embed=discord.Embed(title="Alpha Bronze is a great introduction to Alpha's premium features. Bronze members get increased request limits, command presets, up to ten price alerts at a time, and access to paper trader.", description="Learn more about Alpha Bronze on [our website](https://www.alphabotsystem.com/premium/bronze/)", color=0xFFEA00), suppress=False)
			await silverMessage.edit(embed=discord.Embed(title="Alpha Silver gives you everything Bronze does and more. Not only do Silver members get more pending alerts, they also get access to Alpha's live trader and access to our custom Silver level indicator suite.", description="Learn more about Alpha Silver on [our website](https://www.alphabotsystem.com/premium/silver/)", color=0xFFC400), suppress=False)
			await goldMessage.edit(embed=discord.Embed(title="Alpha Gold is the perfect choice for serious traders. Gold members enjoy unlimited trading through Discord, increased limits, and get access to our full suite of custom indicators.", description="Learn more about Alpha Gold on [our website](https://www.alphabotsystem.com/premium/gold/)", color=0xFF9100), suppress=False)

			# Rules and ToS
			faqAndRulesChannel = client.get_channel(601160698310950914)
			serverRulesMessage = await faqAndRulesChannel.fetch_message(671771929530597426)
			termsOfServiceMessage = await faqAndRulesChannel.fetch_message(671771934475943936)
			faqMessage = await faqAndRulesChannel.fetch_message(671773814182641695)
			await serverRulesMessage.edit(embed=discord.Embed(title="All members of this official Alpha community must follow the community rules. Failure to do so will result in a warning, kick, or ban, based on our sole discretion.", description="[Community rules](https://www.alphabotsystem.com/community-rules/) (last modified on January 31, 2020)", color=constants.colors["deep purple"]), suppress=False)
			await termsOfServiceMessage.edit(embed=discord.Embed(title="By using Alpha branded services you agree to our Terms of Service and Privacy Policy. You can read them on our website.", description="[Terms of Service](https://www.alphabotsystem.com/terms-of-service/) (last modified on March 6, 2020)\n[Privacy Policy](https://www.alphabotsystem.com/privacy-policy/) (last modified on January 31, 2020)", color=constants.colors["deep purple"]), suppress=False)
			await faqMessage.edit(content=None, embed=discord.Embed(title="If you have any questions, refer to our FAQ section, guide, or ask for help in support channels.", description="[Frequently Asked Questions](https://www.alphabotsystem.com/faq/)\n[Feature overview with examples](https://www.alphabotsystem.com/alpha-bot/features/)\nFor other questions, use <#574196284215525386>", color=constants.colors["deep purple"]), suppress=False)

			# Alpha status
			alphaMessage = await client.get_channel(560884869899485233).fetch_message(640502830062632960)
			await alphaMessage.edit(embed=discord.Embed(title=":white_check_mark: Alpha: online", color=constants.colors["deep purple"]), suppress=False)
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def fetch_settings(self, t):
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
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Fatal Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))
			await asyncio.sleep(15)
			await self.fetch_settings(t)

	async def update_fusion_queue(self):
		try:
			instances = await self.fusion.manage_load_distribution()
			if sys.platform == "linux":
				try: db.document(u'fusion/alpha').set({"distribution": instances}, merge=True)
				except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_properties(self):
		try:
			importantEventsChannel = client.get_channel(606035811087155200)

			currentNitroBoosters = [str(e.id) for e in self.alphaServer.premium_subscribers]
			newBoosters = [str(e.id) for e in self.alphaServer.premium_subscribers]
			missingBoosters = []
			for userId in self.alphaSettings["nitroBoosters"]:
				if userId not in currentNitroBoosters: missingBoosters.append(userId)
				if userId in newBoosters: newBoosters.remove(userId)

			globalSettingsRef = db.document(u"alpha/settings")
			globalSettingsRef.set({"nitroBoosters": sorted(currentNitroBoosters)}, merge=True)

			for userId in newBoosters:
				recepient = client.get_user(int(userId))
				await importantEventsChannel.send(embed=discord.Embed(title="{} (id: {}) started boosting Alpha server.".format(str(recepient), userId), color=constants.colors["deep purple"]))

			for userId in missingBoosters:
				recepient = client.get_user(int(userId))
				await importantEventsChannel.send(embed=discord.Embed(title="{} (id: {}) is no longer boosting Alpha server.".format(str(recepient), userId), color=constants.colors["gray"]))

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
					if self.userProperties[userId]["premium"]["plan"] != 0 and str(userId) not in currentNitroBoosters:
						fetchedSettingsRef = db.document(u"alpha/settings/users/{}".format(userId))
						self.userProperties[userId] = Utils.create_user_settings(self.userProperties[userId])
						if self.userProperties[userId]["premium"]["timestamp"] < time.time():
							self.subscribedUsers.pop(userId, None)
							self.userProperties[userId]["premium"]["subscribed"] = False
							self.userProperties[userId]["premium"]["hadWarning"] = False
							fetchedSettingsRef.set(self.userProperties[userId], merge=True)
							recepient = client.get_user(userId)
							embed = discord.Embed(title="Your Alpha Premium subscription has expired.", color=constants.colors["deep purple"])
							try: await recepient.send(embed=embed)
							except: pass
							try: await self.alphaServer.get_member(userId).remove_roles(self.premiumRoles[0], self.premiumRoles[self.userProperties[userId]["premium"]["plan"]], self.premiumRoles["indicators"])
							except: pass
							await importantEventsChannel.send(embed=discord.Embed(title="Alpha Premium for user {} (id: {}) has expired.".format(str(recepient), userId), color=constants.colors["gray"]))
						elif self.userProperties[userId]["premium"]["timestamp"] - 259200 < time.time() and not self.userProperties[userId]["premium"]["hadWarning"]:
							recepient = client.get_user(userId)
							self.userProperties[userId]["premium"]["hadWarning"] = True
							fetchedSettingsRef.set(self.userProperties[userId], merge=True)
							embed = discord.Embed(title="Your Alpha Premium subscription expires on {}.".format(self.userProperties[userId]["premium"]["date"]), color=constants.colors["deep purple"])
							try: await recepient.send(embed=embed)
							except: pass
							await importantEventsChannel.send(embed=discord.Embed(title="Alpha Premium for user {} (id: {}) expires on {}.".format(str(recepient), userId, self.userProperties[userId]["premium"]["date"]), color=constants.colors["gray"]))
						else:
							self.subscribedUsers[userId] = self.userProperties[userId]["premium"]["plan"]
					else:
						self.subscribedUsers[userId] = 3

			for guildId in self.guildProperties:
				if self.guildProperties[guildId]["premium"]["subscribed"]:
					if self.guildProperties[guildId]["premium"]["plan"] != 0:
						fetchedSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
						self.guildProperties[guildId] = Utils.create_server_settings(self.guildProperties[guildId])
						if self.guildProperties[guildId]["premium"]["timestamp"] < time.time():
							self.subscribedGuilds.pop(guildId, None)
							self.guildProperties[guildId]["premium"]["subscribed"] = False
							self.guildProperties[guildId]["premium"]["hadWarning"] = False
							fetchedSettingsRef.set(self.guildProperties[guildId], merge=True)
							guild = client.get_guild(guildId)
							for member in guild.members:
								if member.guild_permissions.administrator:
									embed = discord.Embed(title="Alpha Premium subscription for *{}* server has expired.".format(guild.name), color=constants.colors["deep purple"])
									try: await member.send(embed=embed)
									except: pass
							await importantEventsChannel.send(embed=discord.Embed(title="Alpha Premium for {} server (id: {}) has expired.".format(guild.name, guildId), color=constants.colors["gray"]))
						elif self.guildProperties[guildId]["premium"]["timestamp"] - 259200 < time.time() and not self.guildProperties[guildId]["premium"]["hadWarning"]:
							guild = client.get_guild(guildId)
							self.guildProperties[guildId]["premium"]["hadWarning"] = True
							fetchedSettingsRef.set(self.guildProperties[guildId], merge=True)
							for member in guild.members:
								if member.guild_permissions.administrator:
									embed = discord.Embed(title="Alpha Premium subscription for *{}* server expires on {}.".format(guild.name, self.guildProperties[guildId]["premium"]["date"]), color=constants.colors["deep purple"])
									try: await member.send(embed=embed)
									except: pass
							await importantEventsChannel.send(embed=discord.Embed(title="Alpha Premium for {} server (id: {}) expires on {}.".format(guild.name, guildId, self.guildProperties[guildId]["premium"]["date"]), color=constants.colors["gray"]))
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
						if str(member.avatar_url) == str(member.default_avatar_url): continue

						if member.id not in [401328409499664394, 361916376069439490, 164073578696802305, 390170634891689984] and member.id not in suspiciousUsers["ids"]:
							if member.name.lower() in ["maco <alpha dev>", "macoalgo", "macoalgo [alpha]", "alpha", "mal [alpha]", "notmaliciousupload", "tom [alpha]", "tom (cryptocurrencyfacts)"]:
								if str(member.avatar_url) in suspiciousUsers["oldWhitelist"]: suspiciousUsers["oldWhitelist"].remove(str(member.avatar_url))
								if str(member.avatar_url) in suspiciousUsers["oldBlacklist"]: suspiciousUsers["oldBlacklist"].remove(str(member.avatar_url))
								if str(member.avatar_url) not in self.alphaSettings["avatarWhitelist"]:
									suspiciousUsers["username"].append("{}: {}".format(member.id, str(member.avatar_url)))
									suspiciousUsers["ids"].append(member.id)
							elif member.nick is not None:
								if member.nick.lower() in ["maco <alpha dev>", "macoalgo", "macoalgo [alpha]", "alpha", "mal [alpha]", "notmaliciousupload", "tom [alpha]", "tom (cryptocurrencyfacts)"]:
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
				await botNicknamesMessage.edit(content=botNicknamesText[:2000])
				await suspiciousUserNamesMessage.edit(content=suspiciousUserNamesTest[:2000])
				await suspiciousUserNicknamesMessage.edit(content=suspiciousUserNicknamesText[:2000])
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

			deltaTime = 1 if len(client.cached_messages) == 0 else (datetime.datetime.timestamp(client.cached_messages[-1].created_at) - datetime.datetime.timestamp(client.cached_messages[0].created_at)) / 60
			req = urllib.request.Request("https://status.discordapp.com", headers={"User-Agent": "Mozilla/5.0"})
			webpage = str(urllib.request.urlopen(req).read())
			isDiscordWorking = "All Systems Operational" in webpage

			statisticsEmbed = discord.Embed(title="{}\n{}\n{}\n{}\n{}\n{}".format(numOfCharts, numOfAlerts, numOfPrices, numOfDetails, numOfQuestions, numOfServers), color=constants.colors["deep purple"])
			discordStatusEmbed = discord.Embed(title=":bellhop: Average ping: {:,.1f} milliseconds\n:satellite: Processing {:,.0f} messages per minute\n:signal_strength: Discord: {}".format(client.latency * 1000, len(client.cached_messages) / deltaTime, "all systems operational" if isDiscordWorking else "degraded performance"), color=constants.colors["deep purple" if isDiscordWorking else "gray"])

			if sys.platform == "linux":
				channel = client.get_channel(560884869899485233)
				if self.statistics["c"] > 0:
					try:
						statsMessage = await channel.fetch_message(640502810244415532)
						await statsMessage.edit(embed=statisticsEmbed, suppress=False)
					except: pass
				try:
					statusMessage = await channel.fetch_message(640502825784180756)
					await statusMessage.edit(embed=discordStatusEmbed, suppress=False)
				except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def update_price_status(self, t):
		try:
			cycle = int(t.second / 15)
			fetchPairs = {
				0: ("MEX", "BTCUSD", "ETHUSD"),
				1: ("BIN", "BTCUSDT", "ETHUSDT"),
				2: ("MEX", "BTCUSD", "ETHUSD"),
				3: ("BIN", "BTCUSDT", "ETHUSDT")
			}

			messageRequest = MessageRequest(authorId=401328409499664394, guildProperties=Utils.create_server_settings(self.guildProperties[414498292655980583]))
			parameters = [fetchPairs[cycle][0].lower()]
			price1Text, price2Text = "-", "-"

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, parameters, tickerId=fetchPairs[cycle][1], command="p", defaultPlatforms=["CCXT"])
			payload1, _ = await self.processor.execute_data_server_request((messageRequest.authorId, "quote", request))
			if payload1 is not None: price1Text = "{:,.0f}".format(payload1["raw"]["quotePrice"][0])

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, parameters, tickerId=fetchPairs[cycle][2], command="p", defaultPlatforms=["CCXT"])
			payload2, _ = await self.processor.execute_data_server_request((messageRequest.authorId, "quote", request))
			if payload2 is not None: price2Text = "{:,.0f}".format(payload2["raw"]["quotePrice"][0])

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, [], tickerId="BTCUSD", command="p", defaultPlatforms=["CoinGecko"])
			_ = await self.processor.execute_data_server_request((messageRequest.authorId, "quote", request))

			try: await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="{} ₿ {} Ξ {}".format(fetchPairs[cycle][0], price1Text, price2Text)))
			except: pass
		except asyncio.CancelledError: pass
		except Exception as e:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

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
		# BitMEX data


		# Market overview
		dataStream = {"raw": [656789117262233600, 672933050266419346, 672932971908694036], "id": [675460148038336543, 674288958045421608, 674289070876393492], "type": ["market strength", "signal", "signal"]}

		if sys.platform == "linux":
			for i in range(len(dataStream["raw"])):
				streamChannel = client.get_channel(dataStream["raw"][i])
				try:
					try: streamMessages = await streamChannel.history(limit=10).flatten() # None
					except: continue
					for message in reversed(streamMessages):
						if dataStream["type"][i] == "signal":
							ticker, side, type, price = message.clean_content.lower().split(", ")[:4]
							exchange, tickerId = ticker.split(":")
							if type == "stop":
								embed = discord.Embed(title=tickerId.upper(), description="Stop loss was hit at ${:,.1f}".format(float(price)), color=(constants.colors["yellow"]))
							elif type == "close":
								embed = discord.Embed(title=tickerId.upper(), description="{} closed at ${:,.1f}".format("Short" if side == "buy" else "Long", float(price)), color=(constants.colors["light green"] if side == "buy" else constants.colors["deep orange"]))
							else:
								embed = discord.Embed(title=tickerId.upper(), description="{} opened at ${:,.1f}".format("Long" if side == "buy" else "Short", float(price)), color=(constants.colors["light green"] if side == "buy" else constants.colors["deep orange"]))
						else:
							ticker, trendScore, momentumScore, volatilityScore, volumeScore = message.clean_content.lower().split(", ")[:5]
							exchange, tickerId = ticker.split(":")
							embed = discord.Embed(title=tickerId.upper(), color=constants.colors["deep purple"])
							embed.add_field(name="Trend", value="{}".format(Utils.convert_score(int(trendScore))), inline=True)
							embed.add_field(name="Momentum", value="{}".format(Utils.convert_score(int(momentumScore))), inline=True)
							embed.add_field(name="Volatility", value="{}".format(Utils.convert_score(int(volatilityScore))), inline=True)
							embed.add_field(name="Volume", value="{}".format(Utils.convert_score(int(volumeScore))), inline=True)

						if exchange in ["bitmex", "binance"]: parameters, tickerId = [exchange], tickerId.upper()
						else: parameters, tickerId = [], "{}:{}".format(exchange.upper(), tickerId.upper())

						outputMessage, request = await self.processor.process_chart_arguments(MessageRequest(authorId=401328409499664394, guildProperties=Utils.create_server_settings(self.guildProperties[414498292655980583])), parameters, tickerId, command="c", defaultPlatforms=["TradingLite", "TradingView"])
						request.set_current(timeframe=request.get_timeframes()[0])
						chartName, chartText = await self.processor.execute_data_server_request((401328409499664394, "chart", request))
						if chartName is None: l.log("Warning", "timestamp: {}, failed to fetch data stream chart: {}, request: {}".format(Utils.get_current_date(), chartText, request))
						file = None if chartName is None else discord.File("charts/" + chartName, chartName)

						outgoingChannel = client.get_channel(dataStream["id"][i])
						await outgoingChannel.send(embed=embed, file=file)

						try: await message.delete()
						except: pass
				except asyncio.CancelledError: pass
				except Exception as e:
					exc_type, exc_obj, exc_tb = sys.exc_info()
					fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
					l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def server_ping(self):
		try:
			if sys.platform == "linux" and self.lastMessageTimestamp is not None:
				db.document(u'fusion/alpha').set({"lastUpdate": {"timestamp": datetime.datetime.timestamp(pytz.utc.localize(self.lastMessageTimestamp)), "time": pytz.utc.localize(self.lastMessageTimestamp).strftime("%m. %d. %Y, %H:%M")}}, merge=True)

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
			l.log("Warning", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))
			await asyncio.sleep(15)
			await self.server_ping()

	async def update_queue(self):
		while True:
			try:
				await asyncio.sleep(Utils.seconds_until_cycle())
				if not self.isBotReady: continue
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				await self.update_price_status(t)
				await self.send_alerts()
				if "5m" in timeframes:
					await self.server_ping()
					await self.update_system_status(t)
				if "1H" in timeframes:
					await self.security_check()
					await self.update_properties()
				if "4H" in timeframes:
					await self.update_fusion_queue()
				if "1D" in timeframes:
					await client.loop.run_in_executor(self.executor, self.parser.refresh_coingecko_datasets)
					if self.alphaSettings["lastStatsSnapshot"] != t.month:
						await client.loop.run_in_executor(self.executor, self.fusion.push_active_users, db, bucket, t)
				await self.process_data_streams()
			except asyncio.CancelledError: return
			except Exception as e:
				exc_type, exc_obj, exc_tb = sys.exc_info()
				fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
				l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e))

	async def on_message(self, message):
		try:
			self.lastMessageTimestamp = message.created_at
			messageRequest = MessageRequest(" ".join(message.clean_content.lower().split()), message.author.id if message.webhook_id is None else message.webhook_id, message.guild.id if message.guild is not None else -1)
			messageRequest.personalPremium = self.subscribedUsers[messageRequest.authorId] if messageRequest.authorId in self.subscribedUsers else 0
			messageRequest.serverPremium = self.subscribedGuilds[messageRequest.guildId] if messageRequest.guildId in self.subscribedGuilds else 0
			messageRequest.userProperties = None if messageRequest.authorId not in self.userProperties else Utils.create_user_settings(self.userProperties[messageRequest.authorId])
			messageRequest.guildProperties = None if messageRequest.guildId not in self.guildProperties else Utils.create_server_settings(self.guildProperties[messageRequest.guildId])
			sentMessages = []

			if (self.dedicatedGuild != 0 and self.dedicatedGuild != messageRequest.guildId) or (self.dedicatedGuild == 0 and messageRequest.guildId in constants.vpsServers): return

			isSelf = message.author == client.user
			isUserBlocked = (messageRequest.authorId in constants.blockedBots if message.webhook_id is None else any(e in message.author.name.lower() for e in constants.blockedBotNames)) if message.author.bot else messageRequest.authorId in constants.blockedUsers
			isChannelBlocked = message.channel.id in constants.blockedChannels or messageRequest.guildId in constants.blockedGuilds
			hasContent = message.clean_content != ""

			if not self.isBotReady or isSelf or isUserBlocked or isChannelBlocked or not hasContent: return

			shortcutsEnabled = True if not messageRequest.has_guild_properties() else messageRequest.guildProperties["settings"]["shortcuts"]
			hasSendPermission = True if messageRequest.guildId == -1 else (message.guild.me.permissions_in(message.channel).send_messages and message.guild.me.permissions_in(message.channel).embed_links and message.guild.me.permissions_in(message.channel).attach_files)

			if not messageRequest.content.startswith("preset "):
				parsedPresets = []
				if messageRequest.has_user_properties(): messageRequest.content, messageRequest.presetUsed, parsedPresets = await Presets.process_presets(messageRequest.content, messageRequest.userProperties)
				if not messageRequest.presetUsed and messageRequest.has_guild_properties(): messageRequest.content, messageRequest.presetUsed, parsedPresets = await Presets.process_presets(messageRequest.content, messageRequest.guildProperties)

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
						sentMessages.append(await message.channel.send(embed=embed))
					elif len(parsedPresets) != 0:
						embed = discord.Embed(title="Do you want to add preset `{}` → `{}` to your account?".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"]), color=constants.colors["light blue"])
						addPresetMessage = await message.channel.send(embed=embed)

						def confirm_order(m):
							if m.author.id == messageRequest.authorId:
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
					await message.channel.send(content="Presets are available to premium users only. Visit https://www.alphabotsystem.com/premium/ to learn more.")

			messageRequest.content, messageRequest.shortcutUsed = Utils.shortcuts(messageRequest.content, shortcutsEnabled)
			isCommand = messageRequest.content.startswith(tuple(constants.commandWakephrases)) and not isSelf

			if messageRequest.guildId != -1:
				if messageRequest.guildId in self.maliciousUsers:
					if any([e.id in self.maliciousUsers[messageRequest.guildId][0] for e in message.guild.members]) and time.time() + 60 < self.maliciousUsers[messageRequest.guildId][1]:
						self.maliciousUsers[messageRequest.guildId][1] = time.time()
						embed = discord.Embed(title="This Discord server has one or more members disguising as Alpha bot or one of the team members. Server admins are advised to take action.", description="Users flagged for impersonation are: {}".format(", ".join(["<@!{}>".format(e.id) for e in maliciousUsers])), color=0x000000)
						await message.channel.send(embed=embed)

				if isCommand:
					if not hasSendPermission:
						errorText = "Alpha is missing the permission to a critical permission. Try re-adding Alpha to your server."
						try:
							embed = discord.Embed(title=errorText, color=0x000000)
							embed.add_field(name="Official Alpha website", value="[alphabotsystem.com](https://www.alphabotsystem.com)", inline=True)
							embed.add_field(name="Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
							await message.channel.send(embed=embed)
						except:
							try: await message.channel.send(content=errorText)
							except: pass
						return
					elif len(self.alphaSettings["tosBlacklist"]) != 0:
						if message.guild.name in self.alphaSettings["tosBlacklist"]:
							embed = discord.Embed(title="This Discord server is violating Alpha terms of service. The inability to comply will result in termination of all Alpha services.", color=0x000000)
							embed.add_field(name="Terms of service", value="[Read now](https://www.alphabotsystem.com/terms-of-service/)", inline=True)
							embed.add_field(name="Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
							await message.channel.send(embed=embed)
					elif messageRequest.content != "alpha setup" and not message.author.bot and (messageRequest.guildId != -1 if not messageRequest.has_guild_properties() else not messageRequest.guildProperties["hasDoneSetup"]) and message.author.permissions_in(message.channel).administrator:
						embed = discord.Embed(title="Thanks for adding Alpha to your server, we're thrilled to have you onboard. We think you're going to love everything Alpha can do. Before you start using it, you must complete a short setup process. Type `alpha setup` to begin.", color=constants.colors["pink"])
						await message.channel.send(embed=embed)

			if messageRequest.content.startswith("a "):
				if message.author.bot: return

				command = messageRequest.content.split(" ", 1)[1]
				if command == "help":
					try: await self.help(message, messageRequest)
					except: pass
					return
				elif command == "invite":
					try: await message.channel.send(content="https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372033")
					except: pass
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
									await message.channel.send(embed=discord.Embed(title="No users with this id found.", color=constants.colors["gray"]))
									return

								fetchedSettingsRef = db.document(u"alpha/settings/users/{}".format(userId))
								fetchedSettings = fetchedSettingsRef.get().to_dict()
								fetchedSettings = Utils.create_user_settings(fetchedSettings)

								hadTrial = fetchedSettings["premium"]["hadTrial"]
								wasSubscribed = fetchedSettings["premium"]["subscribed"]
								lastTimestamp = fetchedSettings["premium"]["timestamp"]

								if hadTrial and trial:
									if wasSubscribed: await message.channel.send(embed=discord.Embed(title="This user already has a trial.", color=constants.colors["gray"]))
									else: await message.channel.send(embed=discord.Embed(title="This user already had a trial.", color=constants.colors["gray"]))
									await message.delete()
									return

								timestamp = (lastTimestamp if time.time() < lastTimestamp else time.time()) + 2635200 * duration
								date = datetime.datetime.utcfromtimestamp(timestamp)
								fetchedSettings["premium"] = {"subscribed": True, "hadTrial": hadTrial or trial, "hadWarning": False, "timestamp": timestamp, "date": date.strftime("%m. %d. %Y"), "plan": plan}
								fetchedSettingsRef.set(fetchedSettings, merge=True)
								self.userProperties[userId] = copy.deepcopy(fetchedSettings)
								self.subscribedUsers[userId] = plan if plan != 0 else 3

								try: await self.alphaServer.get_member(userId).add_roles(self.premiumRoles[0], self.premiumRoles[plan])
								except: pass

								if plan == 1: planText = "Bronze"
								elif plan == 2: planText = "Silver"
								else: planText = "Gold"

								if plan != 0:
									if wasSubscribed:
										embed = discord.Embed(title="Your Alpha {} subscription was extended. Current expiry date: {}.".format(planText, fetchedSettings["premium"]["date"]), color=constants.colors["pink"])
										try: await recepient.send(embed=embed)
										except: await client.get_channel(595954290409865226).send(content="<@!{}>".format(userId), embed=embed)
									else:
										embed = discord.Embed(title="Enjoy your Alpha {} subscription. Current expiry date: {}.".format(planText, fetchedSettings["premium"]["date"]), color=constants.colors["pink"])
										try: await recepient.send(embed=embed)
										except: await client.get_channel(595954290409865226).send(content="<@!{}>".format(userId), embed=embed)
									await client.get_channel(606035811087155200).send(embed=discord.Embed(title="User {} (id: {}) subscribed to Alpha {} until {}.".format(str(recepient), userId, planText, fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
								else:
									await client.get_channel(606035811087155200).send(embed=discord.Embed(title="User {} (id: {}) was given Alpha {} with no end date.".format(str(recepient), userId, planText), color=constants.colors["pink"]))

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
									await message.channel.send(embed=discord.Embed(title="No servers with this id found.", color=constants.colors["gray"]))
									return

								fetchedSettingsRef = db.document(u"alpha/settings/servers/{}".format(guildId))
								fetchedSettings = fetchedSettingsRef.get().to_dict()
								fetchedSettings = Utils.create_server_settings(fetchedSettings)

								hadTrial = fetchedSettings["premium"]["hadTrial"]
								wasSubscribed = fetchedSettings["premium"]["subscribed"]
								lastTimestamp = fetchedSettings["premium"]["timestamp"]

								if hadTrial and trial:
									if wasSubscribed: await message.channel.send(embed=discord.Embed(title="This server already has a trial.", color=constants.colors["gray"]))
									else: await message.channel.send(embed=discord.Embed(title="This server already had a trial.", color=constants.colors["gray"]))
									await message.delete()
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

								if plan == 1: planText = "Bronze"
								elif plan == 2: planText = "Silver"
								else: planText = "Gold"

								if plan > 0:
									if wasSubscribed:
										embed = discord.Embed(title="Alpha {} subscription for {} server was extended. Current expiry date: {}.".format(planText, setGuild.name, fetchedSettings["premium"]["date"]), color=constants.colors["pink"])
										try:
											for recepient in recepients: await recepient.send(embed=embed)
										except: await client.get_channel(595954290409865226).send(content=", ".join(["<@!{}>".format(e.id) for e in recepients]), embed=embed)
									else:
										embed = discord.Embed(title="Enjoy Alpha {} subscription for {} server. Current expiry date: {}.".format(planText, setGuild.name, fetchedSettings["premium"]["date"]), color=constants.colors["pink"])
										try:
											for recepient in recepients: await recepient.send(embed=embed)
										except: await client.get_channel(595954290409865226).send(content=", ".join(["<@!{}>".format(e.id) for e in recepients]), embed=embed)
									await client.get_channel(606035811087155200).send(embed=discord.Embed(title="{} server (id: {}) subscribed to Alpha {} until {}.".format(setGuild.name, guildId, planText, fetchedSettings["premium"]["date"]), color=constants.colors["pink"]))
								else:
									await client.get_channel(606035811087155200).send(embed=discord.Embed(title="{} server (id: {}) was given Alpha {} with no end date.".format(setGuild.name, guildId, planText), color=constants.colors["pink"]))

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
					else:
						await self.fusion.process_private_function(client, message, messageRequest, db)
						return
			elif isCommand and hasSendPermission:
				if messageRequest.content.startswith(("alpha ", "alpha, ", "@alpha ", "@alpha, ")):
					self.fusion.process_active_user(messageRequest.authorId, "alpha")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					self.statistics["alpha"] += 1
					rawCaps = " ".join(message.clean_content.split()).split(" ", 1)[1]
					if len(rawCaps) > 500: return
					if (True if not messageRequest.has_guild_properties() else messageRequest.guildProperties["settings"]["assistant"]):
						await message.channel.trigger_typing()
					fallThrough, response = await self.assistant.process_reply(messageRequest.content, rawCaps, True if not messageRequest.has_guild_properties() else messageRequest.guildProperties["settings"]["assistant"])
					if fallThrough:
						if response == "help":
							await self.help(message, messageRequest)
						elif response == "ping":
							await message.channel.send(content="Pong")
						elif response == "premium":
							await message.channel.send(content="Visit https://www.alphabotsystem.com/premium/ to learn more")
						elif response == "invite":
							await message.channel.send(content="https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372033")
						elif response == "vote":
							await message.channel.send(content="https://top.gg/bot/401328409499664394/vote")
						elif response == "referrals":
							embed = discord.Embed(title="Alpha referral links", color=constants.colors["deep purple"])
							embed.add_field(name="Binance", value="Get 10% kickback on all commissions when trading on Binance by [signing up here](https://www.binance.com/en/register?ref=PJF2KLMW)", inline=False)
							embed.add_field(name="Bitmex", value="Get 10% fee discount for the first 6 months when trading on BitMEX by [signing up here](https://www.bitmex.com/register/Cz9JxF)", inline=False)
							embed.add_field(name="TradingView", value="Get $30 after purchasing a paid plan on TradingView by [signing up here](https://www.tradingview.com/gopro/?share_your_love=AlphaBotSystem)", inline=False)
							embed.add_field(name="FTX", value="Get a 5% fee discount on all your trades on FTX by [signing up here](https://ftx.com/#a=Alpha)", inline=False)
							embed.add_field(name="Coinbase", value="Get $13 on Coinbase after [signing up here](https://www.coinbase.com/join/conrad_78)", inline=False)
							embed.add_field(name="Deribit", value="Get 10% fee discount for the first 6 months when trading on Deribit by [signing up here](https://www.deribit.com/reg-8980.6502)", inline=False)
							await message.channel.send(embed=embed)
						elif response == "setup":
							await self.setup(message, messageRequest)
					elif response is not None:
						await message.channel.send(content=response)
				elif messageRequest.content.startswith("toggle "):
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest, override=True): return
					if messageRequest.guildId == -1: return

					if messageRequest.content == "toggle help":
						embed = discord.Embed(title=":control_knobs: Functionality settings", description="Enable or disable certain Alpha features.", color=constants.colors["light blue"])
						embed.add_field(name=":sparkles: Enable TradingLite integration", value="```toggle tradinglite <on/off>```This setting only affects individual users.", inline=False)
						embed.add_field(name=":crystal_ball: Enable or disable the assistant", value="```toggle assistant <on/off>```Admin permissions are required to execute this command.", inline=False)
						embed.add_field(name=":globe_with_meridians: Change preferred market bias", value="```toggle bias <crypto/none>```This affects which market tickers are given priority when requesting charts. Current options are `crypto` and `none`. Admin permissions are required to execute this command.", inline=False)
						embed.add_field(name=":pushpin: Enable or disable shortcuts", value="```toggle shortcuts <on/off>```Admin permissions are required to execute this command.", inline=False)
						embed.add_field(name=":x: Enable or disable autodelete", value="```toggle autodelete <on/off>```Admin permissions are required to execute this command.", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"toggle help\" to pull up this list again.")
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(', toggle | toggle |, ', messageRequest.content.split(" ", 1)[1])
						for requestSlice in requestSlices:
							await self.toggle(message, messageRequest, requestSlice)
				elif messageRequest.content.startswith(("alert ", "alerts ")):
					self.fusion.process_active_user(messageRequest.authorId, "alerts")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest, override=True): return

					if messageRequest.content in ["alert help", "alerts help"]:
						embed = discord.Embed(title=":bell: Price alerts help is no longer available through `alerts help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
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
							await message.channel.send(content="Price alerts are available to premium users only. Visit https://www.alphabotsystem.com/premium/ to learn more.")
				elif messageRequest.content.startswith("preset "):
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest, override=True): return

					if messageRequest.content == "preset help":
						embed = discord.Embed(title=":pushpin: Command presets help is no longer available through `preset help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
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
							await message.channel.send(content="Presets are available to premium users only. Visit https://www.alphabotsystem.com/premium/ to learn more.")
				elif messageRequest.content.startswith("c "):
					self.fusion.process_active_user(messageRequest.authorId, "c")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "c help":
						embed = discord.Embed(title=":chart_with_upwards_trend: Charts help is no longer available through `c help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
					elif messageRequest.content == "c parameters":
						availableIndicators = [
							"NV *(no volume)*", "ACCD *(Accumulation/Distribution)*", "ADR", "Aroon", "ATR", "Awesome *(Awesome Oscillator)*", "BB", "BBW", "CMF", "Chaikin *(Chaikin Oscillator)*", "Chande *(Chande Momentum Oscillator)*", "CI *(Choppiness Index)*", "CCI", "CRSI", "CC *(Correlation Coefficient)*", "DPO", "DM", "DONCH *(Donchian Channels)*", "DEMA", "EOM", "EFI", "EW *(Elliott Wave)*", "ENV *(Envelope)*", "Fisher *(Fisher Transform)*", "HV *(Historical Volatility)*", "HMA", "Ichimoku", "Keltner *(Keltner Channels)*", "KST", "LR *(Linear Regression)*", "MACD", "MOM", "MFI", "Moon *(Moon Phases)*", "MA", "EMA", "WMA", "OBV", "PSAR", "PPHL *(Pivot Points High Low)*", "PPS *(Pivot Points Standard)*", "PO *(Price Oscillator)*", "PVT", "ROC", "RSI", "RVI *(Relative Vigor Index)*", "VI (volatility index)", "SMIEI *(SMI Ergodic Indicator)*", "SMIEO *(SMI Ergodic Oscillator)*", "Stoch", "SRSI *(Stochastic RSI)*", "TEMA *(Triple EMA)*", "TRIX", "Ultimate *(Ultimate Oscillator)*", "VSTOP *(Volatility Stop)*", "VWAP", "VWMA", "WilliamsR", "WilliamsA *(Williams Alligator)*", "WF *(Williams Fractal)*", "ZZ *(Zig Zag)*"
						]
						embed = discord.Embed(title=":chains: Chart parameters", description="All available chart parameters you can use.", color=constants.colors["light blue"])
						embed.add_field(name=":bar_chart: Indicators", value="{}".format(", ".join(availableIndicators)), inline=False)
						embed.add_field(name=":control_knobs: Timeframes", value="1/3/5/15/30-minute, 1/2/3/4-hour, Daily, Weekly and Monthly", inline=False)
						embed.add_field(name=":scales: Exchanges", value=", ".join([(Parser.exchanges[e].name if e in Parser.exchanges else e.title()) for e in constants.supportedExchanges["TradingView"]]), inline=False)
						embed.add_field(name=":chart_with_downwards_trend: Candle types", value="Bars, Candles, Heikin Ashi, Line Break, Line, Area, Renko, Kagi, Point&Figure", inline=False)
						embed.add_field(name=":gear: Other parameters", value="Shorts, Longs, Log, White, Link", inline=False)
						embed.set_footer(text="Use \"c parameters\" to pull up this list again.")
						await message.channel.send(embed=embed)
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
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
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
						embed = discord.Embed(title=":money_with_wings: Prices help is no longer available through `p help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
					elif messageRequest.content not in ["p "]:
						requestSlices = re.split(", p | p |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += 2
							else: self.rateLimited["u"][messageRequest.authorId] = 2

							if self.rateLimited["u"][messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("am "): platform, requestSlice = "Alternative.me", requestSlice[3:]
								elif requestSlice.startswith("cg "): platform, requestSlice = "CoinGecko", requestSlice[3:]
								elif requestSlice.startswith("cx "): platform, requestSlice = "CCXT", requestSlice[3:]
								elif requestSlice.startswith("tm "): platform, requestSlice = "IEXC", requestSlice[3:]

								chartMessages, weight = await self.price(message, messageRequest, requestSlice, platform)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited["u"]: self.rateLimited["u"][messageRequest.authorId] += weight - 2
								else: self.rateLimited["u"][messageRequest.authorId] = weight - 2
						await self.support_message(message, "p")

						self.statistics["p"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("v "):
					self.fusion.process_active_user(messageRequest.authorId, "v")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == ":credit_card: v help":
						embed = discord.Embed(title="Volume help is no longer available through `preset help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
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
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("cg "): platform, requestSlice = "CoinGecko", requestSlice[3:]
								elif requestSlice.startswith("cx "): platform, requestSlice = "CCXT", requestSlice[3:]

								await self.volume(message, messageRequest, requestSlice, platform)
						await self.support_message(message, "v")

						self.statistics["v"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("d "):
					self.fusion.process_active_user(messageRequest.authorId, "d")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "d help":
						embed = discord.Embed(title=":book: Orderbook visualizations help is no longer available through `d help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
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
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("cx "): platform, requestSlice = "CCXT", requestSlice[3:]

								chartMessages, weight = await self.depth(message, messageRequest, requestSlice, platform)
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
						embed = discord.Embed(title=":fire: Heat map help is no longer available through `hmap help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
					elif messageRequest.content == "hmap parameters":
						availableCategories = [
							"Crypto (Cryptocurrency)", "Blockchain (Blockchain Platforms)", "Commerce (Commerce & Advertising)", "Commodities (Commodities)", "Content (Content Management)", "Ai (Data Storage/Analytics & Ai)", "Healthcare (Drugs & Healthcare)", "Energy (Energy & Utilities)", "Events (Events & Entertainment)", "Financial (Financial Services)", "Gambling (Gambling & Betting)", "Gaming (Gaming & Vr)", "Identy (Identy & Reputation)", "Legal (Legal)", "Estate (Real Estate)", "Social (Social Network)", "Software (Software)", "Logistics (Supply & Logistics)", "Trading (Trading & Investing)",
						]
						embed = discord.Embed(title=":chains: Heat map parameters", description="All available heat map parameters you can use.", color=constants.colors["light blue"])
						embed.add_field(name=":control_knobs: Timeframes", value="15-minute, 1-hour, Daily, Weekly, 1/3/6-month and 1-year", inline=False)
						embed.add_field(name=":scales: Filters", value="Top10, Top100, Tokens, Coins, Gainers, Loosers", inline=False)
						embed.add_field(name=":bar_chart: Categories", value="{}".format(", ".join(availableCategories)), inline=False)
						embed.set_footer(text="Use \"hmap parameters\" to pull up this list again.")
						await message.channel.send(embed=embed)
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
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited["u"][messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("bg "): platform, requestSlice = "Bitgur", requestSlice[3:]
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
						embed = discord.Embed(title=":tools: Cryptocurrency details help is no longer available through `mcap help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
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
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
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
						embed = discord.Embed(title=":newspaper: News help is no longer available through `n help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/news/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
					elif messageRequest.content == "n parameters":
						embed = discord.Embed(title=":chains: News parameters", description="All available news parameters you can use.", color=constants.colors["light blue"])
						embed.add_field(name=":scales: Filters", value="General, AMA, Announcement, Airdrop, Brand, Burn, Conference, Contest, Exchange, Hard fork, ICO, Regulation, Meetup, Partnership, Release, Soft fork, Swap, Test, Update, Report", inline=False)
						embed.set_footer(text="Use \"n parameters\" to pull up this list again.")
						await message.channel.send(embed=embed)
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
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
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
						embed = discord.Embed(title=":page_facing_up: Market listings help is no longer available through `mk help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
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
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
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
						embed = discord.Embed(title=":yen: Cryptocurrency conversions help is no longer available through `convert help`", description="Use `alpha help`. Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
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
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
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
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(', paper | paper |, ', messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						for requestSlice in requestSlices:
							if messageRequest.content == "paper leaderboard":
								await self.fetch_leaderboard(message, messageRequest, requestSlice)
							elif messageRequest.content.startswith(("paper balance", "paper bal")):
								await self.fetch_paper_balance(message, messageRequest, requestSlice)
							elif messageRequest.content.startswith("paper history"):
								await self.fetch_paper_orders(message, messageRequest, requestSlice, "history")
							elif messageRequest.content.startswith("paper orders"):
								await self.fetch_paper_orders(message, messageRequest, requestSlice, "openOrders")
							elif messageRequest.content.startswith("paper reset"):
								await self.reset_paper_balance(message, messageRequest, requestSlice)
							else:
								await self.process_paper_trade(message, messageRequest, requestSlice)
						await self.support_message(message, "paper")

						self.statistics["paper"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("stream ") and messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "stream help":
						embed = discord.Embed(title=":abacus: Data Streams", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/data-streams/)", color=constants.colors["light blue"])
						embed.add_field(name=":pencil2: Stream setup", value="```stream set <type>```", inline=False)
						embed.add_field(name=":pencil2: Delete data stream", value="```stream delete```", inline=False)
						embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						embed.set_footer(text="Use \"convert help\" to pull up this list again.")
						await message.channel.send(embed=embed)
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
							await message.channel.send(content="Data streams are available to premium servers only. Visit https://www.alphabotsystem.com/premium/ to learn more.")
			elif messageRequest.content == "brekkeven" and messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
				self.fusion.process_active_user(messageRequest.authorId, "brekkeven")
				if message.author.bot:
					if not await self.bot_verification(message, messageRequest, override=True): return

				await self.brekkeven(message, messageRequest)
				await self.support_message(message)
			else:
				if await self.fusion.spam_warning(message, messageRequest, self.alphaServer, self.alphaServerMembers): return
				if (True if not messageRequest.has_guild_properties() else messageRequest.guildProperties["settings"]["assistant"]):
					response = await self.assistant.funnyReplies(messageRequest.content)
					if response is not None:
						self.statistics["alpha"] += 1
						try: await message.channel.send(content=response)
						except: pass
					return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			self.rateLimited = {"d": {}, "u": {}}
			for side in constants.supportedExchanges:
				for id in constants.supportedExchanges[side]:
					if id not in self.rateLimited["d"]:
						self.rateLimited["d"][id] = {}
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
						fetchedSettings = Utils.create_server_settings(fetchedSettings) if isServer else Utils.create_user_settings(fetchedSettings)
						fetchedSettings, statusMessage = await Presets.update_presets(fetchedSettings, remove=presetName)
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
		if messageRequest.has_guild_properties():
			autodeleteEnabled = messageRequest.guildProperties["settings"]["autodelete"]

		if autodeleteEnabled:
			try: await message.delete()
			except: pass

		for message in sentMessages:
			try:
				if autodeleteEnabled: await message.delete()
				else: await message.remove_reaction("☑", message.channel.guild.me)
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
				if not messageRequest.is_muted() and messageRequest.guildId != 414498292655980583:
					embed = discord.Embed(title="{} webhook is not verified with Alpha. To get it verified, please join Alpha server: https://discord.gg/GQeDE85".format(message.author.name), color=constants.colors["pink"])
					embed.set_author(name="Unverified webhook", icon_url=firebase_storage.icon_bw)
					await message.channel.send(embed=embed)
				return False
		else:
			if message.author.id not in constants.verifiedBots:
				if not messageRequest.is_muted() and messageRequest.guildId != 414498292655980583:
					embed = discord.Embed(title="{}#{} bot is not verified with Alpha. To get it verified, please join Alpha server: https://discord.gg/GQeDE85".format(message.author.name, message.author.discriminator), color=constants.colors["pink"])
					embed.set_author(name="Unverified bot", icon_url=firebase_storage.icon_bw)
					await message.channel.send(embed=embed)
				return False
		history.info("{} ({}): {}".format(Utils.get_current_date(), messageRequest.authorId, messageRequest.content))
		return True

	async def help(self, message, messageRequest):
		embed = discord.Embed(title=":wave: Introduction", description="Alpha bot is the world's most popular Discord bot for requesting charts, set price alerts, and more. Using Alpha is as simple as typing a short command into any Discord channel Alpha has access to. A full guide is available on [our website](https://www.alphabotsystem.com/alpha-bot/features/)", color=constants.colors["light blue"])
		embed.add_field(name=":chart_with_upwards_trend: Charts", value="Easy access to TradingView charts.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/charts/).", inline=False)
		embed.add_field(name=":bell: Alerts", value="Setup price alerts for select crypto exchanges.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/price-alerts/).", inline=False)
		embed.add_field(name=":money_with_wings: Prices", value="Current cryptocurrency prices for thousands of tickers.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/prices/).", inline=False)
		embed.add_field(name=":book: Orderbook visualizations", value="Orderbook snapshot charts of crypto market pairs.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/orderbook-visualizations/).", inline=False)
		embed.add_field(name=":tools: Cryptocurrency details", value="Detailed cryptocurrency information from Parser.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/cryptocurrency-details/).", inline=False)
		# embed.add_field(name=":newspaper: News", value="See latest news and upcoming events in the crypto space.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/news/).", inline=False)
		embed.add_field(name=":yen: Cryptocurrency conversions", value="An easy way to convert between different currencies or units.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/cryptocurrency-conversions/).", inline=False)
		embed.add_field(name=":fire: Heat maps", value="Check various heat maps from Bitgur.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/heat-maps/).", inline=False)
		embed.add_field(name=":pushpin: Command presets", value="Create personal presets for easy access to things you use most.\nLearn more on [our website](https://www.alphabotsystem.com/alpha-bot/features/command-presets/).", inline=False)
		embed.add_field(name=":control_knobs: Functionality settings", value="Enable or disable certain Alpha features.\nType `toggle help` to learn more.", inline=False)
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
			try: await message.channel.send(embed=discord.Embed(title=random.choice(textSet), color=constants.colors["light blue"]))
			except: pass

	async def hold_up(self, message, messageRequest):
		embed = discord.Embed(title="Only up to {:d} requests are allowed per command.".format(int(messageRequest.get_limit() / 2)), color=constants.colors["gray"])
		embed.set_author(name="Too many requests", icon_url=firebase_storage.icon_bw)
		await message.channel.send(embed=embed)

	async def setup(self, message, messageRequest):
		try:
			if messageRequest.guildId != -1:
				if message.author.guild_permissions.administrator:
					accessibleChannels = len([e for e in message.guild.channels if message.guild.me.permissions_in(e).read_messages and e.type == discord.ChannelType.text])
					embed = discord.Embed(title=":wrench: Setup", color=constants.colors["pink"])
					embed.add_field(name=":scroll: Terms of service", value="By using Alpha, you agree to Alpha [Terms of Service](https://www.alphabotsystem.com/terms-of-service/) and [Privacy Policy](https://www.alphabotsystem.com/privacy-policy/). For updates, please join the [official Alpha server](https://discord.gg/GQeDE85).", inline=False)
					embed.add_field(name=":eye: Access", value="Alpha has read access in {} {}. All messages flowing through those channels are processed, but not stored nor analyzed for sentiment, trade, or similar data. Alpha stores anonymous statistical information. If you don't intend on using the bot in some of the channels, restrict its access by disabling its *read messages* permission. For transparency, our message handling system is [open-source](https://github.com/alphabotsystem/Alpha). What data is being used and how is explained in detail in our [Privacy Policy](https://www.alphabotsystem.com/privacy-policy/)".format(accessibleChannels, "channel" if accessibleChannels == 1 else "channels"), inline=False)
					embed.add_field(name=":grey_question: Help command", value="Use `alpha help` to learn more about what Alpha can do.", inline=False)
					embed.add_field(name=":control_knobs: Functionality settings", value="You can enable or disable certain Alpha features. Use `toggle help` to learn more.", inline=False)
					embed.add_field(name=":link: Official Alpha website", value="[alphabotsystem.com](https://www.alphabotsystem.com)", inline=True)
					embed.add_field(name=":tada: Alpha Discord server", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
					embed.add_field(name=":link: Official Alpha Twitter", value="[@AlphaBotSystem](https://twitter.com/AlphaBotSystem)", inline=True)
					embed.set_footer(text="Use \"alpha setup\" to pull up this list again. Prompt expires in 10 minutes.")
					setupMessage = await message.channel.send(embed=embed)

					if messageRequest.guildProperties is None or not messageRequest.guildProperties["hasDoneSetup"]:
						embed = discord.Embed(title=":white_check_mark: Type `agree` in order to complete the setup.", color=constants.colors["pink"])
						embed.set_footer(text="Use \"alpha setup\" to pull up this list again. Prompt expires in 10 minutes.")
						await message.channel.send(embed=embed)

						def confirm_order(m):
							if m.author.id == messageRequest.authorId:
								response = ' '.join(m.clean_content.lower().split())
								if response.startswith(("agree")): return True

						try:
							this = await client.wait_for('message', timeout=600.0, check=confirm_order)
						except:
							embed = discord.Embed(title=":wrench: Setup", description="Process canceled", color=constants.colors["gray"])
							try: await setupMessage.edit(embed=embed)
							except: pass
							try: await confirmation.delete()
							except: pass
							return
						else:
							await message.channel.trigger_typing()

							serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(messageRequest.guildId))
							serverSettings = serverSettingsRef.get().to_dict()
							serverSettings = Utils.update_server_settings(serverSettings, "hasDoneSetup", toVal=True)
							serverSettingsRef.set(serverSettings, merge=True)
							self.guildProperties[messageRequest.guildId] = copy.deepcopy(serverSettings)

							embed = discord.Embed(title=":wrench: Setup", description="Congratulations, the setup process is complete.", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
				else:
					embed = discord.Embed(title="You need administrator permissions to run the setup.", color=constants.colors["gray"])
					await message.channel.send(embed=embed)
			else:
				embed = discord.Embed(title="Alpha setup is only available in servers.", color=constants.colors["gray"])
				await message.channel.send(embed=embed)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, messageRequest.content))

	async def toggle(self, message, messageRequest, command):
		try:
			if command.startswith("assistant"):
				if message.author.guild_permissions.administrator:
					newVal = None
					responseText = ""
					if command == "assistant off": newVal, responseText = False, "Alpha Assistant settings saved."
					elif command == "assistant on": newVal, responseText = True, "Alpha Assistant settings saved."

					if newVal is not None:
						serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(messageRequest.guildId))
						serverSettings = serverSettingsRef.get().to_dict()
						serverSettings = Utils.update_server_settings(serverSettings, "settings", sub="assistant", toVal=newVal)
						serverSettingsRef.set(serverSettings, merge=True)
						self.guildProperties[messageRequest.guildId] = copy.deepcopy(serverSettings)

						await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
			elif command.startswith("bias"):
				if message.author.guild_permissions.administrator:
					newVal = None
					responseText = ""
					if command == "bias crypto": newVal, responseText = "crypto", "Market bias settings saved. Alpha will try matching requested tickers with crypto pairs from now on."
					elif command == "bias none": newVal, responseText = "none", "Market bias settings saved. Alpha will no longer try matching requested tickers."

					if newVal is not None:
						serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(messageRequest.guildId))
						serverSettings = serverSettingsRef.get().to_dict()
						serverSettings = Utils.update_server_settings(serverSettings, "settings", sub="bias", toVal=newVal)
						serverSettingsRef.set(serverSettings, merge=True)
						self.guildProperties[messageRequest.guildId] = copy.deepcopy(serverSettings)

						await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
			elif command.startswith("shortcuts"):
				if message.author.guild_permissions.administrator:
					newVal = None
					responseText = ""
					if command == "shortcuts off": newVal, responseText = False, "Shortcuts are now disabled."
					elif command == "shortcuts on": newVal, responseText = True, "Shortcuts are now enabled."

					if newVal is not None:
						serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(messageRequest.guildId))
						serverSettings = serverSettingsRef.get().to_dict()
						serverSettings = Utils.update_server_settings(serverSettings, "settings", sub="shortcuts", toVal=newVal)
						serverSettingsRef.set(serverSettings, merge=True)
						self.guildProperties[messageRequest.guildId] = copy.deepcopy(serverSettings)

						await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
			elif command.startswith("autodelete"):
				if message.author.guild_permissions.administrator:
					newVal = None
					responseText = ""
					if command == "autodelete off": newVal, responseText = False, "Autodelete settings saved. Charts will be left in chat permanently."
					elif command == "autodelete on": newVal, responseText = True, "Autodelete settings saved. Reqeusted charts will be automatically deleted after a minute."

					if newVal is not None:
						serverSettingsRef = db.document(u"alpha/settings/servers/{}".format(messageRequest.guildId))
						serverSettings = serverSettingsRef.get().to_dict()
						serverSettings = Utils.update_server_settings(serverSettings, "settings", sub="autodelete", toVal=newVal)
						serverSettingsRef.set(serverSettings, merge=True)
						self.guildProperties[messageRequest.guildId] = copy.deepcopy(serverSettings)

						await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
			elif command.startswith("tradinglite"):
				newVal = None
				responseText = ""
				if command == "tradinglite off": newVal, responseText = False, "TradingLite charts will no longer appear by default, unless requested by using `c tl`."
				elif command == "tradinglite on": newVal, responseText = True, "TradingLite charts will now appear whenever possible. You can still explicitly request TradingView charts with `c tv`. You can use `toggle tradinglite off` to turn the feature back off."

				if newVal is not None:
					userSettingsRef = db.document(u"alpha/settings/users/{}".format(messageRequest.authorId))
					userSettings = userSettingsRef.get().to_dict()
					userSettings = Utils.update_user_settings(userSettings, "settings", sub="tradinglite", toVal=newVal)
					userSettingsRef.set(userSettings, merge=True)
					self.userProperties[messageRequest.authorId] = copy.deepcopy(userSettings)

					await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
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
					outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), command="alerts", defaultPlatforms=["Alpha Price Alerts"])
					if outputMessage is not None:
						if not messageRequest.is_muted():
							embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
						return ([], 0)

					await message.channel.trigger_typing()

					ticker = request.get_ticker()
					exchange = request.get_exchange()

					alertsRef = db.document(u"alpha/alerts/{}/{}".format(exchange.id, messageRequest.authorId))
					fetchedAlerts = alertsRef.get().to_dict()
					if fetchedAlerts is None: fetchedAlerts = {}

					sum = 0
					for key in fetchedAlerts: sum += len(fetchedAlerts[key])
					if sum >= messageRequest.get_number_of_price_alerts():
						embed = discord.Embed(title="Only up to {} price alerts per exchange are allowed for {} members.".format(messageRequest.get_number_of_price_alerts(), messageRequest.get_membership_text()), color=constants.colors["gray"])
						embed.set_author(name="Maximum number of price alerts reached", icon_url=firebase_storage.icon_bw)
						await message.channel.send(embed=embed)
						return ([], 0)

					key = ticker.symbol.replace("/", "-")
					newAlert = {
						"id": "%013x" % random.randrange(10**15),
						"timestamp": time.time(),
						"time": Utils.get_current_date(),
						"channel": messageRequest.authorId,
						"action": request.get_filters()[0],
						"level": request.get_numerical_parameters()[0],
						"repeat": False
					}
					levelText = Utils.format_price(exchange.ccxt, ticker.symbol, request.get_numerical_parameters()[0])

					if key not in fetchedAlerts: fetchedAlerts[key] = []
					for alert in fetchedAlerts[key]:
						if alert["action"] == request.get_filters()[0] and alert["level"] == request.get_numerical_parameters()[0]:
							embed = discord.Embed(title="{} alert for {} ({}) at {} {} already exists.".format(request.get_filters()[0].title(), ticker.base, exchange.name, request.get_numerical_parameters()[0], ticker.quote), color=constants.colors["gray"])
							embed.set_author(name="Alert already exists", icon_url=firebase_storage.icon_bw)
							await message.channel.send(embed=embed)
							return ([], 0)

					fetchedAlerts[key].append(newAlert)
					batch = db.batch()
					batch.set(alertsRef, fetchedAlerts, merge=True)
					for i in range(1, self.fusion.numInstances + 1):
						batch.set(db.document(u'fusion/instance-{}'.format(i)), {"needsUpdate": True}, merge=True)

					try:
						batch.commit()
					except:
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
						return ([], 0)
					else:
						embed = discord.Embed(title="{} alert set for {} ({}) at {} {}.".format(request.get_filters()[0].title(), ticker.base, exchange.name, request.get_numerical_parameters()[0], ticker.quote), description=(None if messageRequest.authorId in self.alphaServerMembers else "Alpha will be unable to deliver this alert in case your DMs are disabled. Please join Alpha Discord server for a guaranteed delivery: https://discord.gg/GQeDE85"), color=constants.colors["deep purple"])
						embed.set_author(name="Alert successfully set", icon_url=firebase_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="Invalid command usage. Type `alert help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					await message.channel.send(embed=embed)
			elif method in ["list", "all"]:
				if len(arguments) == 1:
					await message.channel.trigger_typing()

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
									base = Parser.exchanges[exchange].markets[symbol]["base"]
									quote = Parser.exchanges[exchange].markets[symbol]["quote"]
									marketPair = Parser.exchanges[exchange].markets[symbol]["id"].replace("_", "").replace("/", "").replace("-", "").upper()
									levelText = Utils.format_price(Parser.exchanges[exchange], symbol, alert["level"])

									embed = discord.Embed(title="{} alert set for {} ({}) at {} {}".format(alert["action"].title(), marketPair, Parser.exchanges[exchange].name, levelText, quote), color=constants.colors["deep purple"])
									embed.set_footer(text="Alert {}/{} ● (id: {})".format(count, numberOfAlerts, alert["id"]))
									try:
										alertMessage = await message.channel.send(embed=embed)
										await alertMessage.add_reaction('❌')
									except: pass
					else:
						embed = discord.Embed(title="You haven't set any alerts yet.", color=constants.colors["gray"])
						embed.set_author(name="No alerts", icon_url=firebase_storage.icon)
						await message.channel.send(embed=embed)
				else:
					embed = discord.Embed(title="Invalid command usage. Type `alert help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					await message.channel.send(embed=embed)
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
			arguments = requestSlice.replace("`", "").split(" ", 2 + offset)
			method = arguments[0 + offset]

			if method in ["set", "create", "add"]:
				if len(arguments) == 3 + offset:
					await message.channel.trigger_typing()

					title = arguments[1 + offset]
					shortcut = arguments[2 + offset]

					if len(title) > 25:
						embed = discord.Embed(title="Shortcut title can be only up to 25 characters long.", color=constants.colors["gray"])
						embed.set_author(name="Shortcut title is too long", icon_url=firebase_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
						return
					elif len(shortcut) > 200:
						embed = discord.Embed(title="Shortcut command can be only up to 200 characters long.", color=constants.colors["gray"])
						embed.set_author(name="Shortcut command is too long.", icon_url=firebase_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
						return

					fetchedSettingsRef = db.document(u"alpha/settings/{}/{}".format("servers" if isServer else "users", messageRequest.guildId if isServer else messageRequest.authorId))
					fetchedSettings = fetchedSettingsRef.get().to_dict()
					fetchedSettings = Utils.create_server_settings(fetchedSettings) if isServer else Utils.create_user_settings(fetchedSettings)
					fetchedSettings, status = await Presets.update_presets(fetchedSettings, add=title, shortcut=shortcut)
					statusTitle, statusMessage, statusColor = status
					fetchedSettingsRef.set(fetchedSettings, merge=True)
					if isServer: self.guildProperties[messageRequest.guildId] = copy.deepcopy(fetchedSettings)
					else: self.userProperties[messageRequest.authorId] = copy.deepcopy(fetchedSettings)

					embed = discord.Embed(title=statusMessage, color=constants.colors[statusColor])
					embed.set_author(name=statusTitle, icon_url=firebase_storage.icon)
					await message.channel.send(embed=embed)
			elif method in ["list", "all"]:
				if len(arguments) == 1 + offset:
					await message.channel.trigger_typing()

					hasSettings = messageRequest.has_guild_properties() if isServer else messageRequest.has_user_properties()
					settings = {} if not hasSettings else (messageRequest.guildProperties if isServer else messageRequest.userProperties)
					settings = Utils.create_server_settings(settings) if isServer else Utils.create_user_settings(settings)

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
						await message.channel.send(embed=embed)
			elif len(arguments) <= 3 + offset:
				embed = discord.Embed(title="`{}` is not a valid argument. Type `preset help` to learn more.".format(method), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
				await message.channel.send(embed=embed)
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

			outputMessage, request = await self.processor.process_chart_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform, command="c", defaultPlatforms=["Alternative.me", "Woobull Charts", "TradingLite", "TradingView", "Finviz"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return ([], 0)

			if messageRequest.has_user_properties() and messageRequest.userProperties["settings"]["tradinglite"] is None and random.randint(0, 10) == 1 and platform is None:
				await message.channel.send(embed=discord.Embed(title="You can now request TradingLite charts with orderbook heat maps through Alpha. Type `toggle tradinglite on` and give it a try.", description="Learn more on [tradinglite.com](https://www.tradinglite.com)", color=constants.colors["light blue"]))

			for timeframe in request.get_timeframes():
				async with message.channel.typing():
					request.set_current(timeframe=timeframe)
					chartName, chartText = await self.processor.execute_data_server_request((messageRequest.authorId, "chart", request))

				if chartName is None:
					errorMessage = "Requested chart for `{}` is not available.".format(request.get_ticker().name) if chartText is None else chartText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=firebase_storage.icon_bw)
					chartMessage = await message.channel.send(embed=embed)
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				else:
					embed = discord.Embed(title="{}".format(chartText), color=constants.colors["deep purple"])
					chartMessage = await message.channel.send(embed=embed if chartText else None, file=discord.File("charts/" + chartName, chartName))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			self.rateLimited["c"] = {}
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
			return ([], 0)

	async def price(self, message, messageRequest, requestSlice, platform):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform, command="p", defaultPlatforms=["Alternative.me", "LLD", "CoinGecko", "CCXT", "IEXC"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return ([], 0)

			async with message.channel.typing():
				payload, quoteText = await self.processor.execute_data_server_request((messageRequest.authorId, "quote", request))

			if payload is None:
				errorMessage = "Requested price for `{}` is not available.".format(request.get_ticker().name) if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=firebase_storage.icon_bw)
				quoteMessage = await message.channel.send(embed=embed)
				sentMessages.append(quoteMessage)
				try: await quoteMessage.add_reaction("☑")
				except: pass
			else:
				request.set_current(platform=payload["platform"])
				if request.currentPlatform == "Alternative.me":
					embed = discord.Embed(title="{} *({:+.0f} since yesterday)*".format(payload["quotePrice"], payload["change"]), description=payload["quoteConvertedPrice"], color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
					embed.set_footer(text=payload["sourceText"])
					sentMessages.append(await message.channel.send(embed=embed))
				elif request.currentPlatform == "LLD":
					embed = discord.Embed(title=payload["quotePrice"], description=payload["quoteConvertedPrice"], color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
					embed.set_footer(text=payload["sourceText"])
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="{} {} *({:+.2f} %)*".format(payload["quotePrice"], payload["quoteTicker"], payload["change"]), description=payload["quoteConvertedPrice"], color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
					embed.set_footer(text="Price {}".format(payload["sourceText"]))
					sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
			return ([], 0)

	async def volume(self, message, messageRequest, requestSlice, platform):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform, command="v", defaultPlatforms=["Alternative.me", "LLD", "CoinGecko", "CCXT", "IEXC"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return ([], 0)

			async with message.channel.typing():
				payload, quoteText = await self.processor.execute_data_server_request((messageRequest.authorId, "quote", request))

			if payload is None:
				errorMessage = "Requested volume for `{}` is not available.".format(request.get_ticker().name) if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=firebase_storage.icon_bw)
				quoteMessage = await message.channel.send(embed=embed)
				sentMessages.append(quoteMessage)
				try: await quoteMessage.add_reaction("☑")
				except: pass
			else:
				embed = discord.Embed(title="{:,.4f} {}".format(payload["quoteVolume"], payload["baseTicker"]), description=payload["quoteConvertedVolume"], color=constants.colors["orange"])
				embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
				embed.set_footer(text="Volume {}".format(payload["sourceText"]))
				sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
			return ([], 0)

	async def depth(self, message, messageRequest, requestSlice, platform):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform, command="d", defaultPlatforms=["CCXT"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return ([], 0)

			async with message.channel.typing():
				chartName, chartText = await self.processor.execute_data_server_request((messageRequest.authorId, "depth", request))

			if chartName is None:
				embed = discord.Embed(title="Requested orderbook visualization for `{}` is not available.".format(request.get_ticker().name), color=constants.colors["gray"])
				embed.set_author(name="Chart not available", icon_url=firebase_storage.icon_bw)
				chartMessage = await message.channel.send(embed=embed)
				sentMessages.append(chartMessage)
				try: await chartMessage.add_reaction("☑")
				except: pass
			else:
				chartMessage = await message.channel.send(file=discord.File("charts/" + chartName, chartName))
				sentMessages.append(chartMessage)
				try: await chartMessage.add_reaction("☑")
				except: pass

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))
			return ([], 0)

	async def heatmap(self, message, messageRequest, requestSlice, platform):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, request = await self.processor.process_chart_arguments(messageRequest, arguments, platform=platform, command="hmap", defaultPlatforms=["Bitgur"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return ([], 0)

			for timeframe in request.get_timeframes():
				async with message.channel.typing():
					request.set_current(timeframe=timeframe)
					chartName, chartText = await self.processor.execute_data_server_request((messageRequest.authorId, "chart", request))

				if chartName is None:
					try:
						errorMessage = "Requested heat map is not available." if chartText is None else chartText
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Heat map not available", icon_url=firebase_storage.icon_bw)
						chartMessage = await message.channel.send(embed=embed)
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
				else:
					try:
						embed = discord.Embed(title="{}".format(chartText), color=constants.colors["deep purple"])
						chartMessage = await message.channel.send(embed=embed if chartText else None, file=discord.File("charts/" + chartName, chartName))
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)

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

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), command="mcap", defaultPlatforms=["CoinGecko"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return ([], 0)

			ticker = request.get_ticker()
			if ticker.base in Parser.coinGeckoIndex:
				await message.channel.trigger_typing()

				try:
					data = self.parser.coinGecko.get_coin_by_id(id=Parser.coinGeckoIndex[ticker.base]["id"], localization="false", tickers=False, market_data=True, community_data=True, developer_data=True)
				except Exception as e:
					await self.unknown_error(message, messageRequest.authorId, e)
					return

				embed = discord.Embed(title="{} ({})".format(data["name"], ticker.base), description="Ranked #{} by market cap".format(data["market_data"]["market_cap_rank"]), color=constants.colors["lime"])
				embed.set_thumbnail(url=data["image"]["large"])

				if ticker.quote == "": ticker.quote = "USD"
				if ticker.quote.lower() not in data["market_data"]["current_price"]:
					embed = discord.Embed(title="Conversion to {} is not available.".format(ticker.name), color=constants.colors["gray"])
					embed.set_author(name="Conversion not available", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
					return

				usdPrice = ("${:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["usd"])).format(data["market_data"]["current_price"]["usd"])
				eurPrice = ("\n€{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["eur"])).format(data["market_data"]["current_price"]["eur"])
				btcPrice = ""
				ethPrice = ""
				bnbPrice = ""
				xrpPrice = ""
				basePrice = ""
				if ticker.base != "BTC" and "btc" in data["market_data"]["current_price"]:
					btcPrice = ("\n₿{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["btc"])).format(data["market_data"]["current_price"]["btc"])
				if ticker.base != "ETH" and "eth" in data["market_data"]["current_price"]:
					ethPrice = ("\nΞ{:,.%df}" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["eth"])).format(data["market_data"]["current_price"]["eth"])
				if ticker.base != "BNB" and "bnb" in data["market_data"]["current_price"]:
					bnbPrice = ("\n{:,.%df} BNB" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["bnb"])).format(data["market_data"]["current_price"]["bnb"])
				if ticker.base != "XRP" and "xrp" in data["market_data"]["current_price"]:
					xrpPrice = ("\n{:,.%df} XRP" % Utils.add_decimal_zeros(data["market_data"]["current_price"]["xrp"])).format(data["market_data"]["current_price"]["xrp"])
				if ticker.quote.lower() in data["market_data"]["current_price"] and ticker.quote not in ["USD", "EUR", "BTC", "ETH", "BNB", "XRP"]:
					basePrice = ("\n{:,.%df} {}" % Utils.add_decimal_zeros(data["market_data"]["current_price"][ticker.quote.lower()])).format(data["market_data"]["current_price"][ticker.quote.lower()], ticker.quote)
				embed.add_field(name="Price", value=(usdPrice + eurPrice + btcPrice + ethPrice + bnbPrice + xrpPrice + basePrice), inline=True)

				change1h = "Past hour: no data"
				change24h = ""
				change7d = ""
				change30d = ""
				change1y = ""
				if ticker.quote.lower() in data["market_data"]["price_change_percentage_1h_in_currency"]:
					change1h = "Past hour: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_1h_in_currency"][ticker.quote.lower()])
				if ticker.quote.lower() in data["market_data"]["price_change_percentage_24h_in_currency"]:
					change24h = "\nPast day: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_24h_in_currency"][ticker.quote.lower()])
				if ticker.quote.lower() in data["market_data"]["price_change_percentage_7d_in_currency"]:
					change7d = "\nPast week: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_7d_in_currency"][ticker.quote.lower()])
				if ticker.quote.lower() in data["market_data"]["price_change_percentage_30d_in_currency"]:
					change30d = "\nPast month: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_30d_in_currency"][ticker.quote.lower()])
				if ticker.quote.lower() in data["market_data"]["price_change_percentage_1y_in_currency"]:
					change1y = "\nPast year: *{:+,.2f} %*".format(data["market_data"]["price_change_percentage_1y_in_currency"][ticker.quote.lower()])
				embed.add_field(name="Price Change", value=(change1h + change24h + change7d + change30d + change1y), inline=True)

				marketCap = "Market cap: no data"
				totalVolume = ""
				totalSupply = ""
				circulatingSupply = ""
				if data["market_data"]["market_cap"] is not None:
					marketCap = "Market cap: {:,.0f} {}".format(data["market_data"]["market_cap"][ticker.quote.lower()], ticker.quote)
				if data["market_data"]["total_volume"] is not None:
					totalVolume = "\nTotal volume: {:,.0f} {}".format(data["market_data"]["total_volume"][ticker.quote.lower()], ticker.quote)
				if data["market_data"]["total_supply"] is not None:
					totalSupply = "\nTotal supply: {:,.0f}".format(data["market_data"]["total_supply"])
				if data["market_data"]["circulating_supply"] is not None:
					circulatingSupply = "\nCirculating supply: {:,.0f}".format(data["market_data"]["circulating_supply"])
				embed.add_field(name="Details", value=(marketCap + totalVolume + totalSupply + circulatingSupply), inline=False)

				embed.set_footer(text="Data from CoinGecko")

				sentMessages.append(await message.channel.send(embed=embed))
				return
			elif not messageRequest.is_muted():
				embed = discord.Embed(title="Requested market information is not available.", color=constants.colors["gray"])
				embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))
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
					await message.channel.send(embed=embed)
				return
			tickerId, tags = arguments

			await message.channel.trigger_typing()

			try: coinThumbnail = Parser.coinGeckoIndex[base]["image"]
			except: coinThumbnail = firebase_storage.icon_bw

			try:
				sentMessages.append(await message.channel.send(embed=self.coindar.upcoming_news(tickerId, coinThumbnail, tags)))
			except Exception as e:
				embed = discord.Embed(title="News data from Coindar isn't available.", color=constants.colors["gray"])
				embed.set_author(name="Couldn't get news data", icon_url=firebase_storage.icon_bw)
				await message.channel.send(embed=embed)
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

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), command="p", defaultPlatforms=["CCXT"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return ([], 0)

			await message.channel.trigger_typing()

			listings = await self.parser.get_listings(request.get_ticker().id, "", "CCXT")
			if len(listings) == 0:
				embed = discord.Embed(title="`{}` is not listed on any exchange.".format(request.get_ticker().id), color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))
				return

			embed = discord.Embed(color=constants.colors["deep purple"])
			embed.add_field(name="Found on {} exchanges".format(len(listings)), value="{}".format(", ".join(listings)), inline=False)
			embed.set_author(name="{} listings".format(request.get_ticker().id), icon_url=firebase_storage.icon)
			sentMessages.append(await message.channel.send(embed=embed))
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
			arguments = CoinGecko.argument_cleanup(requestSlice).split(" ")

			outputMessage, arguments = CoinGecko.process_converter_arguments(arguments)
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					await message.channel.send(embed=embed)
				return
			amount, base, quote = arguments

			isBaseInIndex = base in Parser.exchangeRates or base in Parser.coinGeckoIndex
			isQuoteInIndex = quote in Parser.exchangeRates or quote in Parser.coinGeckoIndex

			if not isBaseInIndex or not isQuoteInIndex:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Ticker `{}` does not exist".format(quote if isBaseInIndex else base), color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					await message.channel.send(embed=embed)
				return

			await message.channel.trigger_typing()

			convertedValue = self.parser.convert(base, quote, amount)

			embed = discord.Embed(title="{} {} ≈ {:,.8f} {}".format(amount, base, round(convertedValue, 8), quote), color=constants.colors["deep purple"])
			embed.set_author(name="Conversion", icon_url=firebase_storage.icon)
			embed.set_footer(text="Prices on CoinGecko")
			sentMessages.append(await message.channel.send(embed=embed))
			return
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def fetch_leaderboard(self, message, messageRequest, requestSlice):
		try:
			leaderboardRaw = []
			allUserIds = [m.id for m in message.guild.members]
			for userId in self.userProperties:
				if userId not in allUserIds or (len(self.userProperties[userId]["paperTrading"]["history"]) == 0 and len(self.userProperties[userId]["paperTrading"]["openOrders"]) == 0): continue
				balances = self.userProperties[userId]["paperTrading"]["free_balance"]

				totalBtc = 0
				for id in constants.supportedExchanges["trading"]:
					priceData = 0 # FIXME: missing price data
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
			if not messageRequest.has_user_properties(): self.userProperties[messageRequest.authorId] = {}
			self.userProperties[messageRequest.authorId] = Utils.create_user_settings(self.userProperties[messageRequest.authorId])

			arguments = requestSlice.split(" ")[1:]
			allBalances = False
			exchanges = []

			if len(arguments) > 0:
				for i, argument in enumerate(arguments):
					updated, newExchange = await Parser.find_exchange(argument, "CCXT")
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
						embed = discord.Embed(title="{} exchange is not yet supported. Type `paper help` to learn more.".format(Parser.exchanges[id].name), color=constants.colors["gray"])
						embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return

			await message.channel.trigger_typing()

			await client.loop.run_in_executor(self.executor, self.parser.refresh_coingecko_datasets)

			numOfResets = self.userProperties[messageRequest.authorId]["paperTrading"]["sNumOfResets"]
			lastReset = self.userProperties[messageRequest.authorId]["paperTrading"]["sLastReset"]
			paperDescription = "Trading since {} with {} balance {}".format(Utils.timestamp_to_date(lastReset), numOfResets, "reset" if numOfResets == 1 else "resets") if lastReset > 0 else None
			embed = discord.Embed(title=":joystick: Paper trader balance", description=paperDescription, color=constants.colors["deep purple"])

			lastExchangeIndex = 0
			fieldIndex = 0
			for exchange in self.userProperties[messageRequest.authorId]["paperTrading"]:
				if exchange in exchanges:
					balances = self.userProperties[messageRequest.authorId]["paperTrading"][exchange]["balance"]
					lastExchangeIndex = fieldIndex
					fieldIndex += 1

					totalValue = 0
					numberOfAssets = 0

					for base in sorted(balances.keys()):
						isFiat, _ = Parser.check_if_fiat(base, other=Parser.fiatConversionTickers)
						if not isFiat:
							tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = await self.parser.process_ticker_depricated(base, "trading")
							outputMessage, details = await self.parser.find_market_pair_depricated(tickerId, exchange, "trading")
							if outputMessage is not None:
								await self.unknown_error(message, messageRequest.authorId, e, report=True)
								l.log("Warning", "base {} could not be found on {}".format(base, Parser.exchanges[exchange].name))
								return
							_, base, quote, marketPair, exchange = details

						coinName = Parser.coinGeckoIndex[base]["name"] if base in Parser.coinGeckoIndex else base
						amount = balances[base]["amount"]

						if exchange in ["bitmex"]:
							if base == "BTC":
								valueText = "{:,.4f} XBT".format(amount)
								convertedValueText = "≈ {:,.6f} USD".format(amount * 1)
								totalValue += amount * 1
								btcValue = -1
							else:
								coinName = "{} position".format(marketPair)
								valueText = "{:,.0f} contracts".format(amount)
								convertedValueText = "≈ {:,.4f} XBT".format(amount / 1)
								totalValue += amount * 1
								btcValue = -1
						else:
							if isFiat:
								valueText = "{:,.8f} {}".format(amount, base)
								convertedValueText = "Stable in fiat value"
								totalValue += amount
								btcValue = self.parser.convert(base, "BTC", amount)
							elif base == "BTC":
								valueText = "{:,.8f} {}".format(amount, base)
								convertedValueText = "≈ {:,.6f} {}".format(self.parser.convert(base, quote, amount), quote)
								totalValue += self.parser.convert(base, "USD", amount)
								btcValue = self.parser.convert(base, "BTC", amount)
							else:
								valueText = "{:,.8f} {}".format(amount, base)
								convertedValueText = ("{:,.%df} {}" % (6 if quote in Parser.fiatConversionTickers else 8)).format(self.parser.convert(base, quote, amount), quote)
								totalValue += self.parser.convert(base, "USD", amount)
								btcValue = self.parser.convert(base, "BTC", amount)

						if (btcValue > 0.001 or btcValue == -1) or (amount > 0 and allBalances):
							embed.add_field(name="{}:\n{}".format(coinName, valueText), value=convertedValueText, inline=True)
							fieldIndex += 1
							numberOfAssets += 1

					embed.insert_field_at(lastExchangeIndex, name="__{}__".format(Parser.exchanges[exchange].name), value="Holding {} {}. Estimated total value: ${:,.2f} ({:+,.2f} % ROI)".format(numberOfAssets, "assets" if numberOfAssets > 1 else "asset", totalValue, (totalValue / self.paperTrader.startingBalance[exchange] - 1) * 100), inline=False)

			try: await message.channel.send(embed=embed)
			except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
		except asyncio.CancelledError: pass
		except Exception as e:
			await self.unknown_error(message, messageRequest.authorId, e, report=True)
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			l.log("Error", "timestamp: {}, debug info: {}, {}, line {}, description: {}, command: {}".format(Utils.get_current_date(), exc_type, fname, exc_tb.tb_lineno, e, requestSlice))

	async def reset_paper_balance(self, message, messageRequest, requestSlice):
		if not messageRequest.has_user_properties(): self.userProperties[messageRequest.authorId] = {}
		self.userProperties[messageRequest.authorId] = Utils.create_user_settings(self.userProperties[messageRequest.authorId])

		if self.userProperties[messageRequest.authorId]["paperTrading"]["sLastReset"] + 604800 < time.time():
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
					numOfResets = self.userProperties[messageRequest.authorId]["paperTrading"]["sNumOfResets"]
					self.userProperties[messageRequest.authorId].pop("paperTrading", None)
					self.userProperties[messageRequest.authorId] = Utils.create_user_settings(self.userProperties[messageRequest.authorId])
					self.userProperties[messageRequest.authorId]["paperTrading"]["sNumOfResets"] = numOfResets + 1
					self.userProperties[messageRequest.authorId]["paperTrading"]["sLastReset"] = time.time()

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
			if not messageRequest.has_user_properties(): self.userProperties[messageRequest.authorId] = {}
			self.userProperties[messageRequest.authorId] = Utils.create_user_settings(self.userProperties[messageRequest.authorId])

			arguments = requestSlice.split(" ")[1:]
			exchanges = []

			if len(arguments) > 0:
				for i, argument in enumerate(arguments):
					updated, newExchange = await Parser.find_exchange(argument, "CCXT")
					if updated: exchanges.append(newExchange)
					else:
						if not messageRequest.is_muted():
							embed = discord.Embed(title="`{}` is not a valid argument. Type `paper help` to learn more.".format(argument), color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
							try: await message.channel.send(embed=embed)
							except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
						return

			await message.channel.trigger_typing()

			for exchange in self.userProperties[messageRequest.authorId]["paperTrading"]:
				if exchange in exchanges:
					orders = self.userProperties[messageRequest.authorId]["paperTrading"][exchange][sort]

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

								embed.add_field(name="{} {} {} at {} {}".format(side, order["amount"], order["base"], order["quote"], quoteText), value="{} ● (id: {})".format(Utils.timestamp_to_date(order["timestamp"]), order["id"]), inline=True)

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
								embed.set_footer(text="Order {}/{} ● {} ● (id: {})".format(i + 1, numOfOrders, Parser.exchanges[order["exchange"]].name, order["id"]))
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
				tickerId, exchange, tickerParts, isAggregatedSymbol, isCryptoTicker = await self.parser.process_ticker_depricated(arguments[1].upper(), "CCXT")
				if isAggregatedSymbol:
					if not messageRequest.is_muted():
						embed = discord.Embed(title="Aggregated tickers aren't supported with the `paper` command", color=constants.colors["gray"])
						embed.set_author(name="Aggregated tickers", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return

				outputMessage, tickerId, arguments = await self.parser.process_trader_arguments(arguments, orderType, tickerId, exchange)
				if outputMessage is not None:
					if not messageRequest.is_muted():
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return
				execPrice, execAmount, isAmountPercent, isPricePercent, reduceOnly, exchange = arguments

				outputMessage, details = await self.parser.find_market_pair_depricated(tickerId, exchange, "trading")
				if outputMessage is not None:
					if not messageRequest.is_muted():
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
						try: await message.channel.send(embed=embed)
						except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return

				await message.channel.trigger_typing()

				symbol, base, quote, marketPair, exchange = details
				coinThumbnail = firebase_storage.icon_bw
				baseText = base if exchange != "bitmex" else "contracts"

				tf, limitTimestamp, candleOffset = Utils.get_highest_supported_timeframe(Parser.exchanges[exchange].ccxt, datetime.datetime.now().astimezone(pytz.utc))
				try:
					priceData = Parser.exchanges[exchange].fetch_ohlcv(symbol, timeframe=tf.lower(), since=limitTimestamp, limit=300)
					if len(priceData) == 0: raise Exception()
				except:
					embed = discord.Embed(title="Price data for {} on {} isn't available.".format(marketPair, Parser.exchanges[exchange].name), color=constants.colors["gray"])
					embed.set_author(name="Couldn't get price data", icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return

				try: coinThumbnail = Parser.coinGeckoIndex[base]["image"]
				except: pass

				price = [priceData[-1][4], priceData[0][1]] if len(priceData) < candleOffset else [priceData[-1][4], priceData[-candleOffset][1]]
				volume = sum([candle[5] for candle in priceData if int(candle[0] / 1000) >= int(Parser.exchanges[exchange].milliseconds() / 1000) - 86400])

				if execPrice == -1: execPrice = price[0]

				if not messageRequest.has_user_properties(): self.userProperties[messageRequest.authorId] = {}
				self.userProperties[messageRequest.authorId] = Utils.create_user_settings(self.userProperties[messageRequest.authorId])
				outputTitle, outputMessage, details = self.paperTrader.process_trade(self.userProperties[messageRequest.authorId], Parser.exchanges[exchange], symbol, orderType, price[0], execPrice, execAmount, isPricePercent, isAmountPercent, reduceOnly)
				if outputMessage is not None:
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name=outputTitle, icon_url=firebase_storage.icon_bw)
					try: await message.channel.send(embed=embed)
					except Exception as e: await self.unknown_error(message, messageRequest.authorId, e)
					return
				paper, execPrice, execPriceText, execAmount, execAmountText, isLimitOrder = details
				self.userProperties[messageRequest.authorId] = paper

				confirmationText = "Do you want to place a paper {} order of {} {} on {} at {} {}?".format(orderType.replace("-", " "), execAmountText, baseText, Parser.exchanges[exchange].name, execPriceText, quote)
				newOrder = {
					"id": "%013x" % random.randrange(10**15),
					"orderType": orderType,
					"symbol": symbol,
					"base": base,
					"quote": quote,
					"exchange": exchange,
					"amount": execAmount,
					"price": execPrice,
					"timestamp": time.time(),
					"parameters": (isLimitOrder, reduceOnly)
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
					await message.channel.trigger_typing()

					paper = self.paperTrader.post_trade(self.userProperties[messageRequest.authorId], Parser.exchanges[exchange], symbol, orderType, price[0], execPrice, execAmount, isLimitOrder, isPricePercent, isAmountPercent, reduceOnly)
					if paper is None:
						await self.unknown_error(message, messageRequest.authorId, e, report=True)
						return
					self.userProperties[messageRequest.authorId] = paper
					if self.userProperties[messageRequest.authorId]["paperTrading"]["sLastReset"] == 0: self.userProperties[messageRequest.authorId]["paperTrading"]["sLastReset"] = time.time()

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
						successMessage = "Paper {} order of {} {} on {} at {} {} was successfully {}".format(orderType.replace("-", " "), execAmountText, baseText, Parser.exchanges[exchange].name, execPriceText, quote, "executed" if price[0] == execPrice else "placed")
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
				pass
			elif method in ["delete", "remove"]:
				pass
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
	print("""

_____________       ______
___    |__  /__________  /_______ _
__  /| |_  /___  __ \\_  __ \\  __ `/
_  ___ |  / __  /_/ /  / / / /_/ /
/_/  |_/_/  _  .___//_/ /_/\\__,_/\n            /_/
	""")
	parser = argparse.ArgumentParser()
	parser.add_argument("--guild", default=0, type=int, help="Dedicated guild ID", nargs="?", required=False)
	modeOverride = parser.add_mutually_exclusive_group(required=False)
	modeOverride.add_argument('--override', '-O', dest='modeOverride', help="Force run in a different mode", action='store_true')
	parser.set_defaults(modeOverride=False)
	options = parser.parse_args()

	mode = ("debug" if sys.platform == "linux" else "production") if options.modeOverride else ("production" if sys.platform == "linux" else "debug")
	if options.modeOverride: print("[Startup]: Alpha is in startup, running in {} mode.".format(mode))
	else: print("[Startup]: Alpha is in startup")

	client = Alpha() if options.guild == 0 else Alpha(shard_count=1)
	print("[Startup]: object initialization complete")
	client.loop.run_until_complete(client.prepare(for_guild=options.guild))

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

		client = Alpha(loop=client.loop) if options.guild == 0 else Alpha(loop=client.loop, shard_count=1)
