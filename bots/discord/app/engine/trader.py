import os
import sys
import time
import uuid
import copy
import base64
import zmq.asyncio
import zlib
import pickle

from helpers.utils import Utils


class Order(object):
	def __init__(self, parameters, request, priceText, amountText, conversionText):
		self.parameters = parameters
		self.request = request
		self.priceText = priceText
		self.amountText = amountText
		self.conversionText = conversionText

class PaperTrader(object):
	startingBalance = {
		"binance": {"USDT": {"amount": 1000}},
		"bitmex": {"BTC": {"amount": 0.1}},
	}
	baseCurrency = {
		"binance": "USDT",
		"bitmex": "BTC"
	}

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
		isLimitOrder = request.find_parameter_in_list("isLimitOrder", request.get_filters(), default=False)
		isAmountPercent = request.find_parameter_in_list("isAmountPercent", request.get_filters(), default=False)
		isPricePercent = request.find_parameter_in_list("isPricePercent", request.get_filters(), default=False)
		reduceOnly = request.find_parameter_in_list("isReduceOnlyMode", request.get_filters(), default=False) or "stop" in orderType
		if not isLimitOrder:
			execPrice = payload["quotePrice"]
		elif isLimitOrder and len(request.get_numerical_parameters()) != 2:
			outputTitle = "Execution price was not provided."
			outputMessage = "A limit order execution price must be provided."
			return outputTitle, outputMessage, paper, None
		else:
			execPrice = request.get_numerical_parameters()[1]
		execAmount = request.get_numerical_parameters()[0]
		inverseOrderType = "sell" if "buy" in orderType else "buy"

		if exchange.id not in paper:
			paper[exchange.id] = {"balance": copy.deepcopy(PaperTrader.startingBalance[exchange.id]), "openOrders": [], "history": []}

		if isLimitOrder and len(paper[exchange.id]["openOrders"]) >= 1000:
			outputTitle = "Too many open paper orders"
			outputMessage = "Only up to 1000 open paper orders are allowed."
			return outputTitle, outputMessage, paper, None

		base = ticker.name if exchange.id in ["bitmex"] else ticker.base
		quote = "BTC" if exchange.id in ["bitmex"] else ticker.quote
		if base not in paper[exchange.id]["balance"]: paper[exchange.id]["balance"][base] = {"entry": 0.0, "amount": 0.0}
		if quote not in paper[exchange.id]["balance"]: paper[exchange.id]["balance"][quote] = {"entry": 0.0, "amount": 0.0}

		baseOrder = paper[exchange.id]["balance"][base]
		quoteOrder = paper[exchange.id]["balance"][quote]

		if orderType.endswith("buy"):
			if isPricePercent: execPrice = payload["quotePrice"] * (1 - execPrice / 100)
			execAmount = ((abs(quoteOrder["amount"]) * execPrice if exchange.id in ["bitmex"] else abs(quoteOrder["amount"]) / execPrice) * (execAmount / 100)) if isAmountPercent else execAmount
		elif orderType.endswith("sell"):
			if isPricePercent: execPrice = payload["quotePrice"] * (1 + execPrice / 100)
			execAmount = ((quoteOrder["amount"] * execPrice if exchange.id in ["bitmex"] else baseOrder["amount"]) * (execAmount / 100)) if isAmountPercent else execAmount
			if exchange.id in ["bitmex"]: execAmount *= -1

		execPriceText = Utils.format_price(exchange.properties, ticker.symbol, execPrice)
		execPrice = float(execPriceText.replace(",", ""))
		execAmountText = Utils.format_amount(exchange.properties, ticker.symbol, execAmount)
		execAmount = float(execAmountText.replace(",", ""))

		baseValue = execAmount
		quoteValue = execAmount * execPrice
		amountPrecision = exchange.properties.markets[ticker.symbol]["precision"]["amount"]
		amountLimits = exchange.properties.markets[ticker.symbol]["limits"]["amount"]

		if execAmount < amountLimits["min"]:
			outputTitle = "Insuficient paper order size"
			outputMessage = ("Order size of {:,.%df} {} is less than the minimum required size of {:,.%df} {}." % (amountPrecision, amountPrecision)).format(execAmount, quote, amountLimits["min"], base)
			return outputTitle, outputMessage, paper, None
		elif execAmount > amountLimits["max"]:
			outputTitle = "Paper order size exeeds maximum allowed order size"
			outputMessage = ("Order size must not exceed {:,.%df} {}." % (amountPrecision)).format(amountLimits["max"], base)
			return outputTitle, outputMessage, paper, None
		elif not reduceOnly and ((orderType.endswith("sell") and baseValue > baseOrder["amount"]) or (orderType.endswith("buy") and quoteValue > quoteOrder["amount"])):
			outputTitle = "Insuficient paper wallet balance"
			outputMessage = "Order size of {} {} exeeds your paper wallet balance of {:,.8f} {}.".format(execAmountText, base, quoteOrder["amount"] if orderType.endswith("buy") else baseOrder["amount"], quote if orderType.endswith("buy") else base)
			return outputTitle, outputMessage, paper, None
		elif (orderType.endswith("buy") and quoteOrder["amount"] == 0) or (orderType.endswith("sell") and baseOrder["amount"] == 0):
			outputTitle = "Insuficient paper wallet balance"
			outputMessage = "Your {} balance is empty.".format(quote if orderType.endswith("buy") else base)
			return outputTitle, outputMessage, paper, None

		newOrder = {
			"id": str(uuid.uuid4()),
			"orderType": orderType,
			"request": zlib.compress(pickle.dumps(request, -1)),
			"amount": execAmount,
			"price": request.get_numerical_parameters()[0] if isPricePercent else execPrice,
			"highest": execPrice,
			"timestamp": int(time.time() * 1000),
			"status": "placed",
			"parameters": [isPricePercent, isLimitOrder, reduceOnly]
		}
		priceText = "{:,.2f} %".format(request.get_numerical_parameters()[0]) if isPricePercent else "{} {}".format(execPriceText, ticker.quote)
		conversionText = None if isPricePercent else "{} {} ≈ {:,.6f} {}".format(execAmountText, ticker.base, quoteValue, ticker.quote)
		return None, None, paper, Order(newOrder, request, priceText=priceText, conversionText=conversionText, amountText=execAmountText)

	def post_trade(self, paper, orderType, request, payload, pendingOrder):
		ticker = request.get_ticker()
		exchange = request.get_exchange()
		execPrice = pendingOrder.parameters["price"]
		execAmount = pendingOrder.parameters["amount"]
		isPricePercent = pendingOrder.parameters["parameters"][0]
		isLimitOrder = pendingOrder.parameters["parameters"][1]
		reduceOnly = pendingOrder.parameters["parameters"][2]

		base = ticker.base
		quote = ticker.quote
		baseOrder = paper[exchange.id]["balance"][base]
		quoteOrder = paper[exchange.id]["balance"][quote]

		if orderType == "buy":
			if reduceOnly: execAmount = min(abs(quoteOrder["amount"]), execPrice * execAmount) / execPrice
			orderFee = execAmount * exchange.properties.markets[ticker.symbol]["maker" if isLimitOrder else "taker"]
			
			quoteOrder["amount"] -= execPrice * execAmount
			if not isLimitOrder:
				baseOrder["amount"] += execAmount - orderFee
		elif orderType == "sell":
			if reduceOnly: execAmount = min(abs(baseOrder["amount"]), execAmount)
			orderFee = execAmount * exchange.properties.markets[ticker.symbol]["maker" if isLimitOrder else "taker"]

			baseOrder["amount"] -= execAmount
			if not isLimitOrder:
				quoteOrder["amount"] += (execAmount - orderFee) * execPrice

		pendingOrder.parameters["status"] = "placed" if isLimitOrder else "filled"
		paper[exchange.id]["openOrders" if isLimitOrder else "history"].append(pendingOrder.parameters)
		return paper
