import os
import sys
import re
import random
import math
import time
import datetime
import pytz
import urllib
import requests
import copy
import json
import asyncio
import uuid
import zlib
import pickle
from zmq import NOBLOCK
import traceback

import discord
import stripe
import dbl as topgg
from pycoingecko import CoinGeckoAPI
from google.cloud import firestore, error_reporting

from assets import static_storage
from helpers.utils import Utils
from helpers import constants

from TickerParser import TickerParser
from Processor import Processor
from DatabaseConnector import DatabaseConnector
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
	accountProperties = DatabaseConnector(mode="account")
	guildProperties = DatabaseConnector(mode="guild")

	statistics = {"alerts": 0, "alpha": 0, "c": 0, "convert": 0, "d": 0, "flow": 0, "hmap": 0, "mcap": 0, "t": 0, "mk": 0, "n": 0, "p": 0, "paper": 0, "v": 0, "x": 0}
	rateLimited = {}
	lockedUsers = set()
	usedPresetsCache = {}
	maliciousUsers = {}

	discordSettingsLink = None
	discordMessagesLink = None

	ichibotSockets = {}


	# -------------------------
	# Startup
	# -------------------------

	def prepare(self):
		"""Prepares all required objects and fetches Alpha settings

		"""

		self.botStatus = [False, False]
		t = datetime.datetime.now().astimezone(pytz.utc)

		Processor.clientId = b"discord_alpha"
		self.topgg = topgg.DBLClient(client, os.environ["TOPGG_KEY"])
		self.logging = error_reporting.Client(service="discord")

		self.discordSettingsLink = database.document("discord/settings").on_snapshot(self.update_alpha_settings)
		self.discordMessagesLink = database.collection("discord/properties/messages").on_snapshot(self.process_alpha_messages)

		statisticsData = database.document("discord/statistics").get().to_dict()
		slice = "{}-{:02d}".format(t.year, t.month)
		for data in statisticsData[slice]:
			self.statistics[data] = statisticsData[slice][data]
		print("[Startup]: database initialization complete")

	async def on_ready(self):
		"""Initiates all Discord dependent functions and flags the bot as ready to process requests

		"""

		print("[Startup]: Alpha Bot is online")

		t = datetime.datetime.now().astimezone(pytz.utc)

		while not await self.accountProperties.check_status() or not await self.guildProperties.check_status():
			await asyncio.sleep(15)
		self.botStatus[0] = True
		await client.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="alphabotsystem.com"))

		await self.update_system_status(t)
		await self.update_static_messages()
		print("[Startup]: Alpha Bot startup complete")

	def is_bot_ready(self):
		return all(self.botStatus)

	async def update_static_messages(self):
		"""Updates all static content in various Discord channels

		"""

		if not os.environ["PRODUCTION_MODE"]: return
		try:
			faqAndRulesChannel = client.get_channel(601160698310950914)
			guildRulesMessage = await faqAndRulesChannel.fetch_message(671771929530597426)
			termsOfServiceMessage = await faqAndRulesChannel.fetch_message(671771934475943936)
			faqMessage = await faqAndRulesChannel.fetch_message(671773814182641695)
			if guildRulesMessage is not None: await guildRulesMessage.edit(embed=discord.Embed(title="All members of this official Alpha community must follow the community rules. Failure to do so will result in a warning, kick, or ban, based on our sole discretion.", description="[Community rules](https://www.alphabotsystem.com/community-rules) (last modified on January 31, 2020).", color=constants.colors["deep purple"]), suppress=False)
			if termsOfServiceMessage is not None: await termsOfServiceMessage.edit(embed=discord.Embed(title="By using Alpha branded services you agree to our Terms of Service and Privacy Policy. You can read them on our website.", description="[Terms of Service](https://www.alphabotsystem.com/terms-of-service) (last modified on March 6, 2020)\n[Privacy Policy](https://www.alphabotsystem.com/privacy-policy) (last modified on January 31, 2020).", color=constants.colors["deep purple"]), suppress=False)
			if faqMessage is not None: await faqMessage.edit(embed=discord.Embed(title="If you have any questions, refer to our FAQ section, guide, or ask for help in support channels.", description="[Frequently Asked Questions](https://www.alphabotsystem.com/faq)\n[Feature overview with examples](https://www.alphabotsystem.com/guide)\nFor other questions, use <#574196284215525386>.", color=constants.colors["deep purple"]), suppress=False)

		except asyncio.CancelledError: pass
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
			if guild.id in constants.bannedGuilds:
				await guild.leave()
				return
			database.document("discord/properties/guilds/{}".format(guild.id)).set(MessageRequest.create_guild_settings({"properties": {"name": guild.name, "icon": guild.icon}}))
			self.update_guild_count()
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=str(guild.id))

	async def on_guild_remove(self, guild):
		"""Updates quild count on guild_remove event

		Parameters
		----------
		guild : discord.Guild
			Guild object passed by discord.py
		"""

		try:
			properties = await self.guildProperties.get(guild.id)
			if properties is not None and properties["settings"]["setup"]["connection"] is not None:
				holdingId = properties["settings"]["setup"]["connection"]
				if holdingId in await self.accountProperties.keys():
					communityList = (await self.accountProperties.get(holdingId))["customer"]["communitySubscriptions"]
					if str(guild.id) in communityList:
						communityList.remove(str(guild.id))
						database.document("accounts/{}".format(holdingId)).set({"customer": {"communitySubscriptions": communityList}}, merge=True)
			database.document("discord/properties/guilds/{}".format(guild.id)).delete()
			self.update_guild_count()
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=str(guild.id))

	async def on_guild_update(self, before, after):
		try:
			database.document("discord/properties/guilds/{}".format(after.id)).set({"properties": {"name": after.name, "icon": after.icon}}, merge=True)
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=str(after.id))

	def update_guild_count(self):
		requests.post("https://top.gg/api/bots/{}/stats".format(client.user.id), data={"server_count": len(client.guilds)}, headers={"Authorization": os.environ["TOPGG_KEY"]})


	# -------------------------
	# Job queue
	# -------------------------

	async def job_queue(self):
		"""Executes scheduled jobs as long as Alpha Bot is online

		"""

		while True:
			try:
				await asyncio.sleep(Utils.seconds_until_cycle())
				if not self.is_bot_ready() or not await self.guildProperties.check_status() or not await self.accountProperties.check_status(): continue
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "5m" in timeframes:
					await self.update_system_status(t)
					await self.database_sanity_check()
				if "1H" in timeframes:
					await self.update_pro_accounts()
					await self.security_check()

			except asyncio.CancelledError: return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Database management
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


	# -------------------------
	# Message processing
	# -------------------------

	def process_alpha_messages(self, pendingMessages, changes, timestamp):
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
			while not self.botStatus[0]:
				print("[Startup]: pending messages snapshot is waiting for setup completion ({})".format(timestamp))
				time.sleep(60)

			for change in changes:
				message = change.document.to_dict()
				if change.type.name == "ADDED":
					client.loop.create_task(self.send_alpha_messages(change.document.id, message))

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def send_alpha_messages(self, messageId, message):
		try:
			embed = discord.Embed(title=message["title"], description=message.get("description", discord.embeds.EmptyEmbed), color=message["color"])
			if message.get("subtitle") is not None: embed.set_author(name=message["subtitle"], icon_url=message.get("icon", static_storage.icon))
			if message.get("image") is not None: embed.set_image(url=message["image"])
			if message.get("url") is not None: embed.url = message["url"]

			destinationUser = None
			destinationChannel = None
			if message["user"] is not None:
				destinationUser = client.get_user(int(message["user"]))
				if destinationUser is None:
					destinationUser = await client.fetch_user(int(message["user"]))
			if message["channel"] is not None:
				destinationChannel = client.get_channel(int(message["channel"]))
				if destinationChannel is None:
					destinationChannel = await client.fetch_channel(int(message["channel"]))

			if message["user"] is not None:
				try:
					await destinationUser.send(embed=embed)
				except:
					try:
						mentionText = "<@!{}>!".format(message["user"]) if destinationUser is None else None
						await destinationChannel.send(content=mentionText, embed=embed)
					except: pass
				database.document("discord/properties/messages/{}".format(messageId)).delete()
			elif message["channel"] is not None:
				try:
					await destinationChannel.send(embed=embed)
					database.document("discord/properties/messages/{}".format(messageId)).delete()
				except:
					pass
			else:
				raise Exception("no destination found")

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def process_ichibot_messages(self, origin, author):
		try:
			socket = self.ichibotSockets.get(origin)

			while origin in self.ichibotSockets:
				try:
					messageContent = "```ruby"

					while True:
						try: [messenger, message] = await socket.recv_multipart(flags=NOBLOCK)
						except: break
						if messenger.decode() == "alpha":
							embed = discord.Embed(title=message.decode(), color=constants.colors["gray"])
							embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
							await author.send(embed=embed)
						else:
							message = message.decode()
							if len(message) + len(messageContent) + 4 >= 2000:
								messageContent = messageContent[:1997] + "```"
								await author.send(content=messageContent)
								messageContent = "```ruby"
							messageContent += "\n" + message

					if messageContent != "```ruby":
						messageContent = messageContent[:1997] + "```"
						await author.send(content=messageContent)
					await asyncio.sleep(1)

				except:
					print(traceback.format_exc())
					if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=origin.decode('utf-8'))

			socket.close()

		except:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=origin.decode('utf-8'))

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

	async def database_sanity_check(self):
		if not os.environ["PRODUCTION_MODE"]: return
		try:
			guilds = await self.guildProperties.keys()
			accounts = await self.accountProperties.keys()
			if guilds is None or accounts is None: return

			guildIds = [str(g.id) for g in client.guilds]

			for guildId in guilds:
				if guildId not in guildIds:
					properties = await self.guildProperties.get(guildId)

					if "settings" in properties and properties["settings"]["setup"]["connection"] is not None:
						holdingId = properties["settings"]["setup"]["connection"]

						if holdingId in accounts:
							communityList = (await self.accountProperties.get(holdingId))["customer"]["communitySubscriptions"]
							if guildId in communityList:
								communityList.remove(guildId)
								database.document("accounts/{}".format(holdingId)).set({"customer": {"communitySubscriptions": communityList}}, merge=True)
						else:
							self.logging.report("Database sanity check failed: community connected to a missing account ({})".format(holdingId))

					database.document("discord/properties/guilds/{}".format(guildId)).delete()
					self.logging.report("Database sanity check failed: redundant database entry")

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def guild_sanity_check(self, guild, guilds):
		properties = await self.guildProperties.get(guild.id)

		if properties is None or str(guild.id) not in guilds or "addons" not in properties:
			properties = database.document("discord/properties/guilds/{}".format(guild.id)).get().to_dict()
			if properties is None:
				properties = {}
			if "addons" not in properties:
				self.logging.report("Database sanity check failed: no database entry")
				properties = MessageRequest.create_guild_settings(properties)
				database.document("discord/properties/guilds/{}".format(guild.id)).set(properties)

		if "properties" not in properties or properties["properties"]["name"] != guild.name or properties["properties"]["icon"] != guild.icon:
			database.document("discord/properties/guilds/{}".format(guild.id)).set({"properties": {"name": guild.name, "icon": guild.icon}}, merge=True)

		return properties

	async def account_sanity_check(self, accountId):
		properties = await self.accountProperties.get(accountId)

		if properties is None:
			properties = database.document("accounts/{}".format(accountId)).get().to_dict()

		return properties

	async def update_pro_accounts(self):
		try:
			guilds = await self.guildProperties.keys()
			if guilds is None: return

			for guild in client.guilds:
				properties = await self.guild_sanity_check(guild, guilds)
				if not properties["settings"]["setup"]["completed"]: continue

				if properties["addons"]["satellites"].get("connection") is not None:
					accountId = properties["settings"]["setup"]["connection"]
					accountProperties = await self.account_sanity_check(accountId)
					if accountProperties is None:
						self.logging.report("Database sanity check failed: server {} connected to a missing account ({})".format(guild.id, accountId))
						continue

					isPro = accountProperties["customer"]["personalSubscription"].get("subscription", None) is not None

					serverMembers = [e.id for e in guild.members if e.bot]
					satelliteCount = len([botId for botId in constants.satellites if botId in serverMembers])

					if satelliteCount > properties["addons"]["satellites"].get("count", 0) or (satelliteCount != 0 and not properties["addons"]["satellites"]["enabled"]):
						if isPro:
							database.document("discord/properties/guilds/{}".format(guild.id)).set({"addons": {"satellites": {"enabled": True, "count": satelliteCount}}}, merge=True)
							if os.environ["PRODUCTION_MODE"]:
								subscription = stripe.Subscription.retrieve(accountProperties["customer"]["personalSubscription"]["subscription"])
								cycleRatio = (subscription["current_period_end"] - time.time()) / (subscription["current_period_end"] - subscription["current_period_start"])
								stripe.SubscriptionItem.create_usage_record(subscription["items"]["data"][0]["id"], quantity=int(math.ceil((satelliteCount - properties["addons"]["satellites"].get("count", 0)) * 20 * cycleRatio)), timestamp=int(time.time()), action="increment")
							else:
								print("[Development]: Charging {} for {} new satellites".format(accountId, satelliteCount - properties["addons"]["satellites"].get("count", 0)))

					elif not isPro or properties["addons"]["satellites"].get("count", 0) == 0:
						database.document("discord/properties/guilds/{}".format(guild.id)).set({"addons": {"satellites": {"enabled": False}}}, merge=True)

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
			numOfGuilds = ":heart: Used in {:,} Discord communities".format(len(client.guilds))

			statisticsEmbed = discord.Embed(title="{}\n{}\n{}\n{}\n{}".format(numOfCharts, numOfAlerts, numOfPrices, numOfTrades, numOfGuilds), color=constants.colors["deep purple"])

			if os.environ["PRODUCTION_MODE"]:
				statusChannel = client.get_channel(560884869899485233)
				statsMessage = await statusChannel.fetch_message(640502810244415532)
				if statsMessage is not None:
					await statsMessage.edit(embed=statisticsEmbed, suppress=False)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


	# -------------------------
	# Message handling
	# -------------------------

	async def on_message(self, message):
		try:
			# Skip messages with empty content field, messages from self, or all messages when in startup mode
			if message.clean_content == "" or message.type != discord.MessageType.default or message.author == client.user or not self.is_bot_ready(): return

			_rawMessage = " ".join(message.clean_content.split())
			_messageContent = _rawMessage.lower()
			_authorId = message.author.id if message.webhook_id is None else message.webhook_id
			_accountId = None
			_guildId = message.guild.id if message.guild is not None else -1
			_channelId = message.channel.id if message.channel is not None else -1
			if _authorId == 361916376069439490:
				if " --user " in _messageContent:
					_messageContent, _authorId = _messageContent.split(" --user ")[0], int(_messageContent.split(" --user ")[1])
				if " --guild " in _messageContent:
					_messageContent, _guildId = _messageContent.split(" --guild ")[0], int(_messageContent.split(" --guild ")[1])

			# Ignore if user if locked in a prompt, or banned
			if _authorId in self.lockedUsers or _authorId in constants.blockedUsers or _guildId in constants.blockedGuilds: return

			_accountProperties = {}
			_guildProperties = await self.guildProperties.get(_guildId, {})
			if not message.author.bot:
				if message.webhook_id is None: _accountId = await self.accountProperties.match(_authorId)
				if _accountId is None:
					_accountProperties = await self.accountProperties.get(str(_authorId), {})
				else:
					_accountProperties = await self.accountProperties.get(_accountId, {})

			messageRequest = MessageRequest(
				raw=_rawMessage,
				content=_messageContent,
				accountId=_accountId,
				authorId=_authorId,
				channelId=_channelId,
				guildId=_guildId,
				accountProperties=_accountProperties,
				guildProperties=_guildProperties
			)
			sentMessages = []

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
									if response in ["y", "yes", "sure", "confirm", "execute"]: return True
									elif response in ["n", "no", "cancel", "discard", "reject"]: raise Exception()

							try:
								await client.wait_for('message', timeout=60.0, check=confirm_order)
							except:
								self.lockedUsers.discard(messageRequest.authorId)
								embed = discord.Embed(title="Prompt has been canceled.", description="~~Do you want to add `{}` preset to your account?~~".format(parsedPresets[0]["phrase"]), color=constants.colors["gray"])
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
			isCommand = messageRequest.content.startswith(tuple(constants.commandWakephrases))

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
						forcedFetch = MessageRequest.create_guild_settings(database.document("discord/properties/guilds/{}".format(messageRequest.guildId)).get().to_dict())
						if forcedFetch["settings"]["setup"]["completed"]:
							messageRequest.guildProperties = forcedFetch
						elif not message.author.bot and message.author.permissions_in(message.channel).administrator:
							embed = discord.Embed(title="Hello world!", description="Thanks for adding Alpha Bot to your Discord community, we're thrilled to have you onboard. We think you're going to love everything Alpha Bot can do. Before you start using it, you must complete a short setup process. Sign into your [Alpha Account](https://www.alphabotsystem.com/communities) and visit your [Communities Dashboard](https://www.alphabotsystem.com/communities) to begin.", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
						else:
							embed = discord.Embed(title="Hello world!", description="This is Alpha Bot, the most advanced financial bot on Discord. A short setup process hasn't been completed in this Discord community yet. Ask administrators to complete it by signing into their [Alpha Account](https://www.alphabotsystem.com/communities) and visiting their [Communities Dashboard](https://www.alphabotsystem.com/communities).", color=constants.colors["pink"])
							await message.channel.send(embed=embed)
						return

			if messageRequest.content.startswith("a "):
				if message.author.bot: return

				command = messageRequest.content.split(" ", 1)[1]
				if message.author.id == 361916376069439490:
					if command == "user":
						await message.delete()
						settings = copy.deepcopy(messageRequest.accountProperties)
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
					elif command.startswith("say"):
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
					fallThrough, response = self.assistant.process_reply(messageRequest.content, rawCaps, messageRequest.guildProperties["settings"]["assistant"]["enabled"])

					if fallThrough:
						if response == "help":
							await self.help(message, messageRequest)
						elif response == "ping":
							await message.channel.send(content="Pong")
						elif response == "pro":
							await message.channel.send(content="Visit https://www.alphabotsystem.com/pro to learn more about Alpha Pro and how to start your free trial.")
						elif response == "invite":
							await message.channel.send(content="https://discordapp.com/oauth2/authorize?client_id=401328409499664394&scope=applications.commands%20bot&permissions=604372032")
					elif response is not None and response != "":
						await message.channel.send(content=response)
				elif messageRequest.content.startswith("preset "):
					if message.author.bot: return

					if messageRequest.content == "preset help":
						embed = discord.Embed(title=":pushpin: Command presets", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(", preset | preset ", messageRequest.content.split(" ", 1)[1])
						if len(requestSlices) > messageRequest.get_limit() / 2:
							await self.hold_up(message, messageRequest)
							return
						for requestSlice in requestSlices:
							await self.presets(message, messageRequest, requestSlice)
				elif messageRequest.content.startswith("c "):
					if messageRequest.content == "c help":
						embed = discord.Embed(title=":chart_with_upwards_trend: Charts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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
								if requestSlice.startswith("am ") or requestSlice.startswith("wc ") or requestSlice.startswith("tl ") or requestSlice.startswith("tv ") or requestSlice.startswith("bm ") or requestSlice.startswith("gc ") or requestSlice.startswith("fv "):
									await message.channel.send(embed=discord.Embed(title="We're deprecating the old platform override syntax. Use `c {} {}` from now on instead.".format(requestSlice[3:], requestSlice[:2]), color=constants.colors["gray"]))
									return

								chartMessages, weight = await self.chart(message, messageRequest, requestSlice)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2

						self.statistics["c"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("flow "):
					if messageRequest.content == "flow help":
						embed = discord.Embed(title=":microscope: Alpha Flow", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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
								chartMessages, weight = await self.flow(message, messageRequest, requestSlice)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2

						self.statistics["flow"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("hmap "):
					if messageRequest.content == "hmap help":
						embed = discord.Embed(title=":fire: Heat map", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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
								chartMessages, weight = await self.heatmap(message, messageRequest, requestSlice)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += 2 - weight
								else: self.rateLimited[messageRequest.authorId] = 2 - weight

						self.statistics["hmap"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("d "):
					if messageRequest.content == "d help":
						embed = discord.Embed(title=":book: Orderbook visualizations", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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
								chartMessages, weight = await self.depth(message, messageRequest, requestSlice)
								sentMessages += chartMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2

						self.statistics["d"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith(("alert ", "alerts ")):
					if message.author.bot: return

					if messageRequest.content in ["alert help", "alerts help"]:
						embed = discord.Embed(title=":bell: Price Alerts", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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
								quoteMessages, weight = await self.alert(message, messageRequest, requestSlice)
								sentMessages += quoteMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2

						self.statistics["alerts"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("p "):
					if messageRequest.content == "p help":
						embed = discord.Embed(title=":money_with_wings: Prices", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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
								quoteMessages, weight = await self.price(message, messageRequest, requestSlice)
								sentMessages += quoteMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2

						self.statistics["p"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("v "):
					if messageRequest.content == ":credit_card: v help":
						embed = discord.Embed(title="Volume", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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
								volumeMessages, weight = await self.volume(message, messageRequest, requestSlice)
								sentMessages += volumeMessages
								totalWeight += weight - 1

								if messageRequest.authorId in self.rateLimited: self.rateLimited[messageRequest.authorId] += weight - 2
								else: self.rateLimited[messageRequest.authorId] = weight - 2

						self.statistics["v"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("convert "):
					if messageRequest.content == "convert help":
						embed = discord.Embed(title=":yen: Conversions", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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

						self.statistics["convert"] += totalWeight
				elif messageRequest.content.startswith(("m ", "info")):
					if messageRequest.content in ["m help", "info help"]:
						embed = discord.Embed(title=":tools: Market information", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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

						self.statistics["mcap"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("top"):
					if messageRequest.content == "top help":
						embed = discord.Embed(title=":tools: Rankings", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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

						self.statistics["t"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("mk "):
					if messageRequest.content == "mk help":
						embed = discord.Embed(title=":page_facing_up: Market listings", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
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

						self.statistics["mk"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("n ") and messageRequest.authorId in [361916376069439490, 390170634891689984]:
					if messageRequest.content == "n help":
						embed = discord.Embed(title=":newspaper: News", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["gray"])
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

						self.statistics["n"] += totalWeight
						await self.finish_request(message, messageRequest, totalWeight, sentMessages)
				elif messageRequest.content.startswith("x "):
					if messageRequest.content == "x help":
						embed = discord.Embed(title=":dart: Ichibot", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlice = messageRequest.content.split(" ", 1)[1]
						forceDelete = False
						if messageRequest.content.startswith(("x ichibot", "x ichi", "x login")):
							await self.initiate_ichibot(message, messageRequest, requestSlice)
						elif messageRequest.guildId == -1 or (messageRequest.marketBias == "crypto" or len(messageRequest.accountProperties.get("apiKeys", {}).keys()) != 0):
							await self.process_ichibot_command(message, messageRequest, requestSlice)
							forceDelete = True

						self.statistics["x"] += 1
						await self.finish_request(message, messageRequest, 0, [], force=forceDelete)
				elif messageRequest.content.startswith("paper "):
					if messageRequest.content == "paper help":
						embed = discord.Embed(title=":joystick: Alpha Paper Trader", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide).", color=constants.colors["light blue"])
						await message.channel.send(embed=embed)
					else:
						requestSlices = re.split(', paper | paper |, ', messageRequest.content.split(" ", 1)[1])
						totalWeight = len(requestSlices)
						for requestSlice in requestSlices:
							if messageRequest.content == "paper balance":
								await self.fetch_paper_balance(message, messageRequest, requestSlice)
							elif messageRequest.content == "paper leaderboard":
								await self.fetch_paper_leaderboard(message, messageRequest, requestSlice)
							elif messageRequest.content == "paper history":
								await self.fetch_paper_orders(message, messageRequest, requestSlice, "history")
							elif messageRequest.content == "paper orders":
								await self.fetch_paper_orders(message, messageRequest, requestSlice, "openOrders")
							elif messageRequest.content == "paper reset":
								await self.reset_paper_balance(message, messageRequest, requestSlice)
							else:
								await self.process_paper_trade(message, messageRequest, requestSlice)

						self.statistics["paper"] += totalWeight
			else:
				if messageRequest.guildProperties["settings"]["assistant"]["enabled"]:
					response = self.assistant.funnyReplies(messageRequest.content)
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
							accountId = await self.accountProperties.match(user.id)
							properties = await self.accountProperties.get(accountId)

							alertId = footerText.split(" ● id: ")[1]
							if accountId is None:
								database.document("details/marketAlerts/{}/{}".format(user.id, alertId)).delete()
							else:
								database.document("details/marketAlerts/{}/{}".format(accountId, alertId)).delete()

							embed = discord.Embed(title="Alert deleted", color=constants.colors["gray"])
							embed.set_footer(text=footerText)
							try: await reaction.message.edit(embed=embed)
							except: pass

						elif footerText.startswith("Paper order") and " ● id: " in footerText:
							accountId = await self.accountProperties.match(user.id)
							properties = await self.accountProperties.get(accountId)

							orderId = footerText.split(" ● id: ")[1]
							order = database.document("details/openPaperOrders/{}/{}".format(accountId, orderId)).get().to_dict()

							request = pickle.loads(zlib.decompress(order["request"]))
							ticker = request.get_ticker()

							base = ticker.base
							quote = ticker.quote
							if base in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
								baseBalance = properties["paperTrader"]["balance"]
								base = "USD"
							else:
								baseBalance = properties["paperTrader"]["balance"][request.currentPlatform]
							if quote in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
								quoteBalance = properties["paperTrader"]["balance"]
								quote = "USD"
							else:
								quoteBalance = properties["paperTrader"]["balance"][request.currentPlatform]

							if order["orderType"] == "buy":
								quoteBalance[quote] += order["amount"] * order["price"]
							elif order["orderType"] == "sell":
								baseBalance[base] += order["amount"]

							database.document("details/openPaperOrders/{}/{}".format(accountId, orderId)).delete()
							database.document("accounts/{}".format(accountId)).set({"paperTrader": properties["paperTrader"]}, merge=True)
							
							embed = discord.Embed(title="Paper order has been canceled.", color=constants.colors["gray"])
							embed.set_footer(text=footerText)
							try: await reaction.message.edit(embed=embed)
							except: pass
						elif " → `" in titleText and titleText.endswith("`"):
							accountId = await self.accountProperties.match(user.id, user.id)
							properties = await self.accountProperties.get(accountId)

							properties, _ = Presets.update_presets(properties, remove=titleText.split("`")[1])
							if not "customer" in properties:
								database.document("discord/properties/users/{}".format(user.id)).set({"commandPresets": properties["commandPresets"]}, merge=True)
							else:
								database.document("accounts/{}".format(accountId)).set({"commandPresets": properties["commandPresets"]}, merge=True)
							

							embed = discord.Embed(title="Preset deleted", color=constants.colors["gray"])
							embed.set_footer(text=footerText)
							try: await reaction.message.edit(embed=embed)
							except: pass
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def finish_request(self, message, messageRequest, weight, sentMessages, waitTime=60, force=False):
		await asyncio.sleep(waitTime)
		if weight != 0 and messageRequest.authorId in self.rateLimited:
			self.rateLimited[messageRequest.authorId] -= weight
			if self.rateLimited[messageRequest.authorId] < 1: self.rateLimited.pop(messageRequest.authorId, None)

		if (len(sentMessages) != 0 and messageRequest.autodelete) or force:
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
		embed = discord.Embed(title=":wave: Introduction", description="Alpha Bot is the world's most popular Discord bot for requesting charts, set price alerts, and more. Using Alpha Bot is as simple as typing a short command into any Discord channel the bot has access to.", color=constants.colors["light blue"])
		embed.add_field(name=":chart_with_upwards_trend: Charts", value="Easy access to on-demand TradingView, TradingLite, GoCharting, Finviz, and Bookmap charts. Learn more about [charting capabilities](https://www.alphabotsystem.com/guide/charts) on our website.", inline=False)
		embed.add_field(name=":dart: Ichibot integration", value="Trade cryptocurrencies with Ichibot, a best-in-class order execution client. Learn more about [Ichibot](https://www.alphabotsystem.com/guide/ichibot) on our website.", inline=False)
		embed.add_field(name=":bell: Price Alerts", value="Price alerts, right in your community. Learn more about [price alerts](https://www.alphabotsystem.com/guide/price-alerts) on our website.", inline=False)
		embed.add_field(name=":joystick: Paper Trader", value="Execute paper trades through Alpha Bot. Learn more about [paper trader](https://www.alphabotsystem.com/guide/paper-trader) on our website.", inline=False)
		embed.add_field(name=":ocean: Alpha Flow", value="Inform your stock options trading with aggregated BlackBox Stocks data. Learn more about [Alpha Flow](https://www.alphabotsystem.com/pro/flow) on our website.", inline=False)
		embed.add_field(name=":money_with_wings: Prices & Asset Details", value="Prices and details for tens of thousands of tickers. Learn more about [prices](https://www.alphabotsystem.com/guide/prices) and [asset details](https://www.alphabotsystem.com/guide/asset-details) on our website.", inline=False)
		embed.add_field(name=":fire: There's more!", value="A [full guide](https://www.alphabotsystem.com/guide) is available on our website.", inline=False)
		embed.add_field(name=":tada: Official Alpha channels", value="[Join our Discord community](https://discord.gg/GQeDE85) or [Follow us on Twitter @AlphaBotSystem](https://twitter.com/AlphaBotSystem).", inline=False)
		embed.set_footer(text="Use \"alpha help\" to pull up this list again.")
		await message.channel.send(embed=embed)


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
							database.document("accounts/{}".format(messageRequest.accountId)).set({"commandPresets": properties["commandPresets"]}, merge=True)
						elif messageRequest.personal_command_presets_available():
							database.document("accounts/{}".format(messageRequest.accountId)).set({"commandPresets": properties["commandPresets"], "customer": {"addons": {"commandPresets": 1}}}, merge=True)

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

					else:
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
				embed = discord.Embed(title="`{}` is not a valid argument.".format(method), description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/command-presets).", color=constants.colors["gray"])
				embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Charting
	# -------------------------

	async def chart(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_chart_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper())
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/charts).", color=constants.colors["gray"])
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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def flow(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			if messageRequest.flow_available():
				outputMessage, request = Processor.process_chart_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["Alpha Flow"])
				if outputMessage is not None:
					if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
						embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/flow).", color=constants.colors["gray"])
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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def heatmap(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_heatmap_arguments(messageRequest, arguments)
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/heat-maps).", color=constants.colors["gray"])
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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def depth(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), excluded=["CoinGecko", "Quandl", "LLD"])
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/orderbook-visualizations).", color=constants.colors["gray"])
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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Quotes
	# -------------------------

	async def alert(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")
			method = arguments[0]

			if method in ["set", "create", "add"] and len(arguments) >= 2:
				if messageRequest.price_alerts_available():
					outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), isMarketAlert=True, excluded=["CoinGecko", "Quandl", "LLD"])
					if outputMessage is not None:
						if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
							embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/price-alerts).", color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
							sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					await message.channel.trigger_typing()

					ticker = request.get_ticker()
					exchange = request.get_exchange()

					response1, response2 = [], []
					if messageRequest.is_registered():
						response1 = database.collection("details/marketAlerts/{}".format(messageRequest.accountId)).get()
					response2 = database.collection("details/marketAlerts/{}".format(messageRequest.authorId)).get()
					marketAlerts = [e.to_dict() for e in response1] + [e.to_dict() for e in response2]

					if len(marketAlerts) >= 50:
						embed = discord.Embed(title="You can only create up to 50 price alerts.", color=constants.colors["gray"])
						embed.set_author(name="Maximum number of price alerts reached", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					level = request.get_numerical_parameters()[0]
					if request.currentPlatform == "CCXT":
						levelText = TickerParser.get_formatted_price(exchange.id, ticker.symbol, level)
					elif request.currentPlatform == "CoinGecko":
						levelText = ("{:,.%df}" % (4 if TickerParser.check_if_fiat(ticker.quote)[0] and not ticker.isReversed else 8)).format(level)
					elif request.currentPlatform == "IEXC" or request.currentPlatform == "Quandl":
						levelText = "{:,.5f}".format(level)
					else:
						levelText = "{:,.0f}".format(level)

					alertId = str(uuid.uuid4())
					newAlert = {
						"timestamp": time.time(),
						"channel": str(message.channel.id),
						"service": "Discord",
						"request": zlib.compress(pickle.dumps(request, -1)),
						"level": level,
						"levelText": levelText
					}

					for alert in marketAlerts:
						alertRequest = pickle.loads(zlib.decompress(alert["request"]))
						alertExchange = alertRequest.get_exchange()
						if alertRequest.get_ticker() == ticker and alertRequest.currentPlatform == request.currentPlatform and ((alertExchange is None and exchange is None) or alertExchange.id == exchange.id):
							if alert["level"] == newAlert["level"]:
								embed = discord.Embed(title="Price alert for {} ({}) at {} {} already exists.".format(ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote), color=constants.colors["gray"])
								embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
								sentMessages.append(await message.channel.send(embed=embed))
								return (sentMessages, len(sentMessages))
							elif alert["level"] * 0.999 < newAlert["level"] < alert["level"] * 1.001:
								embed = discord.Embed(title="Price alert within 0.1% already exists.", color=constants.colors["gray"])
								embed.set_author(name="Alert already exists", icon_url=static_storage.icon_bw)
								sentMessages.append(await message.channel.send(embed=embed))
								return (sentMessages, len(sentMessages))

					payload, quoteText = await Processor.execute_data_server_request("candle", request)

					if payload is None or len(payload.get("candles", [])) == 0:
						errorMessage = "Requested price alert for `{}` is not available.".format(request.get_ticker().name) if quoteText is None else quoteText
						embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
						embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
						quoteMessage = await message.channel.send(embed=embed)
						sentMessages.append(quoteMessage)
						try: await quoteMessage.add_reaction("☑")
						except: pass
					else:
						request.set_current(platform=payload["platform"])
						if payload["candles"][0][4] * 0.5 > newAlert["level"] or payload["candles"][0][4] * 2 < newAlert["level"]:
							embed = discord.Embed(title="Your desired alert trigger level at {} {} is too far from the current price of {} {}.".format(levelText, ticker.quote, payload["candles"][0][4], payload["quoteTicker"]), color=constants.colors["gray"])
							embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
							embed.set_footer(text="Price {}".format(payload["sourceText"]))
							sentMessages.append(await message.channel.send(embed=embed))
							return (sentMessages, len(sentMessages))

						newAlert["placement"] = "above" if newAlert["level"] > payload["candles"][0][4] else "below"

						embed = discord.Embed(title="Price alert set for {} ({}) at {} {}.".format(ticker.base, request.currentPlatform if exchange is None else exchange.name, levelText, ticker.quote), color=constants.colors["deep purple"])
						embed.set_author(name="Alert successfully set", icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))

						if not messageRequest.is_registered():
							database.document("details/marketAlerts/{}/{}".format(messageRequest.authorId, alertId)).set(newAlert)
						elif messageRequest.serverwide_price_alerts_available():
							database.document("details/marketAlerts/{}/{}".format(messageRequest.accountId, alertId)).set(newAlert)
						elif messageRequest.personal_price_alerts_available():
							database.document("details/marketAlerts/{}/{}".format(messageRequest.accountId, alertId)).set(newAlert)
							database.document("accounts/{}".format(messageRequest.accountId)).set({"customer": {"addons": {"marketAlerts": 1}}}, merge=True)

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

			elif method in ["list", "all"] and len(arguments) == 1:
				await message.channel.trigger_typing()

				response1, response2 = [], []
				if messageRequest.is_registered():
					response1 = database.collection("details/marketAlerts/{}".format(messageRequest.accountId)).get()
				response2 = database.collection("details/marketAlerts/{}".format(messageRequest.authorId)).get()
				marketAlerts = [(e.id, e.to_dict()) for e in response1] + [(e.id, e.to_dict()) for e in response2]
				totalAlertCount = len(marketAlerts)

				for index, (key, alert) in enumerate(marketAlerts):
					alertRequest = pickle.loads(zlib.decompress(alert["request"]))
					ticker = alertRequest.get_ticker()
					exchange = alertRequest.get_exchange()

					embed = discord.Embed(title="Price alert set for {} ({}) at {} {}".format(ticker.base, alertRequest.currentPlatform if exchange is None else exchange.name, alert.get("levelText", alert["level"]), ticker.quote), color=constants.colors["deep purple"])
					embed.set_footer(text="Alert {}/{} ● id: {}".format(index + 1, totalAlertCount, key))
					alertMessage = await message.channel.send(embed=embed)
					sentMessages.append(alertMessage)
					try: await alertMessage.add_reaction('❌')
					except: pass

				if totalAlertCount == 0:
					embed = discord.Embed(title="You haven't set any alerts yet.", color=constants.colors["gray"])
					embed.set_author(name="Price Alerts", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))

			else:
				embed = discord.Embed(title="Invalid command usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/price-alerts).", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def price(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper())
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/prices).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				payload, quoteText = await Processor.execute_data_server_request("quote", request)

			if payload is None or "quotePrice" not in payload:
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
					embed = discord.Embed(title="{} *({:+.0f} since yesterday)*".format(payload["quotePrice"], payload["change"]), description=payload.get("quoteConvertedPrice", discord.embeds.EmptyEmbed), color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
					embed.set_footer(text=payload["sourceText"])
					sentMessages.append(await message.channel.send(embed=embed))
				elif request.currentPlatform == "LLD":
					embed = discord.Embed(title=payload["quotePrice"], description=payload.get("quoteConvertedPrice", discord.embeds.EmptyEmbed), color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
					embed.set_footer(text=payload["sourceText"])
					sentMessages.append(await message.channel.send(embed=embed))
				else:
					embed = discord.Embed(title="{} {}{}".format(payload["quotePrice"], payload["quoteTicker"], "" if "change" not in payload else " *({:+.2f} %)*".format(payload["change"])), description=payload.get("quoteConvertedPrice", discord.embeds.EmptyEmbed), color=constants.colors[payload["messageColor"]])
					embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
					embed.set_footer(text="Price {}".format(payload["sourceText"]))
					sentMessages.append(await message.channel.send(embed=embed))

			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def volume(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper())
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/volume).", color=constants.colors["gray"])
					embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
					sentMessages.append(await message.channel.send(embed=embed))
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				payload, quoteText = await Processor.execute_data_server_request("quote", request)

			if payload is None or "quoteVolume" not in payload:
				errorMessage = "Requested volume for `{}` is not available.".format(request.get_ticker().name) if quoteText is None else quoteText
				embed = discord.Embed(title=errorMessage, color=constants.colors["gray"])
				embed.set_author(name="Data not available", icon_url=static_storage.icon_bw)
				quoteMessage = await message.channel.send(embed=embed)
				sentMessages.append(quoteMessage)
				try: await quoteMessage.add_reaction("☑")
				except: pass
			else:
				embed = discord.Embed(title="{:,.4f} {}".format(payload["quoteVolume"], payload["baseTicker"]), description=payload.get("quoteConvertedVolume", discord.embeds.EmptyEmbed), color=constants.colors["orange"])
				embed.set_author(name=payload["title"], icon_url=payload["thumbnailUrl"])
				embed.set_footer(text="Volume {}".format(payload["sourceText"]))
				sentMessages.append(await message.channel.send(embed=embed))

			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def convert(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			requestSlices = re.split(" into | in to | in | to ", requestSlice)
			if len(requestSlices) != 2 or len(requestSlices[0].split(" ")) != 2 or len(requestSlices[1].split(" ")) != 1:
				if not messageRequest.is_muted():
					embed = discord.Embed(title="Incorrect currency conversion usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/conversions).", color=constants.colors["gray"])
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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Details
	# -------------------------

	async def details(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_detail_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper())
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/asset-details).", color=constants.colors["gray"])
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

				embed = discord.Embed(title=payload["name"], description=payload.get("description", discord.embeds.EmptyEmbed), url=payload.get("url", discord.embeds.EmptyEmbed), color=constants.colors["lime"])
				if payload.get("image") is not None:
					embed.set_thumbnail(url=payload["image"])

				assetFundementals = ""
				assetInfo = ""
				assetSupply = ""
				assetScore = ""
				if payload.get("marketcap") is not None:
					assetFundementals += "\nMarket cap: {:,.0f} {}{}".format(payload["marketcap"], "USD", "" if payload.get("rank") is None else " (ranked #{})".format(payload["rank"]))
				if payload.get("volume") is not None:
					assetFundementals += "\nTotal volume: {:,.0f} {}".format(payload["volume"], "USD")
				if payload.get("industry") is not None:
					assetFundementals += "\nIndustry: {}".format(payload["industry"])
				if payload.get("info") is not None:
					if payload["info"].get("location") is not None:
						assetInfo += "\nLocation: {}".format(payload["info"]["location"])
					if payload["info"].get("employees") is not None:
						assetInfo += "\nEmployees: {}".format(payload["info"]["employees"])
				if payload.get("supply") is not None:
					if payload["supply"].get("total") is not None:
						assetSupply += "\nTotal supply: {:,.0f} {}".format(payload["supply"]["total"], ticker.base)
					if payload["supply"].get("circulating") is not None:
						assetSupply += "\nCirculating supply: {:,.0f} {}".format(payload["supply"]["circulating"], ticker.base)
				if payload.get("score") is not None:
					if payload["score"].get("developer") is not None:
						assetScore += "\nDeveloper score: {:,.1f}/100".format(payload["score"]["developer"])
					if payload["score"].get("community") is not None:
						assetScore += "\nCommunity score: {:,.1f}/100".format(payload["score"]["community"])
					if payload["score"].get("liquidity") is not None:
						assetScore += "\nLiquidity score: {:,.1f}/100".format(payload["score"]["liquidity"])
					if payload["score"].get("public interest") is not None:
						assetScore += "\nPublic interest: {:,.3f}".format(payload["score"]["public interest"])
				detailsText = assetFundementals[1:] + assetInfo + assetSupply + assetScore
				if detailsText != "":
					embed.add_field(name="Details", value=detailsText, inline=False)

				assetPriceDetails = ""
				if payload["price"].get("current") is not None:
					assetPriceDetails += ("\nCurrent: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["current"])).format(payload["price"]["current"])
				if payload["price"].get("ath") is not None:
					assetPriceDetails += ("\nAll-time high: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["ath"])).format(payload["price"]["ath"])
				if payload["price"].get("atl") is not None:
					assetPriceDetails += ("\nAll-time low: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["atl"])).format(payload["price"]["atl"])
				if payload["price"].get("1y high") is not None:
					assetPriceDetails += ("\n1-year high: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["1y high"])).format(payload["price"]["1y high"])
				if payload["price"].get("1y low") is not None:
					assetPriceDetails += ("\n1-year low: ${:,.%df}" % Utils.add_decimal_zeros(payload["price"]["1y low"])).format(payload["price"]["1y low"])
				if payload["price"].get("per") is not None:
					assetPriceDetails += "\nPrice-to-earnings ratio: {:,.2f}".format(payload["price"]["per"])
				if assetPriceDetails != "":
					embed.add_field(name="Price", value=assetPriceDetails[1:], inline=True)

				change24h = "Past day: no data"
				change30d = ""
				change1y = ""
				if payload["change"].get("past day") is not None:
					change24h = "\nPast day: *{:+,.2f} %*".format(payload["change"]["past day"])
				if payload["change"].get("past month") is not None:
					change30d = "\nPast month: *{:+,.2f} %*".format(payload["change"]["past month"])
				if payload["change"].get("past year") is not None:
					change1y = "\nPast year: *{:+,.2f} %*".format(payload["change"]["past year"])
				embed.add_field(name="Price change", value=(change24h + change30d + change1y), inline=True)
				embed.set_footer(text="Data {}".format(payload["sourceText"]))

				sentMessages.append(await message.channel.send(embed=embed))

			autodeleteOverride = request.find_parameter_in_list("autoDeleteOverride", request.get_filters(), default=False)
			messageRequest.autodelete = messageRequest.autodelete or autodeleteOverride

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def rankings(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ", 2)
			method = arguments[0]

			if method in ["alpha", "requests", "charts"]:
				if messageRequest.statistics_available():
					response = []
					async with message.channel.typing():
						rawData = database.document("dataserver/statistics").get().to_dict()
						response = rawData["top"][messageRequest.marketBias][:9:-1]

					embed = discord.Embed(title="Top Alpha Bot requests", color=constants.colors["deep purple"])
					for token in response:
						embed.add_field(name=token["id"], value="Rank {:,.2f}/100".format(token["rank"]), inline=True)
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
					rawData = CoinGeckoAPI().get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, price_change_percentage="24h")
					for e in rawData:
						if e.get("price_change_percentage_24h_in_currency", None) is not None:
							response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
					response = sorted(response, key=lambda k: k["change"], reverse=True)[:10]
				
				embed = discord.Embed(title="Top gainers", color=constants.colors["deep purple"])
				for token in response:
					embed.add_field(name=token["symbol"], value="Gained {:,.2f} %".format(token["change"]), inline=True)
				sentMessages.append(await message.channel.send(embed=embed))

			elif method in ["losers", "loosers", "loss", "losses"]:
				response = []
				async with message.channel.typing():
					rawData = CoinGeckoAPI().get_coins_markets(vs_currency="usd", order="market_cap_desc", per_page=250, price_change_percentage="24h")
					for e in rawData:
						if e.get("price_change_percentage_24h_in_currency", None) is not None:
							response.append({"symbol": e["symbol"].upper(), "change": e["price_change_percentage_24h_in_currency"]})
				response = sorted(response, key=lambda k: k["change"])[:10]
				
				embed = discord.Embed(title="Top losers", color=constants.colors["deep purple"])
				for token in response:
					embed.add_field(name=token["symbol"], value="Lost {:,.2f} %".format(token["change"]), inline=True)
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def markets(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")

			outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[1:], tickerId=arguments[0].upper(), platformQueue=["CCXT"])
			if outputMessage is not None:
				if not messageRequest.is_muted() and outputMessage != "":
					embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/asset-details).", color=constants.colors["gray"])
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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# News
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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Ichibot
	# -------------------------

	async def initiate_ichibot(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = requestSlice.split(" ")
			method = arguments[0]

			if method in ["ichibot", "ichi"]:
				if messageRequest.is_registered():
					outputMessage, request = Processor.process_trade_arguments(messageRequest, arguments[1:], platformQueue=["Ichibot"])
					if outputMessage is not None:
						if not messageRequest.is_muted() and outputMessage != "":
							embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/ichibot).", color=constants.colors["gray"])
							embed.set_author(name="Invalid argument", icon_url=static_storage.ichibot)
							sentMessages.append(await message.channel.send(embed=embed))
						return (sentMessages, len(sentMessages))

					origin = "{}_{}_ichibot".format(messageRequest.accountId, messageRequest.authorId)
					encodedAccountId = bytes(messageRequest.accountId, encoding='utf8')
					encodedExchangeId = bytes(request.get_exchange().id, encoding='utf8')

					if origin in self.ichibotSockets:
						socket = self.ichibotSockets.get(origin)
					else:
						socket = Processor.get_direct_ichibot_socket(origin)
						self.ichibotSockets[origin] = socket
						client.loop.create_task(self.process_ichibot_messages(origin, message.author))

					await socket.send_multipart([encodedAccountId, encodedExchangeId, b"init"])

					if not isinstance(message.channel, discord.channel.DMChannel):
						embed = discord.Embed(title="Ichibot connection to {} is being initiated.".format(request.get_exchange().name), description="You can start trading by opening your Direct Messages with Alpha Bot. Visit [Ichibot instructions](https://gitlab.com/Ichimikichiki/ichibot-client-app/-/wikis/home) to learn more on how to use it.", color=constants.colors["deep purple"])
						embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
						await message.channel.send(embed=embed)

					embed = discord.Embed(title="Ichibot connection to {} is being initiated.".format(request.get_exchange().name), color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					await message.author.send(embed=embed)

				else:
					embed = discord.Embed(title=":dart: You must have an Alpha Account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), connect your account with your Discord profile, and add an API key.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.icon)
					await message.channel.send(embed=embed)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def process_ichibot_command(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			if requestSlice == "login":
				embed = discord.Embed(title=":dart: API key preferences are available in your Alpha Account settings.", description="[Sign into you Alpha Account](https://www.alphabotsystem.com/sign-in) and visit [Ichibot preferences](https://www.alphabotsystem.com/account/ichibot) to update your API keys.", color=constants.colors["deep purple"])
				embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
				await message.channel.send(embed=embed)
			
			elif messageRequest.is_registered():
				origin = "{}_{}_ichibot".format(messageRequest.accountId, messageRequest.authorId)
				encodedAccountId = bytes(messageRequest.accountId, encoding='utf8')

				if origin in self.ichibotSockets:
					socket = self.ichibotSockets.get(origin)
				else:
					socket = Processor.get_direct_ichibot_socket(origin)
					self.ichibotSockets[origin] = socket
					client.loop.create_task(self.process_ichibot_messages(origin, message.author))

				await socket.send_multipart([encodedAccountId, b"", b"ping"])
				_, exchangeId = await socket.recv_multipart()

				if exchangeId == b"":
					embed = discord.Embed(title="Which exchange would you like to connect to?", description="Ichibot is available on FTX, Binance and Binance Futures.", color=constants.colors["pink"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					missingExchangeMessage = await message.channel.send(embed=embed)
					self.lockedUsers.add(messageRequest.authorId)

					def setup_connection(m):
						if m.author.id == messageRequest.authorId:
							return True

					try:
						m = await client.wait_for('message', timeout=60.0, check=setup_connection)
					except:
						self.lockedUsers.discard(messageRequest.authorId)
						embed = discord.Embed(title="Ichibot connection has been canceled.", description="~~Which exchange would you like to connect to?~~", color=constants.colors["gray"])
						embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
						try: await missingExchangeMessage.edit(embed=embed)
						except: pass
						return (sentMessages, len(sentMessages))
					else:
						self.lockedUsers.discard(messageRequest.authorId)
						sentMessages2, responses = await self.initiate_ichibot(message, messageRequest, "ichibot " + ' '.join(m.clean_content.lower().split()))
						if responses != 0: return (sentMessages + sentMessages2, len(sentMessages) + responses)

				await socket.send_multipart([encodedAccountId, b"", bytes(messageRequest.raw.split(" ", 1)[1], encoding='utf8')])
				try: await message.add_reaction("✅")
				except: pass

				if requestSlice in ["q", "quit", "exit", "logout"]:
					self.ichibotSockets.pop(origin)
					embed = discord.Embed(title="Ichibot connection has been closed.", color=constants.colors["deep purple"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					await message.channel.send(embed=embed)

					embed = discord.Embed(title="Would you like to erase your saved API keys?", description="Erasing all saved API keys cannot be undone. You'll have to re-add the keys, if you want to continue trading with Ichibot.", color=constants.colors["pink"])
					embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
					missingExchangeMessage = await message.channel.send(embed=embed)
					self.lockedUsers.add(messageRequest.authorId)

					def api_key_deletion_prompt(m):
						if m.author.id == messageRequest.authorId:
							response = ' '.join(m.clean_content.lower().split())
							if response in ["y", "yes", "sure", "confirm", "execute"]: return True
							raise Exception()

					try:
						await client.wait_for('message', timeout=60.0, check=api_key_deletion_prompt)
					except:
						self.lockedUsers.discard(messageRequest.authorId)
						embed = discord.Embed(title="Prompt has been canceled.", description="~~Would you like to erase your API keys?~~", color=constants.colors["gray"])
						embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
						try: await missingExchangeMessage.edit(embed=embed)
						except: pass
					else:
						self.lockedUsers.discard(messageRequest.authorId)
						database.document("accounts/{}".format(messageRequest.accountId)).set({"apiKeys": {}}, merge=True)
						embed = discord.Embed(title="Your API keys have been erased.", color=constants.colors["deep purple"])
						embed.set_author(name="Ichibot", icon_url=static_storage.ichibot)
						await message.channel.send(embed=embed)

			else:
				embed = discord.Embed(title=":dart: You must have an Alpha Account connected to your Discord to execute live trades.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), connect your account with your Discord profile, and add an API key.", color=constants.colors["deep purple"])
				embed.set_author(name="Ichibot", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))


	# -------------------------
	# Paper Trading
	# -------------------------

	async def fetch_paper_leaderboard(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			async with message.channel.typing():
				paperTraders = await client.loop.run_in_executor(None, database.collection("accounts").where("paperTrader.globalLastReset", "!=", 0).get)
				topBalances = []

				for account in paperTraders:
					properties = account.to_dict()
					balance = properties["paperTrader"]["balance"]
					totalValue = balance.get("USD", 1000)

					for platform, balances in balance.items():
						if platform == "USD": continue
						for asset, holding in balances.items():
							if holding == 0: continue
							payload, quoteText = await Processor.process_conversion(messageRequest, asset, "USD", holding)
							totalValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0

					paperOrders = await client.loop.run_in_executor(None, database.collection("details/openPaperOrders/{}".format(account.id)).get)
					for element in paperOrders:
						order = element.to_dict()
						if order["orderType"] in ["buy", "sell"]:
							paperRequest = pickle.loads(zlib.decompress(order["request"]))
							ticker = paperRequest.get_ticker()
							payload, quoteText = await Processor.process_conversion(messageRequest, ticker.quote if order["orderType"] == "buy" else ticker.base, "USD", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1))
							totalValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0

					topBalances.append((totalValue, properties["paperTrader"]["globalLastReset"], properties["oauth"]["discord"]["userId"]))

				topBalances.sort(reverse=True)

				embed = discord.Embed(title="Paper trading leaderboard:", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

				for index, (balance, lastReset, authorId) in enumerate(topBalances[:10]):
					embed.add_field(name="#{}: <@!{}> with {} USD".format(index + 1, authorId, balance), value="Since {}".format(Utils.timestamp_to_date(lastReset)), inline=False)

				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def fetch_paper_balance(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			if not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				paperOrders = await client.loop.run_in_executor(None, database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)).get)
				paperBalances = messageRequest.accountProperties["paperTrader"].get("balance", {})

				embed = discord.Embed(title="Paper balance:", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

				holdingAssets = set()
				totalValue = 0

				for platform, balances in paperBalances.items():
					if platform == "USD": continue
					for asset, holding in balances.items():
						if holding == 0: continue
						if platform == "CCXT":
							ticker, _ = TickerParser.find_coingecko_crypto_market(Ticker(asset))
						else:
							ticker, _ = TickerParser.find_iexc_market(Ticker(asset), None)

						balanceText = ""
						valueText = "No conversion"

						balanceText = "{:,.4f} {}".format(holding, asset)
						payload, quoteText = await Processor.process_conversion(messageRequest, asset, "USD", holding)
						convertedValue = payload["raw"]["quotePrice"][0] if quoteText is None else 0
						valueText = "≈ {:,.4f} {}".format(convertedValue, "USD")
						totalValue += convertedValue

						embed.add_field(name=balanceText, value=valueText, inline=True)
						holdingAssets.add(platform + "_" +  asset)

				usdBalance = paperBalances.get("USD", 1000)
				balanceText = "{:,.4f} USD".format(usdBalance)
				totalValue += usdBalance
				embed.add_field(name=balanceText, value="Stable in fiat value", inline=True)
				if usdBalance != 0:
					holdingAssets.add("USD")

				lastResetTimestamp = messageRequest.accountProperties["paperTrader"]["globalLastReset"]
				resetCount = messageRequest.accountProperties["paperTrader"]["globalResetCount"]

				openOrdersValue = 0
				for element in paperOrders:
					order = element.to_dict()
					if order["orderType"] in ["buy", "sell"]:
						paperRequest = pickle.loads(zlib.decompress(order["request"]))
						ticker = paperRequest.get_ticker()
						payload, quoteText = await Processor.process_conversion(messageRequest, ticker.quote if order["orderType"] == "buy" else ticker.base, "USD", order["amount"] * (order["price"] if order["orderType"] == "buy" else 1))
						openOrdersValue += payload["raw"]["quotePrice"][0] if quoteText is None else 0
						holdingAssets.add(paperRequest.currentPlatform + "_" + ticker.base)

				if openOrdersValue > 0:
					totalValue += openOrdersValue
					valueText = "{:,.4f} USD".format(openOrdersValue)
					embed.add_field(name="Locked up in open orders:", value=valueText, inline=True)

				embed.description = "Holding {} asset{} with estimated total value of {:,.2f} USD and {:+,.2f} % ROI.{}".format(len(holdingAssets), "" if len(holdingAssets) == 1 else "s", totalValue, (totalValue / 1000 - 1) * 100, " Trading since {} with {} balance reset{}.".format(Utils.timestamp_to_date(lastResetTimestamp), resetCount, "" if resetCount == 1 else "s") if resetCount != 0 else "")
				sentMessages.append(await message.channel.send(embed=embed))

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def fetch_paper_orders(self, message, messageRequest, requestSlice, mathod):
		sentMessages = []
		try:
			if not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)
				return (sentMessages, len(sentMessages))

			async with message.channel.typing():
				if mathod == "history":
					paperHistory = await client.loop.run_in_executor(None, database.collection("details/paperOrderHistory/{}".format(messageRequest.accountId)).limit(50).get)
					if len(paperHistory) == 0:
						embed = discord.Embed(title="No paper trading history.", color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))
					else:
						embed = discord.Embed(title="Paper trading history:", color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)

						for element in paperHistory:
							order = element.to_dict()
							paperRequest = pickle.loads(zlib.decompress(order["request"]))
							ticker = paperRequest.get_ticker()

							side = ""
							if order["orderType"] == "buy": side = "Bought"
							elif order["orderType"] == "sell": side = "Sold"
							elif order["orderType"].startswith("stop"): side = "Stop sold"
							embed.add_field(name="{} {} {} at {} {}".format(side, order["amountText"], ticker.base, order["priceText"], ticker.quote), value="{} ● id: {}".format(Utils.timestamp_to_date(order["timestamp"] / 1000), element.id), inline=False)

						sentMessages.append(await message.channel.send(embed=embed))

				else:
					paperOrders = await client.loop.run_in_executor(None, database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)).get)
					if len(paperOrders) == 0:
						embed = discord.Embed(title="No open paper orders.", color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
						sentMessages.append(await message.channel.send(embed=embed))
					else:
						numberOfOrders = len(paperOrders)
						destination = message.channel if numberOfOrders < 10 else message.author
						for i, element in enumerate(paperOrders):
							order = element.to_dict()
							paperRequest = pickle.loads(zlib.decompress(order["request"]))
							ticker = paperRequest.get_ticker()

							quoteText = ticker.quote
							side = order["orderType"].replace("-", " ").capitalize()

							embed = discord.Embed(title="{} {} {} at {} {}".format(side, order["amountText"], ticker.base, order["priceText"], quoteText), color=constants.colors["deep purple"])
							embed.set_footer(text="Paper order {}/{} ● id: {}".format(i + 1, numberOfOrders, element.id))
							orderMessage = await message.channel.send(embed=embed)
							sentMessages.append(orderMessage)
							await orderMessage.add_reaction('❌')

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def process_paper_trade(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			arguments = self.paperTrader.argument_cleanup(requestSlice).split(" ")
			orderType = arguments[0]

			if orderType in ["buy", "sell", "stop-sell"] and 2 <= len(arguments) <= 8:
				outputMessage, request = Processor.process_quote_arguments(messageRequest, arguments[2:], tickerId=arguments[1].upper(), isPaperTrade=True, excluded=["CoinGecko", "Quandl", "LLD"])
				if outputMessage is not None:
					if not messageRequest.is_muted() and messageRequest.is_registered() and outputMessage != "":
						embed = discord.Embed(title=outputMessage, description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/paper-trader).", color=constants.colors["gray"])
						embed.set_author(name="Invalid argument", icon_url=static_storage.icon_bw)
						sentMessages.append(await message.channel.send(embed=embed))
					return (sentMessages, len(sentMessages))
				elif not messageRequest.is_registered():
					embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
					await message.channel.send(embed=embed)
					return (sentMessages, len(sentMessages))

				ticker = request.get_ticker()

				async with message.channel.typing():
					payload, tradeText = await Processor.execute_data_server_request("quote", request)

				if payload is None or "quotePrice" not in payload is None:
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

					confirmationText = "Do you want to place a paper {} order of {} {} at {}?".format(orderType.replace("-", " "), pendingOrder.amountText, ticker.base, pendingOrder.priceText)
					embed = discord.Embed(title=confirmationText, description=pendingOrder.conversionText, color=constants.colors["pink"])
					embed.set_author(name="Paper order confirmation", icon_url=payload["thumbnailUrl"])
					orderConfirmationMessage = await message.channel.send(embed=embed)
					self.lockedUsers.add(messageRequest.authorId)

					def confirm_order(m):
						if m.author.id == messageRequest.authorId:
							response = ' '.join(m.clean_content.lower().split())
							if response in ["y", "yes", "sure", "confirm", "execute"]: return True
							elif response in ["n", "no", "cancel", "discard", "reject"]: raise Exception()

					try:
						await client.wait_for('message', timeout=60.0, check=confirm_order)
					except:
						self.lockedUsers.discard(messageRequest.authorId)
						embed = discord.Embed(title="Paper order has been canceled.", description="~~{}~~".format(confirmationText), color=constants.colors["gray"])
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
							database.document("accounts/{}".format(messageRequest.accountId)).set({"paperTrader": paper}, merge=True)
							if pendingOrder.parameters["parameters"][1]:
								openOrders = await client.loop.run_in_executor(None, database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)).get)
								if len(openOrders) >= 50:
									embed = discord.Embed(title="You can only create up to 50 pending paper trades.", color=constants.colors["gray"])
									embed.set_author(name="Maximum number of open paper orders reached", icon_url=static_storage.icon_bw)
									sentMessages.append(await message.channel.send(embed=embed))
									return (sentMessages, len(sentMessages))
								database.document("details/openPaperOrders/{}/{}".format(messageRequest.accountId, str(uuid.uuid4()))).set(pendingOrder.parameters)
							else:
								database.document("details/paperOrderHistory/{}/{}".format(messageRequest.accountId, str(uuid.uuid4()))).set(pendingOrder.parameters)

						successMessage = "Paper {} order of {} {} at {} was successfully {}.".format(orderType.replace("-", " "), pendingOrder.amountText, request.get_ticker().base, pendingOrder.priceText, "executed" if pendingOrder.parameters["parameters"][0] else "placed")
						embed = discord.Embed(title=successMessage, color=constants.colors["deep purple"])
						embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
						await message.channel.send(embed=embed)
			else:
				embed = discord.Embed(title="Invalid command usage.", description="Detailed guide with examples is available on [our website](https://www.alphabotsystem.com/guide/paper-trader).", color=constants.colors["gray"])
				embed.set_author(name="Invalid usage", icon_url=static_storage.icon_bw)
				await message.channel.send(embed=embed)

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
			await self.unknown_error(message, messageRequest.authorId, report=True)
		return (sentMessages, len(sentMessages))

	async def reset_paper_balance(self, message, messageRequest, requestSlice):
		sentMessages = []
		try:
			if not messageRequest.is_registered():
				embed = discord.Embed(title=":joystick: You must have an Alpha Account connected to your Discord to use Alpha Paper Trader.", description="[Sign up for a free account on our website](https://www.alphabotsystem.com/sign-up). If you already signed up, [sign in](https://www.alphabotsystem.com/sign-in), and connect your account with your Discord profile.", color=constants.colors["deep purple"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				await message.channel.send(embed=embed)

			elif messageRequest.accountProperties["paperTrader"]["globalLastReset"] + 604800 < time.time() or messageRequest.accountProperties["paperTrader"]["globalResetCount"] == 0:
				embed = discord.Embed(title="Do you really want to reset your paper balance? This cannot be undone.", description="Paper balance can only be reset once every seven days. Your last public reset date will be publicly visible.", color=constants.colors["pink"])
				embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon)
				resetBalanceMessage = await message.channel.send(embed=embed)
				self.lockedUsers.add(messageRequest.authorId)

				def confirm_order(m):
					if m.author.id == messageRequest.authorId:
						response = ' '.join(m.clean_content.lower().split())
						if response in ["y", "yes", "sure", "confirm", "execute"]: return True
						elif response in ["n", "no", "cancel", "discard", "reject"]: raise Exception()

				try:
					await client.wait_for('message', timeout=60.0, check=confirm_order)
				except:
					self.lockedUsers.discard(messageRequest.authorId)
					embed = discord.Embed(title="Paper balance reset canceled.", description="~~Do you really want to reset your paper balance? This cannot be undone.~~", color=constants.colors["gray"])
					embed.set_author(name="Alpha Paper Trader", icon_url=static_storage.icon_bw)
					await resetBalanceMessage.edit(embed=embed)
				else:
					self.lockedUsers.discard(messageRequest.authorId)

					def delete_collection(coll_ref, batch_size):
						docs = coll_ref.limit(batch_size).stream()
						deleted = 0

						for doc in docs:
							doc.reference.delete()
							deleted += 1

						if deleted >= batch_size:
							return delete_collection(coll_ref, batch_size)

					async with message.channel.typing():
						await client.loop.run_in_executor(None, delete_collection, database.collection("details/openPaperOrders/{}".format(messageRequest.accountId)), 300)
						await client.loop.run_in_executor(None, delete_collection, database.collection("details/paperOrderHistory/{}".format(messageRequest.accountId)), 300)

					paper = {
						"globalResetCount": messageRequest.accountProperties["paperTrader"]["globalResetCount"] + 1,
						"globalLastReset": int(time.time())
					}
					database.document("accounts/{}".format(messageRequest.accountId)).set({"paperTrader": paper}, merge=True)

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
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception(user=f"{message.author.id}: {message.clean_content}")
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
	try: client.loop.run_until_complete(client.topgg.close())
	except: pass
	try: client.loop.run_until_complete(client.close())
	except: pass
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

	client = Alpha(intents=intents, chunk_guilds_at_startup=False, status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.playing, name="a reboot, brb!"))
	print("[Startup]: object initialization complete")

	while True:
		client.prepare()
		client.loop.create_task(client.job_queue())
		try:
			token = os.environ["DISCORD_PRODUCTION_TOKEN" if os.environ["PRODUCTION_MODE"] else "DISCORD_DEVELOPMENT_TOKEN"]
			client.loop.run_until_complete(client.start(token))
		except (KeyboardInterrupt, SystemExit):
			handle_exit()
			client.loop.close()
			break
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: client.logging.report_exception()
			handle_exit()

		client = Alpha(loop=client.loop, intents=intents, chunk_guilds_at_startup=False, status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.playing, name="a reboot, brb!"))
