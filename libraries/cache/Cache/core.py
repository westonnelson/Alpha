from threading import Thread
from time import time, sleep


class Cache(object):
	def __init__(self, ttl=None):
		self.ttl = ttl
		self.__cache = {}
		self.__times = {}
		if ttl is not None:
			Thread(target=self.__loop, daemon=True).start()

	def get(self, value, default=None):
		return self.__cache.get(value, default)

	def set(self, key, value):
		self.__cache[key] = value
		self.__times[key] = time()

	def has(self, key):
		return key in self.__cache

	def pop(self, value, default=None):
		self.__times.pop(value, default)
		return self.__cache.pop(value, default)

	def keys(self):
		return self.__cache.keys()

	def values(self):
		return self.__cache.values()

	def items(self):
		return self.__cache.items()

	def __loop(self):
		while True:
			try:
				sleep(1)
				keysToRemove = []
				for key, value in self.__times.items():
					if value <= time() - self.ttl:
						keysToRemove.append(key)
				for key in keysToRemove:
					self.pop(key)
			except:
				pass