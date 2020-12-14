import os
import sys
from random import randint
import argparse
import time
import datetime
import uuid
import pytz
import urllib
import copy
import atexit
import asyncio
import zlib
import pickle
import concurrent
import traceback

import discord
from google.cloud import firestore, error_reporting

from Processor import Processor

from helpers.utils import Utils


database = firestore.Client()


class Alpha(discord.Client):
	isBotReady = False
	clientId = None
	clientName = None

	timeOffset = 0
	lastPing = 0
	exponentialBakcoff = 0

	guildProperties = {}

	tickerId = None
	exchange = None
	platform = None
	isFree = False

	discordPropertiesGuildsLink = None
	dataserverParserIndexLink = None


	def prepare(self):
		"""Prepares all required objects and fetches Alpha settings

		"""

		Processor.clientId = b"discord_satellite"
		self.executor = concurrent.futures.ThreadPoolExecutor()
		self.logging = error_reporting.Client()
		self.timeOffset = randint(0, 30)

		self.discordPropertiesGuildsLink = database.collection("discord/properties/guilds").where("addons.satellites.enabled", "==", True).on_snapshot(self.update_guild_properties)
		print("[Startup]: database link activated")

		time.sleep(self.timeOffset)
		self.clientName = str(uuid.uuid4())
		self.get_assigned_id()
		print("[Startup]: received task: {}/{}/{}".format(self.platform, self.exchange, self.tickerId))
		print("[Startup]: parser initialization complete")

	async def on_ready(self):
		"""Initiates all Discord dependent functions and flags the bot as ready to process requests

		"""

		self.isBotReady = True

		print("[Startup]: Alpha Satellite is online")

	def get_assigned_id(self):
		try:
			currentSelectedId = self.clientId
			assignments = database.document("dataserver/satellites").get().to_dict()
			
			if self.clientId is None or assignments[self.clientId]["uuid"] != self.clientName:
				for clientId in assignments:
					if currentSelectedId is None or assignments[clientId]["ping"] < assignments[currentSelectedId]["ping"]:
						currentSelectedId = clientId

			if os.environ["PRODUCTION_MODE"] and time.time() > self.lastPing:
				database.document("dataserver/satellites").set({currentSelectedId: {"ping": int(time.time()), "uuid": self.clientName}}, merge=True)
				self.lastPing = time.time() + 1 * 1.1 ** self.exponentialBakcoff
				self.exponentialBakcoff += 1

			if self.clientId is None or not self.isBotReady:
				self.clientId = currentSelectedId
				self.platform, self.exchange, self.tickerId = assignments[self.clientId]["task"]
				self.isFree = self.tickerId in ["BTCUSD", "ETHUSD"] and self.platform == "CoinGecko"
			elif self.clientId != currentSelectedId and os.environ["PRODUCTION_MODE"]:
				self.isBotReady = False
				self.clientId = currentSelectedId
				self.platform, self.exchange, self.tickerId = assignments[self.clientId]["task"]
				self.isFree = self.tickerId in ["BTCUSD", "ETHUSD"] and self.platform == "CoinGecko"
				print("[Shutdown]: Task missmatch, shutting down")
				raise KeyboardInterrupt

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
				self.guildProperties[int(change.document.id)] = change.document.to_dict()
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def job_queue(self):
		"""Updates Alpha Bot user status with latest prices

		"""

		while True:
			try:
				await asyncio.sleep(Utils.seconds_until_cycle())
				if not self.isBotReady: continue
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "1m" in timeframes:
					self.get_assigned_id()
					await asyncio.sleep(self.timeOffset)

					outputMessage, request = Processor.process_quote_arguments(client.user.id, [] if self.exchange is None else [self.exchange], tickerId=self.tickerId, platformQueue=[self.platform])
					if outputMessage is not None:
						print(outputMessage)
						if os.environ["PRODUCTION_MODE"]: self.logging.report(outputMessage)
						continue

					payload, quoteText = await Processor.execute_data_server_request("quote", request, timeout=10)
					if payload is None or payload["quotePrice"] is None:
						print("Requested price for `{}` is not available".format(request.get_ticker().name) if quoteText is None else quoteText)
						continue

					priceText = "{} {}".format(payload["quotePrice"], payload["quoteTicker"])
					if request.get_exchange() is not None:
						statusText = "{:+.2f} % | {} | alphabotsystem.com".format(payload["change"], request.get_exchange().name)
					else:
						statusText = "{:+.2f} % | alphabotsystem.com".format(payload["change"])

					for guild in client.guilds:
						if not self.isFree and (guild.id not in self.guildProperties or not self.guildProperties[guild.id]["addons"]["satellites"]["enabled"]):
							try: await guild.me.edit(nick="Disabled")
							except: continue
						elif self.isFree or (guild.id in self.guildProperties and self.guildProperties[guild.id]["addons"]["satellites"]["connection"] is not None):
							try: await guild.me.edit(nick=priceText)
							except: continue
						else:
							try: await guild.me.edit(nick="Alpha Pro required")
							except: continue

					await client.change_presence(status=(discord.Status.online if payload["change"] >= 0 else discord.Status.dnd), activity=discord.Activity(type=discord.ActivityType.watching, name=statusText))

			except asyncio.CancelledError: return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


# -------------------------
# Initialization
# -------------------------

def handle_exit():
	print("\n[Shutdown]: closing tasks")
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
	print("[Startup]: Alpha Satellite is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))

	intents = discord.Intents.none()
	intents.guilds = True

	client = Alpha(intents=intents, status=discord.Status.idle, activity=None)
	print("[Startup]: object initialization complete")
	client.prepare()

	while True:
		client.loop.create_task(client.job_queue())
		try:
			token = os.environ["ID_{}".format(client.clientId)]
			client.loop.run_until_complete(client.start(token))
		except (KeyboardInterrupt, SystemExit):
			handle_exit()
			client.loop.close()
			break
		except:
			handle_exit()

		client = Alpha(loop=client.loop, intents=intents, status=discord.Status.idle, activity=None)