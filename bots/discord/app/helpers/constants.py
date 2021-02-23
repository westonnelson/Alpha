commandWakephrases = ["alpha ", "alert ", "preset ", "c ", "flow ", "hmap ", "d ", "alerts ", "p ", "v ", "convert ", "m ", "info ", "t ", "top ", "mcap ", "mc ", "mk ", "n ", "x ", "paper "]
commandKeywords = ["alpha", "alert", "preset", "c", "flow", "hmap", "d", "alerts", "p", "v", "convert", "m", "info", "t", "top", "mcap", "mc", "mk", "n", "x", "paper"]

colors = {
	"red": 0xF44336,
	"pink": 0xE91E63,
	"purple": 0x9C27B0,
	"deep purple": 0x673AB7,
	"indigo": 0x3F51B5,
	"blue": 0x2196F3,
	"light blue": 0x03A9F4,
	"cyan": 0x00BCD4,
	"teal": 0x009688,
	"green": 0x4CAF50,
	"light green": 0x8BC34A,
	"lime": 0xCDDC39,
	"yellow": 0xFFEB3B,
	"amber": 0xFFC107,
	"orange": 0xFF9800,
	"deep orange": 0xFF5722,
	"brown": 0x795548,
	"gray": 0x9E9E9E
}

supportMessages = {
	"crypto": {
		# "preset": [[":bulb: Get access to Command Presets for as little as $1.00 with Alpha Pro.", "Learn more about Alpha Pro and how to start your free trial on [our website](https://www.alphabotsystem.com/pro)."]],
		# "alerts": [[":bulb: You can set price alerts right through Alpha with Alpha Pro. Setting one is as easy as running `alert set btc 24000` to be notified when Bitcoin price hits $24000.", "Learn more on [our website](https://www.alphabotsystem.com/pro/price-alerts)."]],
		# "noads": [[":bulb: Servers can now request to remove Alpha's ads.", "Learn more about exact pricing on [our website](https://www.alphabotsystem.com/pro/pricing). Community admins can visit their [Communities Dashboard](https://www.alphabotsystem.com/communities) and remove ads in community settings."]],
		"referral": [["Get a discount on crypto trading fees by signing up with our referral links!", "If you like Alpha Bot and would like to support it for free, sign up with on [Binance and get 10% back](https://www.binance.com/en/register?ref=PJF2KLMW), [BitMEX and get 10% back for 6 months](https://www.bitmex.com/register/cv1ZSO), [FTX and get 5% back](https://ftx.com/#a=Alpha), or [Deribit and get 10% back for 6 months](https://www.deribit.com/reg-8980.6502)."]]
	},
	"traditional": {
		"preset": [[":bulb: Get access to Command Presets for as little as $1.00 with Alpha Pro.", "Learn more about Alpha Pro and how to start your free trial on [our website](https://www.alphabotsystem.com/pro)."]],
		"flow": [[":bulb: Get access to BlackBox Stocks order flow data via Alpha Flow for as little as $15.00 with Alpha Pro.", "Learn more about Alpha Pro and how to start your free trial on [our website](https://www.alphabotsystem.com/pro)."]],
		"alerts": [[":bulb: You can set price alerts right through Alpha with Alpha Pro. Setting one is as easy as running `alert set aapl 125` to be notified when Apple stock price hits $125.", "Learn more on [our website](https://www.alphabotsystem.com/pro/price-alerts)."]],
		"noads": [[":bulb: Servers can now request to remove Alpha's ads.", "Learn more about exact pricing on [our website](https://www.alphabotsystem.com/pro/pricing). Community admins can visit their [Communities Dashboard](https://www.alphabotsystem.com/communities) and remove ads in community settings."]],
	}
}
frequency = {
	"crypto": 40,
	"traditional": 20
}

