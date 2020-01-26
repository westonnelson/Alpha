import os
import sys
import logging
import discord

from bot.helpers.utils import Utils
from bot.keys.f802e1fba977727845e8872c1743a714 import Keys as ApiKeys

class Logger(object):
	@staticmethod
	def log(p, m="", post=True, color=0x2196F3, thread=False):
		if thread:
			t = threading.Thread(target=Logger.push_log_message, args=(p, m, post, color,))
			t.start()
		else:
			Logger.push_log_message(p, m, post, color)

	@staticmethod
	def push_log_message(p, m, post, color):
		message = m if m != "" else p
		prefix = p if m != "" else "Log"

		logger = logging.getLogger("Logger")
		logger.setLevel(logging.DEBUG)

		fh = logging.FileHandler('output.log', mode='a')
		fh.setLevel(logging.DEBUG)
		logger.addHandler(fh)

		ch = logging.StreamHandler(stream=sys.stdout)
		ch.setLevel(logging.INFO)
		logger.addHandler(ch)

		logger.info("[{}]: {}".format(prefix, message))

		logger.handlers = []

		if sys.platform == "linux" and post:
			try:
				url = ApiKeys.get_log_webhook()
				if prefix == "Info" or prefix == "Alerts":
					url = ApiKeys.get_log_webhook(mode="quiet")
				elif prefix == "Status":
					color = 0x03A9F4
				elif prefix == "Exchange":
					color = 0xFFC107
				elif prefix == "Warning":
					color = 0xFF9800
				elif prefix == "Error":
					color = 0xFF5722
				elif prefix == "Fatal error":
					color = 0xF44336

				logWebhook = discord.Webhook.from_url(url, adapter=discord.RequestsWebhookAdapter())
				logEmbed = discord.Embed(description=message, color=color)
				logEmbed.set_footer(text=Utils.get_current_date())
				logWebhook.send(embed=logEmbed, username='Alpha')
			except: pass
