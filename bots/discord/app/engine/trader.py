import os
import sys
import time
import uuid
import copy
import base64
import zmq.asyncio
import zlib
import pickle

from TickerParser import TickerParser
from helpers.utils import Utils


class Order(object):
	def __init__(self, parameters, request, priceText, amountText, conversionText):
		self.parameters = parameters
		self.request = request
		self.priceText = priceText
		self.amountText = amountText
		self.conversionText = conversionText

class PaperTrader(object):
	def argument_cleanup(self, raw):
		cleanUp = {
			"buy": ["long"],
			"sell": ["short"],
			"stop-sell": ["sell stop", "short stop", "stop"]
		}
		for e in cleanUp:
			for i in cleanUp[e]:
				raw = raw.replace(i, e)

		raw = raw.replace("@", " @ ").replace("%", " % ").replace(",", ".")
		return " ".join(raw.split())

	def process_trade(self, paper, orderType, request, payload):
		outputTitle = None
		outputMessage = None

		exchange = request.get_exchange()
		ticker = request.get_ticker()
		if ticker.isReversed:
			outputTitle = "Cannot trade on an inverse ticker."
			outputMessage = "Try flipping the base and the quote currency, then try again with an inverse order."
			return outputTitle, outputMessage, paper, None

		isLimitOrder = request.find_parameter_in_list("isLimitOrder", request.get_filters(), default=False)
		isAmountPercent = request.find_parameter_in_list("isAmountPercent", request.get_filters(), default=False)
		isPricePercent = request.find_parameter_in_list("isPricePercent", request.get_filters(), default=False)
		if not isLimitOrder:
			execPrice = payload["raw"]["quotePrice"][-1]
		elif isLimitOrder and len(request.get_numerical_parameters()) != 2:
			outputTitle = "Execution price was not provided."
			outputMessage = "A limit order execution price must be provided."
			return outputTitle, outputMessage, paper, None
		else:
			execPrice = request.get_numerical_parameters()[1]
		execAmount = request.get_numerical_parameters()[0]

		if "balance" not in paper:
			paper["balance"] = {"USD": 1000, "CCXT": {}, "IEXC": {}}
		if ticker.base in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			baseBalance = paper["balance"].get("USD")
		else:
			baseBalance = paper["balance"][request.currentPlatform].get(ticker.base, 0)
		if ticker.quote in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			quoteBalance = paper["balance"].get("USD")
		else:
			quoteBalance = paper["balance"][request.currentPlatform].get(ticker.quote, 0)

		if orderType.endswith("buy"):
			if isPricePercent: execPrice = payload["raw"]["quotePrice"] * (1 - execPrice / 100)
			execAmount = (abs(quoteBalance) / execPrice * (execAmount / 100)) if isAmountPercent else execAmount
		elif orderType.endswith("sell"):
			if isPricePercent: execPrice = payload["raw"]["quotePrice"] * (1 + execPrice / 100)
			execAmount = (baseBalance * (execAmount / 100)) if isAmountPercent else execAmount

		if request.currentPlatform == "CCXT":
			execPriceText = TickerParser.get_formatted_price(exchange.id, ticker.symbol, execPrice)
			execPrice = float(execPriceText.replace(",", ""))
			execAmountText = TickerParser.get_formatted_amount(exchange.id, ticker.symbol, execAmount)
		else:
			execPriceText = "{:,.6f}".format(execPrice)
			execAmountText = "{:,.6f}".format(execAmount)

		baseValue = execAmount
		quoteValue = execAmount * execPrice

		if execAmount == 0:
			outputTitle = "Insuficient paper order size"
			outputMessage = "Cannot execute an order of 0.0 {}.".format(ticker.base)
			return outputTitle, outputMessage, paper, None
		elif (orderType.endswith("sell") and baseValue > baseBalance) or (orderType.endswith("buy") and quoteValue * 0.9999999999 > quoteBalance):
			outputTitle = "Insuficient paper wallet balance"
			outputMessage = "Order size of {} {} exeeds your paper wallet balance of {:,.8f} {}.".format(execAmountText, ticker.base, quoteBalance if orderType.endswith("buy") else baseBalance, ticker.quote if orderType.endswith("buy") else ticker.base)
			return outputTitle, outputMessage, paper, None
		elif (orderType.endswith("buy") and quoteBalance == 0) or (orderType.endswith("sell") and baseBalance == 0):
			outputTitle = "Insuficient paper wallet balance"
			outputMessage = "Your {} balance is empty.".format(ticker.quote if orderType.endswith("buy") else ticker.base)
			return outputTitle, outputMessage, paper, None

		newOrder = {
			"orderType": orderType,
			"request": zlib.compress(pickle.dumps(request, -1)),
			"amount": execAmount,
			"amountText": execAmountText,
			"price": request.get_numerical_parameters()[0] if isPricePercent else execPrice,
			"priceText": execPriceText,
			"timestamp": int(time.time() * 1000),
			"status": "placed",
			"parameters": [isPricePercent, isLimitOrder]
		}
		priceText = "{:,.2f} %".format(request.get_numerical_parameters()[0]) if isPricePercent else "{} {}".format(execPriceText, ticker.quote)
		conversionText = None if isPricePercent else "{} {} ≈ {:,.6f} {}".format(execAmountText, ticker.base, quoteValue, ticker.quote)
		return None, None, paper, Order(newOrder, request, priceText=priceText, conversionText=conversionText, amountText=execAmountText)

	def post_trade(self, paper, orderType, request, payload, pendingOrder):
		ticker = request.get_ticker()
		exchange = request.get_exchange()
		execPrice = pendingOrder.parameters["price"]
		execAmount = pendingOrder.parameters["amount"]
		isLimitOrder = pendingOrder.parameters["parameters"][1]

		base = ticker.base
		quote = ticker.quote
		if base in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			baseBalance = paper["balance"]
			base = "USD"
		else:
			baseBalance = paper["balance"][request.currentPlatform]
		if quote in ["USD", "USDT", "USDC", "DAI", "HUSD", "TUSD", "PAX", "USDK", "USDN", "BUSD", "GUSD", "USDS"]:
			quoteBalance = paper["balance"]
			quote = "USD"
		else:
			quoteBalance = paper["balance"][request.currentPlatform]

		orderFee = execAmount * 0.001

		if orderType == "buy":
			quoteBalance[quote] = quoteBalance[quote] - execPrice * execAmount - orderFee
			if not isLimitOrder:
				baseBalance[base] = baseBalance.get(base, 0) + execAmount
		elif orderType == "sell":
			baseBalance[base] = baseBalance[base] - execAmount
			if not isLimitOrder:
				quoteBalance[quote] = quoteBalance.get(quote, 0) + (execAmount - orderFee) * execPrice

		pendingOrder.parameters["status"] = "placed" if isLimitOrder else "filled"
		return paper
