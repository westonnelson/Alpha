import os
import sys
import re
import random
import time
import datetime
import pytz
import urllib
import argparse
import logging
import atexit
import asyncio
import concurrent
import traceback

import discord
from ccxt import exchanges as ccxtExchanges
import dbl as topgg

from firebase_admin import initialize_app, credentials, firestore, storage
from google.cloud import error_reporting

from bot.keys.f802e1fba977727845e8872c1743a714 import Keys as ApiKeys
from bot.assets import firebase_storage
from bot.helpers.utils import Utils
from bot.helpers import constants
from bot.helpers import config

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
from bot.engine.constructs.ticker import Ticker


os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = "bot/keys/bf12e1515c25c7d8c0352f1413ab9a15.json"
firebase = initialize_app(credentials.Certificate("bot/keys/bf12e1515c25c7d8c0352f1413ab9a15.json"), {'storageBucket': ApiKeys.get_firebase_bucket()})
db = firestore.client()
bucket = storage.bucket()

# Command history
history = logging.getLogger("History")
history.setLevel(logging.DEBUG)
hfh = logging.FileHandler("command_history.log", mode="a")
hfh.setLevel(logging.DEBUG)
history.addHandler(hfh)


class Alpha(discord.AutoShardedClient):
	isBotReady = False
	lastMessageTimestamp = None

	assistant = Assistant()
	parser = Parser()
	paperTrader = PaperTrader()
	processor = Processor()
	fusion = Fusion()

	coindar = Coindar()
	encryptionHandler = EncryptionHandler()

	alphaSettings = {}
	accountProperties = {}
	guildProperties = {}
	databasePathMap = {}

	statistics = {"alpha": 0, "alerts": 0, "c": 0, "p": 0, "v": 0, "d": 0, "hmap": 0, "mcap": 0, "news": 0, "mk": 0, "convert": 0, "paper": 0, "live": 0}
	rateLimited = {}
	lockedUsers = set()
	usedPresetsCache = {}
	maliciousUsers = {}


	# -------------------------
	# Startup
	# -------------------------

	async def prepare(self, for_guild=-1):
		"""Prepares all required objects and fetches Alpha settings

		Parameters
		----------
		for_guild : int, optional
			Guild ID of a premium tier community
		"""

		t = datetime.datetime.now().astimezone(pytz.utc)

		atexit.register(self.cleanup)
		self.dedicatedGuild = for_guild
		self.executor = concurrent.futures.ThreadPoolExecutor()
		self.topgg = topgg.DBLClient(client, ApiKeys.get_topgg_key())
		self.logging = error_reporting.Client()

		difference = set(ccxtExchanges).symmetric_difference(constants.ccxtSupportedExchanges)
		newExchanges = [e for e in difference if e not in constants.ccxtSupportedExchanges]
		unsupportedExchanges = [e for e in difference if e in constants.ccxtSupportedExchanges]
		if len(newExchanges) != 0: self.logging.report("Newly supported CCXT exchanges: {}".format(newExchanges))
		if len(unsupportedExchanges) != 0: self.logging.report("Now unsupported CCXT exchanges: {}".format(unsupportedExchanges))

		db.document("discord/settings").on_snapshot(self.update_alpha_settings)
		db.collection("accounts").on_snapshot(self.update_account_properties)
		db.collection("discord/properties/users").on_snapshot(self.update_unknown_user_properties)
		db.collection("discord/properties/guilds").on_snapshot(self.update_guild_properties)
		db.document("discord/messages").on_snapshot(self.send_pending_messages)

		statisticsData = db.document("discord/statistics").get().to_dict()
		slice = "{}-{:02d}".format(t.year, t.month)
		for data in statisticsData[slice]["discord"]:
			self.statistics[data] = statisticsData[slice]["discord"][data]
		print("[Startup]: database link activated")

		for type in constants.supportedExchanges:
			for id in constants.supportedExchanges[type]:
				if id not in Parser.exchanges:
					Parser.exchanges[id] = Exchange(id, type)
		print("[Startup]: exchange initialization complete")

		await client.loop.run_in_executor(self.executor, self.parser.refresh_coingecko_datasets)
		await client.loop.run_in_executor(self.executor, Parser.refresh_ccxt_index)
		print("[Startup]: coin index refresh complete")
		await self.update_fusion_load_distribution()
		print("[Startup]: fusion queue updated")

	async def on_ready(self):
		"""Initiates all Discord dependent functions, flags the bot as ready to process requests

		"""

		t = datetime.datetime.now().astimezone(pytz.utc)

		self.alphaGuild = client.get_guild(414498292655980583)
		self.premiumRoles = [
			discord.utils.get(self.alphaGuild.roles, id=651042597472698368), # base premium role
			discord.utils.get(self.alphaGuild.roles, id=601518889469345810), # Alpha Bronze role
			discord.utils.get(self.alphaGuild.roles, id=601519642070089748), # Alpha Silver role
			discord.utils.get(self.alphaGuild.roles, id=484387309303758848), # Alpha Gold role
			discord.utils.get(self.alphaGuild.roles, id=650353024954531840), # Indicator suite role
			discord.utils.get(self.alphaGuild.roles, id=606913869847199786), # Nitro Boosters role
			discord.utils.get(self.alphaGuild.roles, id=663108866225209416)  # Contributors role
		]

		await self.update_system_status(t)
		print("[Startup]: system status check complete")
		if sys.platform == "linux":
			await self.update_guild_count()
			await self.update_static_messages()

		print("[Startup]: waiting for quild chuning")
		await self.wait_for_chunked()
		print("[Startup]: all quilds chunked")

		await self.security_check()
		print("[Startup]: security check complete")
		await client.loop.run_in_executor(self.executor, self.update_nitro_boosters)
		await self.update_alpha_guild_roles()
		print("[Startup]: user and guild properties updated")
		await self.update_price_status(t)

		db.document("discord/properties").set({"status": {"instance-1": "ready"}}, merge=True)
		self.isBotReady = True
		print("[Startup]: Alpha Bot is online, running with {}.".format("1 shard" if client.shard_ids is None else "{} shards".format(len(client.shard_ids))))

	async def wait_for_chunked(self):
		"""Waits for all guilds to be chunked

		"""

		for i, guild in enumerate(client.guilds):
			if not guild.chunked:
				print("[Startup] waiting for guild {}/{}".format(i + 1, len(client.guilds)))
				await asyncio.sleep(1)

	async def update_static_messages(self):
		"""Updates all static content in various Discord channels

		"""

		try:
			# Alpha Premium messages
			premiumChannel = client.get_channel(669917049895518208)
			bronzeMessage = await premiumChannel.fetch_message(692432080919068673)
			silverMessage = await premiumChannel.fetch_message(692432084798668800)
			goldMessage = await premiumChannel.fetch_message(692432089324191804)
			await bronzeMessage.edit(embed=discord.Embed(title="Alpha Bronze is a great introduction to Alpha's premium features. Bronze members get increased request limits, command presets, price alerts, access to Alpha's crypto paper trader, and access to our Market Breadth + Adaptive Presets indicator.", description="Learn more about Alpha Bronze on [our website](https://www.alphabotsystem.com/premium/bronze)", color=0x800000), suppress=False)
			await silverMessage.edit(embed=discord.Embed(title="Alpha Silver gives you everything Bronze does and more. Not only do Silver members get everything Bronze members get, they also get access to Alpha's crypto live trader and to our custom Silver level indicator suite.", description="Learn more about Alpha Silver on [our website](https://www.alphabotsystem.com/premium/silver)", color=0xB7BACA), suppress=False)
			await goldMessage.edit(embed=discord.Embed(title="Alpha Gold is the perfect choice for serious traders. Gold members get everything Bronze and Silver get, plus they also enjoy virtually unlimited trading through Discord, trading signals, and access to our full suite of custom indicators.", description="Learn more about Alpha Gold on [our website](https://www.alphabotsystem.com/premium/gold)", color=0xDD9909), suppress=False)

			# Rules and ToS
			faqAndRulesChannel = client.get_channel(601160698310950914)
			guildRulesMessage = await faqAndRulesChannel.fetch_message(671771929530597426)
			termsOfServiceMessage = await faqAndRulesChannel.fetch_message(671771934475943936)
			faqMessage = await faqAndRulesChannel.fetch_message(671773814182641695)
			await guildRulesMessage.edit(embed=discord.Embed(title="All members of this official Alpha community must follow the community rules. Failure to do so will result in a warning, kick, or ban, based on our sole discretion.", description="[Community rules](https://www.alphabotsystem.com/community-rules) (last modified on January 31, 2020)", color=constants.colors["deep purple"]), suppress=False)
			await termsOfServiceMessage.edit(embed=discord.Embed(title="By using Alpha branded services you agree to our Terms of Service and Privacy Policy. You can read them on our website.", description="[Terms of Service](https://www.alphabotsystem.com/terms-of-service) (last modified on March 6, 2020)\n[Privacy Policy](https://www.alphabotsystem.com/privacy-policy) (last modified on January 31, 2020)", color=constants.colors["deep purple"]), suppress=False)
			await faqMessage.edit(content=None, embed=discord.Embed(title="If you have any questions, refer to our FAQ section, guide, or ask for help in support channels.", description="[Frequently Asked Questions](https://www.alphabotsystem.com/faq)\n[Feature overview with examples](https://www.alphabotsystem.com/guide)\nFor other questions, use <#574196284215525386>", color=constants.colors["deep purple"]), suppress=False)

			# Alpha status
			alphaMessage = await client.get_channel(560884869899485233).fetch_message(640502830062632960)
			await alphaMessage.edit(embed=discord.Embed(title=":white_check_mark: Alpha Bot: online", color=constants.colors["deep purple"]), suppress=False)
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	def cleanup(self):
		"""Cleanup before shutdown

		"""

		try:
			if self.statistics["c"] > 0 and sys.platform == "linux":
				statisticsRef = db.document("discord/statistics")
				t = datetime.datetime.now().astimezone(pytz.utc)
				statisticsRef.set({"{}-{:02d}".format(t.year, t.month): {"discord": self.statistics}}, merge=True)
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	# -------------------------
	# Guild count & management
	# -------------------------

	async def on_guild_join(self, guild):
		"""Updates quild count on guild_join event and leaves all guilds flagged as banned

		Parameters
		----------
		guild : discord.Guild
			Guild object passed by discord.py
		"""

		await self.update_guild_count()
		if guild.id in constants.bannedGuilds:
			await guild.leave()

	async def on_guild_remove(self, guild):
		"""Updates quild count on guild_remove event

		Parameters
		----------
		guild : discord.Guild
			Guild object passed by discord.py
		"""
		await self.update_guild_count()

	async def on_member_join(self, member):
		"""Scanns each member joining into Alpha community guild for spam

		Parameters
		----------
		guild : discord.Member
			Member object passed by discord.py
		"""

		try:
			if not self.isBotReady: return
			if member.guild.id == 414498292655980583:
				if member.id in self.accountProperties: await self.update_alpha_guild_roles()
				await self.fusion.check_for_spam(member, self.alphaGuild)
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	async def update_guild_count(self):
		"""Push new guild count to Top.gg

		"""

		try: await self.topgg.post_guild_count()
		except: pass

	# -------------------------
	# Job queue
	# -------------------------

	async def job_queue(self):
		"""Executes scheduled jobs as long as Alpha Bot is online

		"""

		while True:
			try:
				await asyncio.sleep(Utils.seconds_until_cycle())
				if not self.isBotReady: continue
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				await self.update_price_status(t)
				if "1m" in timeframes:
					await self.server_ping()
				if "15m" in timeframes:
					await self.update_alpha_guild_roles()
					await self.update_system_status(t)
				if "1H" in timeframes:
					await self.security_check()
					await client.loop.run_in_executor(self.executor, self.update_nitro_boosters)
				if "1D" in timeframes:
					await client.loop.run_in_executor(self.executor, self.parser.refresh_coingecko_datasets)
					await client.loop.run_in_executor(self.executor, self.fusion.push_active_users, db, bucket, t)
					await self.update_fusion_load_distribution()
				await self.process_data_streams()
			except asyncio.CancelledError: return
			except Exception:
				print(traceback.format_exc())
				self.logging.report_exception()

	# -------------------------
	# User management
	# -------------------------

	def update_alpha_settings(self, settings, changes, timestamp):
		"""Updates Alpha settings when server side snapshot updates

		Parameters
		----------
		settings : [google.cloud.firestore_v1.document.DocumentSnapshot]
			complete document snapshot
		changes : [google.cloud.firestore_v1.watch.DocumentChange]
			database changes in the sent snapshot
		timestamp : int
			timestamp indicating time of change in the database
		"""

		self.alphaSettings = settings[0].to_dict()

	def update_account_properties(self, alphaAccounts, changes, timestamp):
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
				if change.type.name in ["ADDED", "MODIFIED"] and "userId" in properties["oauth"]["discord"] and properties["oauth"]["discord"]["userId"] is not None:
					userId = int(properties["oauth"]["discord"]["userId"])
					self.accountProperties[userId] = properties
					self.databasePathMap[userId] = "accounts/{}".format(accountId)
				else:
					for userId in self.accountProperties:
						if "connection" in self.accountProperties[userId] and self.accountProperties[userId]["connection"] == accountId:
							self.accountProperties[userId] = {"connection": None, "trace": self.accountProperties[userId].pop("trace", None)}
							self.databasePathMap[userId] = "discord/properties/users/{}".format(userId)
							break
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	def update_unknown_user_properties(self, unknownUsers, changes, timestamp):
		"""Updates properties of unknown users

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
				userId = int(change.document.id)
				if ("connection" not in properties or properties["connection"] is None) and userId not in self.accountProperties:
					self.accountProperties[userId] = properties
					self.databasePathMap[userId] = "discord/properties/users/{}".format(userId)
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	def update_guild_properties(self, discordGuilds, changes, timestamp):
		"""Updates Discord guild properties

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
				self.guildProperties[int(change.document.id)] = change.document.to_dict()
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	async def update_fusion_load_distribution(self):
		"""Updates Fusion network load distribution

		"""

		try:
			loadDistribution = await self.fusion.update_load_distribution()
			if sys.platform == "linux" and loadDistribution is not None:
				db.document("fusion/properties").set({"distribution": loadDistribution}, merge=True)
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	def send_pending_messages(self, pendingMessages, changes, timestamp):
		"""Sends all pending messages to dedicated channels

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
			pendingMessages = pendingMessages[0].to_dict()
			if len(pendingMessages.keys()) != 0:
				db.document("discord/messages").set({})
				while True:
					if self.isBotReady: break
					print("[Setup]: pending messages snapshot is waiting for setup completion ({})".format(timestamp))
					time.sleep(5)

				for messageId in pendingMessages:
					message = pendingMessages[messageId]
					embed = discord.Embed(title=message["title"], description=message["description"], color=message["color"])
					if message["subtitle"] is not None: embed.set_author(name=message["subtitle"], icon_url=firebase_storage.icon)
					destinationUser = None if message["user"] is None else client.get_user(int(message["user"]))
					destinationChannel = None if message["channel"] is None else client.get_channel(int(message["channel"]))
					try:
						client.loop.create_task(destinationUser.send(embed=embed))
					except:
						if destinationChannel is not None:
							client.loop.create_task(destinationChannel.send(content=(None if destinationUser is None else "<@!{}>!".format(destinationUser.id)), embed=embed))
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	def find_database_path(self, id):
		"""Finds a database path for a passed Discord user Id

		Parameters
		----------
		id : int
			user id
		"""

		if id in self.databasePathMap:
			return self.databasePathMap[id]
		else:
			return "discord/properties/users/{}".format(id)

	async def update_alpha_guild_roles(self):
		"""Updates Alpha community guild roles

		"""

		try:
			if sys.platform == "linux":
				for member in self.alphaGuild.members:
					if member.id in self.accountProperties and "customer" in self.accountProperties[member.id]:
						if "plan" in self.accountProperties[member.id]["customer"]["personalSubscription"]:
							if self.premiumRoles[5] in member.roles: # Nitro Boosters
								if "username" in self.accountProperties[member.id]["oauth"]["tradingview"] and self.accountProperties[member.id]["oauth"]["tradingview"]["username"] != "":
									if self.premiumRoles[4] not in member.roles: await member.add_roles(self.premiumRoles[4])
								else:
									if self.premiumRoles[4] in member.roles: await member.remove_roles(self.premiumRoles[4])
							elif self.premiumRoles[6] in member.roles: # Contributors role
								if "username" in self.accountProperties[member.id]["oauth"]["tradingview"] and self.accountProperties[member.id]["oauth"]["tradingview"]["username"] != "":
									if self.premiumRoles[4] not in member.roles: await member.add_roles(self.premiumRoles[4])
								else:
									if self.premiumRoles[4] in member.roles: await member.remove_roles(self.premiumRoles[4])
							elif self.accountProperties[member.id]["customer"]["personalSubscription"]["plan"] == "plan_GcZGxEIAIDTYlN": # Alpha Bronze
								if self.premiumRoles[0] not in member.roles or self.premiumRoles[1] not in member.roles or self.premiumRoles[2] in member.roles or self.premiumRoles[3] in member.roles:
									await member.add_roles(self.premiumRoles[0], self.premiumRoles[1])
									await member.remove_roles(self.premiumRoles[2], self.premiumRoles[3])
								if "username" in self.accountProperties[member.id]["oauth"]["tradingview"] and self.accountProperties[member.id]["oauth"]["tradingview"]["username"] != "":
									if self.premiumRoles[4] not in member.roles: await member.add_roles(self.premiumRoles[4])
								else:
									if self.premiumRoles[4] in member.roles: await member.remove_roles(self.premiumRoles[4])
							elif self.accountProperties[member.id]["customer"]["personalSubscription"]["plan"] == "plan_GcZIXyYzYmTfVy": # Alpha Silver
								if self.premiumRoles[0] not in member.roles or self.premiumRoles[1] in member.roles or self.premiumRoles[2] not in member.roles or self.premiumRoles[3] in member.roles:
									await member.add_roles(self.premiumRoles[0], self.premiumRoles[2])
									await member.remove_roles(self.premiumRoles[1], self.premiumRoles[3])
								if "username" in self.accountProperties[member.id]["oauth"]["tradingview"] and self.accountProperties[member.id]["oauth"]["tradingview"]["username"] != "":
									if self.premiumRoles[4] not in member.roles: await member.add_roles(self.premiumRoles[4])
								else:
									if self.premiumRoles[4] in member.roles: await member.remove_roles(self.premiumRoles[4])
							elif self.accountProperties[member.id]["customer"]["personalSubscription"]["plan"] == "plan_GcZL9PWcEULzjn": # Alpha Gold
								if self.premiumRoles[0] not in member.roles or self.premiumRoles[1] in member.roles or self.premiumRoles[2] in member.roles or self.premiumRoles[3] not in member.roles:
									await member.add_roles(self.premiumRoles[0], self.premiumRoles[3])
									await member.remove_roles(self.premiumRoles[1], self.premiumRoles[2])
								if "username" in self.accountProperties[member.id]["oauth"]["tradingview"] and self.accountProperties[member.id]["oauth"]["tradingview"]["username"] != "":
									if self.premiumRoles[4] not in member.roles: await member.add_roles(self.premiumRoles[4])
								else:
									if self.premiumRoles[4] in member.roles: await member.remove_roles(self.premiumRoles[4])
							elif self.premiumRoles[0] in member.roles or self.premiumRoles[1] in member.roles or self.premiumRoles[2] in member.roles or self.premiumRoles[3] in member.roles or self.premiumRoles[4] in member.roles:
								await member.remove_roles(self.premiumRoles[0], self.premiumRoles[1], self.premiumRoles[2], self.premiumRoles[3], self.premiumRoles[4])
						else:
							if self.premiumRoles[5] in member.roles: # Nitro Boosters
								if "username" in self.accountProperties[member.id]["oauth"]["tradingview"] and self.accountProperties[member.id]["oauth"]["tradingview"]["username"] != "":
									if self.premiumRoles[4] not in member.roles: await member.add_roles(self.premiumRoles[4])
								else:
									if self.premiumRoles[4] in member.roles: await member.remove_roles(self.premiumRoles[4])
							elif self.premiumRoles[6] in member.roles: # Contributors role
								if "username" in self.accountProperties[member.id]["oauth"]["tradingview"] and self.accountProperties[member.id]["oauth"]["tradingview"]["username"] != "":
									if self.premiumRoles[4] not in member.roles: await member.add_roles(self.premiumRoles[4])
								else:
									if self.premiumRoles[4] in member.roles: await member.remove_roles(self.premiumRoles[4])
							elif self.premiumRoles[0] in member.roles or self.premiumRoles[1] in member.roles or self.premiumRoles[2] in member.roles or self.premiumRoles[3] in member.roles or self.premiumRoles[4] in member.roles:
								await member.remove_roles(self.premiumRoles[0], self.premiumRoles[1], self.premiumRoles[2], self.premiumRoles[3], self.premiumRoles[4])
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	# -------------------------
	# Job functions
	# -------------------------

	async def security_check(self):
		"""Executes a security check for possible impersonators or scammers

		"""

		try:
			guildNames = [e.name for e in client.guilds]
			guildsToRemove = []
			for key in ["blacklist", "whitelist"]:
				for guild in self.alphaSettings["tosWatchlist"]["nicknames"][key]:
					if guild not in guildNames: guildsToRemove.append(guild)
				for guild in guildsToRemove:
					if guild in self.alphaSettings["tosWatchlist"]["nicknames"][key]: self.alphaSettings["tosWatchlist"]["nicknames"][key].pop(guild)

			suspiciousUsers = {"ids": [], "username": [], "nickname": [], "oldWhitelist": list(self.alphaSettings["tosWatchlist"]["avatars"]["whitelist"]), "oldBlacklist": list(self.alphaSettings["tosWatchlist"]["avatars"]["blacklist"])}
			botNicknames = []
			for guild in client.guilds:
				if guild.id in constants.bannedGuilds:
					await guild.leave()

				if guild.me is not None:
					isBlacklisted = guild.name in self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]
					isWhitelisted = guild.name in self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"]

					if guild.me.nick is not None:
						if isBlacklisted:
							if guild.me.nick == self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"][guild.name]:
								if guild.me.guild_permissions.change_nickname:
									try:
										await guild.me.edit(nick=None)
										self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
									except: pass
								continue
							else: self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
						if isWhitelisted:
							if guild.me.nick == self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"][guild.name]: continue
							else: self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"].pop(guild.name)

						for i in range(0, len(guild.me.nick.replace(" ", "")) - 2):
							slice = guild.me.nick.lower().replace(" ", "")[i:i+3]
							if slice in guild.name.lower() and slice not in ["the"]:
								botNicknames.append("```{}: {}```".format(guild.name, guild.me.nick))
								break
					else:
						if isBlacklisted: self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
						if isWhitelisted: self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"].pop(guild.name)

				for member in guild.members:
					if str(member.avatar_url) in self.alphaSettings["tosWatchlist"]["avatars"]["blacklist"]:
						if guild.id not in self.maliciousUsers: self.maliciousUsers[guild.id] = [[], 0]
						self.maliciousUsers[guild.id][0].append(member.id)
						if str(member.avatar_url) in suspiciousUsers["oldBlacklist"]: suspiciousUsers["oldBlacklist"].remove(str(member.avatar_url))
					else:
						if str(member.avatar_url) == str(member.default_avatar_url): continue

						if member.id not in [401328409499664394, 361916376069439490, 164073578696802305, 390170634891689984] and member.id not in suspiciousUsers["ids"]:
							if member.name.lower() in ["maco <alpha dev>", "macoalgo", "macoalgo [alpha]", "alpha", "mal [alpha]", "notmaliciousupload", "tom [alpha]", "tom (cryptocurrencyfacts)"]:
								if str(member.avatar_url) in suspiciousUsers["oldWhitelist"]: suspiciousUsers["oldWhitelist"].remove(str(member.avatar_url))
								if str(member.avatar_url) in suspiciousUsers["oldBlacklist"]: suspiciousUsers["oldBlacklist"].remove(str(member.avatar_url))
								if str(member.avatar_url) not in self.alphaSettings["tosWatchlist"]["avatars"]["whitelist"]:
									suspiciousUsers["username"].append("{}: {}".format(member.id, str(member.avatar_url)))
									suspiciousUsers["ids"].append(member.id)
							elif member.nick is not None:
								if member.nick.lower() in ["maco <alpha dev>", "macoalgo", "macoalgo [alpha]", "alpha", "mal [alpha]", "notmaliciousupload", "tom [alpha]"]:
									if str(member.avatar_url) in suspiciousUsers["oldWhitelist"]: suspiciousUsers["oldWhitelist"].remove(str(member.avatar_url))
									if str(member.avatar_url) in suspiciousUsers["oldBlacklist"]: suspiciousUsers["oldBlacklist"].remove(str(member.avatar_url))
									if str(member.avatar_url) not in self.alphaSettings["tosWatchlist"]["avatars"]["whitelist"]:
										suspiciousUsers["nickname"].append("{}: {}".format(member.id, str(member.avatar_url)))
										suspiciousUsers["ids"].append(member.id)

			for oldAvatar in suspiciousUsers["oldWhitelist"]: self.alphaSettings["tosWatchlist"]["avatars"]["whitelist"].remove(oldAvatar)
			for oldAvatar in suspiciousUsers["oldBlacklist"]: self.alphaSettings["tosWatchlist"]["avatars"]["blacklist"].remove(oldAvatar)

			botNicknamesText = "No bot nicknames to review"
			suspiciousUserNamesTest = "No usernames to review"
			suspiciousUserNicknamesText = "No user nicknames to review"
			if len(botNicknames) > 0: botNicknamesText = "These guilds might be rebranding Alpha Bot:{}".format("".join(botNicknames))
			if len(suspiciousUsers["username"]) > 0: suspiciousUserNamesTest = "These users might be impersonating Alpha Bot or staff:\n{}".format("\n".join(suspiciousUsers["username"]))
			if len(suspiciousUsers["nickname"]) > 0: suspiciousUserNicknamesText = "These users might be impersonating Alpha Bot or staff via nicknames:\n{}".format("\n".join(suspiciousUsers["nickname"]))

			if sys.platform == "linux":
				usageReviewChannel = client.get_channel(571786092077121536)
				botNicknamesMessage = await usageReviewChannel.fetch_message(650084120063508501)
				suspiciousUserNamesMessage = await usageReviewChannel.fetch_message(650084126711349289)
				suspiciousUserNicknamesMessage = await usageReviewChannel.fetch_message(650084149096480831)
				await botNicknamesMessage.edit(content=botNicknamesText[:2000])
				await suspiciousUserNamesMessage.edit(content=suspiciousUserNamesTest[:2000])
				await suspiciousUserNicknamesMessage.edit(content=suspiciousUserNicknamesText[:2000])

				db.document("discord/settings").set({"tosWatchlist": self.alphaSettings["tosWatchlist"]}, merge=True)
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	def update_nitro_boosters(self):
		"""Updates a Nitro boosters list for Alpha community guild

		"""

		try:
			if sys.platform == "linux":
				currentNitroBoosters = [str(e.id) for e in self.alphaGuild.premium_subscribers]
				newBoosters = [str(e.id) for e in self.alphaGuild.premium_subscribers]
				missingBoosters = []
				for userId in self.alphaSettings["serverProperties"]["nitroBoosters"]:
					if userId not in currentNitroBoosters: missingBoosters.append(userId)
					if userId in newBoosters: newBoosters.remove(userId)

				if len(newBoosters) != 0 or len(missingBoosters) != 0:
					db.document("discord/settings").set({"serverProperties": {"nitroBoosters": sorted(currentNitroBoosters)}}, merge=True)
					for userId in newBoosters:
						self.fusion.webhook_send(ApiKeys.get_log_webhook(mode="important_events"), embeds=[discord.Embed(title="<@!{}> started boosting Alpha community guild.".format(userId), color=constants.colors["deep purple"])])
					for userId in missingBoosters:
						self.fusion.webhook_send(ApiKeys.get_log_webhook(mode="important_events"), embeds=[discord.Embed(title="<@!{}> is no longer boosting Alpha community guild.".format(userId), color=constants.colors["deep purple"])])
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	async def update_system_status(self, t):
		"""Updates system status messages in Alpha community guild

		Parameters
		----------
		t : datetime.datetime
			current datetime
		"""

		try:
			statisticsRef = db.document("discord/statistics")
			statisticsRef.set({"{}-{:02d}".format(t.year, t.month): {"discord": self.statistics}}, merge=True)

			numOfCharts = ":chart_with_upwards_trend: {:,} charts requested".format(self.statistics["c"] + self.statistics["hmap"])
			numOfAlerts = ":bell: {:,} alerts set".format(self.statistics["alerts"])
			numOfPrices = ":money_with_wings: {:,} prices pulled".format(self.statistics["d"] + self.statistics["p"] + self.statistics["v"])
			numOfDetails = ":tools: {:,} coin details looked up".format(self.statistics["mcap"] + self.statistics["mk"] + self.statistics["convert"])
			numOfTrades = ":dart: {:,} trades executed".format(self.statistics["paper"] + self.statistics["live"])
			numOfQuestions = ":crystal_ball: {:,} questions asked".format(self.statistics["alpha"])
			numOfGuilds = ":heart: Used in {:,} guilds with {:,} members".format(len(client.guilds), len(client.users))

			deltaTime = 1 if len(client.cached_messages) == 0 else (datetime.datetime.timestamp(client.cached_messages[-1].created_at) - datetime.datetime.timestamp(client.cached_messages[0].created_at)) / 60
			req = urllib.request.Request("https://status.discordapp.com", headers={"User-Agent": "Mozilla/5.0"})
			webpage = str(urllib.request.urlopen(req).read())
			isDiscordWorking = "All Systems Operational" in webpage

			statisticsEmbed = discord.Embed(title="{}\n{}\n{}\n{}\n{}\n{}".format(numOfCharts, numOfAlerts, numOfPrices, numOfDetails, numOfQuestions, numOfGuilds), color=constants.colors["deep purple"])
			discordStatusEmbed = discord.Embed(title=":bellhop: Average ping: {:,.1f} milliseconds\n:satellite: Processing {:,.0f} messages per minute\n:signal_strength: Discord: {}".format(client.latency * 1000, len(client.cached_messages) / deltaTime, "all systems operational" if isDiscordWorking else "degraded performance"), color=constants.colors["deep purple" if isDiscordWorking else "gray"])

			if sys.platform == "linux":
				channel = client.get_channel(560884869899485233)
				if self.statistics["c"] > 0:
					statsMessage = await channel.fetch_message(640502810244415532)
					await statsMessage.edit(embed=statisticsEmbed, suppress=False)
				statusMessage = await channel.fetch_message(640502825784180756)
				await statusMessage.edit(embed=discordStatusEmbed, suppress=False)
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	async def update_price_status(self, t):
		"""Updates Alpha Bot user status with latest prices

		Parameters
		----------
		t : datetime.datetime
			current datetime
		"""

		try:
			cycle = int(t.second / 15)
			fetchPairs = {
				0: ("MEX", "BTCUSD", "ETHUSD"),
				1: ("BIN", "BTCUSDT", "ETHUSDT"),
				2: ("MEX", "BTCUSD", "ETHUSD"),
				3: ("BIN", "BTCUSDT", "ETHUSDT")
			}

			messageRequest = MessageRequest(authorId=401328409499664394, guildProperties=Utils.create_guild_settings(self.guildProperties[414498292655980583]))
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

			try: await client.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="{} ₿ {} Ξ {}".format(fetchPairs[cycle][0], price1Text, price2Text)))
			except: pass
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	async def process_data_streams(self):
		"""Posts data stream charts in Alpha community guild

		"""

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

						outputMessage, request = await self.processor.process_chart_arguments(MessageRequest(authorId=401328409499664394, guildProperties=self.guildProperties[414498292655980583]), parameters, tickerId, command="c", defaultPlatforms=["TradingLite", "TradingView"])
						request.set_current(timeframe=request.get_timeframes()[0])
						chartName, chartText = await self.processor.execute_data_server_request((401328409499664394, "chart", request))
						file = None if chartName is None else discord.File("charts/" + chartName, chartName)

						outgoingChannel = client.get_channel(dataStream["id"][i])
						await outgoingChannel.send(embed=embed, file=file)

						await message.delete()
				except asyncio.CancelledError: pass
				except Exception:
					print(traceback.format_exc())
					self.logging.report_exception()

	async def server_ping(self):
		"""Pings the server and checks pings from all Fusion network instances

		"""

		try:
			if sys.platform == "linux" and self.lastMessageTimestamp is not None:
				db.document("discord/properties").set({"pings": {"instance-1": datetime.datetime.timestamp(pytz.utc.localize(self.lastMessageTimestamp))}}, merge=True)

			fusionProperties = db.document("fusion/properties").get().to_dict()
			for instance in fusionProperties["pings"]:
				num = int(instance.split("-")[1])
				if fusionProperties["pings"][instance] + 300 < time.time():
					self.logging.report("fusion instance {} is unresponsive".format(num))
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	# -------------------------
	# Message handling
	# -------------------------

	async def on_message(self, message):
		try:
			self.lastMessageTimestamp = message.created_at
			_authorId = message.author.id if message.webhook_id is None else message.webhook_id
			_guildId = message.guild.id if message.guild is not None else -1
			messageRequest = MessageRequest(
				raw=message.clean_content,
				content=" ".join(message.clean_content.lower().split()),
				authorId=_authorId,
				guildId=_guildId,
				accountProperties=({} if _authorId not in self.accountProperties else self.accountProperties[_authorId]),
				guildProperties=({} if _guildId not in self.guildProperties else self.guildProperties[_guildId])
			)
			sentMessages = []

			if (self.dedicatedGuild != 0 and self.dedicatedGuild != messageRequest.guildId) or (self.dedicatedGuild == 0 and messageRequest.guildId in constants.vpsServers): return

			isSelf = message.author == client.user
			isUserBlocked = (messageRequest.authorId in constants.blockedBots if message.webhook_id is None else any(e in message.author.name.lower() for e in constants.blockedBotNames)) if message.author.bot else messageRequest.authorId in constants.blockedUsers
			isChannelBlocked = message.channel.id in constants.blockedChannels or messageRequest.guildId in constants.blockedGuilds
			hasContent = message.clean_content != ""
			isUserLocked = messageRequest.authorId in self.lockedUsers

			if not self.isBotReady or isSelf or isUserBlocked or isChannelBlocked or not hasContent or isUserLocked: return

			shortcutsEnabled = messageRequest.guildProperties["settings"]["messageProcessing"]["shortcuts"]
			hasSendPermission = True if messageRequest.guildId == -1 else (message.guild.me.permissions_in(message.channel).send_messages and message.guild.me.permissions_in(message.channel).embed_links and message.guild.me.permissions_in(message.channel).attach_files)

			if not messageRequest.content.startswith("preset "):
				messageRequest.content, messageRequest.presetUsed, parsedPresets = Presets.process_presets(messageRequest.content, messageRequest.accountProperties)

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

						embed = discord.Embed(title="Running `{}` command from personal preset.".format(messageRequest.content), color=constants.colors["light blue"])
						sentMessages.append(await message.channel.send(embed=embed))
					elif len(parsedPresets) != 0:
						embed = discord.Embed(title="Do you want to add preset `{}` → `{}` to your account?".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"]), color=constants.colors["light blue"])
						addPresetMessage = await message.channel.send(embed=embed)
						self.lockedUsers.add(messageRequest.authorId)

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
							self.lockedUsers.discard(messageRequest.authorId)
							return
						else:
							self.lockedUsers.discard(messageRequest.authorId)
							messageRequest.content = "preset add {} {}".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"])
				elif len(parsedPresets) != 0:
					embed = discord.Embed(title="Presets are available to Alpha Premium users only. Visit https://www.alphabotsystem.com/premium to learn more.", color=0xDD9909)
					embed.set_image(url="https://www.alphabotsystem.com/files/uploads/premium-hero.jpg")
					await message.channel.send(embed=embed)

			messageRequest.content, messageRequest.shortcutUsed = Utils.shortcuts(messageRequest.content, shortcutsEnabled)
			isCommand = messageRequest.content.startswith(tuple(constants.commandWakephrases)) and not isSelf

			if messageRequest.guildId != -1:
				if messageRequest.guildId in self.maliciousUsers:
					if any([e.id in self.maliciousUsers[messageRequest.guildId][0] for e in message.guild.members]) and time.time() + 60 < self.maliciousUsers[messageRequest.guildId][1]:
						self.maliciousUsers[messageRequest.guildId][1] = time.time()
						embed = discord.Embed(title="This Discord guild has one or more members disguising as Alpha Bot or one of the team members. Guild admins are advised to take action.", description="Users flagged for impersonation are: {}".format(", ".join(["<@!{}>".format(e.id) for e in self.maliciousUsers])), color=0x000000)
						await message.channel.send(embed=embed)

				if isCommand:
					if not hasSendPermission:
						errorText = "Alpha Bot is missing a critical permission. Try re-adding it to your guild."
						embed = discord.Embed(title=errorText, color=0x000000)
						embed.add_field(name="Frequently asked questions", value="[alphabotsystem.com/faq](https://www.alphabotsystem.com/faq)", inline=True)
						embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
						try:
							await message.channel.send(embed=embed)
						except:
							try: await message.channel.send(content=errorText)
							except: pass
						return
					elif len(self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]) != 0 and message.guild.name in self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]:
						embed = discord.Embed(title="This Discord community guild was flagged for rebranding Alpha and therefore violating the Terms of Service. Inability to comply will result in termination of all Alpha services.", color=0x000000)
						embed.add_field(name="Terms of service", value="[Read now](https://www.alphabotsystem.com/terms-of-service)", inline=True)
						embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
						await message.channel.send(embed=embed)
					elif messageRequest.content != "alpha setup" and (messageRequest.guildId != -1 and not messageRequest.guildProperties["settings"]["setup"]["completed"]):
						if not message.author.bot and message.author.permissions_in(message.channel).administrator:
							embed = discord.Embed(title="Thanks for adding Alpha Bot to your guild, we're thrilled to have you onboard. We think you're going to love everything Alpha Bot can do. Before you start using it, you must complete a short setup process. Type `alpha setup` to begin.", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
						else:
							embed = discord.Embed(title="A short setup process for Alpha hasn't been completed in this guild yet. Ask guild administrators to complete the setup process by typing `alpha setup`.", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
						return

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
					if command == "restart":
						self.isBotReady = False
						db.document("discord/properties").set({"status": {"instance-1": "rebooting"}}, merge=True)
						try:
							await message.delete()
							alphaMessage = await client.get_channel(560884869899485233).fetch_message(640502830062632960)
							await alphaMessage.edit(embed=discord.Embed(title=":warning: Alpha Bot: restarting", color=constants.colors["gray"]))
						except: pass
						raise KeyboardInterrupt
					elif command == "reboot":
						self.isBotReady = False
						db.document("discord/properties").set({"status": {"instance-1": "rebooting"}}, merge=True)
						try:
							await message.delete()
							alphaMessage = await client.get_channel(560884869899485233).fetch_message(640502830062632960)
							await alphaMessage.edit(embed=discord.Embed(title=":warning: Alpha Bot: restarting", color=constants.colors["gray"]))
						except: pass
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
					if messageRequest.guildProperties["settings"]["assistant"]["enabled"]:
						await message.channel.trigger_typing()
					fallThrough, response = await self.assistant.process_reply(messageRequest.content, rawCaps, messageRequest.guildProperties["settings"]["assistant"]["enabled"])
					if fallThrough:
						if response == "help":
							await self.help(message, messageRequest)
						elif response == "ping":
							await message.channel.send(content="Pong")
						elif response == "premium":
							await message.channel.send(content="Visit https://www.alphabotsystem.com/premium to learn more")
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
						elif response == "settings":
							pass
					elif response is not None:
						await message.channel.send(content=response)
				elif messageRequest.content.startswith("toggle "):
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest, override=True): return
					if messageRequest.guildId == -1: return

					if messageRequest.content == "toggle help":
						embed = discord.Embed(title=":control_knobs: Functionality settings", description="Enable or disable certain Alpha Bot features.", color=constants.colors["light blue"])
						embed.add_field(name=":sparkles: Enable TradingLite integration", value="```toggle tradinglite <on/off>```This setting only affects individual users.", inline=False)
						embed.add_field(name=":crystal_ball: Enable or disable the assistant", value="```toggle assistant <on/off>```Admin permissions are required to execute this command.", inline=False)
						embed.add_field(name=":globe_with_meridians: Change preferred market bias", value="```toggle bias <crypto/none>```This affects which market tickers are given priority when requesting charts. Current options are `crypto` and `none`. Admin permissions are required to execute this command.", inline=False)
						embed.add_field(name=":pushpin: Enable or disable shortcuts", value="```toggle shortcuts <on/off>```Admin permissions are required to execute this command.", inline=False)
						embed.add_field(name=":x: Enable or disable autodelete", value="```toggle autodelete <on/off>```Admin permissions are required to execute this command.", inline=False)
						embed.add_field(name=":tada: Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
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
						embed = discord.Embed(title=":bell: Price alerts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
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
							await self.add_tip_message(message, "alerts")
						else:
							embed = discord.Embed(title="Price alerts are available to premium users only. Visit https://www.alphabotsystem.com/premium to learn more.", color=0xDD9909)
							embed.set_image(url="https://www.alphabotsystem.com/files/uploads/premium-hero.jpg")
							await message.channel.send(embed=embed)
				elif messageRequest.content.startswith("preset "):
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest, override=True): return

					if messageRequest.content == "preset help":
						embed = discord.Embed(title=":pushpin: Command presets", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						if messageRequest.is_bronze():
							requestSlices = re.split(", preset | preset", messageRequest.content.split(" ", 1)[1])
							if len(requestSlices) > messageRequest.get_limit() / 2:
								await self.hold_up(message, messageRequest)
								return
							for requestSlice in requestSlices:
								await self.presets(message, messageRequest, requestSlice)
							await self.add_tip_message(message, "preset")
						else:
							embed = discord.Embed(title="Presets are available to premium users only. Visit https://www.alphabotsystem.com/premium to learn more.", color=0xDD9909)
							embed.set_image(url="https://www.alphabotsystem.com/files/uploads/premium-hero.jpg")
							await message.channel.send(embed=embed)
				elif messageRequest.content.startswith("c "):
					self.fusion.process_active_user(messageRequest.authorId, "c")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "c help":
						embed = discord.Embed(title=":chart_with_upwards_trend: Charts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
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
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 2
							else: self.rateLimited[messageRequest.authorId] = 2

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
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

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2
						await self.add_tip_message(message, "c")

						self.statistics["c"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("p "):
					self.fusion.process_active_user(messageRequest.authorId, "p")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "p help":
						embed = discord.Embed(title=":money_with_wings: Prices", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					elif messageRequest.content not in ["p "]:
						requestSlices = re.split(", p | p |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 2
							else: self.rateLimited[messageRequest.authorId] = 2

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
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

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2
						await self.add_tip_message(message, "p")

						self.statistics["p"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("v "):
					self.fusion.process_active_user(messageRequest.authorId, "v")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == ":credit_card: v help":
						embed = discord.Embed(title="Volume", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					elif messageRequest.content not in ["v "]:
						requestSlices = re.split(", v | v |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 1
							else: self.rateLimited[messageRequest.authorId] = 1

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("cg "): platform, requestSlice = "CoinGecko", requestSlice[3:]
								elif requestSlice.startswith("cx "): platform, requestSlice = "CCXT", requestSlice[3:]

								await self.volume(message, messageRequest, requestSlice, platform)
						await self.add_tip_message(message, "v")

						self.statistics["v"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("d "):
					self.fusion.process_active_user(messageRequest.authorId, "d")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "d help":
						embed = discord.Embed(title=":book: Orderbook visualizations", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					elif messageRequest.content not in ["d "]:
						requestSlices = re.split(", d | d |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 2
							else: self.rateLimited[messageRequest.authorId] = 2

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("cx "): platform, requestSlice = "CCXT", requestSlice[3:]

								chartMessages, weight = await self.depth(message, messageRequest, requestSlice, platform)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2
						await self.add_tip_message(message, "d")

						self.statistics["d"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("hmap "):
					self.fusion.process_active_user(messageRequest.authorId, "hmap")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "hmap help":
						embed = discord.Embed(title=":fire: Heat map", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
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
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 2
							else: self.rateLimited[messageRequest.authorId] = 2

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								platform = None
								if requestSlice.startswith("bg "): platform, requestSlice = "Bitgur", requestSlice[3:]
								elif requestSlice.startswith("fv "): platform, requestSlice = "Finviz", requestSlice[3:]

								chartMessages, weight = await self.heatmap(message, messageRequest, requestSlice, platform)

								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 2 - weight
								else: self.rateLimited[messageRequest.authorId] = 2 - weight
						await self.add_tip_message(message, "hmap")

						self.statistics["hmap"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith(("mcap ", "mc ")):
					self.fusion.process_active_user(messageRequest.authorId, "mcap")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content in ["mcap help", "mc help"]:
						embed = discord.Embed(title=":tools: Cryptocurrency details", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", mcap | mcap |, mc | mc |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 1
							else: self.rateLimited[messageRequest.authorId] = 1

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								await self.mcap(message, messageRequest, requestSlice)
						await self.add_tip_message(message, "mcap")

						self.statistics["mcap"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("n ") and messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
					self.fusion.process_active_user(messageRequest.authorId, "n")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "n help":
						embed = discord.Embed(title=":newspaper: News", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
					# elif messageRequest.content == "n parameters":
					# 	embed = discord.Embed(title=":chains: News parameters", description="All available news parameters you can use.", color=constants.colors["light blue"])
					# 	embed.add_field(name=":scales: Filters", value="General, AMA, Announcement, Airdrop, Brand, Burn, Conference, Contest, Exchange, Hard fork, ICO, Regulation, Meetup, Partnership, Release, Soft fork, Swap, Test, Update, Report", inline=False)
					# 	embed.set_footer(text="Use \"n parameters\" to pull up this list again.")
					# 	await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", n | n |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 1
							else: self.rateLimited[messageRequest.authorId] = 1

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
								break
							else:
								await self.news(message, messageRequest, requestSlice)
						await self.add_tip_message(message, "n")

						self.statistics["news"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("mk "):
					self.fusion.process_active_user(messageRequest.authorId, "mk")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "mk help":
						embed = discord.Embed(title=":page_facing_up: Market listings", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", mk | mk |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 1
							else: self.rateLimited[messageRequest.authorId] = 1

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								await self.markets(message, messageRequest, requestSlice)
						await self.add_tip_message(message, "mk")

						self.statistics["mk"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("convert "):
					self.fusion.process_active_user(messageRequest.authorId, "convert")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "convert help":
						embed = discord.Embed(title=":yen: Cryptocurrency conversions", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", convert | convert |, ", messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						if totalWeight > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 1
							else: self.rateLimited[messageRequest.authorId] = 1

							if self.rateLimited[messageRequest.authorId] >= messageRequest.get_limit():
								await message.channel.send(content="<@!{}>".format(messageRequest.authorId), embed=discord.Embed(title="You reached your limit of requests per minute. You can try again in a bit.", color=constants.colors["gray"]))
								self.rateLimited[messageRequest.authorId] = messageRequest.get_limit()
								totalWeight = messageRequest.get_limit()
								break
							else:
								await self.convert(message, messageRequest, requestSlice)
						await self.add_tip_message(message, "convert")

						self.statistics["convert"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("paper ") and message.channel.id in [611107823111372810, 479927662337458176]:
					self.fusion.process_active_user(messageRequest.authorId, "paper")
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "paper help":
						embed = discord.Embed(title=":joystick: Paper trader", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)

						# embed.add_field(name=":pencil2: Placing orders", value="```paper <order type> <coin> <exchange> <amount>/<amount@price>```If no price is provided, market price will be used. Both amount and price can be specified in percentages.", inline=False)
						# embed.add_field(name=":moneybag: Checking your available paper balance", value="```paper balance <exchange> [all]```If no exchange is provided, all will be listed. Paper balances smaller than 0.001 BTC are hidden by defauly. Use `all` parameter to show all available balances.", inline=False)
						# embed.add_field(name=":package: Checking your trading history", value="```paper history <exchange> [all]```Only the last 50 trades are saved.", inline=False)
						# embed.add_field(name=":outbox_tray: Checking your open or pending orders", value="```paper orders <exchange> [all]```", inline=False)
						# embed.add_field(name=":track_previous: Reset your paper trader balances", value="```paper reset```You can only reset your paper trader balances once every seven days.", inline=False)
					else:
						requestSlices = re.split(', paper | paper |, ', messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						for requestSlice in requestSlices:
							if messageRequest.content.startswith(("paper balance", "paper bal")):
								await self.fetch_paper_balance(message, messageRequest, requestSlice)
							elif messageRequest.content.startswith("paper history"):
								await self.fetch_paper_orders(message, messageRequest, requestSlice, "history")
							elif messageRequest.content.startswith("paper orders"):
								await self.fetch_paper_orders(message, messageRequest, requestSlice, "openOrders")
							elif messageRequest.content.startswith("paper reset"):
								await self.reset_paper_balance(message, messageRequest, requestSlice)
							else:
								await self.process_paper_trade(message, messageRequest, requestSlice)
						await self.add_tip_message(message, "paper")

						self.statistics["paper"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("stream ") and messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
					if message.author.bot:
						if not await self.bot_verification(message, messageRequest): return

					if messageRequest.content == "stream help":
						embed = discord.Embed(title=":abacus: Data Streams", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)

						# embed.add_field(name=":pencil2: Stream setup", value="```stream set <type>```", inline=False)
						# embed.add_field(name=":pencil2: Delete data stream", value="```stream delete```", inline=False)
					else:
						if messageRequest.is_bronze_guild():
							requestSlices = re.split(", stream | stream |, ", messageRequest.content.split(" ", 1)[1])
							if len(requestSlices) > messageRequest.get_limit() / 2:
								await self.hold_up(message, messageRequest)
								return
							for requestSlice in requestSlices:
								await self.data_stream(message, messageRequest, requestSlice)
								self.statistics["alerts"] += 1
							await self.add_tip_message(message, "alerts")
						else:
							await message.channel.send(content="Data streams are available to premium guilds only. Visit https://www.alphabotsystem.com/premium to learn more.")
			elif messageRequest.content == "brekkeven" and messageRequest.authorId in [361916376069439490, 164073578696802305, 390170634891689984]:
				self.fusion.process_active_user(messageRequest.authorId, "brekkeven")
				if message.author.bot:
					if not await self.bot_verification(message, messageRequest, override=True): return

				await self.brekkeven(message, messageRequest)
				await self.add_tip_message(message)
			else:
				if messageRequest.guildProperties["settings"]["assistant"]["enabled"]:
					response = await self.assistant.funnyReplies(messageRequest.content)
					if response is not None:
						self.statistics["alpha"] += 1
						try: await message.channel.send(content=response)
						except: pass
						return
				if await self.fusion.spam_warning(message, messageRequest, self.alphaGuild, [e.id for e in self.alphaGuild.members]): return
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	# -------------------------
	# Message actions
	# -------------------------

	async def on_reaction_add(self, reaction, user):
		try:
			if user.id in [487714342301859854, 401328409499664394]: return
			if reaction.message.author.id in [487714342301859854, 401328409499664394]:
				try: users = await reaction.users().flatten()
				except: return
				if reaction.message.author in users:
					if reaction.emoji == "☑":
						if reaction.message.guild is not None:
							guildPermissions = user.permissions_in(reaction.message.channel).manage_messages or user.id in [361916376069439490, 243053168823369728]
							if len(reaction.message.attachments) == 0:
								await reaction.message.delete()
							elif str(user.id) in reaction.message.attachments[0].filename or guildPermissions:
								await reaction.message.delete()
						else:
							await reaction.message.delete()
					elif reaction.emoji == '❌' and reaction.message.embeds[0]:
						titleText = reaction.message.embeds[0].title
						footerText = reaction.message.embeds[0].footer.text
						if footerText.startswith("Alert") and " ● id: " in footerText:
							alertId = footerText.split(" ● id: ")[1]
							marketAlerts = self.accountProperties[user.id]["marketAlerts"]

							for id in constants.supportedExchanges["Alpha Market Alerts"]:
								if id in marketAlerts:
									deletedAlerts = []
									for alert in marketAlerts[id]:
										if alertId == alert["id"]:
											deletedAlerts.append(alert)
									if len(deletedAlerts) == 1:
										marketAlerts[id].remove(deletedAlerts[0])
										db.document(self.find_database_path(user.id)).set({"marketAlerts": marketAlerts}, merge=True)
										embed = discord.Embed(title="Alert deleted", color=constants.colors["gray"])
										embed.set_footer(text=footerText)
										try: await reaction.message.edit(embed=embed)
										except: pass
										break
						elif footerText.startswith("Order") and " ● id: " in footerText:
							orderId = footerText.split(" ● id: ")[1]
							paper = self.accountProperties[user.id]["paperTrader"]

							for id in constants.supportedExchanges["Alpha Paper Trader"]:
								if id in paper:
									deletedOrders = []
									for order in paper[id]["openOrders"]:
										if orderId == order["id"]:
											deletedOrders.append(order)

									if len(deletedOrders) == 1:
										order = deletedOrders[0]
										if order["orderType"] == "buy":
											paper[id]["balance"][order["quote"]]["amount"] += order["amount"] * order["price"]
										elif order["orderType"] == "sell":
											paper[id]["balance"][order["base"]]["amount"] += order["amount"]
										paper[id]["openOrders"].remove(order)
										db.document(self.find_database_path(user.id)).set({"paperTrader": paper}, merge=True)
										embed = discord.Embed(title="Order canceled", color=constants.colors["gray"])
										embed.set_footer(text=footerText)
										try: await reaction.message.edit(embed=embed)
										except: pass
										break
						elif " → `" in titleText and titleText.endswith("`"):
							properties = self.accountProperties[user.id]
							properties, _ = Presets.update_presets(properties, remove=titleText.split("`")[1])
							db.document(self.find_database_path(user.id)).set({"commandPresets": properties["commandPresets"]}, merge=True)

							embed = discord.Embed(title="Preset deleted", color=constants.colors["gray"])
							embed.set_footer(text=footerText)
							try: await reaction.message.edit(embed=embed)
							except: pass
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()

	async def finish_request(self, message, messageRequest, weight, sentMessages):
		await asyncio.sleep(60)
		if messageRequest.authorId in self.rateLimited:
			self.rateLimited[messageRequest.authorId] -= weight
			if self.rateLimited[messageRequest.authorId] < 1: self.rateLimited.pop(messageRequest.authorId, None)

		autodeleteEnabled = messageRequest.guildProperties["settings"]["messageProcessing"]["autodelete"]
		if autodeleteEnabled:
			try: await message.delete()
			except: pass

		for message in sentMessages:
			try:
				if autodeleteEnabled: await message.delete()
				else: await message.remove_reaction("☑", message.channel.guild.me)
			except: pass

	# -------------------------
	# Help functionality
	# -------------------------

	async def help(self, message, messageRequest):
		embed = discord.Embed(title=":wave: Introduction", description="Alpha Bot is the world's most popular Discord bot for requesting charts, set price alerts, and more. Using Alpha Bot is as simple as typing a short command into any Discord channel the bot has access to. A full guide is available on [our website](https://www.alphabotsystem.com/guide)", color=constants.colors["light blue"])
		embed.add_field(name=":chart_with_upwards_trend: Charts", value="Easy access to TradingView and TradingLite charts. [View examples](https://www.alphabotsystem.com/guide/charts).", inline=False)
		embed.add_field(name=":bell: Alerts", value="Setup price alerts for select crypto exchanges. [View examples](https://www.alphabotsystem.com/guide/price-alerts).", inline=False)
		embed.add_field(name=":money_with_wings: Prices", value="Cryptocurrency prices for thousands of tickers. [View examples](https://www.alphabotsystem.com/guide/prices).", inline=False)
		embed.add_field(name=":book: Orderbook visualizations", value="Orderbook snapshot visualizations for crypto markets. [View examples](https://www.alphabotsystem.com/guide/orderbook-visualizations).", inline=False)
		embed.add_field(name=":tools: Cryptocurrency details", value="Detailed cryptocurrency information from CoinGecko. [View examples](https://www.alphabotsystem.com/guide/cryptocurrency-details).", inline=False)
		# embed.add_field(name=":newspaper: News", value="See latest news and upcoming events in the crypto space. [View examples](https://www.alphabotsystem.com/guide/news).", inline=False)
		embed.add_field(name=":yen: Cryptocurrency conversions", value="An easy way to convert between crypto and fiat rates. [View examples](https://www.alphabotsystem.com/guide/cryptocurrency-conversions).", inline=False)
		embed.add_field(name=":fire: Heat maps", value="Various heat maps from Bitgur. [View examples](https://www.alphabotsystem.com/guide/heat-maps).", inline=False)
		embed.add_field(name=":pushpin: Command presets", value="Create personal presets for easy access to features you use the most. [View examples](https://www.alphabotsystem.com/guide/command-presets).", inline=False)
		embed.add_field(name=":control_knobs: Functionality settings", value="Enable or disable certain Alpha Bot features. Type `toggle help` to learn more.", inline=False)
		embed.add_field(name=":crystal_ball: Assistant", value="Pull up Wikipedia articles, calculate math problems and get answers to many other question. Start a message with `alpha` and continue with your question.", inline=False)
		embed.add_field(name=":link: Official Alpha website", value="[alphabotsystem.com](https://www.alphabotsystem.com)", inline=True)
		embed.add_field(name=":tada: Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
		embed.add_field(name=":link: Official Alpha Twitter", value="[@AlphaBotSystem](https://twitter.com/AlphaBotSystem)", inline=True)
		embed.set_footer(text="Use \"alpha help\" to pull up this list again.")
		if messageRequest.shortcutUsed:
			try: await message.author.send(embed=embed)
			except: await message.channel.send(embed=embed)
		else:
			await message.channel.send(embed=embed)

	async def add_tip_message(self, message, command=None):
		if random.randint(0, 25) == 1:
			c = command
			while c == command: c, textSet = random.choice(list(constants.supportMessages.items()))
			try: await message.channel.send(embed=discord.Embed(title=random.choice(textSet), color=constants.colors["light blue"]))
			except: pass

	# -------------------------
	# Settings
	# -------------------------

	async def setup(self, message, messageRequest):
		try:
			if messageRequest.guildId != -1:
				if message.author.guild_permissions.administrator:
					if messageRequest.guildProperties is None or not messageRequest.guildProperties["settings"]["setup"]["completed"]:
						self.lockedUsers.add(messageRequest.authorId)
						await message.channel.trigger_typing()

						def select_bias(m):
							if m.author.id == messageRequest.authorId:
								response = ' '.join(m.clean_content.lower().split())
								if response in ["none", "crypto"]:
									db.document("discord/properties/guilds/{}".format(messageRequest.guildId)).set({"settings": {"messageProcessing": {"bias": response}}}, merge=True)
									return True

						def confirm_order(m):
							if m.author.id == messageRequest.authorId:
								response = ' '.join(m.clean_content.lower().split())
								if response in ["agree"]:
									return True

						accessibleChannels = len([e for e in message.guild.channels if message.guild.me.permissions_in(e).read_messages and e.type == discord.ChannelType.text])
						embed = discord.Embed(title=":wrench: Setup", color=constants.colors["deep purple"])
						embed.add_field(name=":scroll: Terms of service", value="By using Alpha Bot, you agree to Alpha [Terms of Service](https://www.alphabotsystem.com/terms-of-service) and [Privacy Policy](https://www.alphabotsystem.com/privacy-policy). For updates, please join the [official Alpha guild](https://discord.gg/GQeDE85).", inline=False)
						embed.add_field(name=":eye: Access", value="Alpha Bot has read access in {} {}. All messages flowing through those channels are processed, but not stored nor analyzed for sentiment, trade, or similar data. Alpha stores anonymous statistical information. If you don't intend on using the bot in some of the channels, restrict its access by disabling its *read messages* permission. For transparency, our message handling system is [open-source](https://github.com/alphabotsystem/Alpha). What data is being used and how is explained in detail in our [Privacy Policy](https://www.alphabotsystem.com/privacy-policy)".format(accessibleChannels, "channel" if accessibleChannels == 1 else "channels"), inline=False)
						embed.add_field(name=":grey_question: Help command", value="Use `alpha help` to learn more about what Alpha Bot can do.", inline=False)
						embed.add_field(name=":control_knobs: Functionality settings", value="You can enable or disable certain Alpha features. Use `toggle help` to learn more.", inline=False)
						embed.add_field(name=":link: Official Alpha website", value="[alphabotsystem.com](https://www.alphabotsystem.com)", inline=True)
						embed.add_field(name=":tada: Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
						embed.add_field(name=":link: Official Alpha Twitter", value="[@AlphaBotSystem](https://twitter.com/AlphaBotSystem)", inline=True)
						embed.set_footer(text="Use \"alpha setup\" to pull up this list again. Prompt expires in 5 minutes.")
						setupMessage = await message.channel.send(embed=embed)

						try:
							embed = discord.Embed(title=":globe_with_meridians: Select a preferred market bias. Alpha Bot will use this information to prioritize certain tickers when processing requests. Current available options are `none` or `crypto`.", description="With crypto market bias, Alpha Bot will attempt to match people's requests with cryptocurrency tickers. Reply with `crypto` to choose this option.\nNo market bias is best suited for conventional markets like stocks and forex. Reply with `none` to chosse this option.\nYou can always change this option by using the `toggle` command.", color=constants.colors["pink"])
							embed.set_footer(text="Use \"alpha setup\" to start again. Prompt expires in 5 minutes.")
							await message.channel.send(embed=embed)
							await client.wait_for('message', timeout=300.0, check=select_bias)
						except Exception:
							embed = discord.Embed(title=":wrench: Setup", description="Process canceled", color=constants.colors["gray"])
							try: await setupMessage.edit(embed=embed)
							except: pass
							return

						try:
							embed = discord.Embed(title=":white_check_mark: Reply with `agree` to confirm your  in order to complete the setup.", color=constants.colors["pink"])
							embed.set_footer(text="Use \"alpha setup\" to start again. Prompt expires in 5 minutes.")
							await message.channel.send(embed=embed)
							await client.wait_for('message', timeout=300.0, check=confirm_order)
						except:
							embed = discord.Embed(title=":wrench: Setup", description="Process canceled", color=constants.colors["gray"])
							try: await setupMessage.edit(embed=embed)
							except: pass
							return

						self.lockedUsers.discard(messageRequest.authorId)
						await message.channel.trigger_typing()

						db.document("discord/properties/guilds/{}".format(messageRequest.guildId)).set({"settings": {"setup": {"completed": True}}}, merge=True)
						embed = discord.Embed(title=":wrench: Setup", description="Congratulations, the setup process is complete.", color=constants.colors["pink"])
						await message.channel.send(embed=embed)
					else:
						embed = discord.Embed(title="Setup process has already been completed in this guild.", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
				else:
					embed = discord.Embed(title="You need administrator permissions to run the setup process.", color=constants.colors["gray"])
					await message.channel.send(embed=embed)
			else:
				embed = discord.Embed(title="Alpha Bot setup process is only available in guilds.", color=constants.colors["gray"])
				await message.channel.send(embed=embed)
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)

	async def toggle(self, message, messageRequest, requestSlice):
		try:
			if requestSlice.startswith("assistant"):
				if message.author.guild_permissions.administrator:
					newVal = None
					responseText = ""
					if requestSlice == "assistant off": newVal, responseText = False, "Assistant settings saved."
					elif requestSlice == "assistant on": newVal, responseText = True, "Assistant settings saved."

					if newVal is not None:
						db.document("discord/properties/guilds/{}".format(messageRequest.guildId)).set({"settings": {"assistant": {"enabled": newVal}}}, merge=True)
						await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
			elif requestSlice.startswith("bias"):
				if message.author.guild_permissions.administrator:
					newVal = None
					responseText = ""
					if requestSlice == "bias crypto": newVal, responseText = "crypto", "Market bias settings saved. Alpha Bot will try matching requested tickers with crypto pairs from now on."
					elif requestSlice == "bias none": newVal, responseText = "none", "Market bias settings saved. Alpha Bot will no longer try matching requested tickers."

					if newVal is not None:
						db.document("discord/properties/guilds/{}".format(messageRequest.guildId)).set({"settings": {"messageProcessing": {"bias": newVal}}}, merge=True)
						await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
			elif requestSlice.startswith("shortcuts"):
				if message.author.guild_permissions.administrator:
					newVal = None
					responseText = ""
					if requestSlice == "shortcuts off": newVal, responseText = False, "Shortcuts are now disabled."
					elif requestSlice == "shortcuts on": newVal, responseText = True, "Shortcuts are now enabled."

					if newVal is not None:
						db.document("discord/properties/guilds/{}".format(messageRequest.guildId)).set({"settings": {"messageProcessing": {"shortcuts": newVal}}}, merge=True)
						await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
			elif requestSlice.startswith("autodelete"):
				if message.author.guild_permissions.administrator:
					newVal = None
					responseText = ""
					if requestSlice == "autodelete off": newVal, responseText = False, "Autodelete settings saved. Charts will be left in chat permanently."
					elif requestSlice == "autodelete on": newVal, responseText = True, "Autodelete settings saved. Reqeusted charts will be automatically deleted after a minute."

					if newVal is not None:
						db.document("discord/properties/guilds/{}".format(messageRequest.guildId)).set({"settings": {"messageProcessing": {"autodelete": newVal}}}, merge=True)
						await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
			elif requestSlice.startswith("tradinglite"):
				newVal = None
				responseText = ""
				if requestSlice == "tradinglite off": newVal, responseText = False, "TradingLite charts will no longer appear by default, unless requested by using `c tl`."
				elif requestSlice == "tradinglite on": newVal, responseText = True, "TradingLite charts will now appear whenever possible. You can still explicitly request TradingView charts with `c tv`. You can use `toggle tradinglite off` to turn the feature back off."

				if newVal is not None:
					db.document(self.find_database_path(messageRequest.authorId)).set({"settings": {"charts": {"useTradingLite": newVal}}}, merge=True)
					await message.channel.send(embed=discord.Embed(title=responseText, color=constants.colors["pink"]))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)

	# -------------------------
	# Commands
	# -------------------------

	async def alert(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")
			method = arguments[0]

			if method in ["set", "create", "add"]:
				if len(arguments) >= 3:
					outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), command="alerts", defaultPlatforms=["Alpha Market Alerts"])
					if outputMessage is not None:
						if not messageRequest.is_muted():
							embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					await message.channel.trigger_typing()

					ticker = request.get_ticker()
					exchange = request.get_exchange()

					if exchange.id not in self.accountProperties[messageRequest.authorId]["marketAlerts"]: self.accountProperties[messageRequest.authorId]["marketAlerts"][exchange.id] = {}
					marketAlerts = self.accountProperties[messageRequest.authorId]["marketAlerts"][exchange.id]

					totalAlertCount = 0
					for key in marketAlerts: totalAlertCount += len(marketAlerts[key])
					if totalAlertCount >= messageRequest.get_number_of_price_alerts():
						embed = discord.Embed(title="Only up to {} price alerts per exchange are allowed for {} members.".format(messageRequest.get_number_of_price_alerts(), messageRequest.get_membership_text()), color=constants.colors["gray"])
						embed.set_author(name="Maximum number of price alerts reached", icon_url=firebase_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					databaseKey = ticker.symbol.replace("/", "-")
					newAlert = {
						"id": "%013x" % random.randrange(10**15),
						"timestamp": time.time(),
						"time": Utils.get_current_date(),
						"channel": str(messageRequest.authorId),
						"action": request.get_filters()[0],
						"level": request.get_numerical_parameters()[0],
						"repeat": False
					}
					levelText = Utils.format_price(exchange.ccxt, ticker.symbol, request.get_numerical_parameters()[0])

					if databaseKey not in marketAlerts: marketAlerts[databaseKey] = []
					for alert in marketAlerts[databaseKey]:
						if alert["action"] == request.get_filters()[0] and alert["level"] == request.get_numerical_parameters()[0]:
							embed = discord.Embed(title="{} alert for {} ({}) at {} {} already exists.".format(request.get_filters()[0].title(), ticker.base, exchange.name, request.get_numerical_parameters()[0], ticker.quote), color=constants.colors["gray"])
							embed.set_author(name="Alert already exists", icon_url=firebase_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))

					marketAlerts[databaseKey].append(newAlert)
					db.document(self.find_database_path(messageRequest.authorId)).set({"marketAlerts": {exchange.id: marketAlerts}}, merge=True)

					embed = discord.Embed(title="{} alert set for {} ({}) at {} {}.".format(request.get_filters()[0].title(), ticker.base, exchange.name, request.get_numerical_parameters()[0], ticker.quote), description=(None if messageRequest.authorId in [e.id for e in self.alphaGuild.members] else "Alpha Bot will be unable to deliver this alert in case your direct messages are disabled. Please join Alpha Discord guild for a guaranteed delivery: https://discord.gg/GQeDE85"), color=constants.colors["deep purple"])
					embed.set_author(name="Alert successfully set", icon_url=firebase_storage.icon)
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="Invalid command usage. Type `alert help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
			elif method in ["list", "all"]:
				if len(arguments) == 1:
					hasAlerts = False
					for id in constants.supportedExchanges["Alpha Market Alerts"]:
						if id in self.accountProperties[messageRequest.authorId]["marketAlerts"]:
							totalAlertCount = 0
							index = 0
							for key in self.accountProperties[messageRequest.authorId]["marketAlerts"][id]: totalAlertCount += len(self.accountProperties[messageRequest.authorId]["marketAlerts"][id][key])
							for databaseKey in self.accountProperties[messageRequest.authorId]["marketAlerts"][id]:
								symbol = databaseKey.replace("-", "/")
								for alert in self.accountProperties[messageRequest.authorId]["marketAlerts"][id][databaseKey]:
									hasAlerts = True
									index += 1
									base = Parser.exchanges[id].ccxt.markets[symbol]["base"]
									quote = Parser.exchanges[id].ccxt.markets[symbol]["quote"]
									tickerName = Ticker.generate_market_name(symbol, Parser.exchanges[id])
									levelText = Utils.format_price(Parser.exchanges[id].ccxt, symbol, alert["level"])

									embed = discord.Embed(title="{} alert set for {} ({}) at {} {}".format(alert["action"].title(), tickerName, Parser.exchanges[id].name, levelText, quote), color=constants.colors["deep purple"])
									embed.set_footer(text="Alert {}/{} on {} ● id: {}".format(index, totalAlertCount, Parser.exchanges[id].name, alert["id"]))
									alertMessage = await message.channel.send(embed=embed)
									sentMessages.append(alertMessage)
									try: await alertMessage.add_reaction('❌')
									except: pass
					if not hasAlerts:
						embed = discord.Embed(title="You haven't set any alerts yet.", color=constants.colors["gray"])
						embed.set_author(name="No alerts", icon_url=firebase_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="Invalid command usage. Type `alert help` to learn more.", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
			return ([], 0)

	async def presets(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.replace("`", "").split(" ", 2)
			method = arguments[0]

			if method in ["set", "create", "add"]:
				if len(arguments) == 3:
					await message.channel.trigger_typing()

					title = arguments[1]
					shortcut = arguments[2]

					if len(title) > 25:
						embed = discord.Embed(title="Shortcut title can be only up to 25 characters long.", color=constants.colors["gray"])
						embed.set_author(name="Shortcut title is too long", icon_url=firebase_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))
					elif len(shortcut) > 200:
						embed = discord.Embed(title="Shortcut command can be only up to 200 characters long.", color=constants.colors["gray"])
						embed.set_author(name="Shortcut command is too long.", icon_url=firebase_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					properties, statusParts = Presets.update_presets(messageRequest.accountProperties, add=title, shortcut=shortcut, messageRequest=messageRequest)
					statusTitle, statusMessage, statusColor = statusParts
					db.document(self.find_database_path(messageRequest.authorId)).set({"commandPresets": properties["commandPresets"]}, merge=True)

					embed = discord.Embed(title=statusMessage, color=constants.colors[statusColor])
					embed.set_author(name=statusTitle, icon_url=firebase_storage.icon)
					sentMessages.append(await message.channel.send(embed=embed))
			elif method in ["list", "all"]:
				if len(arguments) == 1:
					await message.channel.trigger_typing()

					if len(messageRequest.accountProperties["commandPresets"]) > 0:
						allPresets = {}
						numberOfPresets = len(messageRequest.accountProperties["commandPresets"])
						for preset in messageRequest.accountProperties["commandPresets"]:
							allPresets[preset["phrase"]] = preset["shortcut"]

						for i, phrase in enumerate(sorted(allPresets.keys())):
							embed = discord.Embed(title="`{}` → `{}`".format(phrase, allPresets[phrase]), color=constants.colors["deep purple"])
							embed.set_footer(text="Preset {}/{}".format(i + 1, numberOfPresets))
							presetMessage = await message.channel.send(embed=embed)
							sentMessages.append(presetMessage)
							try: await presetMessage.add_reaction('❌')
							except: pass
					else:
						embed = discord.Embed(title="You don't have any presets set", color=constants.colors["gray"])
						embed.set_author(name="No presets", icon_url=firebase_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="`{}` is not a valid argument. Type `preset help` to learn more.".format(method), color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
			return ([], 0)

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
				return (sentMessages, len(sentMessages))

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
					chartMessage = await message.channel.send(content=chartText, file=discord.File("charts/" + chartName, chartName))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: return ([], 0)
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
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
				return (sentMessages, len(sentMessages))

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
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
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
				return (sentMessages, len(sentMessages))

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
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
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
				return (sentMessages, len(sentMessages))

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
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
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
				return (sentMessages, len(sentMessages))

			for timeframe in request.get_timeframes():
				async with message.channel.typing():
					request.set_current(timeframe=timeframe)
					chartName, chartText = await self.processor.execute_data_server_request((messageRequest.authorId, "chart", request))

				if chartName is None:
					errorMessage = "Requested heat map is not available." if chartText is None else chartText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Heat map not available", icon_url=firebase_storage.icon_bw)
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
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
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
				return (sentMessages, len(sentMessages))

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
			elif not messageRequest.is_muted():
				embed = discord.Embed(title="Requested market information is not available.", color=constants.colors["gray"])
				embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
			return ([], 0)

	async def news(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, arguments = self.coindar.process_news_arguments(arguments)
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))
			tickerId, tags = arguments

			await message.channel.trigger_typing()

			try: coinThumbnail = Parser.coinGeckoIndex[base]["image"]
			except: coinThumbnail = firebase_storage.icon_bw

			try:
				sentMessages.append(await message.channel.send(embed=self.coindar.upcoming_news(tickerId, coinThumbnail, tags)))
			except Exception as e:
				embed = discord.Embed(title="News data from Coindar isn't available.", color=constants.colors["gray"])
				embed.set_author(name="Couldn't get news data", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
			return ([], 0)

	async def markets(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), command="mk", defaultPlatforms=["CCXT"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			await message.channel.trigger_typing()

			listings, total = self.parser.get_listings(request.get_ticker())
			if total != 0:
				thumbnailUrl = Parser.coinGeckoIndex[request.get_ticker().base]["image"] if request.get_ticker().base in Parser.coinGeckoIndex else firebase_storage.icon
				assetName = Parser.coinGeckoIndex[request.get_ticker().base]["name"] if request.get_ticker().base in Parser.coinGeckoIndex else request.get_ticker().base

				embed = discord.Embed(color=constants.colors["deep purple"])
				embed.set_author(name="{} listings".format(assetName), icon_url=thumbnailUrl)
				for quote, exchanges in listings:
					embed.add_field(name="{} pair found on {} exchanges".format(quote, len(exchanges)), value="{}".format(", ".join(exchanges)), inline=False)
				sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="`{}` is not listed on any crypto exchange.".format(request.get_ticker().id), color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))
			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
			return ([], 0)

	async def convert(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = CoinGecko.argument_cleanup(requestSlice).split(" ")

			outputMessage, arguments = CoinGecko.process_converter_arguments(arguments)
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))
			amount, base, quote = arguments

			isBaseInIndex = base in Parser.exchangeRates or base in Parser.coinGeckoIndex
			isQuoteInIndex = quote in Parser.exchangeRates or quote in Parser.coinGeckoIndex

			if not isBaseInIndex or not isQuoteInIndex:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Ticker `{}` does not exist".format(quote if isBaseInIndex else base), color=constants.colors["gray"])
					embed.set_author(name="Ticker not found", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			await message.channel.trigger_typing()

			convertedValue = self.parser.convert(base, quote, amount)

			embed = discord.Embed(title="{} {} ≈ {:,.8f} {}".format(amount, base, round(convertedValue, 8), quote), color=constants.colors["deep purple"])
			embed.set_author(name="Conversion", icon_url=firebase_storage.icon)
			embed.set_footer(text="Prices on CoinGecko")
			sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
			return ([], 0)

	async def fetch_paper_balance(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")[1:]

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments, command="paper", defaultPlatforms=["CCXT"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			await message.channel.trigger_typing()

			exchange = request.get_exchange()

			if exchange is not None:
				if exchange.id in constants.supportedExchanges["Alpha Paper Trader"]:
					embed = discord.Embed(title="Paper balance on {}".format(exchange.name), color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon)

					if exchange.id not in self.accountProperties[messageRequest.authorId]["paperTrader"]:
						self.accountProperties[messageRequest.authorId]["paperTrader"][exchange.id] = {"balance": PaperTrader.startingBalance[exchange.id], "openOrders": [], "history": []}
					paper = self.accountProperties[messageRequest.authorId]["paperTrader"]

					totalValue = 0
					numberOfAssets = 0
					exchangeBaseCurrency = PaperTrader.baseCurrency[exchange.id]

					for base in sorted(paper[exchange.id]["balance"].keys()):
						isFiat, _ = Parser.check_if_fiat(base)
						ticker, _ = await Parser.find_ccxt_crypto_market(Ticker(base), exchange, "CCXT", messageRequest.guildProperties["settings"]["charts"]["defaults"])

						amount = paper[exchange.id]["balance"][base]["amount"]

						if exchange.id in ["bitmex"]:
							if base == "BTC":
								valueText = "{:,.4f} XBT\n≈ {:,.6f} USD".format(amount, amount * 1)
								totalValue += amount * 1
								btcValue = -1
							else:
								coinName = "{} position".format(ticker.name)
								valueText = "{:,.0f} contracts\n≈ {:,.4f} XBT".format(amount, amount / 1)
								totalValue += amount * 1
								btcValue = -1
						else:
							if isFiat:
								valueText = "{:,.6f} {}\nStable in fiat value".format(amount, base)
								totalValue += amount
								btcValue = self.parser.convert(base, "BTC", amount)
							elif base == "BTC":
								convertedValue = self.parser.convert(base, exchangeBaseCurrency, amount)
								btcValue = self.parser.convert(base, "BTC", amount)
								valueText = "{:,.8f} {}\n≈ {:,.6f} {}".format(amount, base, convertedValue, exchangeBaseCurrency)
								totalValue += convertedValue
							else:
								convertedValue = self.parser.convert(base, exchangeBaseCurrency, amount)
								btcValue = self.parser.convert(base, "BTC", amount)
								valueText = "{:,.8f} {}\n{:,.8f} {}".format(amount, base, convertedValue, exchangeBaseCurrency)
								totalValue += convertedValue

						if btcValue > 0.001 or btcValue == -1:
							embed.add_field(name="{}:".format(Parser.coinGeckoIndex[base]["name"]), value=valueText, inline=True)
							numberOfAssets += 1

					embed.description = "Holding {} {} with estimated total value: {:,.2f} {} and {:+,.2f} % ROI.{}".format(numberOfAssets, "assets" if numberOfAssets > 1 else "asset", totalValue, exchangeBaseCurrency, (totalValue / PaperTrader.startingBalance[exchange.id][exchangeBaseCurrency]["amount"] - 1) * 100, " Trading since {} with {} balance {}.".format(Utils.timestamp_to_date(paper["globalLastReset"]), paper["globalResetCount"], "reset" if paper["globalResetCount"] == 1 else "resets") if paper["globalLastReset"] != 0 else "")
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="{} exchange is not supported. Type `paper help` to learn more.".format(exchange.name), color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="An exchange must be provided. Type `paper help` to learn more.", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
			return ([], 0)

	async def fetch_paper_orders(self, message, messageRequest, requestSlice, type):
		try:
			sentMessages = []
			arguments = requestSlice.split(" ")[1:]

			outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments, command="paper", defaultPlatforms=["CCXT"])
			if outputMessage is not None:
				if not messageRequest.is_muted():
					embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			await message.channel.trigger_typing()

			exchange = request.get_exchange()

			if exchange is not None:
				if exchange.id in constants.supportedExchanges["Alpha Paper Trader"]:
					if exchange.id not in self.accountProperties[messageRequest.authorId]["paperTrader"]:
						self.accountProperties[messageRequest.authorId]["paperTrader"][exchange.id] = {"balance": PaperTrader.startingBalance[exchange.id], "openOrders": [], "history": []}
					paper = self.accountProperties[messageRequest.authorId]["paperTrader"][exchange.id]

					if type == "history":
						if len(paper["history"]) == 0:
							embed = discord.Embed(title="No paper trading history on {}".format(exchange.name), color=constants.colors["deep purple"])
							embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon)
							sentMessages.append(await message.channel.send(embed=embed))
						else:
							embed = discord.Embed(title="Paper trading history on {}".format(exchange.name), color=constants.colors["deep purple"])
							embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon)

							for order in paper["history"]:
								quoteText = order["quote"]
								side = ""
								if order["orderType"] == "buy": side = "Bought"
								elif order["orderType"] == "sell": side = "Sold"
								elif order["orderType"].startswith("stop"): side = "Stop loss hit"
								elif order["orderType"].startswith("trailing-stop"): side, quoteText = "Trailing stop hit", "%"
								embed.add_field(name="{} {} {} at {} {}".format(side, order["amount"], order["base"], order["quote"], quoteText), value="{} ● id: {}".format(Utils.timestamp_to_date(order["timestamp"]), order["id"]), inline=False)

							sentMessages.append(await message.channel.send(embed=embed))
					else:
						openOrderCount = len(paper["openOrders"])
						if openOrderCount == 0:
							embed = discord.Embed(title="No open paper orders on {}".format(exchange.name), color=constants.colors["deep purple"])
							embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon)
							sentMessages.append(await message.channel.send(embed=embed))
						else:
							for i, order in enumerate(paper["openOrders"]):
								quoteText = order["quote"]
								side = order["orderType"].replace("-", " ").capitalize()
								if order["orderType"].startswith("trailing-stop"): quoteText = "%"

								embed = discord.Embed(title="{} {} {} at {} {}".format(side, order["amount"], order["base"], order["price"], quoteText), color=constants.colors["deep purple"])
								embed.set_footer(text="Order {}/{} ● id: {}".format(i + 1, openOrderCount, order["id"]))
								orderMessage = await message.channel.send(embed=embed)
								sentMessages.append(orderMessage)
								await orderMessage.add_reaction('❌')
				else:
					embed = discord.Embed(title="{} exchange is not supported. Type `paper help` to learn more.".format(exchange.name), color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="An exchange must be provided. Type `paper help` to learn more.", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)

	async def process_paper_trade(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []
			arguments = self.paperTrader.argument_cleanup(requestSlice).split(" ")
			orderType = arguments[0]

			if orderType in ["buy", "sell", "stop-sell", "trailing-stop-sell"] and 2 <= len(arguments) <= 8:
				outputMessage, request = await self.processor.process_price_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), command="paper", defaultPlatforms=["Alpha Paper Trader"])
				if outputMessage is not None:
					if not messageRequest.is_muted():
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=firebase_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return ([], 0)

				async with message.channel.typing():
					payload, quoteText = await self.processor.execute_data_server_request((messageRequest.authorId, "quote", request))

				if payload is None:
					errorMessage = "Requested paper {} order for {} could not be executed.".format(orderType.replace("-", " "), request.get_ticker().name) if quoteText is None else quoteText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=firebase_storage.icon_bw)
					quoteMessage = await message.channel.send(embed=embed)
					sentMessages.append(quoteMessage)
					try: await quoteMessage.add_reaction("☑")
					except: pass
				else:
					outputTitle, outputMessage, paper, pendingOrder = self.paperTrader.process_trade(self.accountProperties[messageRequest.authorId]["paperTrader"], orderType, request, payload)
					if pendingOrder is None:
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name=outputTitle, icon_url=firebase_storage.icon_bw)
						await message.channel.send(embed=embed)
						return

					confirmationText = "Do you want to place a paper {} order of {} {} on {} at {}?".format(orderType.replace("-", " "), pendingOrder.amountText, request.get_ticker().base, request.get_exchange().name, pendingOrder.priceText)
					embed = discord.Embed(title=confirmationText, color=constants.colors["pink"])
					embed.set_author(name="Confirm paper order", icon_url=payload["thumbnailUrl"])
					orderConfirmationMessage = await message.channel.send(embed=embed)
					self.lockedUsers.add(messageRequest.authorId)

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
						self.lockedUsers.discard(messageRequest.authorId)
					else:
						self.lockedUsers.discard(messageRequest.authorId)
						async with message.channel.typing():
							paper = self.paperTrader.post_trade(paper, orderType, request, payload, pendingOrder)
							if paper is None:
								await self.unknown_error(message, messageRequest.authorId)
								return

							if paper["globalLastReset"] == 0: paper["globalLastReset"] = int(time.time())
							db.document(self.find_database_path(messageRequest.authorId)).set({"paperTrader": paper}, merge=True)

						successMessage = "Paper {} order of {} {} on {} at {} was successfully {}".format(orderType.replace("-", " "), pendingOrder.amountText, request.get_ticker().base, request.get_exchange().name, pendingOrder.priceText, "executed" if pendingOrder.parameters["parameters"][0] else "placed")
						embed = discord.Embed(title=successMessage, color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon)
						await message.channel.send(embed=embed)
			else:
				embed = discord.Embed(title="Invalid command usage. Type `paper help` to learn more.", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=firebase_storage.icon_bw)
				await message.channel.send(embed=embed)
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)

	async def reset_paper_balance(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []

			if self.accountProperties[messageRequest.authorId]["paperTrader"]["globalLastReset"] + 604800 < time.time() or self.accountProperties[messageRequest.authorId]["paperTrader"]["globalResetCount"] == 0:
				embed = discord.Embed(title="Do you really want to reset your paper balance? This cannot be undone.", color=constants.colors["pink"])
				embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon)
				resetBalanceMessage = sentMessages.append(await message.channel.send(embed=embed))
				self.lockedUsers.add(messageRequest.authorId)

				def confirm_order(m):
					if m.author.id == messageRequest.authorId:
						response = ' '.join(m.clean_content.lower().split())
						if response.startswith(("y", "yes", "sure", "confirm", "execute")): return True
						elif response.startswith(("n", "no", "cancel", "discard", "reject")): raise Exception()

				try:
					this = await client.wait_for('message', timeout=60.0, check=confirm_order)
				except:
					embed = discord.Embed(title="Paper reset canceled.", description="~~Do you really want to reset your paper balance? This cannot be undone.~~", color=constants.colors["gray"])
					embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon_bw)
					await resetBalanceMessage.edit(embed=embed)
					self.lockedUsers.discard(messageRequest.authorId)
				else:
					self.lockedUsers.discard(messageRequest.authorId)
					paper = self.accountProperties[messageRequest.authorId]["paperTrader"]
					for exchange in constants.supportedExchanges["Alpha Paper Trader"]:
						paper.pop(exchange, None)
					paper["globalResetCount"] += 1
					paper["globalLastReset"] = time.time()

					db.document(self.find_database_path(messageRequest.authorId)).set({"paperTrader": paper}, merge=True)

					embed = discord.Embed(title="Paper balance was reset successfully.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon)
					sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="Paper balance can only be reset once every seven days.", color=constants.colors["gray"])
				embed.set_author(name="Alpha Paper Trader", icon_url=firebase_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

			return (sentMessages, len(sentMessages))
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
			return ([], 0)

	async def data_stream(self, message, messageRequest, requestSlice):
		try:
			arguments = requestSlice.split(" ", 2)
			method = arguments[0]

			if method in ["set", "create", "add"]:
				pass
			elif method in ["delete", "remove"]:
				pass
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)

	async def brekkeven(self, message, messageRequest, requestSlice):
		try:
			sentMessages = []

			return
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)

	# -------------------------
	# Error handling
	# -------------------------

	async def unknown_error(self, message, authorId, e=None, report=False):
		embed = discord.Embed(title="Looks like something went wrong.{}".format(" The issue was reported." if report else ""), color=constants.colors["gray"])
		embed.set_author(name="Something went wrong", icon_url=firebase_storage.icon_bw)
		try: await message.channel.send(embed=embed)
		except: return
		if sys.platform != "linux" and not report and e is not None:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			print("[Quiet Error]: debug info: {}, {}, line {}, description: {}".format(exc_type, fname, exc_tb.tb_lineno, e))

	async def bot_verification(self, message, messageRequest, override=False):
		"""Checks if a bot or a webhook is verified by checking its ID against a list of verified IDs

		Parameters
		----------
		message : discord.Message
			Received message object
		messageRequest : bot.engine.constructs.MessageRequest
			MessageRequest object constructed from discord.Message
		override : bool, optional
			A flag used to bypass verification and rejecting bots request

		Returns
		-------
		bool
			a boolean indicating if bots request shall be processed
		"""

		if override: return False
		if message.webhook_id is not None:
			if message.webhook_id not in constants.verifiedWebhooks:
				if not messageRequest.is_muted() and messageRequest.guildId != 414498292655980583:
					embed = discord.Embed(title="{} webhook is not verified with Alpha Bot. To get it verified, please join Alpha guild: https://discord.gg/GQeDE85".format(message.author.name), color=constants.colors["pink"])
					embed.set_author(name="Unverified webhook", icon_url=firebase_storage.icon_bw)
					await message.channel.send(embed=embed)
				return False
		else:
			if message.author.id not in constants.verifiedBots:
				if not messageRequest.is_muted() and messageRequest.guildId != 414498292655980583:
					embed = discord.Embed(title="{}#{} bot is not verified with Alpha Bot. To get it verified, please join Alpha guild: https://discord.gg/GQeDE85".format(message.author.name, message.author.discriminator), color=constants.colors["pink"])
					embed.set_author(name="Unverified bot", icon_url=firebase_storage.icon_bw)
					await message.channel.send(embed=embed)
				return False
		history.info("{} ({}): {}".format(Utils.get_current_date(), messageRequest.authorId, messageRequest.content))
		return True

	async def hold_up(self, message, messageRequest):
		embed = discord.Embed(title="Only up to {:d} requests are allowed per command.".format(int(messageRequest.get_limit() / 2)), color=constants.colors["gray"])
		embed.set_author(name="Too many requests", icon_url=firebase_storage.icon_bw)
		await message.channel.send(embed=embed)


# -------------------------
# Initialization
# -------------------------

def handle_exit():
	print("[Alerts]: timestamp: {}, description: closing tasks".format(Utils.get_current_date()))
	client.loop.run_until_complete(client.topgg.close())
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

	config.inProduction = (sys.platform == "linux" and not options.modeOverride) or (sys.platform != "linux" and options.modeOverride)

	if options.modeOverride: print("[Startup]: Alpha Bot is in startup, running in {} mode.".format("production" if config.inProduction else "debug"))

	client = Alpha() if options.guild == 0 else Alpha(shard_count=1)
	print("[Startup]: object initialization complete")
	client.loop.run_until_complete(client.prepare(for_guild=options.guild))

	while True:
		client.loop.create_task(client.job_queue())
		try:
			client.loop.run_until_complete(client.start(ApiKeys.get_discord_token(mode=("production" if config.inProduction else "debug"))))
		except KeyboardInterrupt:
			handle_exit()
			client.loop.close()
			break
		except (Exception, SystemExit):
			handle_exit()

		client = Alpha(loop=client.loop) if options.guild == 0 else Alpha(loop=client.loop, shard_count=1)
