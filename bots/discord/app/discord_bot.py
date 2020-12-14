import os
import sys
import re
import random
import math
import time
import datetime
import pytz
import urllib
import copy
import atexit
import asyncio
import uuid
import zlib
import pickle
import concurrent
import traceback

import discord
import stripe
import dbl as topgg
from google.cloud import firestore, error_reporting

from assets import static_storage
from helpers.utils import Utils
from helpers import constants

from TickerParser import TickerParser
from Processor import Processor
from engine.assistant import Assistant
from engine.presets import Presets
from engine.trader import PaperTrader

from MessageRequest import MessageRequest
from TickerParser import Ticker
from TickerParser import Exchange
from TickerParser import supported


database = firestore.Client()
stripe.api_key = os.environ["STRIPE_KEY"]


class Alpha(discord.AutoShardedClient):
	botStatus = []

	assistant = Assistant()
	paperTrader = PaperTrader()

	alphaSettings = {}
	accountProperties = {}
	guildProperties = {}
	accountIdMap = {}

	statistics = {"alerts": 0, "alpha": 0, "c": 0, "convert": 0, "d": 0, "flow": 0, "hmap": 0, "mcap": 0, "t": 0, "mk": 0, "n": 0, "p": 0, "paper": 0, "v": 0, "x": 0}
	rateLimited = {}
	lockedUsers = set()
	usedPresetsCache = {}
	maliciousUsers = {}

	discordSettingsLink = None
	accountsLink = None
	discordPropertiesUnregisteredUsersLink = None
	discordPropertiesGuildsLink = None
	discordMessagesLink = None
	dataserverParserIndexLink = None


	# -------------------------
	# Startup
	# -------------------------

	def prepare(self):
		"""Prepares all required objects and fetches Alpha settings

		"""

		self.botStatus = [False, False, False, False, False]
		t = datetime.datetime.now().astimezone(pytz.utc)

		atexit.register(self.cleanup)
		Processor.clientId = b"discord_alpha"
		self.executor = concurrent.futures.ThreadPoolExecutor()
		self.topgg = topgg.DBLClient(client, os.environ["TOPGG_KEY"])
		self.logging = error_reporting.Client()

		self.discordSettingsLink = database.document("discord/settings").on_snapshot(self.update_alpha_settings)
		self.accountsLink = database.collection("accounts").on_snapshot(self.update_account_properties)
		self.discordPropertiesGuildsLink = database.collection("discord/properties/guilds").on_snapshot(self.update_guild_properties)
		self.discordPropertiesUnregisteredUsersLink = database.collection("discord/properties/users").on_snapshot(self.update_unregistered_users_properties)
		self.discordMessagesLink = database.collection("discord/properties/messages").on_snapshot(self.send_pending_messages)

		statisticsData = database.document("discord/statistics").get().to_dict()
		slice = "{}-{:02d}".format(t.year, t.month)
		for data in statisticsData[slice]:
			self.statistics[data] = statisticsData[slice][data]
		print("[Startup]: parser initialization complete")

	async def on_ready(self):
		"""Initiates all Discord dependent functions and flags the bot as ready to process requests

		"""

		t = datetime.datetime.now().astimezone(pytz.utc)

		self.botStatus[0] = True
		print("[Startup]: Alpha Bot is online")

		await self.update_system_status(t)
		print("[Startup]: system status check complete")
		if os.environ["PRODUCTION_MODE"]:
			await self.update_guild_count()
			await self.update_static_messages()

		while not self.is_bot_ready():
			await asyncio.sleep(1)
		await client.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="alphabotsystem.com"))

		print("[Startup]: Alpha Bot startup complete")

	def is_bot_ready(self):
		return all(self.botStatus)

	async def update_static_messages(self):
		"""Updates all static content in various Discord channels

		"""

		try:
			# Alpha Pro messages
			proChannel = client.get_channel(669917049895518208)
			proIntroMessage = await proChannel.fetch_message(752536299407147092)
			proAlertsMessage = await proChannel.fetch_message(752536309616345108)
			proFlowMessage = await proChannel.fetch_message(752536312938102818)
			proSatellitesMessage = await proChannel.fetch_message(752536318411800716)
			proPresetsMessage = await proChannel.fetch_message(752536323218341938)
			proPricingMessage = await proChannel.fetch_message(752536331002839131)
			introEmbed = discord.Embed(title="Professional tools. For every trader.", description="Command presets, price alerts, satellites for displaying metrics like price and volume, option order flow, the ability to place live trades, and more via Alpha help turn your Discord into the only the app you need as a trader. Upgrading to Pro means less time spent switching between apps, less money spent paying for different services, and always having your trading tools at your fingertips regardless of what device you are using. Learn more about Alpha Pro on [our website](https://www.alphabotsystem.com/pro).", color=0xB7BACA)
			introEmbed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
			alertsEmbed = discord.Embed(title="Be ready when the markets move. Get notified before it happens.", description="Always stay up-to-date on price action with Alpha Price Alerts. No need to use another program, setting a real time price alert with Alpha Bot is as easy as typing a quick command. With Alpha Pro you never worry about missing a breakout or breakdown again. Learn more about Price Alerts on [our website](https://www.alphabotsystem.com/pro/price-alerts).", color=0xB7BACA)
			alertsEmbed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fdiscord-3.jpg?alt=media&token=37a90c3c-8be8-476d-a72d-8cde2ee1b52b")
			flowEmbed = discord.Embed(title="Order flow data, directly from BlackBox Stocks.", description="Get access to on-demand historic option sweeps order flow from BlackBoxStocks with Alpha Flow. Options flow data is presented in a clean and easy-to-read format, so users can quickly plan their next trade. Given that data is delayed by 24-hours, Alpha Flow is perfect for playing the market open based on yesterday’s action. Plus, Alpha Flow comes at an unbeatable price. Learn more about Alpha Flow on [our website](https://www.alphabotsystem.com/pro/flow).", color=0xB7BACA)
			flowEmbed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fpro-1.jpg?alt=media&token=589e5814-a2f1-439b-a314-cb66c02ab176")
			satellitesEmbed = discord.Embed(title="Track prices and various other metrics effortlessly.", description="Watch any price or any other market metric directly in Discord. With a wide selection of available Satellite Bots, you can make sure your financial community is always on top of the market action. Learn more about Satellite Bots on [our website](https://www.alphabotsystem.com/pro/satellites). Can’t find a ticker you want? Leave us a message in <#601095556982374401> and we’ll add it!", color=0xB7BACA)
			satellitesEmbed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fpro-2.jpg?alt=media&token=6c28741f-27f1-4f7c-8c8c-e7be75c25177")
			presetsEmbed = discord.Embed(title="A shortcut to things you use most, available everywhere.", description="Create custom presets to make talking to Alpha Bot easy. Any command you would give Alpha can be shortened by creating presets. This is great for calling a complex sequence of charts or charts with many indicators you use often. If you use the same set of parameters often, creating presets can save you time and energy. Learn more about Command Presets on [our website](https://www.alphabotsystem.com/pro/command-presets).", color=0xB7BACA)
			presetsEmbed.set_image(url="https://firebasestorage.googleapis.com/v0/b/nlc-bot-36685.appspot.com/o/alpha%2Fassets%2Fdiscord%2Fpro-3.jpg?alt=media&token=7370aefb-003c-4ba8-a31e-96072a5ad91b")
			pricingEmbed = discord.Embed(title="Pay for the features you want to use. All with one subscription.", description="Learn more about Alpha Pro pricing on [our website](https://www.alphabotsystem.com/pro/pricing).", color=0xB7BACA)
			pricingEmbed.set_image(url="https://www.alphabotsystem.com/files/uploads/alpha-pro.jpg")
			if proIntroMessage is not None: await proIntroMessage.edit(embed=introEmbed, suppress=False)
			if proAlertsMessage is not None: await proAlertsMessage.edit(embed=alertsEmbed, suppress=False)
			if proFlowMessage is not None: await proFlowMessage.edit(embed=flowEmbed, suppress=False)
			if proSatellitesMessage is not None: await proSatellitesMessage.edit(embed=satellitesEmbed, suppress=False)
			if proPresetsMessage is not None: await proPresetsMessage.edit(embed=presetsEmbed, suppress=False)
			if proPricingMessage is not None: await proPricingMessage.edit(embed=pricingEmbed, suppress=False)

			# Rules and ToS
			faqAndRulesChannel = client.get_channel(601160698310950914)
			guildRulesMessage = await faqAndRulesChannel.fetch_message(671771929530597426)
			termsOfServiceMessage = await faqAndRulesChannel.fetch_message(671771934475943936)
			faqMessage = await faqAndRulesChannel.fetch_message(671773814182641695)
			if guildRulesMessage is not None: await guildRulesMessage.edit(embed=discord.Embed(title="All members of this official Alpha community must follow the community rules. Failure to do so will result in a warning, kick, or ban, based on our sole discretion.", description="[Community rules](https://www.alphabotsystem.com/community-rules) (last modified on January 31, 2020).", color=constants.colors["deep purple"]), suppress=False)
			if termsOfServiceMessage is not None: await termsOfServiceMessage.edit(embed=discord.Embed(title="By using Alpha branded services you agree to our Terms of Service and Privacy Policy. You can read them on our website.", description="[Terms of Service](https://www.alphabotsystem.com/terms-of-service) (last modified on March 6, 2020)\n[Privacy Policy](https://www.alphabotsystem.com/privacy-policy) (last modified on January 31, 2020).", color=constants.colors["deep purple"]), suppress=False)
			if faqMessage is not None: await faqMessage.edit(embed=discord.Embed(title="If you have any questions, refer to our FAQ section, guide, or ask for help in support channels.", description="[Frequently Asked Questions](https://www.alphabotsystem.com/faq)\n[Feature overview with examples](https://www.alphabotsystem.com/guide/alpha-bot)\nFor other questions, use <#574196284215525386>.", color=constants.colors["deep purple"]), suppress=False)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def cleanup(self):
		"""Cleanup before shutdown

		"""

		try:
			if os.environ["PRODUCTION_MODE"] and self.statistics["c"] > 1000000:
				statisticsRef = database.document("discord/statistics")
				t = datetime.datetime.now().astimezone(pytz.utc)
				statisticsRef.set({"{}-{:02d}".format(t.year, t.month): self.statistics}, merge=True)
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


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

		try:
			database.document("discord/properties/guilds/{}".format(guild.id)).set(MessageRequest.create_guild_settings({"properties": {"name": guild.name, "icon": guild.icon}}))
			if guild.id in constants.bannedGuilds:
				await guild.leave()
			self.topgg.post_guild_count()
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def on_guild_remove(self, guild):
		"""Updates quild count on guild_remove event

		Parameters
		----------
		guild : discord.Guild
			Guild object passed by discord.py
		"""

		try:
			await self.update_guild_count()
			if guild.id in self.guildProperties and self.guildProperties[guild.id]["settings"]["setup"]["connection"] is not None:
				holdingId = self.guildProperties[guild.id]["settings"]["setup"]["connection"]
				if holdingId in self.accountProperties:
					communityList = self.accountProperties[holdingId]["customer"]["communitySubscriptions"]
					if str(guild.id) in communityList:
						communityList.remove(str(guild.id))
						database.document("accounts/{}".format(holdingId)).set({"customer": {"communitySubscriptions": communityList}}, merge=True)
			database.document("discord/properties/guilds/{}".format(guild.id)).delete()
			self.topgg.post_guild_count()
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def on_guild_update(self, before, after):
		try:
			database.document("discord/properties/guilds/{}".format(after.id)).set({"properties": {"name": after.name, "icon": after.icon}}, merge=True)
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

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
				if not self.is_bot_ready(): continue
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "5m" in timeframes:
					await client.loop.run_in_executor(self.executor, self.database_sanity_check)
					await client.loop.run_in_executor(self.executor, self.update_satellite_bot_counts)
					await self.update_online_member_count()
					await self.update_system_status(t)
				if "1H" in timeframes:
					await self.security_check()

			except asyncio.CancelledError: return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


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
		self.botStatus[1] = True

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
							self.accountIdMap.pop(self.account_id_for(userId))
							self.accountIdMap.pop(userId)
						self.accountIdMap[userId] = accountId
						self.accountIdMap[accountId] = userId
				else:
					self.accountProperties.pop(accountId)
					self.accountIdMap.pop(self.account_id_for(accountId))
					self.accountIdMap.pop(accountId)
			self.botStatus[2] = True

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
					if properties.get("connection") is None: continue
					self.accountProperties[accountId] = properties
					self.accountIdMap[accountId] = int(accountId)
					self.accountIdMap[int(accountId)] = accountId
				else:
					self.accountProperties.pop(accountId)
					self.accountIdMap.pop(self.account_id_for(accountId))
					self.accountIdMap.pop(accountId)
			self.botStatus[3] = True

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def update_guild_properties(self, settings, changes, timestamp):
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
				guildId = int(change.document.id)
				if change.type.name in ["ADDED", "MODIFIED"]:
					self.guildProperties[guildId] = change.document.to_dict()
				else:
					self.guildProperties.pop(guildId)
			self.botStatus[4] = True

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def send_pending_messages(self, pendingMessages, changes, timestamp):
		"""Sends all pending messages to dedicated channels

		Parameters
		----------
		pendingMessages : [google.cloud.firestore_v1.document.DocumentSnapshot]
			complete document snapshot
		changes : [google.cloud.firestore_v1.watch.DocumentChange]
			database changes in the sent snapshot
		timestamp : int
			timestamp indicating time of change in the database
		"""

		if len(changes) == 0 or not os.environ["PRODUCTION_MODE"]: return
		try:
			while not self.is_bot_ready():
				print("[Startup]: pending messages snapshot is waiting for setup completion ({})".format(timestamp))
				time.sleep(5)

			for change in changes:
				message = change.document.to_dict()
				if change.type.name == "ADDED":
					database.document("discord/properties/messages/{}".format(change.document.id)).delete()
					embed = discord.Embed(title=message["title"], description=message["description"], color=message["color"])
					if message["subtitle"] is not None: embed.set_author(name=message["subtitle"], icon_url=(message["icon"] if "icon" in message else static_storage.icon))
					if "image" in message: embed.set_image(url=message["image"])
					if "url" in message: embed.url = message["url"]

					destinationUser = None if message["user"] is None else client.get_user(int(message["user"]))
					destinationChannel = None if message["channel"] is None else client.get_channel(int(message["channel"]))
					
					if destinationUser is not None:
						try:
							client.loop.create_task(destinationUser.send(embed=embed))
						except:
							mentionText = None if destinationUser is None else "<@!{}>!".format(destinationUser.id)
							client.loop.create_task(destinationChannel.send(content=mentionText, embed=embed))
					elif destinationChannel is not None and destinationChannel.id not in [742325964482019359, 739052329361211474, 738109593376260246, 738427004969287781]:
						try:
							client.loop.create_task(destinationChannel.send(embed=embed))
						except:
							pass
					elif destinationChannel is not None:
						try:
							client.loop.create_task(self.post_announcement(destinationChannel, embed))
						except:
							pass

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def post_announcement(self, channel, embed):
		try:
			announcement = await channel.send(embed=embed)
			await announcement.publish()
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def account_id_for(self, id):
		"""Finds an account id for a passed Discord user Id

		Parameters
		----------
		id : int
			user id
		"""

		return self.accountIdMap.get(id, None)


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
							nameSlice = guild.me.nick.lower().replace(" ", "")[i:i+3]
							if nameSlice in guild.name.lower() and nameSlice not in ["the"]:
								botNicknames.append("```{}: {}```".format(guild.name, guild.me.nick))
								break
					else:
						if isBlacklisted: self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"].pop(guild.name)
						if isWhitelisted: self.alphaSettings["tosWatchlist"]["nicknames"]["whitelist"].pop(guild.name)

			botNicknamesText = "No bot nicknames to review"
			if len(botNicknames) > 0: botNicknamesText = "These guilds might be rebranding Alpha Bot:{}".format("".join(botNicknames))

			if os.environ["PRODUCTION_MODE"]:
				usageReviewChannel = client.get_channel(571786092077121536)
				botNicknamesMessage = await usageReviewChannel.fetch_message(709335020174573620)
				await botNicknamesMessage.edit(content=botNicknamesText[:2000])

				database.document("discord/settings").set({"tosWatchlist": self.alphaSettings["tosWatchlist"]}, merge=True)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def database_sanity_check(self):
		if not os.environ["PRODUCTION_MODE"]: return
		try:
			for guild in client.guilds:
				if guild.id not in self.guildProperties or "addons" not in self.guildProperties[guild.id]:
					database.document("discord/properties/guilds/{}".format(guild.id)).set(MessageRequest.create_guild_settings(self.guildProperties.get(guild.id, {"properties": {"name": guild.name, "icon": guild.icon}})))
			guildIds = [g.id for g in client.guilds]
			for guildId in list(self.guildProperties.keys()):
				if guildId not in guildIds:
					if self.guildProperties[guildId]["settings"]["setup"]["connection"] is not None:
						holdingId = self.guildProperties[guildId]["settings"]["setup"]["connection"]
						if holdingId in self.accountProperties:
							communityList = self.accountProperties[holdingId]["customer"]["communitySubscriptions"]
							if str(guildId) in communityList:
								communityList.remove(str(guildId))
								database.document("accounts/{}".format(holdingId)).set({"customer": {"communitySubscriptions": communityList}}, merge=True)
					database.document("discord/properties/guilds/{}".format(guildId)).delete()

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	def update_satellite_bot_counts(self):
		try:
			affectedAccountIds = []
			countMap = {}
			for guild in client.guilds:
				if self.guildProperties[guild.id]["settings"]["setup"]["completed"] and self.guildProperties[guild.id]["addons"]["satellites"].get("connection") is not None:
					accountProperties = self.accountProperties[self.guildProperties[guild.id]["addons"]["satellites"]["connection"]]
					satellitesEnabled = self.guildProperties[guild.id]["addons"]["satellites"]["enabled"]
					isPro = accountProperties["customer"]["personalSubscription"].get("subscription", None) is not None

					satelliteCount = 0
					serverMembers = [e.id for e in guild.members]
					for key, value in constants.satellites.items():
						if value in serverMembers:
							satelliteCount += 1

					if satelliteCount > 0:
						if satellitesEnabled and not isPro:
							database.document("discord/properties/guilds/{}".format(guild.id)).set({"addons": {"satellites": {"enabled": False, "count": 0}}}, merge=True)
						
						elif satellitesEnabled and isPro:
							database.document("discord/properties/guilds/{}".format(guild.id)).set({"addons": {"satellites": {"count": satelliteCount}}}, merge=True)
							accountId = self.guildProperties[guild.id]["addons"]["satellites"]["connection"]
							if accountId not in affectedAccountIds: affectedAccountIds.append(accountId)
							countMap[accountId] = countMap.get(accountId, []) + [(guild.id, satelliteCount)]

						elif not satellitesEnabled and isPro:
							database.document("discord/properties/guilds/{}".format(guild.id)).set({"addons": {"satellites": {"count": satelliteCount, "enabled": True}}}, merge=True)
							accountId = self.guildProperties[guild.id]["addons"]["satellites"]["connection"]
							if accountId not in affectedAccountIds: affectedAccountIds.append(accountId)
							countMap[accountId] = countMap.get(accountId, []) + [(guild.id, satelliteCount)]
			
			for accountId in affectedAccountIds:
				guildMap, satelliteCount = [e[0] for e in countMap[accountId]], sum([e[1] for e in countMap[accountId]])
				accountProperties = self.accountProperties[accountId]
				
				if accountProperties["customer"]["addons"].get("satellites", 0) < satelliteCount:
					if os.environ["PRODUCTION_MODE"]:
						subscription = stripe.Subscription.retrieve(accountProperties["customer"]["personalSubscription"]["subscription"])
						cycleRatio = (subscription["current_period_end"] - time.time()) / (subscription["current_period_end"] - subscription["current_period_start"])
						stripe.SubscriptionItem.create_usage_record(subscription["items"]["data"][0]["id"], quantity=int(math.ceil((satelliteCount - accountProperties["customer"]["addons"].get("satellites", 0)) * 20 * cycleRatio)), timestamp=int(time.time()))
						database.document("accounts/{}".format(accountId)).set({"customer": {"addons": {"satellites": satelliteCount}}}, merge=True)
					else:
						print("[Development]: Charging {} for {} new satellites".format(accountId, satelliteCount - accountProperties["customer"]["addons"].get("satellites", 0)))

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def update_online_member_count(self):
		try:
			affectedAccountIds = []
			countMap = {}
			adjustmentConstant = 5 * 60 / 86400.0
			for guild in client.guilds:
				if self.guildProperties[guild.id]["settings"]["setup"]["completed"] and self.guildProperties[guild.id]["addons"]["noads"].get("connection") is not None:
					accountProperties = self.accountProperties[self.guildProperties[guild.id]["addons"]["noads"]["connection"]]
					noadsEnabled = self.guildProperties[guild.id]["addons"]["noads"]["enabled"]
					isPro = accountProperties["customer"]["personalSubscription"].get("subscription", None) is not None

					if noadsEnabled and not isPro:
						database.document("discord/properties/guilds/{}".format(guild.id)).set({"addons": {"noads": {"enabled": False, "count": 0}}}, merge=True)

					elif noadsEnabled and isPro:
						try:
							result = await client.http.request(discord.http.Route("GET", "/guilds/{}/preview".format(guild.id), guild_id=guild.id))
							onlineCount = result["approximate_presence_count"]
							if onlineCount == 0: raise Exception("incorrect online count")
						except Exception as e:
							if os.environ["PRODUCTION_MODE"]: self.logging.report("Error getting guild preview: ({}) {}".format(e, guild.id))
							continue
						database.document("discord/properties/guilds/{}".format(guild.id)).set({"addons": {"noads": {"count": onlineCount}}}, merge=True)
						accountId = self.guildProperties[guild.id]["addons"]["noads"]["connection"]
						if accountId not in affectedAccountIds: affectedAccountIds.append(accountId)
						countMap[accountId] = countMap.get(accountId, []) + [(guild.id, onlineCount)]

					elif self.guildProperties[guild.id]["addons"]["noads"].get("count", 0) != 0:
						database.document("discord/properties/guilds/{}".format(guild.id)).set({"addons": {"noads": {"count": 0}}}, merge=True)
						accountId = self.guildProperties[guild.id]["addons"]["noads"]["connection"]
						if accountId not in affectedAccountIds: affectedAccountIds.append(accountId)
						countMap[accountId] = countMap.get(accountId, []) + [(guild.id, 0)]

			for accountId in affectedAccountIds:
				guildMap, onlineCount = [e[0] for e in countMap[accountId]], sum([e[1] for e in countMap[accountId]])
				estimatedCount = 0
				accountProperties = self.accountProperties[accountId]
				
				if accountProperties["customer"]["addons"].get("noads", 0) == 0 and onlineCount != 0:
					estimatedCount = onlineCount
					if os.environ["PRODUCTION_MODE"]:
						subscription = stripe.Subscription.retrieve(accountProperties["customer"]["personalSubscription"]["subscription"])
						cycleRatio = (subscription["current_period_end"] - time.time()) / (subscription["current_period_end"] - subscription["current_period_start"])
						stripe.SubscriptionItem.create_usage_record(subscription["items"]["data"][0]["id"], quantity=int(math.ceil(math.log2(estimatedCount) * 10 * cycleRatio)), timestamp=int(time.time()))
					else:
						print("[Development]: Charging {} for {} users".format(accountId, estimatedCount))

				elif onlineCount != 0:
					estimatedCount = accountProperties["customer"]["addons"]["noads"] * (1 - adjustmentConstant) + onlineCount * adjustmentConstant

				if estimatedCount != accountProperties["customer"]["addons"].get("noads", 0):
					if os.environ["PRODUCTION_MODE"]:
						database.document("accounts/{}".format(accountId)).set({"customer": {"addons": {"noads": estimatedCount}}}, merge=True)
					else:
						print("[Development]: Estimated user count set to {} for {}".format(estimatedCount, accountId))

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def update_system_status(self, t):
		"""Updates system status messages in Alpha community guild

		Parameters
		----------
		t : datetime.datetime
			current datetime
		"""

		try:
			statisticsRef = database.document("discord/statistics")
			statisticsRef.set({"{}-{:02d}".format(t.year, t.month): self.statistics}, merge=True)

			numOfCharts = ":chart_with_upwards_trend: {:,} charts requested".format(self.statistics["c"] + self.statistics["hmap"])
			numOfAlerts = ":bell: {:,} alerts set".format(self.statistics["alerts"])
			numOfPrices = ":money_with_wings: {:,} prices & details pulled".format(self.statistics["d"] + self.statistics["p"] + self.statistics["v"] + self.statistics["mcap"] + self.statistics["mk"] + self.statistics["convert"])
			numOfTrades = ":dart: {:,} trades executed".format(self.statistics["paper"] + self.statistics["x"])
			numOfQuestions = ":crystal_ball: {:,} questions asked".format(self.statistics["alpha"])
			numOfGuilds = ":heart: Used in {:,} Discord communities".format(len(client.guilds))

			req = urllib.request.Request("https://status.discordapp.com", headers={"User-Agent": "Mozilla/5.0"})
			webpage = str(urllib.request.urlopen(req).read())
			isAlphaOnline = "All Systems Operational" in webpage

			statisticsEmbed = discord.Embed(title="{}\n{}\n{}\n{}\n{}\n{}".format(numOfCharts, numOfAlerts, numOfPrices, numOfTrades, numOfQuestions, numOfGuilds), color=constants.colors["deep purple"])
			statusEmbed = discord.Embed(title="{} Alpha Bot: {}".format(":white_check_mark:" if isAlphaOnline else ":warning:", "all systems operational" if isAlphaOnline else "degraded performance"), color=constants.colors["deep purple" if isAlphaOnline else "gray"])

			if os.environ["PRODUCTION_MODE"]:
				statusChannel = client.get_channel(560884869899485233)
				statsMessage = await statusChannel.fetch_message(640502810244415532)
				onlineMessage = await statusChannel.fetch_message(640502830062632960)
				if statsMessage is not None:
					await statsMessage.edit(embed=statisticsEmbed, suppress=False)
				if onlineMessage is not None:
					await onlineMessage.edit(embed=statusEmbed, suppress=False)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Message handling
	# -------------------------

	async def on_message(self, message):
		try:
			_rawMessage = " ".join(message.clean_content.split())
			_messageContent = _rawMessage.lower()
			_authorId = message.author.id if message.webhook_id is None else message.webhook_id
			_guildId = message.guild.id if message.guild is not None else -1
			if _authorId == 361916376069439490 and " --user " in _messageContent: _messageContent, _authorId = _messageContent.split(" --user ")[0], int(_messageContent.split(" --user ")[1])
			if _authorId == 361916376069439490 and " --guild " in _messageContent: _messageContent, _guildId = _messageContent.split(" --guild ")[0], int(_messageContent.split(" --guild ")[1])
			messageRequest = MessageRequest(
				raw=_rawMessage,
				content=_messageContent,
				authorId=_authorId,
				guildId=_guildId,
				accountProperties=({} if self.account_id_for(_authorId) is None else self.accountProperties[self.account_id_for(_authorId)]),
				guildProperties=({} if _guildId not in self.guildProperties else self.guildProperties[_guildId])
			)
			sentMessages = []

			isSelf = message.author == client.user
			isUserBlocked = (messageRequest.authorId in constants.blockedBots if message.webhook_id is None else any(e in message.author.name.lower() for e in constants.blockedBotNames)) if message.author.bot else messageRequest.authorId in constants.blockedUsers
			isChannelBlocked = message.channel.id in constants.blockedChannels or messageRequest.guildId in constants.blockedGuilds
			hasContent = messageRequest.raw != "" and message.type == discord.MessageType.default
			isUserLocked = messageRequest.authorId in self.lockedUsers

			if not self.is_bot_ready() or isSelf or isUserBlocked or isChannelBlocked or not hasContent or isUserLocked: return

			hasPermissions = True if messageRequest.guildId == -1 else (message.guild.me.permissions_in(message.channel).send_messages and message.guild.me.permissions_in(message.channel).embed_links and message.guild.me.permissions_in(message.channel).attach_files and message.guild.me.permissions_in(message.channel).add_reactions and message.guild.me.permissions_in(message.channel).manage_messages)

			if not messageRequest.content.startswith("preset "):
				messageRequest.content, messageRequest.presetUsed, parsedPresets = Presets.process_presets(messageRequest.content, messageRequest.accountProperties)

				if not messageRequest.presetUsed and messageRequest.guildId in self.usedPresetsCache:
					for preset in self.usedPresetsCache[messageRequest.guildId]:
						if preset["phrase"] == messageRequest.content:
							if preset["phrase"] not in [p["phrase"] for p in parsedPresets]:
								parsedPresets = [preset]
								messageRequest.presetUsed = False
								break
				
				if messageRequest.presetUsed or len(parsedPresets) != 0:
					if messageRequest.command_presets_available():
						if messageRequest.presetUsed:
							if messageRequest.guildId != -1:
								if messageRequest.guildId not in self.usedPresetsCache: self.usedPresetsCache[messageRequest.guildId] = []
								for preset in parsedPresets:
									if preset not in self.usedPresetsCache[messageRequest.guildId]: self.usedPresetsCache[messageRequest.guildId].append(preset)
								self.usedPresetsCache[messageRequest.guildId] = self.usedPresetsCache[messageRequest.guildId][-3:]

							embed = discord.Embed(title="Running `{}` command from personal preset.".format(messageRequest.content), color=constants.colors["light blue"])
							sentMessages.append(await message.channel.send(embed=embed))
						elif len(parsedPresets) != 0:
							embed = discord.Embed(title="Do you want to add `{}` preset to your account?".format(parsedPresets[0]["phrase"]), description="`{}` → `{}`".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"]), color=constants.colors["light blue"])
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
								self.lockedUsers.discard(messageRequest.authorId)
								embed = discord.Embed(title="Canceled", description="~~Do you want to add `{}` preset to your account?~~".format(parsedPresets[0]["phrase"]), color=constants.colors["gray"])
								try: await addPresetMessage.edit(embed=embed)
								except: pass
								return
							else:
								self.lockedUsers.discard(messageRequest.authorId)
								messageRequest.content = "preset add {} {}".format(parsedPresets[0]["phrase"], parsedPresets[0]["shortcut"])

					elif messageRequest.is_pro():
						if not message.author.bot and message.author.permissions_in(message.channel).administrator:
							embed = discord.Embed(title=":pushpin: Command Presets are disabled.", description="You can enable Command Presets feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
							embed.set_author(name="Command Presets", icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)
						else:
							embed = discord.Embed(title=":pushpin: Command Presets are disabled.", description="You can enable Command Presets feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
							embed.set_author(name="Command Presets", icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)
						return

					elif messageRequest.is_registered():
						embed = discord.Embed(title=":gem: Command Presets are available to Alpha Pro users or communities for only $1.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
						embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
						await message.channel.send(embed=embed)
						return

			messageRequest.content = Utils.shortcuts(messageRequest.content)
			isCommand = messageRequest.content.startswith(tuple(constants.commandWakephrases)) and not isSelf

			if messageRequest.guildId != -1:
				if messageRequest.guildId in self.maliciousUsers:
					if any([e.id in self.maliciousUsers[messageRequest.guildId][0] for e in message.guild.members]) and time.time() + 60 < self.maliciousUsers[messageRequest.guildId][1]:
						self.maliciousUsers[messageRequest.guildId][1] = time.time()
						embed = discord.Embed(title="This Discord guild has one or more members disguising as Alpha Bot or one of the team members. Guild admins are advised to take action.", description="Users flagged for impersonation are: {}".format(", ".join(["<@!{}>".format(e.id) for e in self.maliciousUsers])), color=0x000000)
						try: await message.channel.send(embed=embed)
						except: pass

				if isCommand:
					if not hasPermissions:
						p1 = message.guild.me.permissions_in(message.channel).send_messages
						p2 = message.guild.me.permissions_in(message.channel).embed_links
						p3 = message.guild.me.permissions_in(message.channel).attach_files
						p4 = message.guild.me.permissions_in(message.channel).add_reactions
						p5 = message.guild.me.permissions_in(message.channel).manage_messages
						errorText = "Alpha Bot is missing one or more critical permissions."
						permissionsText = "Send messages: {}\nEmbed links: {}\nAttach files: {}\nAdd reactions: {}\nManage Messages: {}".format(":white_check_mark:" if p1 else ":x:", ":white_check_mark:" if p2 else ":x:", ":white_check_mark:" if p3 else ":x:", ":white_check_mark:" if p4 else ":x:", ":white_check_mark:" if p5 else ":x:")
						embed = discord.Embed(title=errorText, description=permissionsText, color=0x000000)
						embed.add_field(name="Frequently asked questions", value="[alphabotsystem.com/faq](https://www.alphabotsystem.com/faq)", inline=False)
						embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=False)
						try:
							await message.channel.send(embed=embed)
						except:
							try: await message.channel.send(content="{}\n{}".format(errorText, permissionsText))
							except: pass
						return
					elif len(self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]) != 0 and message.guild.name in self.alphaSettings["tosWatchlist"]["nicknames"]["blacklist"]:
						embed = discord.Embed(title="This Discord community guild was flagged for rebranding Alpha and is therefore violating the Terms of Service. Inability to comply will result in termination of all Alpha branded services.", color=0x000000)
						embed.add_field(name="Terms of service", value="[Read now](https://www.alphabotsystem.com/terms-of-service)", inline=True)
						embed.add_field(name="Alpha Discord guild", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
						await message.channel.send(embed=embed)
					elif not messageRequest.guildProperties["settings"]["setup"]["completed"]:
						if not message.author.bot and message.author.permissions_in(message.channel).administrator:
							embed = discord.Embed(title="Hello world!", description="Thanks for adding Alpha Bot to your Discord community, we're thrilled to have you onboard. We think you're going to love everything Alpha Bot can do. Before you start using it, you must complete a short setup process. Sign into your [Alpha Account](https://www.alphabotsystem.com/communities) and visit your [Communities Dashboard](https://www.alphabotsystem.com/communities) to begin.", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
						else:
							embed = discord.Embed(title="Hello world!", description="This is Alpha Bot, the most advanced financial bot on Discord. A short setup process hasn't been completed in this Discord community yet. Ask administrators to complete it by signing into their [Alpha Account](https://www.alphabotsystem.com/communities) and visiting their [Communities Dashboard](https://www.alphabotsystem.com/communities).", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
						return

			if messageRequest.content.startswith("a "):
				if message.author.bot: return

				command = messageRequest.content.split(" ", 1)[1]
				if message.author.id in [361916376069439490, 390170634891689984]:
					if command == "user":
						await message.delete()
						settings = copy.deepcopy(messageRequest.accountProperties)
						settings.pop("apiKeys", None)
						settings.pop("commandPresets", None)
						if "oauth" in settings: settings["oauth"]["discord"].pop("accessToken", None)
						settings.pop("paperTrader", None)
						await message.author.send(content="```json\n{}\n```".format(json.dumps(settings, indent=3, sort_keys=True)))
					elif command == "guild":
						await message.delete()
						settings = copy.deepcopy(messageRequest.guildProperties)
						await message.author.send(content="```json\n{}\n```".format(json.dumps(settings, indent=3, sort_keys=True)))
					elif command.startswith("del"):
						if message.guild.me.guild_permissions.manage_messages:
							parameters = messageRequest.content.split("del ", 1)
							if len(parameters) == 2:
								await message.channel.purge(limit=int(parameters[1]) + 1, bulk=True)
					elif command.startswith("say") and messageRequest.authorId == 361916376069439490:
						say = message.content.split("say ", 1)
						await message.channel.send(say[1])
			elif isCommand:
				if messageRequest.content.startswith(("alpha ", "alpha, ", "@alpha ", "@alpha, ")):
					self.statistics["alpha"] += 1
					
					if messageRequest.content == messageRequest.raw.lower():
						rawCaps = messageRequest.raw.split(" ", 1)[1]
					else:
						rawCaps = messageRequest.content.split(" ", 1)[1]
					
					if len(rawCaps) > 500: return
					if messageRequest.guildProperties["settings"]["assistant"]["enabled"]:
						await message.channel.trigger_typing()
					fallThrough, response = await self.assistant.process_reply(messageRequest.content, rawCaps, messageRequest.guildProperties["settings"]["assistant"]["enabled"])

					if fallThrough:
						if response == "help":
							await self.help(message, messageRequest)
						elif response == "ping":
							await message.channel.send(content="Pong")
						elif response == "pro":
							await message.channel.send(content="Visit https://www.alphabotsystem.com/pro to learn more about Alpha Pro and how to start your free trial.")
						elif response == "invite":
							await message.channel.send(content="https://discord.com/oauth2/authorize?client_id=401328409499664394&scope=bot&permissions=604372032")
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
						elif response == "settings":
							pass
					elif response is not None and response != "":
						await message.channel.send(content=response)
				elif messageRequest.content.startswith("preset "):
					if message.author.bot: return

					if messageRequest.content == "preset help":
						embed = discord.Embed(title=":pushpin: Command presets", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", preset | preset ", messageRequest.content.split(" ", 1)[1])
						if len(requestSlices) > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							await self.presets(message, messageRequest, requestSlice)
						await self.add_tip_message(message, messageRequest, "preset")
				elif messageRequest.content.startswith("c "):
					if messageRequest.content == "c help":
						embed = discord.Embed(title=":chart_with_upwards_trend: Charts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
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
								elif requestSlice.startswith("bm "): platform, requestSlice = "Bookmap", requestSlice[3:]
								elif requestSlice.startswith("gc "): platform, requestSlice = "GoCharting", requestSlice[3:]
								elif requestSlice.startswith("fv "): platform, requestSlice = "Finviz", requestSlice[3:]

								chartMessages, weight = await self.chart(message, messageRequest, requestSlice, platform)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2
						await self.add_tip_message(message, messageRequest, "c")

						self.statistics["c"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("flow "):
					if messageRequest.content == "flow help":
						embed = discord.Embed(title=":microscope: Alpha Flow", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", flow | flow |, ", messageRequest.content.split(" ", 1)[1])
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
								if requestSlice.startswith("bb "): platform, requestSlice = "Alpha Flow", requestSlice[3:]

								chartMessages, weight = await self.flow(message, messageRequest, requestSlice, platform)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2
						await self.add_tip_message(message, messageRequest, "flow")

						self.statistics["flow"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("hmap "):
					if messageRequest.content == "hmap help":
						embed = discord.Embed(title=":fire: Heat map", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
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
						await self.add_tip_message(message, messageRequest, "hmap")

						self.statistics["hmap"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("d "):
					if messageRequest.content == "d help":
						embed = discord.Embed(title=":book: Orderbook visualizations", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
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
						await self.add_tip_message(message, messageRequest, "d")

						self.statistics["d"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith(("alert ", "alerts ")):
					if message.author.bot: return

					if messageRequest.content in ["alert help", "alerts help"]:
						embed = discord.Embed(title=":bell: Price Alerts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", alert | alert |, alerts | alerts |, ", messageRequest.content.split(" ", 1)[1])
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
								elif requestSlice.startswith("cm "): platform, requestSlice = "CCXT", requestSlice[3:]
								elif requestSlice.startswith("tm "): platform, requestSlice = "IEXC", requestSlice[3:]

								quoteMessages, weight = await self.alert(message, messageRequest, requestSlice, platform)
								sentMessages += quoteMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2
						await self.add_tip_message(message, messageRequest, "alerts")

						self.statistics["alerts"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("p "):
					if messageRequest.content == "p help":
						embed = discord.Embed(title=":money_with_wings: Prices", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
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
								elif requestSlice.startswith("cm "): platform, requestSlice = "CCXT", requestSlice[3:]
								elif requestSlice.startswith("tm "): platform, requestSlice = "IEXC", requestSlice[3:]

								quoteMessages, weight = await self.price(message, messageRequest, requestSlice, platform)
								sentMessages += quoteMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2
						await self.add_tip_message(message, messageRequest, "p")

						self.statistics["p"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("v "):
					if messageRequest.content == ":credit_card: v help":
						embed = discord.Embed(title="Volume", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
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
								elif requestSlice.startswith("tm "): platform, requestSlice = "IEXC", requestSlice[3:]

								await self.volume(message, messageRequest, requestSlice, platform)
						await self.add_tip_message(message, messageRequest, "v")

						self.statistics["v"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("convert "):
					if messageRequest.content == "convert help":
						embed = discord.Embed(title=":yen: Cryptocurrency conversions", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
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
						await self.add_tip_message(message, messageRequest, "convert")

						self.statistics["convert"] += totalWeight
				elif messageRequest.content.startswith(("m ", "info")):
					if messageRequest.content in ["m help", "info help"]:
						embed = discord.Embed(title=":tools: Market information", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", m | m |, info | info |, ", messageRequest.content.split(" ", 1)[1])
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
								await self.details(message, messageRequest, requestSlice)
						await self.add_tip_message(message, messageRequest, "mcap")

						self.statistics["mcap"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith(("t ", "top")):
					if messageRequest.content in ["t help", "top help"]:
						embed = discord.Embed(title=":tools: Rankings", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", t | t |, top | top |, ", messageRequest.content.split(" ", 1)[1])
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
								await self.rankings(message, messageRequest, requestSlice)
						await self.add_tip_message(message, messageRequest, "top")

						self.statistics["t"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("mk "):
					if messageRequest.content == "mk help":
						embed = discord.Embed(title=":page_facing_up: Market listings", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
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
						await self.add_tip_message(message, messageRequest, "mk")

						self.statistics["mk"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("n ") and messageRequest.authorId in [361916376069439490, 390170634891689984]:
					if messageRequest.content == "n help":
						embed = discord.Embed(title=":newspaper: News", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["gray"])
						await message.channel.send(embed=embed)
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
						await self.add_tip_message(message, messageRequest, "n")

						self.statistics["n"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("stream ") and messageRequest.authorId in [361916376069439490, 390170634891689984]:
					if message.author.bot: return

					if messageRequest.content == "stream help":
						embed = discord.Embed(title=":abacus: Data Streams", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", stream | stream |, ", messageRequest.content.split(" ", 1)[1])
						if len(requestSlices) > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							await self.data_stream(message, messageRequest, requestSlice)
							self.statistics["alerts"] += 1
						await self.add_tip_message(message, messageRequest, "alerts")
						
						await self.finish_request(message, messageRequest, totalWeight, [])
				elif messageRequest.content.startswith("x ") and messageRequest.authorId == 361916376069439490:
					if messageRequest.content == "x help":
						embed = discord.Embed(title=":dart: Live trading", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(', x | x |, ', messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						for requestSlice in requestSlices:
							if messageRequest.content.startswith(("x balance", "x bal")):
								await self.fetch_live_balance(message, messageRequest, requestSlice)
							elif messageRequest.content.startswith("x history"):
								await self.fetch_live_orders(message, messageRequest, requestSlice, "history")
							elif messageRequest.content.startswith("x orders"):
								await self.fetch_live_orders(message, messageRequest, requestSlice, "openOrders")
							elif messageRequest.content.startswith("x reset"):
								await message.channel.send(content="Nice try")
							else:
								await self.process_live_trade(message, messageRequest, requestSlice)
						await self.add_tip_message(message, messageRequest, "x")

						self.statistics["x"] += totalWeight
				elif messageRequest.content.startswith("paper "):
					if messageRequest.content == "paper help":
						embed = discord.Embed(title=":joystick: Alpha Paper Trader", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
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
						await self.add_tip_message(message, messageRequest, "paper")

						self.statistics["paper"] += totalWeight
			elif messageRequest.content == "brekkeven" and messageRequest.authorId in [361916376069439490, 390170634891689984]:
				if message.author.bot: return

				await self.brekkeven(message, messageRequest)
				await self.add_tip_message(message, messageRequest)
			else:
				if messageRequest.guildProperties["settings"]["assistant"]["enabled"]:
					response = await self.assistant.funnyReplies(messageRequest.content)
					if response is not None:
						self.statistics["alpha"] += 1
						try: await message.channel.send(content=response)
						except: pass
						return
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


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
								try: await reaction.message.delete()
								except: pass
							elif str(user.id) in reaction.message.attachments[0].filename or guildPermissions:
								try: await reaction.message.delete()
								except: pass
						else:
							await reaction.message.delete()

					elif reaction.emoji == '❌' and len(reaction.message.embeds) == 1:
						titleText = reaction.message.embeds[0].title
						footerText = reaction.message.embeds[0].footer.text
						if footerText.startswith("Alert") and " ● id: " in footerText:
							alertId = footerText.split(" ● id: ")[1]
							accountId = self.account_id_for(user.id)
							marketAlerts = self.accountProperties[accountId].get("marketAlerts", [])
							
							try:
								marketAlerts.remove(alertId)
								database.document("details/marketAlerts/{}/{}".format(accountId, alertId)).delete()
								if "customer" in self.accountProperties[accountId]:
									database.document("accounts/{}".format(self.account_id_for(user.id))).set({"marketAlerts": marketAlerts}, merge=True)
								else:
									database.document("discord/properties/users/{}".format(user.id)).set({"marketAlerts": marketAlerts}, merge=True)
							except: pass

							embed = discord.Embed(title="Alert deleted", color=constants.colors["gray"])
							embed.set_footer(text=footerText)
							try: await reaction.message.edit(embed=embed)
							except: pass

						elif footerText.startswith("Paper order") and " ● id: " in footerText:
							orderId = footerText.split(" ● id: ")[1]
							paper = self.accountProperties[self.account_id_for(user.id)]["paperTrader"]

							for id in supported.cryptoExchanges["Alpha Paper Trader"]:
								if id in paper:
									for order in paper[id]["openOrders"]:
										if orderId == order["id"]:
											paperRequest = pickle.loads(zlib.decompress(order["request"]))
											ticker = paperRequest.get_ticker()

											if order["orderType"] == "buy":
												paper[id]["balance"][ticker.quote]["amount"] += order["amount"] * order["price"]
											elif order["orderType"] == "sell":
												paper[id]["balance"][ticker.base]["amount"] += order["amount"]
											paper[id]["openOrders"].remove(order)
											database.document("accounts/{}".format(self.account_id_for(user.id))).set({"paperTrader": paper}, merge=True)
											break
							
							embed = discord.Embed(title="Paper order canceled", color=constants.colors["gray"])
							embed.set_footer(text=footerText)
							try: await reaction.message.edit(embed=embed)
							except: pass
						elif " → `" in titleText and titleText.endswith("`"):
							properties = self.accountProperties[self.account_id_for(user.id)]
							properties, _ = Presets.update_presets(properties, remove=titleText.split("`")[1])
							if not "customer" in self.accountProperties[self.account_id_for(user.id)]:
								database.document("discord/properties/users/{}".format(user.id)).set({"commandPresets": properties["commandPresets"]}, merge=True)
							else:
								database.document("accounts/{}".format(self.account_id_for(user.id))).set({"commandPresets": properties["commandPresets"]}, merge=True)
							

							embed = discord.Embed(title="Preset deleted", color=constants.colors["gray"])
							embed.set_footer(text=footerText)
							try: await reaction.message.edit(embed=embed)
							except: pass
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def finish_request(self, message, messageRequest, weight, sentMessages):
		await asyncio.sleep(60)
		if messageRequest.authorId in self.rateLimited:
			self.rateLimited[messageRequest.authorId] -= weight
			if self.rateLimited[messageRequest.authorId] < 1: self.rateLimited.pop(messageRequest.authorId, None)

		if len(sentMessages) != 0 and messageRequest.autodelete:
			try: await message.delete()
			except: pass

		for message in sentMessages:
			try:
				if messageRequest.autodelete: await message.delete()
				else: await message.remove_reaction("☑", message.channel.guild.me)
			except: pass


	# -------------------------
	# Help functionality
	# -------------------------

	async def help(self, message, messageRequest):
		embed = discord.Embed(title=":wave: Introduction", description="Alpha Bot is the world's most popular Discord bot for requesting charts, set price alerts, and more. Using Alpha Bot is as simple as typing a short command into any Discord channel the bot has access to. A full guide is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot).", color=constants.colors["light blue"])
		embed.add_field(name=":chart_with_upwards_trend: Charts", value="Easy access to TradingView, TradingLite, and Finviz charts. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/charts).", inline=False)
		embed.add_field(name=":bell: Alerts", value="Setup price alerts for select crypto exchanges. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/price-alerts).", inline=False)
		embed.add_field(name=":money_with_wings: Prices", value="Prices for tens of thousands of tickers. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/prices).", inline=False)
		embed.add_field(name=":joystick: Alpha Paper Trader", value="Execute crypto paper trades through Alpha Bot. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", inline=False)
		embed.add_field(name=":fire: Heat Maps", value="Various heat maps from Bitgur. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/heat-maps).", inline=False)
		embed.add_field(name=":book: Orderbook Visualizations", value="Orderbook snapshot visualizations for crypto markets. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/orderbook-visualizations).", inline=False)
		embed.add_field(name=":tools: Cryptocurrency Details", value="Detailed cryptocurrency information from CoinGecko. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/cryptocurrency-details).", inline=False)
		embed.add_field(name=":yen: Cryptocurrency Conversions", value="An easy way to convert between crypto and fiat rates. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/cryptocurrency-conversions).", inline=False)
		embed.add_field(name=":pushpin: Command Presets", value="Create personal presets for easy access to features you use the most. [View examples](https://www.alphabotsystem.com/guide/alpha-bot/command-presets).", inline=False)
		embed.add_field(name=":crystal_ball: Assistant", value="Pull up Wikipedia articles, calculate math problems and get answers to many other question. Start a message with `alpha` and continue with your question.", inline=False)
		embed.add_field(name=":link: Official Alpha website", value="[alphabotsystem.com](https://www.alphabotsystem.com)", inline=True)
		embed.add_field(name=":tada: Alpha Discord community", value="[Join now](https://discord.gg/GQeDE85)", inline=True)
		embed.add_field(name=":link: Official Alpha Twitter", value="[@AlphaBotSystem](https://twitter.com/AlphaBotSystem)", inline=True)
		embed.set_footer(text="Use \"alpha help\" to pull up this list again.")
		await message.channel.send(embed=embed)

	async def add_tip_message(self, message, messageRequest, command=None):
		if not messageRequest.ads_disabled() and random.randint(0, 20) == 1:
			c = command
			while c == command: c, textSet = random.choice(list(constants.supportMessages.items()))
			selectedTip = random.choice(textSet)
			await message.channel.send(embed=discord.Embed(title=selectedTip[0], description=selectedTip[1], color=constants.colors["light blue"]))


	# -------------------------
	# Command Presets
	# -------------------------

	async def presets(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.replace("`", "").split(" ", 2)
			method = arguments[0]

			if method in ["set", "create", "add"]:
				if len(arguments) == 3:
					if messageRequest.command_presets_available():
						await message.channel.trigger_typing()

						title = arguments[1]
						shortcut = arguments[2]

						if len(title) > 20:
							embed = discord.Embed(title="Shortcut title can be only up to 20 characters long.", color=constants.colors["gray"])
							embed.set_author(name="Shortcut title is too long", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))
						elif len(shortcut) > 200:
							embed = discord.Embed(title="Shortcut command can be only up to 200 characters long.", color=constants.colors["gray"])
							embed.set_author(name="Shortcut command is too long.", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))

						properties, statusParts = Presets.update_presets(messageRequest.accountProperties, add=title, shortcut=shortcut, messageRequest=messageRequest)
						statusTitle, statusMessage, statusColor = statusParts

						if not messageRequest.is_registered():
							database.document("discord/properties/users/{}".format(messageRequest.authorId)).set({"commandPresets": properties["commandPresets"]}, merge=True)
						elif messageRequest.serverwide_command_presets_available():
							database.document("accounts/{}".format(self.account_id_for(messageRequest.authorId))).set({"commandPresets": properties["commandPresets"]}, merge=True)
						elif messageRequest.personal_command_presets_available():
							database.document("accounts/{}".format(self.account_id_for(messageRequest.authorId))).set({"commandPresets": properties["commandPresets"], "customer": {"addons": {"commandPresets": 1}}}, merge=True)

						embed = discord.Embed(title=statusMessage, color=constants.colors[statusColor])
						embed.set_author(name=statusTitle, icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))

					elif messageRequest.is_pro():
						if not message.author.bot and message.author.permissions_in(message.channel).administrator:
							embed = discord.Embed(title=":pushpin: Command Presets are disabled.", description="You can enable Command Presets feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
							embed.set_author(name="Command Presets", icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)
						else:
							embed = discord.Embed(title=":pushpin: Command Presets are disabled.", description="You can enable Command Presets feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
							embed.set_author(name="Command Presets", icon_url=static_storage.icon_bw)
							await message.channel.send(embed=embed)

					elif messageRequest.is_registered():
						embed = discord.Embed(title=":gem: Command Presets are available to Alpha Pro users or communities for only $1.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
						embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
						await message.channel.send(embed=embed)

			elif method in ["list", "all"]:
				if len(arguments) == 1:
					await message.channel.trigger_typing()
					
					if "commandPresets" in messageRequest.accountProperties and len(messageRequest.accountProperties["commandPresets"]) > 0:
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
						embed = discord.Embed(title="You don't have any presets.", color=constants.colors["gray"])
						embed.set_author(name="Command Presets", icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))

			else:
				embed = discord.Embed(title="`{}` is not a valid argument.".format(method), description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/command-presets).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Charting
	# -------------------------

	async def chart(self, message, messageRequest, requestSlice, platform):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_chart_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform)
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/charts).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			for timeframe in request.get_timeframes():
				async with message.channel.typing():
					request.set_current(timeframe=timeframe)
					payload, chartText = await Processor.execute_data_server_request("chart", request)

				if payload is None:
					errorMessage = "Requested chart for `{}` is not available.".format(request.get_ticker().name) if chartText is None else chartText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
					chartMessage = await message.channel.send(embed=embed)
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				else:
					chartMessage = await message.channel.send(content=chartText, file=discord.File(payload, filename="{:.0f}-{}-{}.png".format(time.time() * 1000, messageRequest.authorId, random.randint(1000, 9999))))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
			
			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def flow(self, message, messageRequest, requestSlice, platform):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			if messageRequest.flow_available():
				outputMessage, request = Processor.process_chart_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform, platformQueue=["Alpha Flow"])
				if outputMessage is not None:
					if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
						embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/flow).", color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))

				for timeframe in request.get_timeframes():
					async with message.channel.typing():
						request.set_current(timeframe=timeframe)
						payload, chartText = await Processor.execute_data_server_request("chart", request)

					if payload is None:
						errorMessage = "Requested orderflow data for `{}` is not available.".format(request.get_ticker().name) if chartText is None else chartText
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
						chartMessage = await message.channel.send(embed=embed)
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass
					else:
						chartMessage = await message.channel.send(content=chartText, file=discord.File(payload, filename="{:.0f}-{}-{}.png".format(time.time() * 1000, messageRequest.authorId, random.randint(1000, 9999))))
						sentMessages.append(chartMessage)
						try: await chartMessage.add_reaction("☑")
						except: pass

				autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
				messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

			elif messageRequest.is_pro():
				if not message.author.bot and message.author.permissions_in(message.channel).administrator:
					embed = discord.Embed(title=":microscope: Alpha Flow is disabled.", description="You can enable Alpha Flow feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
					embed.set_author(name="Alpha Flow", icon_url=static_storage.icon_bw)
					await message.channel.send(embed=embed)
				else:
					embed = discord.Embed(title=":microscope: Alpha Flow is disabled.", description="You can enable Alpha Flow feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
					embed.set_author(name="Alpha Flow", icon_url=static_storage.icon_bw)
					await message.channel.send(embed=embed)

			else:
				embed = discord.Embed(title=":gem: Alpha Flow is available to Alpha Pro users or communities for only $15.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
				embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
				await message.channel.send(embed=embed)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def heatmap(self, message, messageRequest, requestSlice, platform):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_heatmap_arguments(messageRequest, arguments, platform=platform)
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/heat-maps).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			for timeframe in request.get_timeframes():
				async with message.channel.typing():
					request.set_current(timeframe=timeframe)
					payload, chartText = await Processor.execute_data_server_request("heatmap", request)

				if payload is None:
					errorMessage = "Requested heat map is not available." if chartText is None else chartText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Heat map not available", icon_url=static_storage.icon_bw)
					chartMessage = await message.channel.send(embed=embed)
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass
				else:
					chartMessage = await message.channel.send(content=chartText, file=discord.File(payload, filename="{:.0f}-{}-{}.png".format(time.time() * 1000, messageRequest.authorId, random.randint(1000, 9999))))
					sentMessages.append(chartMessage)
					try: await chartMessage.add_reaction("☑")
					except: pass

			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def depth(self, message, messageRequest, requestSlice, platform):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform, platformQueue=["CCXT"])
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/orderbook-visualizations).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				payload, chartText = await Processor.execute_data_server_request("depth", request)

			if payload is None:
				embed = discord.Embed(title="Requested orderbook visualization for `{}` is not available.".format(request.get_ticker().name), color=constants.colors["gray"])
				embed.set_author(name="Chart not available", icon_url=static_storage.icon_bw)
				chartMessage = await message.channel.send(embed=embed)
				sentMessages.append(chartMessage)
				try: await chartMessage.add_reaction("☑")
				except: pass
			else:
				chartMessage = await message.channel.send(content=chartText, file=discord.File(payload, filename="{:.0f}-{}-{}.png".format(time.time() * 1000, messageRequest.authorId, random.randint(1000, 9999))))
				sentMessages.append(chartMessage)
				try: await chartMessage.add_reaction("☑")
				except: pass

			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Quotes
	# -------------------------

	async def alert(self, message, messageRequest, requestSlice, platform):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")
			method = arguments[0]

			if method in ["set", "create", "add"]:
				if messageRequest.price_alerts_available():
					outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), platform=platform, isMarketAlert=True, excluded=["CoinGecko", "LLD"])
					if outputMessage is not None:
						if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
							embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/price-alerts).", color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					await message.channel.trigger_typing()

					ticker = request.get_ticker()
					exchange = request.get_exchange()
					marketAlerts = messageRequest.accountProperties.get("marketAlerts", [])

					if len(marketAlerts) >= 50:
						embed = discord.Embed(title="You can only create up to 50 price alerts.", color=constants.colors["gray"])
						embed.set_author(name="Maximum number of price alerts reached", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					alertId = str(uuid.uuid4())
					newAlert = {
						"timestamp": time.time(),
						"channel": str(message.channel.id),
						"service": "Discord",
						"request": zlib.compress(pickle.dumps(request, -1)),
						"level": request.get_numerical_parameters()[0]
					}

					if request.currentPlatform == "CCXT":
						levelText = Utils.format_price(exchange.properties, ticker.symbol, newAlert["level"])
					elif request.currentPlatform == "CoinGecko":
						levelText = ("{:,.%df}" % (4 if TickerParser.check_if_fiat(ticker.quote)[0] and not ticker.isReversed else 8)).format(newAlert["level"])
					elif request.currentPlatform == "IEXC":
						levelText = "{:,.5f}".format(newAlert["level"])
					else:
						levelText = "{:,.0f}".format(newAlert["level"])

					for key in marketAlerts:
						alert = database.document("details/marketAlerts/{}/{}".format(messageRequest.authorId, key)).get().to_dict()
						alertRequest = pickle.loads(zlib.decompress(alert["request"]))
						alertExchange = alertRequest.get_exchange()
						if alertRequest.get_ticker() == ticker and alertRequest.currentPlatform == request.currentPlatform and ((alertExchange is None and exchange is None) or alertExchange.id == exchange.id) and alert["level"] == newAlert["level"]:
							embed = discord.Embed(title="Price alert for {} ({}) at {} {} already exists.".format(ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote), color=constants.colors["gray"])
							embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))

					payload, quoteText = await Processor.execute_data_server_request("quote", request)
					
					if payload is None or payload["quotePrice"] is None:
						errorMessage = "Requested price alert for `{}` is not available.".format(request.get_ticker().name) if quoteText is None else quoteText
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
						quoteMessage = await message.channel.send(embed=embed)
						sentMessages.append(quoteMessage)
						try: await quoteMessage.add_reaction("☑")
						except: pass
					else:
						request.set_current(platform=payload["platform"])
						if payload["raw"]["quotePrice"][0] * 0.523 > newAlert["level"] or payload["raw"]["quotePrice"][0] * 1.523 < newAlert["level"]:
							embed = discord.Embed(title="Your desired alert trigger level at {} {} is too far from the current price of {} {}.".format(levelText, ticker.quote, payload["quotePrice"], payload["quoteTicker"]), color=constants.colors["gray"])
							embed.set_author(name="Price alerts", icon_url=static_storage.icon_bw)
							embed.set_footer(text="Price {}".format(payload["sourceText"]))
							sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))

						marketAlerts.append(alertId)
						if not messageRequest.is_registered():
							database.document("details/marketAlerts/{}/{}".format(messageRequest.authorId, alertId)).delete()
							database.document("discord/properties/users/{}".format(messageRequest.authorId)).set({"marketAlerts": marketAlerts}, merge=True)
						elif messageRequest.serverwide_command_presets_available():
							database.document("details/marketAlerts/{}/{}".format(self.account_id_for(messageRequest.authorId), alertId)).delete()
							database.document("accounts/{}".format(self.account_id_for(messageRequest.authorId))).set({"marketAlerts": marketAlerts}, merge=True)
						elif messageRequest.personal_command_presets_available():
							database.document("details/marketAlerts/{}/{}".format(self.account_id_for(messageRequest.authorId), alertId)).delete()
							database.document("accounts/{}".format(self.account_id_for(messageRequest.authorId))).set({"marketAlerts": marketAlerts, "customer": {"addons": {"marketAlerts": 1}}}, merge=True)

						embed = discord.Embed(title="Price alert set for {} ({}) at {} {}.".format(ticker.base, request.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote), color=constants.colors["deep purple"])
						embed.set_author(name="Alert successfully set", icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))

				elif messageRequest.is_pro():
					if not message.author.bot and message.author.permissions_in(message.channel).administrator:
						embed = discord.Embed(title=":bell: Price Alerts are disabled.", description="You can enable Price Alerts feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
						embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
						await message.channel.send(embed=embed)
					else:
						embed = discord.Embed(title=":bell: Price Alerts are disabled.", description="You can enable Price Alerts feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
						embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
						await message.channel.send(embed=embed)

				else:
					embed = discord.Embed(title=":gem: Price Alerts are available to Alpha Pro users or communities for only $2.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
					embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
					await message.channel.send(embed=embed)

			elif method in ["list", "all"]:
				if len(arguments) == 1:
					totalAlertCount = len(messageRequest.accountProperties.get("marketAlerts", []))

					for index, key in enumerate(messageRequest.accountProperties.get("marketAlerts", [])):
						await message.channel.trigger_typing()

						alert = database.document("details/marketAlerts/{}/{}".format(self.account_id_for(messageRequest.authorId), key)).get().to_dict()
						
						alertRequest = pickle.loads(zlib.decompress(alert["request"]))
						ticker = alertRequest.get_ticker()
						exchange = alertRequest.get_exchange()

						if alertRequest.currentPlatform == "CCXT":
							levelText = Utils.format_price(exchange.properties, ticker.symbol, alert["level"])
						elif alertRequest.currentPlatform == "CoinGecko":
							levelText = ("{:,.%df}" % (4 if TickerParser.check_if_fiat(ticker.quote)[0] and not ticker.isReversed else 8)).format(alert["level"])
						elif alertRequest.currentPlatform == "IEXC":
							levelText = "{:,.5f}".format(alert["level"])
						else:
							levelText = "{:,.0f}".format(alert["level"])

						embed = discord.Embed(title="Price alert set for {} ({}) at {} {}".format(ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote), color=constants.colors["deep purple"])
						embed.set_footer(text="Alert {}/{} ● id: {}".format(index + 1, totalAlertCount, key))
						alertMessage = await message.channel.send(embed=embed)
						sentMessages.append(alertMessage)
						try: await alertMessage.add_reaction('❌')
						except: pass

					if totalAlertCount == 0:
						embed = discord.Embed(title="You haven't set any alerts yet.", color=constants.colors["gray"])
						embed.set_author(name="Alpha Price Alerts", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))

				else:
					embed = discord.Embed(title="Invalid command usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/price-alerts).", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def price(self, message, messageRequest, requestSlice, platform):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform)
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/prices).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				payload, quoteText = await Processor.execute_data_server_request("quote", request)

			if payload is None or payload["quotePrice"] is None:
				errorMessage = "Requested price for `{}` is not available.".format(request.get_ticker().name) if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
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
					embed = discord.Embed(title="{} {}{}".format(payload["quotePrice"], payload["quoteTicker"], "" if payload["change"] is None else " *({:+.2f} %)*".format(payload["change"])), description=payload["quoteConvertedPrice"], color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
					embed.set_footer(text="Price {}".format(payload["sourceText"]))
					sentMessages.append(await message.channel.send(embed=embed))

			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def volume(self, message, messageRequest, requestSlice, platform):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platform=platform)
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/volume).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				payload, quoteText = await Processor.execute_data_server_request("quote", request)

			if payload is None or payload["quoteVolume"] is None:
				errorMessage = "Requested volume for `{}` is not available.".format(request.get_ticker().name) if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
				quoteMessage = await message.channel.send(embed=embed)
				sentMessages.append(quoteMessage)
				try: await quoteMessage.add_reaction("☑")
				except: pass
			else:
				embed = discord.Embed(title="{:,.4f} {}".format(payload["quoteVolume"], payload["baseTicker"]), description=payload["quoteConvertedVolume"], color=constants.colors["orange"])
				embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
				embed.set_footer(text="Volume {}".format(payload["sourceText"]))
				sentMessages.append(await message.channel.send(embed=embed))

			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def convert(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			requestSlices = re.split(" into | in to | in | to ", requestSlice)
			if len(requestSlices) != 2 or len(requestSlices[0].split(" ")) != 2 or len(requestSlices[1].split(" ")) != 1:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Incorrect currency conversion usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/conversions).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))
			arguments1 = requestSlices[0].split(" ")
			arguments2 = requestSlices[1].split(" ")

			async with message.channel.typing():
				payload, quoteText = await Processor.process_conversion(messageRequest, arguments1[1].upper(), arguments2[0].upper(), arguments1[0])

			if payload is None:
				errorMessage = "Requested conversion is not available." if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Conversion not available", icon_url=static_storage.icon_bw)
				quoteMessage = await message.channel.send(embed=embed)
				sentMessages.append(quoteMessage)
				try: await quoteMessage.add_reaction("☑")
				except: pass
			else:
				embed = discord.Embed(title="{} {} ≈ {}".format(payload["quotePrice"], payload["baseTicker"], payload["quoteConvertedPrice"]), color=constants.colors[payload["messageColor"]])
				embed.set_author(name="Conversion", icon_url=static_storage.icon)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Details
	# -------------------------

	async def details(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_detail_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["CoinGecko"])
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/cryptocurrency-details).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				payload, detailText = await Processor.execute_data_server_request("detail", request)

			if payload is None:
				errorMessage = "Requested details for `{}` are not available.".format(request.get_ticker().name) if detailText is None else detailText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
				quoteMessage = await message.channel.send(embed=embed)
				sentMessages.append(quoteMessage)
				try: await quoteMessage.add_reaction("☑")
				except: pass
			else:
				request.set_current(platform=payload["platform"])
				ticker = request.get_ticker()

				embed = discord.Embed(title=payload["name"], description="Ranked #{} by market cap".format(payload["rank"]), color=constants.colors["lime"])
				embed.set_thumbnail(url=payload["image"])

				marketCap = "Market cap: no data"
				totalVolume = ""
				totalSupply = ""
				circulatingSupply = ""
				developerScore = ""
				communityScore = ""
				liquidityScore = ""
				publicInterestScore = ""
				if payload["marketcap"] is not None:
					marketCap = "Market cap: {:,.0f} {}".format(payload["marketcap"], "USD")
				if payload["volume"] is not None:
					totalVolume = "\nTotal volume: {:,.0f} {}".format(payload["volume"], "USD")
				if payload["supply"]["total"] is not None:
					totalSupply = "\nTotal supply: {:,.0f} {}".format(payload["supply"]["total"], ticker.base)
				if payload["supply"]["circulating"] is not None:
					circulatingSupply = "\nCirculating supply: {:,.0f} {}".format(payload["supply"]["circulating"], ticker.base)
				if payload["score"]["developer"] is not None:
					developerScore = "\nDeveloper score: {:,.1f}/100".format(payload["score"]["developer"])
				if payload["score"]["community"] is not None:
					communityScore = "\nCommunity score: {:,.1f}/100".format(payload["score"]["community"])
				if payload["score"]["liquidity"] is not None:
					liquidityScore = "\nLiquidity score: {:,.1f}/100".format(payload["score"]["liquidity"])
				if payload["score"]["public interest"] is not None:
					publicInterestScore = "\nPublic interest: {:,.1f}".format(payload["score"]["public interest"])

				embed.add_field(name="Details", value=(marketCap + totalVolume + totalSupply + circulatingSupply + developerScore + communityScore + liquidityScore + publicInterestScore), inline=False)

				formattedUsdPrice = ("${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["current"])).format(payload["price"]["current"])
				formattedUsdAth = ""
				formattedUsdAtl = ""
				if payload["price"]["ath"] is not None:
					formattedUsdAth = ("\nATH: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["ath"])).format(payload["price"]["ath"])
				if payload["price"]["atl"]:
					formattedUsdAtl = ("\nATL: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["atl"])).format(payload["price"]["atl"])
				embed.add_field(name="Price", value="__{}__{}{}".format(formattedUsdPrice, formattedUsdAth, formattedUsdAtl), inline=True)

				change24h = "Past day: no data"
				change30d = ""
				change1y = ""
				if payload["change"]["past day"]:
					change24h = "\nPast day: *{:+,.2f} %*".format(payload["change"]["past day"])
				if payload["change"]["past month"]:
					change30d = "\nPast month: *{:+,.2f} %*".format(payload["change"]["past month"])
				if payload["change"]["past year"]:
					change1y = "\nPast year: *{:+,.2f} %*".format(payload["change"]["past year"])
				embed.add_field(name="Price change", value=(change24h + change30d + change1y), inline=True)
				embed.set_footer(text="Data {}".format(payload["sourceText"]))

				sentMessages.append(await message.channel.send(embed=embed))

			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def rankings(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ", 2)
			method = arguments[0]

			if method in ["alpha", "requests", "charts"]:
				if messageRequest.flow_available():
					response = []
					async with message.channel.typing():
						rawData = database.document("dataserver/statistics").get().to_dict()
						response = rawData["top"][messageRequest.guildProperties["settings"]["messageProcessing"]["bias"]][:9:-1]

					embed = discord.Embed(title="Top Alpha Bot requests", color=constants.colors["deep purple"])
					for token in response:
						embed.add_field(name=token["id"], value="Rank {:,.1f}/100".format(token["rank"]), inline=True)
					sentMessages.append(await message.channel.send(embed=embed))

				elif messageRequest.is_pro():
					if not message.author.bot and message.author.permissions_in(message.channel).administrator:
						embed = discord.Embed(title=":pushpin: Alpha Statistics are disabled.", description="You can enable Alpha Statistics feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord) or for the entire community in your [Communities Dashboard](https://www.alphabotsystem.com/communities/manage?id={}).".format(messageRequest.guildId), color=constants.colors["gray"])
						embed.set_author(name="Alpha Statistics", icon_url=static_storage.icon_bw)
						await message.channel.send(embed=embed)
					else:
						embed = discord.Embed(title=":pushpin: Alpha Statistics are disabled.", description="You can enable Alpha Statistics feature for your account in [Discord Preferences](https://www.alphabotsystem.com/account/discord).", color=constants.colors["gray"])
						embed.set_author(name="Alpha Statistics", icon_url=static_storage.icon_bw)
						await message.channel.send(embed=embed)

				else:
					embed = discord.Embed(title=":gem: Alpha Statistics information is available to Alpha Pro users or communities for only $5.00 per month.", description="If you'd like to start your 14-day free trial, visit your [subscription page](https://www.alphabotsystem.com/account/subscription).", color=constants.colors["deep purple"])
					embed.set_image(url="https://www.alphabotsystem.com/files/uploads/pro-hero.jpg")
					await message.channel.send(embed=embed)

			elif method in ["gainers", "gain", "gains"]:
				response = []
				async with message.channel.typing():
					rawData = self.coinGecko.get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, price_change_percentage="24h")
					for e in rawData:
						if e.get("price_change_percentage_24h_in_currency", None) is not None:
							response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
					response = sorted(response, key=lambda k: k["change"], reverse=True)[:10]
				
				embed = discord.Embed(title="Top gainers", color=constants.colors["deep purple"])
				for token in response:
					embed.add_field(name=token["symbol"], value="Gained {:,.1f} %".format(token["change"]), inline=True)
				sentMessages.append(await message.channel.send(embed=embed))

			elif method in ["losers", "loosers", "loss", "losses"]:
				response = []
				async with message.channel.typing():
					rawData = self.coinGecko.get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, price_change_percentage="24h")
					for e in rawData:
						if e.get("price_change_percentage_24h_in_currency", None) is not None:
							response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
				response = sorted(response, key=lambda k: k["change"])[:10]
				
				embed = discord.Embed(title="Top losers", color=constants.colors["deep purple"])
				for token in response:
					embed.add_field(name=token["symbol"], value="Lost {:,.1f} %".format(token["change"]), inline=True)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def markets(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["CCXT"])
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/cryptocurrency-details).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			await message.channel.trigger_typing()

			listings, total = TickerParser.get_listings(request.get_ticker())
			if total != 0:
				embed = discord.Embed(color=constants.colors["deep purple"])
				embed.set_author(name="{} listings".format(request.get_ticker().base))
				for quote, exchanges in listings:
					embed.add_field(name="{} pair found on {} exchanges".format(quote, len(exchanges)), value="{}".format(", ".join(exchanges)), inline=False)
				sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="`{}` is not listed on any crypto exchange.".format(request.get_ticker().id), color=constants.colors["gray"])
				embed.set_author(name="No listings", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))
			
			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Streams
	# -------------------------

	async def news(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			

			# autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			# messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def data_stream(self, message, messageRequest, requestSlice):
		sentMessages = []
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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Live Trading
	# -------------------------

	async def process_live_trade(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = self.liveTrader.argument_cleanup(requestSlice).split(" ")
			orderType = arguments[0]

			if orderType in ["buy", "scaled-buy", "sell", "scaled-sell", "stop-buy", "stop-sell", "trailing-stop-buy", "trailing-stop-sell"] and 2 <= len(arguments) <= 8:
				if not messageRequest.is_registered():
					embed = discord.Embed(title=":dart: You must have an Alpha Account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile on the overview page.", color=constants.colors["deep purple"])
					embed.set_author(name="Live trading", icon_url=static_storage.icon)
					await message.channel.send(embed=embed)
					return (sentMessages, len(sentMessages))

				outputMessage, request = Processor.process_trade_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), platformQueue=["Ichibot"])
				if outputMessage is not None:
					if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
						embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/live-trader).", color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))

				ticker = request.get_ticker()

				async with message.channel.typing():
					payload, tradeText = await Processor.execute_data_server_request("trade", request)

				if payload is None or payload["quotePrice"] is None:
					errorMessage = "Requested live {} order for {} could not be executed.".format(orderType.replace("-", " "), ticker.name) if tradeText is None else tradeText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					tradeMessage = await message.channel.send(embed=embed)
					sentMessages.append(tradeMessage)
					try: await tradeMessage.add_reaction("☑")
					except: pass
				else:
					outputTitle, outputMessage, pendingOrder = self.liveTrader.process_trade(orderType, request, payload)
					if pendingOrder is None:
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name=outputTitle, icon_url=static_storage.icon_bw)
						await message.channel.send(embed=embed)
						return

					confirmationText = "Do you want to place a {} order of {} {} on {} at {}?".format(orderType.replace("-", " "), pendingOrder.amountText, ticker.base, request.get_exchange().name, pendingOrder.priceText)
					embed = discord.Embed(title=confirmationText, description=pendingOrder.conversionText, color=constants.colors["pink"])
					embed.set_author(name="Live order confirmation", icon_url=payload["thumbnailUrl"])
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
						self.lockedUsers.discard(messageRequest.authorId)
						embed = discord.Embed(title="Order canceled", description="~~{}~~".format(confirmationText), color=constants.colors["gray"])
						embed.set_author(name="Live trading", icon_url=static_storage.icon_bw)
						try: await orderConfirmationMessage.edit(embed=embed)
						except: pass
					else:
						self.lockedUsers.discard(messageRequest.authorId)
						async with message.channel.typing():
							response = self.liveTrader.post_trade(messageRequest, request, pendingOrder)
							if response is None:
								await self.unknown_error(message, messageRequest.authorId)
								return

						successMessage = "{} order of {} {} on {} at {} was successfully {}.".format(orderType.replace("-", " "), pendingOrder.amountText, request.get_ticker().base, request.get_exchange().name, pendingOrder.priceText, "executed" if pendingOrder.parameters["parameters"][0] else "placed")
						embed = discord.Embed(title=successMessage, color=constants.colors["deep purple"])
						embed.set_author(name="Live trading", icon_url=static_storage.icon)
						await message.channel.send(embed=embed)
			else:
				embed = discord.Embed(title="Invalid command usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/live-trader).", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def brekkeven(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			pass

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Paper Trading
	# -------------------------

	async def fetch_paper_balance(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")[1:]

			outputMessage, request = Processor.process_trade_arguments(messageRequest, arguments, platformQueue=["Alpha Paper Trader"])
			if outputMessage is not None:
				if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))
			elif not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile on the overview page.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)
				return (sentMessages, len(sentMessages))

			exchange = request.get_exchange()

			if exchange is not None:
				if exchange.id in supported.cryptoExchanges["Alpha Paper Trader"]:
					async with message.channel.typing():
						embed = discord.Embed(title="Paper balance on {}".format(exchange.name), color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

						paper = messageRequest.accountProperties["paperTrader"]
						if exchange.id not in paper:
							paper[exchange.id] = {"balance": copy.deepcopy(PaperTrader.startingBalance[exchange.id]), "openOrders": [], "history": []}

						totalValue = 0
						holdingAssets = set()
						exchangeBaseCurrency = PaperTrader.baseCurrency[exchange.id]

						for base in sorted(paper[exchange.id]["balance"].keys()):
							isFiat, _ = TickerParser.check_if_fiat(base)
							ticker, _ = TickerParser.find_ccxt_crypto_market(Ticker(base), exchange, "CCXT", messageRequest.guildProperties["settings"]["charts"]["defaults"])

							amount = paper[exchange.id]["balance"][base]["amount"]

							balanceText = ""
							valueText = "No conversion"
							if exchange.id in ["bitmex"]:
								if base == "BTC":
									btcValue = -1
									payload, quoteText = await Processor.process_conversion(messageRequest, base, exchangeBaseCurrency, amount)
									convertedValue = payload["raw"]["quotePrice"][0] if quoteText is None else 0
									balanceText = "{:,.4f} XBT".format(amount)
									valueText = "≈ {:,.6f} USD".format(convertedValue)
									totalValue += convertedValue
								else:
									btcValue = -1
									coinName = "{} position".format(ticker.name)
									valueText = "{:,.0f} contracts\n≈ {:,.4f} XBT".format(amount, amount / 1)
									totalValue += convertedValue
							else:
								if isFiat:
									payload, quoteText = await Processor.process_conversion(messageRequest, base, "BTC", amount)
									btcValue = payload["raw"]["quotePrice"][0] if quoteText is None else 0
									balanceText = "{:,.6f} {}".format(amount, base)
									valueText = "Stable in fiat value"
									totalValue += amount
								elif base == "BTC":
									btcValue = amount
									balanceText = "{:,.8f} BTC".format(amount)
									payload, quoteText = await Processor.process_conversion(messageRequest, base, exchangeBaseCurrency, amount)
									convertedValue = payload["raw"]["quotePrice"][0] if quoteText is None else 0
									if convertedValue is not None:
										valueText = "≈ {:,.6f} {}".format(amount, base, convertedValue, exchangeBaseCurrency)
										totalValue += convertedValue
								else:
									balanceText = "{:,.8f} {}".format(amount, base)
									payload, quoteText = await Processor.process_conversion(messageRequest, base, "BTC", amount)
									btcValue = payload["raw"]["quotePrice"][0] if quoteText is None else 0
									payload, quoteText = await Processor.process_conversion(messageRequest, base, exchangeBaseCurrency, amount)
									convertedValue = payload["raw"]["quotePrice"][0] if quoteText is None else 0
									if convertedValue is not None:
										valueText = "≈ {:,.8f} {}".format(convertedValue, exchangeBaseCurrency)
										totalValue += convertedValue

							if btcValue is not None and btcValue > 0.001:
								embed.add_field(name="{}: {}".format(base, balanceText), value=valueText, inline=True)
								holdingAssets.add(base)

						openOrdersBtcValue = 0
						openOrdersConvertedValue = 0
						for order in paper[exchange.id]["openOrders"]:
							if order["orderType"] in ["buy", "sell"]:
								paperRequest = pickle.loads(zlib.decompress(order["request"]))
								ticker = paperRequest.get_ticker()

								payload, quoteText = await Processor.process_conversion(messageRequest, ticker.quote if order["orderType"] == "buy" else ticker.base, "BTC", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1))
								openOrdersBtcValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0
								payload, quoteText = await Processor.process_conversion(messageRequest, ticker.quote if order["orderType"] == "buy" else ticker.base, exchangeBaseCurrency, order["amount"] * (order["price"] if order["orderType"] == "buy" else 1))
								openOrdersConvertedValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0
								holdingAssets.add(ticker.base)
						if openOrdersConvertedValue > 0:
							totalValue += openOrdersConvertedValue
							valueText = "{:,.8f} BTC\n{:,.8f} {}".format(openOrdersBtcValue, openOrdersConvertedValue, exchangeBaseCurrency)
							embed.add_field(name="Locked up in open orders:", value=valueText, inline=True)

					embed.description = "Holding {} {} with estimated total value of {:,.2f} {} and {:+,.2f} % ROI.{}".format(len(holdingAssets), "assets" if len(holdingAssets) > 1 else "asset", totalValue, exchangeBaseCurrency, (totalValue / PaperTrader.startingBalance[exchange.id][exchangeBaseCurrency]["amount"] - 1) * 100, " Trading since {} with {} balance {}.".format(Utils.timestamp_to_date(paper["globalLastReset"]), paper["globalResetCount"], "reset" if paper["globalResetCount"] == 1 else "resets") if paper["globalLastReset"] != 0 else "")
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="{} exchange is not supported.".format(exchange.name), description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="An exchange must be provided.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def fetch_paper_orders(self, message, messageRequest, requestSlice, type):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")[1:]

			outputMessage, request = Processor.process_trade_arguments(messageRequest, arguments, platformQueue=["Alpha Paper Trader"])
			if outputMessage is not None:
				if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))
			elif not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile on the overview page.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)
				return (sentMessages, len(sentMessages))

			await message.channel.trigger_typing()

			exchange = request.get_exchange()

			if exchange is not None:
				if exchange.id in supported.cryptoExchanges["Alpha Paper Trader"]:
					paper = messageRequest.accountProperties["paperTrader"]

					if type == "history":
						if exchange.id not in paper or len(paper[exchange.id]["history"]) == 0:
							embed = discord.Embed(title="No paper trading history on {}".format(exchange.name), color=constants.colors["deep purple"])
							embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
							sentMessages.append(await message.channel.send(embed=embed))
						else:
							embed = discord.Embed(title="Paper trading history on {}".format(exchange.name), color=constants.colors["deep purple"])
							embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

							for order in paper[exchange.id]["history"][-25:]:
								paperRequest = pickle.loads(zlib.decompress(order["request"]))
								ticker = paperRequest.get_ticker()

								quoteText = ticker.quote
								side = ""
								if order["orderType"] == "buy": side = "Bought"
								elif order["orderType"] == "sell": side = "Sold"
								elif order["orderType"].startswith("stop"): side = "Stop loss hit"
								elif order["orderType"].startswith("trailing-stop"): side, quoteText = "Trailing stop hit", "%"
								embed.add_field(name="{} {} {} at {} {}".format(side, order["amount"], ticker.base, order["price"], quoteText), value="{} ● id: {}".format(Utils.timestamp_to_date(order["timestamp"] / 1000), order["id"]), inline=False)

							sentMessages.append(await message.channel.send(embed=embed))
					else:
						if exchange.id not in paper or len(paper[exchange.id]["openOrders"]) == 0:
							embed = discord.Embed(title="No open paper orders on {}".format(exchange.name), color=constants.colors["deep purple"])
							embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
							sentMessages.append(await message.channel.send(embed=embed))
						else:
							for i, order in enumerate(paper[exchange.id]["openOrders"]):
								paperRequest = pickle.loads(zlib.decompress(order["request"]))
								ticker = paperRequest.get_ticker()

								quoteText = ticker.quote
								side = order["orderType"].replace("-", " ").capitalize()
								if order["orderType"].startswith("trailing-stop"): quoteText = "%"

								embed = discord.Embed(title="{} {} {} at {} {}".format(side, order["amount"], ticker.base, order["price"], quoteText), color=constants.colors["deep purple"])
								embed.set_footer(text="Paper order {}/{} ● id: {}".format(i + 1, len(paper[exchange.id]["openOrders"]), order["id"]))
								orderMessage = await message.channel.send(embed=embed)
								sentMessages.append(orderMessage)
								await orderMessage.add_reaction('❌')
				else:
					embed = discord.Embed(title="{} exchange is not supported.".format(exchange.name), description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", color=constants.colors["gray"])
					embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="An exchange must be provided.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def process_paper_trade(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = self.paperTrader.argument_cleanup(requestSlice).split(" ")
			orderType = arguments[0]

			if orderType in ["buy", "sell", "stop-sell", "trailing-stop-sell"] and 2 <= len(arguments) <= 8:
				outputMessage, request = Processor.process_trade_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), platformQueue=["Alpha Paper Trader"])
				if outputMessage is not None:
					if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
						embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))
				elif not messageRequest.is_registered():
					embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile on the overview page.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await message.channel.send(embed=embed)
					return (sentMessages, len(sentMessages))

				ticker = request.get_ticker()

				async with message.channel.typing():
					payload, tradeText = await Processor.execute_data_server_request("trade", request)

				if payload is None or payload["quotePrice"] is None:
					errorMessage = "Requested paper {} order for `{}` could not be executed.".format(orderType.replace("-", " "), ticker.name) if tradeText is None else tradeText
					embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
					embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
					tradeMessage = await message.channel.send(embed=embed)
					sentMessages.append(tradeMessage)
					try: await tradeMessage.add_reaction("☑")
					except: pass
				else:
					outputTitle, outputMessage, paper, pendingOrder = self.paperTrader.process_trade(messageRequest.accountProperties["paperTrader"], orderType, request, payload)
					if pendingOrder is None:
						embed = discord.Embed(title=outputMessage, color=constants.colors["gray"])
						embed.set_author(name=outputTitle, icon_url=static_storage.icon_bw)
						await message.channel.send(embed=embed)
						return

					confirmationText = "Do you want to place a paper {} order of {} {} on {} at {}?".format(orderType.replace("-", " "), pendingOrder.amountText, ticker.base, request.get_exchange().name, pendingOrder.priceText)
					embed = discord.Embed(title=confirmationText, description=pendingOrder.conversionText, color=constants.colors["pink"])
					embed.set_author(name="Paper order confirmation", icon_url=payload["thumbnailUrl"])
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
						self.lockedUsers.discard(messageRequest.authorId)
						embed = discord.Embed(title="Paper order canceled", description="~~{}~~".format(confirmationText), color=constants.colors["gray"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
						try: await orderConfirmationMessage.edit(embed=embed)
						except: pass
					else:
						self.lockedUsers.discard(messageRequest.authorId)
						async with message.channel.typing():
							paper = self.paperTrader.post_trade(paper, orderType, request, payload, pendingOrder)
							if paper is None:
								await self.unknown_error(message, messageRequest.authorId)
								return

							if paper["globalLastReset"] == 0: paper["globalLastReset"] = int(time.time())
							database.document("accounts/{}".format(self.account_id_for(messageRequest.authorId))).set({"paperTrader": paper}, merge=True)

						successMessage = "Paper {} order of {} {} on {} at {} was successfully {}.".format(orderType.replace("-", " "), pendingOrder.amountText, request.get_ticker().base, request.get_exchange().name, pendingOrder.priceText, "executed" if pendingOrder.parameters["parameters"][0] else "placed")
						embed = discord.Embed(title=successMessage, color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
						await message.channel.send(embed=embed)
			else:
				embed = discord.Embed(title="Invalid command usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/alpha-bot/paper-trader).", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def reset_paper_balance(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			if not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile on the overview page.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)
			elif messageRequest.accountProperties["paperTrader"]["globalLastReset"] + 604800 < time.time() or messageRequest.accountProperties["paperTrader"]["globalResetCount"] == 0:
				embed = discord.Embed(title="Do you really want to reset your paper balance? This cannot be undone.", color=constants.colors["pink"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
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
					self.lockedUsers.discard(messageRequest.authorId)
					embed = discord.Embed(title="Paper balance reset canceled.", description="~~Do you really want to reset your paper balance? This cannot be undone.~~", color=constants.colors["gray"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
					await resetBalanceMessage.edit(embed=embed)
				else:
					self.lockedUsers.discard(messageRequest.authorId)
					paper = messageRequest.accountProperties["paperTrader"]
					for exchange in supported.cryptoExchanges["Alpha Paper Trader"]:
						paper.pop(exchange, None)
					paper["globalResetCount"] += 1
					paper["globalLastReset"] = int(time.time())

					database.document("accounts/{}".format(self.account_id_for(messageRequest.authorId))).set({"paperTrader": paper}, merge=True)

					embed = discord.Embed(title="Paper balance has been reset successfully.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					sentMessages.append(await message.channel.send(embed=embed))
			else:
				embed = discord.Embed(title="Paper balance can only be reset once every seven days.", color=constants.colors["gray"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Error handling
	# -------------------------

	async def unknown_error(self, message, authorId, e=None, report=False):
		embed = discord.Embed(title="Looks like something went wrong.{}".format(" The issue was reported." if report else ""), color=constants.colors["gray"])
		embed.set_author(name="Something went wrong", icon_url=static_storage.icon_bw)
		try: await message.channel.send(embed=embed)
		except: return
		if not os.environ["PRODUCTION_MODE"] and not report and e is not None:
			exc_type, exc_obj, exc_tb = sys.exc_info()
			fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
			print("[Quiet Error]: debug info: {}, {}, line {}, description: {}".format(exc_type, fname, exc_tb.tb_lineno, e))

	async def hold_up(self, message, messageRequest):
		embed = discord.Embed(title="Only up to {:d} requests are allowed per command.".format(int(messageRequest.get_limit() / 2)), color=constants.colors["gray"])
		embed.set_author(name="Too many requests", icon_url=static_storage.icon_bw)
		await message.channel.send(embed=embed)


# -------------------------
# Initialization
# -------------------------

def handle_exit():
	print("\n[Shutdown]: closing tasks")
	client.loop.run_until_complete(client.topgg.close())
	client.loop.run_until_complete(client.logout())
	for t in asyncio.all_tasks(loop=client.loop):
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
	os.environ["PRODUCTION_MODE"] = os.environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in os.environ and os.environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Alpha Bot is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))

	intents = discord.Intents.all()
	intents.bans = False
	intents.invites = False
	intents.voice_states = False
	intents.typing = False
	intents.presences = False

	client = Alpha(intents=intents, chunk_guilds_at_startup=False, status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.playing, name="startup"))
	print("[Startup]: object initialization complete")
	client.prepare()

	while True:
		client.loop.create_task(client.job_queue())
		try:
			token = os.environ["DISCORD_PRODUCTION_TOKEN" if os.environ["PRODUCTION_MODE"] else "DISCORD_DEVELOPMENT_TOKEN"]
			client.loop.run_until_complete(client.start(token))
		except (KeyboardInterrupt, SystemExit):
			handle_exit()
			client.loop.close()
			break
		except:
			handle_exit()

		client = Alpha(loop=client.loop, intents=intents, chunk_guilds_at_startup=False, status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.playing, name="startup"))
