import os
import json
import random

import google.oauth2.credentials

from engine.connections.assistant.base import AlphaAssistant
from helpers import constants


class Assistant(object):
	def __init__(self):
		assistantCredentials = google.oauth2.credentials.Credentials(token=None, **json.loads(os.environ["GOOGLE_ASSISTANT_OAUTH"]))
		http_request = google.auth.transport.requests.Request()
		assistantCredentials.refresh(http_request)
		self.grpc_channel = google.auth.transport.grpc.secure_authorized_channel(assistantCredentials, http_request, "embeddedassistant.googleapis.com")

	def process_reply(self, raw, rawCaps, hasPermissions):
		command = raw.split(" ", 1)[1]
		if command in ["help", "ping", "pro", "invite", "status", "vote", "referrals", "settings"] or not hasPermissions: return True, command
		response = self.funnyReplies(rawCaps.lower())
		if response is not None: return False, response
		with AlphaAssistant("en-US", "nlc-bot-36685-nlc-bot-9w6rhy", "Alpha", False, self.grpc_channel, 60 * 3 + 5) as assistant:
			try: response, response_html = assistant.assist(text_query=rawCaps)
			except: return False, None

			if response	is not None and response != "":
				if "Here are some things you can ask for:" in response:
					return True, "help"
				elif any(trigger in response for trigger in constants.badPunTrigger):
					with open("app/assets/jokes.json") as json_data:
						return False, "Here's a pun that might make you laugh :smile:\n{}".format(random.choice(json.load(json_data)))
				else:
					for override in constants.messageOverrides:
						for trigger in constants.messageOverrides[override]:
							if trigger.lower() in response.lower():
								return False, override
					return False, " ".join(response.replace("Google Assistant", "Alpha").replace("Google", "Alpha").split())
			else:
				return False, None

	def funnyReplies(self, raw):
		for response in constants.funnyReplies:
			for trigger in constants.funnyReplies[response]:
				if raw == trigger: return response
		return None