messageOverrides = {
	"I can't help you with that.": {
		"Your public IP address is:",
		"Your shopping list",
		"your shopping list",
		"What do you want to add?",
		"What's the reminder?",
		"Okay, make a reminder. When do you want to be reminded?",
		"Sorry, I can't set reminders yet.",
		"When do you want to be reminded?",
		"It looks like I need permission",
		"I'll need your permission",
		"Your Assistant needs permission to help you with that."
	},
	"At first I was just an idea, then MacoAlgo#9999 and the team put their heads together. And now here I am :blush:": {
		"I was made by a team of people at Google",
		"I was made by a team at Google",
		"At first I was just an idea, then a bunch of people at Google put their heads together. And now here I am",
		"The Google team is like my family, they mean a lot to me",
		"Everyone at Google is sort of like my family"
	},
	"Earning and maintaining your trust is a priority at Alpha Bot System. You can learn more about Alpha's principles and practices at https://www.alphabotsystem.com/privacy-policy.": {
		"Earning and maintaining your trust is a priority at Google. Google protects your data and ensures you are in control. You can learn more about Google's principles and practices at safety.google.com. To see and manage your account information, visit myaccount.google.com. That's myaccount.google.com."
	}
}
funnyReplies = {
	"No u": [
		"fuck you alpha",
		"alpha fuck you",
		"fuck off alpha",
		"alpha fuck off",
		"fuck u alpha",
		"alpha fuck u",
		"alpha you slut",
		"you slut alpha",
		"alpha gay",
		"gay alpha",
	],
	"Soon:tm:": [
		"wen moon",
		"when moon"
	],
	"U a retard https://www.youtube.com/watch?v=e-6eWEhjMa4": [
		"who a retard",
	],
	"Happy to help": [
		"thank you Alpha"
	]
}
badPunTrigger = [
	"Lucky Trivia",
	"We can play some games",
	"We can play a game",
	"We can play trivia",
	"Ask me to play a game",
	"You can play a game",
	"I've got this"
	"I've been waiting for this moment",
	"Bored? Not while I'm around",
	"Do you want to play?",
	"Boredom doesn't stand a chance against interesting facts",
	"Interesting facts are the perfect boredom remedy",
	"I can fix that with a fun fact",
	"Let's have some fun",
]

# Users
blockedUsers = {
	211986377171140609, 464581380467064832, 195802900797194245
}

# Servers
bannedGuilds = {
	468854180048666625, 577324120371494913, 520669492242677780, 571511712629653514, 632275906303361024, 538361750651797504, 602875011157721099, 725498973267296387
}
blockedGuilds = {
	264445053596991498, 446425626988249089
}

