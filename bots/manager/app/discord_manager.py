import os
import sys
import time
import datetime
import pytz
import argparse
import asyncio
import traceback

import discord
from google.cloud import error_reporting

from DatabaseConnector import DatabaseConnector

from helpers.utils import Utils


class Alpha(discord.Client):
	accountProperties = DatabaseConnector(mode="account")

	def prepare(self):
		"""Prepares all required objects and fetches Alpha settings

		"""

		self.logging = error_reporting.Client()

	async def on_ready(self):
		"""Initiates all Discord dependent functions and flags the bot as ready to process requests

		"""

		self.alphaGuild = client.get_guild(414498292655980583)
		self.proRoles = [
			discord.utils.get(self.alphaGuild.roles, id=484387309303758848), # Alpha Pro role
			discord.utils.get(self.alphaGuild.roles, id=647824289923334155)  # Registered Alpha Account role
		]

		print("[Startup]: Alpha Manager is online")

	async def on_member_join(self, member):
		"""Scanns each member joining into Alpha community guild for spam

		Parameters
		----------
		guild : discord.Member
			Member object passed by discord.py
		"""

		try:
			await self.update_alpha_guild_roles(only=member.id)
		except asyncio.CancelledError: pass
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()

	async def update_alpha_guild_roles(self, only=None):
		"""Updates Alpha community guild roles

		"""

		try:
			if not await self.accountProperties.check_status(): return

			for member in self.alphaGuild.members:
				if only is not None and only != member.id: continue

				await asyncio.sleep(0.1)
				accountId = await self.accountProperties.match(member.id)

				if accountId is not None:
					properties = await self.accountProperties.get(accountId)
					
					if self.proRoles[1] not in member.roles:
						await member.add_roles(self.proRoles[1])

					if properties["customer"]["personalSubscription"].get("plan", "free") != "free":
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
				t = datetime.datetime.now().astimezone(pytz.utc)
				timeframes = Utils.get_accepted_timeframes(t)

				if "5m" in timeframes:
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