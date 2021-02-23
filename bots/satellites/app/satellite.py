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
from DatabaseConnector import DatabaseConnector

from helpers.utils import Utils


database = firestore.Client()


class Alpha(discord.Client):
	isBotReady = False
	clientId = None
	clientName = None

	timeOffset = 0
	lastPing = 0
	exponentialBakcoff = 0

	guildProperties = DatabaseConnector(mode="guild")

	tickerId = None
	exchange = None
	platform = None
	isFree = False


	def prepare(self):
		"""Prepares all required objects and fetches Alpha settings

		"""

		Processor.clientId = b"discord_satellite"
		self.logging = error_reporting.Client()
		self.timeOffset = randint(0, 30)

		self.priceText = None

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
			tasks = database.collection("dataserver/configuration/satellites").get()
			assignments = {doc.id: doc.to_dict() for doc in tasks}
			
			if self.clientId is None or assignments[self.clientId]["uuid"] != self.clientName:
				for clientId in assignments:
					if currentSelectedId is None or assignments[clientId]["ping"] < assignments[currentSelectedId]["ping"]:
						currentSelectedId = clientId

			if os.environ["PRODUCTION_MODE"] and time.time() > self.lastPing:
				database.document("dataserver/configuration/satellites/{}".format(currentSelectedId)).set({"ping": int(time.time()), "uuid": self.clientName}, merge=True)
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

	async def on_guild_join(self, guild):
		"""Updates quild count on guild_join event and leaves all guilds flagged as banned

		Parameters
		----------
		guild : discord.Guild
			Guild object passed by discord.py
		"""

		try:
			properties = await self.guildProperties.get(guild.id)
			if properties is None:
				return
			elif not self.isFree and not properties["addons"]["satellites"]["enabled"]:
				try: await guild.me.edit(nick="Disabled")
				except: return
			elif self.isFree or properties["addons"]["satellites"]["connection"] is not None:
				try: await guild.me.edit(nick=self.priceText)
				except: return
			else:
				try: await guild.me.edit(nick="Alpha Pro required")
				except: return
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def job_queue(self):
		"""Updates Alpha Bot user status with latest prices

		"""

		while True:
			try:
				await asyncio.sleep(Utils.seconds_until_cycle())
				if not await self.guildProperties.check_status(): continue
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				isPremium = self.tickerId in ["EURUSD", "GBPUSD", "AUDJPY", "AUDUSD", "EURJPY", "GBPJPY", "NZDJPY", "NZDUSD", "CADUSD", "JPYUSD", "ZARUSD"]
				refreshRate = "5m" if len(client.guilds) > 1 and (not isPremium or len(client.guilds) > 15) else "15m"

				if "1m" in timeframes:
					self.get_assigned_id()
				if refreshRate in timeframes:
					await asyncio.sleep(self.timeOffset)

					try: outputMessage, request = Processor.process_quote_arguments(client.user.id, [] if self.exchange is None else [self.exchange], tickerId=self.tickerId, platformQueue=[self.platform])
					except: continue
					if outputMessage is not None:
						print(outputMessage)
						if os.environ["PRODUCTION_MODE"]: self.logging.report(outputMessage)
						continue

					try: payload, quoteText = await Processor.execute_data_server_request("quote", request, timeout=30)
					except: continue
					if payload is None or payload["quotePrice"] is None:
						print("Requested price for `{}` is not available".format(request.get_ticker().name) if quoteText is None else quoteText)
						continue

					self.priceText = "{} {}".format(payload["quotePrice"], payload["quoteTicker"])
					changeText = "" if payload["change"] is None else "{:+.2f} % | ".format(payload["change"])
					tickerText = "{} | ".format(request.get_ticker().id) if request.get_exchange() is None else "{} on {} | ".format(request.get_ticker().id, request.get_exchange().name)
					statusText = "{}{}alphabotsystem.com".format(changeText, tickerText)
					status = discord.Status.online if payload["change"] is None or payload["change"] >= 0 else discord.Status.dnd

					for guild in client.guilds:
						properties = await self.guildProperties.get(guild.id)
						if properties is None:
							continue
						elif not self.isFree and not properties["addons"]["satellites"]["enabled"]:
							try: await guild.me.edit(nick="Disabled")
							except: continue
						elif self.isFree or properties["addons"]["satellites"]["connection"] is not None:
							try: await guild.me.edit(nick=self.priceText)
							except: continue
						else:
							try: await guild.me.edit(nick="Alpha Pro required")
							except: continue

					try: await client.change_presence(status=status, activity=discord.Activity(type=discord.ActivityType.watching, name=statusText))
					except: pass

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