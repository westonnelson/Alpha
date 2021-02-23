import os
import signal
import time
import zmq
import pickle
import traceback
from threading import Thread

from google.cloud import firestore, error_reporting

database = firestore.Client()
LRU_READY = "\x01"


class DatabaseHandler(object):
	serviceStatus = []

	accountProperties = {}
	guildProperties = {}
	accountIdMap = {}

	def __init__(self):
		self.isServiceAvailable = True
		signal.signal(signal.SIGINT, self.exit_gracefully)
		signal.signal(signal.SIGTERM, self.exit_gracefully)

		self.serviceStatus = [False, False, False]

		self.logging = error_reporting.Client()

		self.accountsLink = database.collection("accounts").on_snapshot(self.update_account_properties)
		self.discordPropertiesGuildsLink = database.collection("discord/properties/guilds").on_snapshot(self.update_guild_properties)
		self.discordPropertiesUnregisteredUsersLink = database.collection("discord/properties/users").on_snapshot(self.update_unregistered_users_properties)

	def exit_gracefully(self):
		print("[Startup]: Database handler is exiting")
		self.isServiceAvailable = False

	def queue(self):
		try:
			context = zmq.Context(1)
			frontend = context.socket(zmq.XREP)
			frontend.bind("tcp://*:6900")
			backend = context.socket(zmq.XREQ)
			backend.bind("tcp://*:6969")

			zmq.device(zmq.QUEUE, frontend, backend)
		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
		finally:
			pass
			frontend.close()
			backend.close()
			context.term()

	def run(self, identity):
		context = zmq.Context()
		socket = context.socket(zmq.ROUTER)
		socket.connect("tcp://localhost:6969")

		while self.isServiceAvailable:
			try:
				response = None
				queue, origin, delimeter, service, entityId = socket.recv_multipart()

				if service == b"account_fetch":
					response = self.accountProperties.get(entityId.decode())
				elif service == b"guild_fetch":
					response = self.guildProperties.get(entityId.decode())
				elif service == b"account_keys":
					response = list(self.accountProperties.keys())
				elif service == b"guild_keys":
					response = list(self.guildProperties.keys())
				elif service == b"account_match":
					response = self.accountIdMap.get(entityId.decode())
				elif service == b"account_status":
					response = all(self.serviceStatus[:1])
				elif service == b"guild_status":
					response = all(self.serviceStatus[2:])

			except (KeyboardInterrupt, SystemExit): return
			except Exception:
				print(traceback.format_exc())
				if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()
			finally:
				try: socket.send_multipart([queue, origin, delimeter, pickle.dumps(response, -1)])
				except: pass

		socket.close()

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
					userId = properties["oauth"]["discord"].get("userId")
					if userId is not None:
						if userId in self.accountProperties:
							self.accountProperties.pop(userId)
						self.accountIdMap[userId] = accountId
						self.accountIdMap[accountId] = userId
				else:
					self.accountProperties.pop(accountId)
					self.accountIdMap.pop(self.accountIdMap.get(accountId))
					self.accountIdMap.pop(accountId)
			self.serviceStatus[0] = True

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
					if properties.get("connection") is not None: continue
					self.accountProperties[accountId] = properties
				else:
					self.accountProperties.pop(accountId)
			self.serviceStatus[1] = True

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
				guildId = change.document.id
				if change.type.name in ["ADDED", "MODIFIED"]:
					self.guildProperties[guildId] = change.document.to_dict()
				else:
					self.guildProperties.pop(guildId)
			self.serviceStatus[2] = True

		except Exception:
			print(traceback.format_exc())
			if os.environ["PRODUCTION_MODE"]: self.logging.report_exception()


if __name__ == "__main__":
	os.environ["PRODUCTION_MODE"] = os.environ["PRODUCTION_MODE"] if "PRODUCTION_MODE" in os.environ and os.environ["PRODUCTION_MODE"] else ""
	print("[Startup]: Database handler Server is in startup, running in {} mode.".format("production" if os.environ["PRODUCTION_MODE"] else "development"))
	databaseHandler = DatabaseHandler()

	while not all(databaseHandler.serviceStatus):
		time.sleep(1)
	print("[Startup]: Database handler is ready")

	processingThreads = []
	for i in range(3):
		p = Thread(target=databaseHandler.run, args=(str(i),))
		p.start()
		processingThreads.append(p)
	time.sleep(10)

	print("[Startup]: Database handler is online")
	databaseHandler.queue()
	