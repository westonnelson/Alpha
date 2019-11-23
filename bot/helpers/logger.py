import os
import sys
import logging
import discord

from bot.helpers.utils import Utils

class Logger(object):
	@staticmethod
	def log(p, m="", post=True, color=0x2C97DE, thread=False):
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
				urls = ["https://discordapp.com/api/webhooks/626870056521039884/gG2qvvNgNLY8YpbHXop3pB28L8wQ5rI6xLYmH4jJv8aFxbJe8Pr-THm1Hx6FzZWXjvGb"]
				if prefix == "Status":
					urls = ["https://discordapp.com/api/webhooks/624986397547298838/J_J_U1ZlN7ERVRwrtajNl-hIB7XPQIdtUDjSX0AKI5tDnCOhYyLDrJsuz6lGjIMVLQp2"]
					color = 0x1ECE6D
				elif prefix == "Exchange":
					color = 0xF2C500
				elif prefix == "Warning":
					color = 0xE87E04
				elif prefix == "Error":
					color = 0xE94B35
				elif prefix == "Fatal error":
					color = 0xEC1561

				for url in urls:
					logWebhook = discord.Webhook.from_url(url, adapter=discord.RequestsWebhookAdapter())
					logEmbed = discord.Embed(description=message, color=color)
					logEmbed.set_footer(text=Utils.get_current_date())
					logWebhook.send(embed=logEmbed, username='Alpha')
			except: pass
