import os
import sys
import logging
import discord

from bot.helpers.utils import Utils

logger = logging.getLogger("Logger")

class Logger(object):
	@staticmethod
	def log(p, m="", post=True):
		message = m if m != "" else p
		prefix = p if m != "" else "Log"

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
				color = 0x2C97DE
				if prefix == "Status":
					color = 0x1ECE6D
				elif prefix == "Exchange":
					color = 0xF2C500
				elif prefix == "Warning":
					color = 0xE87E04
				elif prefix == "Error":
					color = 0xE94B35
				elif prefix == "Fatal error":
					color = 0xEC1561
				logWebhook = discord.Webhook.from_url("https://discordapp.com/api/webhooks/563389936204644377/pKf9zLSoAiHJgMEUkoDTRGVv-9xQxcMqJdjTsYm0HG1nsOS_zNhZifuSo3rOP4Au0t2x", adapter=discord.RequestsWebhookAdapter())
				logEmbed = discord.Embed(description=message, color=color)
				logEmbed.set_footer(text=Utils.get_current_date())
				logWebhook.send(embed=logEmbed, username='Alpha')
			except:
				pass
