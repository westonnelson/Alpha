import os
import sys
import time
import datetime
import pytz
import argparse
import asyncio
import traceback

import discord
from google.cloud import firestore, error_reporting

from helpers.utils import Utils


database = firestore.Client()


class Alpha(discord.Client):
	botStatus = []

	accountProperties = {}

	accountsLink = None
	tradingViewAccessLink = None
	pendingAccess = {}
	accessMap = {}

	def prepare(self):
		"""Prepares all required objects and fetches Alpha settings

		"""

		self.botStatus = [False, False]

		self.logging = error_reporting.Client()
		self.accountsLink = database.collection("accounts").where("oauth.discord.userId", ">", "").on_snapshot(self.update_account_properties)

	async def on_ready(self):
		"""Initiates all Discord dependent functions and flags the bot as ready to process requests

		"""

		self.alphaGuild = client.get_guild(414498292655980583)
		self.proRoles = [
			discord.utils.get(self.alphaGuild.roles, id=484387309303758848), # Alpha Pro role
			discord.utils.get(self.alphaGuild.roles, id=647824289923334155)  # Registered Alpha Account role
		]

		self.botStatus[0] = True
		while not self.is_bot_ready():
			await asyncio.sleep(1)
		print("[Startup]: Alpha Manager is online")

	def is_bot_ready(self):
		return all(self.botStatus)

	async def on_member_join(self, member):
		"""Scanns each member joining into Alpha community guild for spam

		Parameters
		----------
		guild : discord.Member
			Member object passed by discord.py
		"""

		try:
			if member.id in self.accountProperties: await self.update_alpha_guild_roles()
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

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
				else:
					self.accountProperties.pop(accountId, None)
			self.botStatus[1] = True

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def update_alpha_guild_roles(self):
		"""Updates Alpha community guild roles

		"""

		try:
			for member in self.alphaGuild.members:
				isDicscordConnected = False
				try:
					for accountId in self.accountProperties.keys():
						if str(member.id) == self.accountProperties[accountId]["oauth"]["discord"]["userId"]:
							isDicscordConnected = True
							break
				except: pass

				if isDicscordConnected:
					if self.proRoles[1] not in member.roles:
						await member.add_roles(self.proRoles[1])

					if self.accountProperties[accountId]["customer"]["personalSubscription"].get("plan", "free") != "free":
						if self.proRoles[0] not in member.roles:
							await member.add_roles(self.proRoles[0])
					elif self.proRoles[0] in member.roles:
						await member.remove_roles(self.proRoles[0])

				elif self.proRoles[0] in member.roles or self.proRoles[1] in member.roles:
					await member.remove_roles(self.proRoles[0], self.proRoles[1])

		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

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

				if "1m" in timeframes:
					await self.update_alpha_guild_roles()
			except asyncio.CancelledError: return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


# -------------------------
# Initialization
# -------------------------

def handle_exit():
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
	print("[Startup]: Alpha Manager is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))

	intents = discord.Intents.none()
	intents.guilds = True
	intents.members = True

	client = Alpha(intents=intents, status=discord.Status.invisible, activity=None)
	print("[Startup]: object initialization complete")
	client.prepare()

	while True:
		client.loop.create_task(client.job_queue())
		try:
			token = os.environ["DISCORD_MANAGER_TOKEN"]
			client.loop.run_until_complete(client.start(token))
		except (KeyboardInterrupt, SystemExit):
			handle_exit()
			client.loop.close()
			break
		except:
			handle_exit()

		client = Alpha(loop=client.loop, status=discord.Status.invisible)