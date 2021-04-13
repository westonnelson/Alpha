class Utils(object):
	@staticmethod
	def create_guild_settings(settings):
		settingsTemplate = {
			"addons": {
				"satellites": {
					"enabled": False
				},
				"marketAlerts": {
					"enabled": False
				},
				"commandPresets": {
					"enabled": False
				},
				"flow": {
					"enabled": False
				},
				"statistics": {
					"enabled": False
				}
			},
			"settings": {
				"setup": {
					"completed": False,
					"connection": None,
					"tos": 1.0
				},
				"charts": {
					"defaults": {
						"exchange": None
					}
				},
				"assistant": {
					"enabled": True
				},
				"messageProcessing": {
					"bias": "traditional",
					"autodelete": False
				}
			}
		}

		if settings is None: settings = {}
		Utils.__recursive_fill(settings, settingsTemplate)

		return settings

	@staticmethod
	def __recursive_fill(settings, template):
		for e in template:
			if type(template[e]) is dict:
				if e not in settings:
					settings[e] = template[e].copy()
				else:
					Utils.__recursive_fill(settings[e], template[e])
			elif e not in settings:
				settings[e] = template[e]