satellites = {
	709850457467650138: ["CCXT", "bitmex", "BTCUSD"],
	709853039120351284: ["CCXT", "bitmex", "ETHUSD"],
	709891252530970711: ["CCXT", "bitmex", "XRPUSD"],
	738420458252271629: ["CCXT", "bitmex", "BCHUSD"],
	738420614574112868: ["CCXT", "bitmex", "LTCUSD"],
	738429177413500960: ["CCXT", "binance", "BTCUSD"],
	738429689810518036: ["CCXT", "binance", "ETHUSD"],
	739085555186532384: ["CCXT", "binance", "XRPUSD"],
	739107100126478337: ["CCXT", "binance", "BCHUSD"],
	739107866111377429: ["CCXT", "binance", "LTCUSD"],
	743453643004575774: ["CCXT", "binance", "LINKUSD"],
	743461822467932291: ["CCXT", "binance", "ADAUSD"],
	743456887692984422: ["CCXT", "binance", "BNBUSD"],
	743463011628613662: ["CCXT", "binance", "XTZUSD"],
	739108704170803283: ["CCXT", "binance", "EOSUSD"],
	739413924868522086: ["CCXT", "binance", "XLMUSD"],
	743440072522727514: ["CCXT", "huobipro", "BTCUSD"],
	743432577553006664: ["CCXT", "ftx", "BTCUSD"],
	745364520280522894: ["CCXT", "ftx", "BTCMOVE"],
	743433528964022383: ["CCXT", "ftx", "ETHUSD"],
	745319169775632404: ["CCXT", "ftx", "SRMUSD"],
	745379499570495629: ["CCXT", "ftx", "COMPUSD"],
	745395371924127825: ["CCXT", "ftx", "YFIUSD"],
	# 751080162300526653: ["CoinGecko", "", "BTCUSD"],
	# 751080770243657779: ["CoinGecko", "", "ETHUSD"],
	751081142580412546: ["CoinGecko", "", "DOTUSD"],
	752207000728895590: ["CoinGecko", "", "CROUSD"],
	751081514178969670: ["CoinGecko", "", "HBARUSD"],
	751081914252918815: ["CoinGecko", "", "ZILUSD"],
	753250930022940702: ["CoinGecko", "", "VETUSD"],
	751085283264692264: ["CoinGecko", "", "SHAUSD"],
	751085756914728980: ["CoinGecko", "", "OCEUSD"],
	774377137515134996: ["CoinGecko", "", "XJPUSD"],
	710075001403080714: ["IEXC", "", "AAPL"],
	710074695495712788: ["IEXC", "", "TSLA"],
	710074767784280127: ["IEXC", "", "AMD"],
	710074859815698553: ["IEXC", "", "NVDA"],
	710074952153301022: ["IEXC", "", "MSFT"],
	710075054356037663: ["IEXC", "", "AMZN"],
	751488841822634116: ["IEXC", "", "GOOGL"],
	751489005018677351: ["IEXC", "", "NFLX"],
	786986778430799872: ["IEXC", "", "NIO"],
	786988356852383794: ["IEXC", "", "PLTR"],
	787004889019580436: ["IEXC", "", "AQB"],
	787707381781626940: ["IEXC", "", "EH"],
	787714760074199080: ["IEXC", "", "ROKU"],
	787715515518156811: ["IEXC", "", "FSLY"],
	788087979671289866: ["IEXC", "", "FB"],
	799586219285282816: ["IEXC", "", "ALPP"],
	799586320170483713: ["IEXC", "", "COUV"],
	799586397979410432: ["IEXC", "", "GAXY"],
	799586470212927508: ["IEXC", "", "DSGT"],
	799586549187870720: ["IEXC", "", "ARBKF"],
	799586605106331719: ["IEXC", "", "ABML"],
	799600754235015240: ["CoinGecko", "", "RENUSD"],
	799600802322186281: ["CoinGecko", "", "UNIUSD"],
	799600878470299659: ["coinGecko", "", "LRCUSD"],
	800307532366741504: ["IEXC", "", "EURUSD"],
	800307918242709504: ["IEXC", "", "GBPUSD"],
	802500457142157312: ["CoinGecko", "", "AAVEUSD"],
	802649161928540200: ["IEXC", "", "AUDJPY"],
	802649221260902451: ["IEXC", "", "AUDUSD"],
	802649315100721192: ["IEXC", "", "EURJPY"],
	802649384193097779: ["IEXC", "", "GBPJPY"],
	802649469488201748: ["IEXC", "", "NZDJPY"],
	802649532705013801: ["IEXC", "", "NZDUSD"],
	802649628716564501: ["IEXC", "", "CADUSD"],
	802649693862887466: ["IEXC", "", "JPYUSD"],
	802649746791596062: ["IEXC", "", "ZARUSD"],
	802860366052458516: ["CoinGecko", "", "CRVUSD"],
	805157874887819325: ["IEXC", "", "GME"],
	809728857573163059: ["IEXC", "", "BOTY"],
	809728957293002752: ["CCXT", "binance", "DOGEUSD"],
	809729046661431306: ["IEXC", "", "AMC"],
	809729141112700968: ["IEXC", "", "NOK"],
	811912820902854687: ["CoinGecko", "", "RDDUSD"],
}