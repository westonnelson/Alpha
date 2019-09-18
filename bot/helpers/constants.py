premiumMessage = "__**A L P H A   P R E M I U M**__\n\nWith Alpha premium you'll get access to price alerts, command presets, message forwarding service (currently not available), a dedicated VPS (for servers only), as well as increased rate limits.\n\n**Price alerts (beta)**\nWith price alerts, you can get instant notifications through Discord whenever the price of supported coins crosses a certain level or when popular indicators cross a specific value.\n\n**Command presets**\nPresets allow you to quickly call commands you use most often, create indicator sets and more.\n\n**Dedicated VPS (for servers only)**\nA dedicated virtual private server will deliver cutting edge performace of Alpha in your discord server even during high load.\n\n**Other perks**\nThe package also comes with raised limits. Instead of 10 charts, you can request up to 30 charts per minute.\n\nAll users and servers are eligible for one month free trial. Join our server to learn more: https://discord.gg/H9sS6WK"
termsOfService = "__**A L P H A   T E R M S   O F   S E R V I C E**__\n\nBy using Alpha bot, you agree to these terms. Inability to comply will result in Alpha stopping to respond to users requests. Rules are enforced by an automated system. Clever ways to trick the flagging system will result in an instant server ban.\n\n1. Deliberate attempts to break the bot will not be tolerated.\n\n2. The bot should be accessible to all users in a server. Charging for it is forbidden.\n\n3. Alpha bot should not be rebranded or renamed to your server's name. This also includes adding prefixes or suffixes. Neutral nicknames are allowed.\n\n4. All messages flowing through the bot are used to collect anonymous statistical information.\n\n5. We are not responsible for any content provided by Alpha."

rulesAndTOS = "__**D I S C L A I M E R**__\n\nNone of the of the information provided here is considered financial advice. Please seek financial guidance before considering making trades in this market, each country is subject to various laws and tax implications.\n\n\n__**S E R V E R   R U L E S**__\n\n1. Be respectful to each others thoughts and opinions. We will not tolerate any insults, racism, harassment or threats of violence against members, even as a joke.\n\n2. We are free from all forms of advertising. Do not post referral links, discord invitations or other invitations of any kind.\n\n3. Do not post any NSFW content and limit the use of offensive language across all channels and nicknames.\n\n4. Never disclose identifiable information about yourself, including but not limited to - account balances/holdings, location, email, phone number etc.\n\n5. No user should ever ask you to send them money.\n\n6. It is an offense to impersonate members. Anyone caught doing this will be instantly banned from this server and from using the bot.\n\n\n__**T E R M S   O F   S E R V I C E**__\n\nBy using Alpha bot, you agree to these terms. Inability to comply will result in Alpha stopping to respond to users requests. Rules are enforced by an automated system. Clever ways to trick the flagging system will result in an instant server ban.\n\n1. Deliberate attempts to break the bot will not be tolerated.\n\n2. The bot should be accessible to all users in a server. Charging for it is forbidden.\n\n3. Alpha bot should not be rebranded or renamed to your server's name. This includes adding prefixes or suffixes, while neutral nicknames are allowed.\n\n4. Alpha has read access in all channels it is added. All messages flowing through the bot are processed by the bot.\n\n5. We are not responsible for any content provided by Alpha."
faq1 = "__**G E N E R A L**__\n\n**Can you add other indicators or timeframes to the bot?**\nUnfortunately no, TradingView doesn't allow third-party indicators added to the chart widget that Alpha is using. All built-in indicators and timeframes are already supported.\n\n**Can indicator parameters be changed?**\nUnfortunately no, TradingView doesn't allow that functionality in the chart widget that Alpha is using.\n\n**Why doesn't Alpha use TradingView Premium?**\nTradingView Premium is not available for the widget used to make Alpha work.\n\n**I'm reaching the chart rate limit. Can I increase it?**\nYes, you can increase the limit by purchasing Alpha premium (personal)\n\n**Why does Alpha have specific permission?**\nSend messages, read messages, read message history, attach files, embed links, add reactions: this allows Alpha to properly read and respond to commands, attach charts and add a checkbox after each sent image.\nManage messages: by clicking on the checkbox under images sent by Alpha, the corresponding message will be removed.\nChange nickname: in case term #3 of Alpha ToS is broken, Alpha can automatically fix the issue.\nSSend TTS messages, use external emojis, manage Webhooks: can be turned off, used for future-proofing.\n\n\n__**F O R**__"
faq2 = "__**S E R V E R   O W N E R S**__\n\n**Alpha doesn't post charts in the chat. What to do?**\nCheck the permissions given to Alpha and make sure it's allowed to read messages, send messages, and send attachments.\n\n**Alpha causes server spam.**\nYour only option would be to enable auto-delete mode. To do that, type `a autodelete enable` in your server (administrator privileges are required). If you want to disable it, type `a autodelete disable`.\n\n**I don't want Google Assistant functionality. Can I disable it?**\nYes, type `a assistant disable` in your server (administrator privileges are required). If you want to enable it again, type `a assistant enable`.\n\n**Mex interferes with another bot. How can I disable it?**\nType `a shortcuts disable` in your server (administrator privileges are required). If you want to enable it again, type `a shortcuts enable`.\n\n\n__**F O R**__"
faq3 = "__**D E V E L O P E R S**__\n\n**Is Alpha open-sourced?**\nNo. I'm not planning to open-source it any time soon.\n\n**Alpha won't respond to other bots. Is this normal?**\nYes, this is expected behavior and was put in place due to safety reasons. Alpha only responds to verified bots. To get your bot verified, please contact me for further instructions.\n\n**...#0000 is unverified, why?**\n...#0000 is likely not a bot, but a webhook. I cannot verify webhooks due to how they work on Discord."

supportedExchanges = {
	"charts": [
		'binance', 'coinbasepro', 'bittrex', 'poloniex', 'kraken', 'bitmex', 'bitfinex2', 'bitflyer', 'okcoincny', 'okcoinusd', 'bithumb',
		'bitso', 'bitstamp', 'btcchina', 'cobinhood', 'coinfloor', 'foxbit', 'gemini', 'hitbtc2', 'huobipro', 'itbit', 'mercado'
	],
	"ohlcv": [
		'binance', 'coinbasepro', 'bittrex', 'poloniex', 'kraken', 'kucoin', 'bitmex', 'bitfinex2', 'okex', 'huobipro', 'bitflyer', 'okcoincny', 'okcoinusd',
		'bithumb', 'bitso', 'bitstamp', 'btcchina', 'cobinhood', 'coinfloor', 'foxbit', 'gemini', 'hitbtc2', 'ethfinex', 'itbit', 'mercado'
	],
	"orderbook": [
		'binance', 'coinbasepro', 'bittrex', 'poloniex', 'kraken', 'kucoin', 'bitmex', 'bitfinex2', 'okex', 'huobipro', 'bitflyer', 'okcoincny', 'okcoinusd',
		'bithumb', 'bitso', 'bitstamp', 'btcchina', 'cobinhood', 'coinfloor', 'foxbit', 'gemini', 'hitbtc2', 'ethfinex', 'itbit', 'mercado'
	],
	"alerts": [
		'binance', 'bitmex'
	],
	"trading": [
		'binance', 'bittrex'
	]
}

supportedTimeframes = {
	1: ["1", "1m", "1min", "1mins", "1minute", "1minutes"],
	3: ["3", "3m", "3min", "3mins", "3minute", "3minutes"],
	5: ["5", "5m", "5min", "5mins", "5minute", "5minutes"],
	10: ["10", "10m", "10min", "10mins", "10minute", "10minutes"],
	15: ["15", "15m", "15min", "15mins", "15minute", "15minutes"],
	30: ["30", "30m", "30min", "30mins", "30minute", "30minutes"],
	45: ["45", "45m", "45min", "45mins", "45minute", "45minutes"],
	60: ["60", "60m", "60min", "60mins", "60minute", "60minutes", "1", "1h", "1hr", "1hour", "1hours", "hourly", "hour", "hr", "h"],
	120: ["120", "120m", "120min", "120mins", "120minute", "120minutes", "2", "2h", "2hr", "2hrs", "2hour", "2hours"],
	180: ["180", "180m", "180min", "180mins", "180minute", "180minutes", "3", "3h", "3hr", "3hrs", "3hour", "3hours"],
	240: ["240", "240m", "240min", "240mins", "240minute", "240minutes", "4", "4h", "4hr", "4hrs", "4hour", "4hours"],
	360: ["360", "360m", "360min", "360mins", "360minute", "360minutes", "6", "6h", "6hr", "6hrs", "6hour", "6hours"],
	720: ["720", "720m", "720min", "720mins", "720minute", "720minutes", "12", "12h", "12hr", "12hrs", "12hour", "12hours"],
	1440: ["24", "24h", "24hr", "24hrs", "24hour", "24hours", "d", "day", "1", "1d", "1day", "daily", "1440", "1440m", "1440min", "1440mins", "1440minute", "1440minutes"],
	10080: ["7", "7d", "7day", "7days", "w", "week", "1w", "1week", "weekly"],
	43829: ["30d", "30day", "30days", "1", "1m", "m", "mo", "month", "1mo", "1month", "monthly"],
	87658: ["2", "2m", "2m", "2mo", "2month", "2months"],
	131487: ["3", "3m", "3m", "3mo", "3month", "3months"],
	262974: ["6", "6m", "5m", "6mo", "6month", "6months"],
	525949: ["12", "12m", "12mo", "12month", "12months", "year", "yearly", "1year", "1y", "y" "annual", "annually"],
	1051898: ["24", "24m", "24mo", "24month", "24months", "2year", "2y"],
	1577847: ["36", "36m", "36mo", "36month", "36months", "3year", "3y"],
	2103796: ["48", "48m", "48mo", "48month", "48months", "4year", "4y"]
}

messageOverrides = {
	"I can't help you with that.": [
		"Your public IP address is:",
		"Your shopping list",
		"your shopping list",
		"What do you want to add?",
		"What's the reminder?",
		"Okay, make a reminder. When do you want to be reminded?",
		"Sorry, I can't set reminders yet.",
		"When do you want to be reminded?",
		"It looks like I need permission",
		"I'll need your permission"
	],
	"At first I was just an idea, then Maco#9999 and the team put their heads together. And now here I am :blush:": [
		"I was made by a team of people at Google",
		"I was made by a team at Google",
		"At first I was just an idea, then a bunch of people at Google put their heads together. And now here I am",
		"The Google team is like my family, they mean a lot to me",
		"Everyone at Google is sort of like my family"
	],
	"For privacy related concerns, please join Alpha server: https://discord.gg/GQeDE85": [
		"Earning and maintaining your trust is a priority at Google. Google protects your data and ensures you are in control. You can learn more about Google's principles and practices at safety.google.com. To see and manage your account information, visit myaccount.google.com. That's myaccount.google.com."
	]
}
funnyReplies = {
	"No u": [
		"fuck you alpha",
		"alpha fuck you",
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
	211986377171140609, 143789813080784896, 464581380467064832, 148212496954556416, 233060085088124929, 466052277078065152
}

# Bots
verifiedBots = {
	545279536275652658, 159985870458322944, 225522547154747392, 235240434073337857, 155149108183695360, 535886692125507585, 541244033327169536, 168274283414421504, 349920059549941761,
	574614264296374291, 419264971206164511, 449172824087592970, 407540162411626498, 598480541066592257, 619780178184372235
}
blockedBots = {
	439205512425504771, 409875566800404480, 372508787833307146, 559115332573462539, 537911037500981250, 491614535812120596, 329668530926780426, 588987603970424842, 228537642583588864,
	365975655608745985, 555837614318551051, 475142965904277514, 496207188805550080, 466455242222075934, 454755968736296960, 486926264800903171, 134133271750639616, 485090842550337536,
	486929002632839172, 591982949755519005, 568621953523384321, 549604857125011456, 484908115297435660, 282859044593598464, 273612111610118144, 305398845389406209, 543771182936358912,
	564036955919220747, 376725806816296960, 515741200280715266, 510522505371582476, 289066747443675143, 510976676914528257, 496208876279037953, 372022813839851520, 512332990567677976,
	345450194613043201, 509406093542293504, 395385545326592010, 445935792981016596, 544228999921139715, 575212314555449349, 455517180021440521, 270904126974590976, 204255221017214977,
	458334461877288971, 346353957029019648, 434296797809344523, 571259265671626753, 433098783262244881, 292953664492929025, 448177481040658442, 424792664249204739, 185476724627210241,
	491769129318088714, 569205519626141718, 319533843482673152, 178966653982212096, 452988914215550997, 479147589363826688, 365878696986411008, 379985883522138112, 235148962103951360,
	555955826880413696, 500297618505859072, 116275390695079945, 187636089073172481, 498512758908780566, 512517352357625866, 564267993006211073, 537631610724679699, 235088799074484224,
	172002275412279296, 585243322902249492, 466608402894684170, 206955935229280256, 247134460024193027, 526453056477462548, 506918730790600704, 431133287306493983, 521448643312484352,
	524471167164350484, 387548561816027138, 323161971102973963, 365594481594204161, 550737379460382752, 360801859461447700, 418823684459855882, 470873878575710219, 512212602613399552,
	184405311681986560, 476828276820803584, 575769520531046400, 550355487858884618, 541611655323320320, 210253908076003342, 240254129333731328, 324631108731928587, 85614143951892480,
	474828124534865931, 232916519594491906, 552009858447179788, 297188793444859914, 591161654264463360, 330416853971107840, 538077613437222913, 570637298170068993, 242730576195354624,
	340988108222758934, 417901250755952640, 315926021457051650, 448156485470388224, 356065937318871041, 464272403766444044, 172350728478785536, 426486738660098061, 447176783704489985,
	303181184718995457, 394812811630477315, 548984223592218634, 365082775121821696, 414925323197612032, 542162758234537995, 408785106942164992, 580463767327080468, 432332949913075713,
	495966118339674133, 213466096718708737, 278157415160086529, 554386140573073408, 451167306782081024, 471323073434353666, 566658809502760971, 109379894718234624, 252128902418268161,
	526620171658330112, 594086157025804291, 268420199370194944, 251930037673132032, 474918935494393876, 593761139318587393, 372365416086896640, 594097204734722054, 532045200823025666,
	439454842071547905, 293425321108176906, 543974987795791872, 593225699792257047, 566107036212002817, 574299414793814054, 503720029456695306, 545886978369912832, 384552492991774720,
	418842777720193037, 316478352133193728, 368362411591204865, 284035252408680448, 280497242714931202, 285480424904327179, 294882584201003009, 234395307759108106, 554504420206051328,
	518078963734675456, 574114242052096021, 540066893361577984, 534589798267224065, 160105994217586689, 531626253082558484, 333388886732701696, 575107435363041280, 484453228025741313,
	581479300482465792, 474041654605512704, 600030176902119424, 602734276664098826, 508081897146941461, 593606865603264522, 319603147616157696, 379906184854896651, 516666179419373588,
	433430291256705035, 433615162394804224, 189702078958927872, 443151241816834048, 411086963207307274, 592482755137372171, 598220796241903626, 276060004262477825, 455090842013532160,
	320458922580377602, 302050872383242240, 422087909634736160, 115385224119975941, 476259371912003597, 368105370532577280, 298673420181438465, 346937321138028546, 310039170792030211,
	340319472357474304, 554363496100528130, 205190545914462208, 482501140852768798, 569955063519510549, 409016661983887380, 564344176716546058, 367061304042586124, 475503071724568586,
	411087731754532865, 411087731754532865, 393069757768794113, 581026255969976335, 484309284826382349, 581136923339390984, 202917352378073088, 439717362271518722, 406057980685975553,
	134073775925886976, 216303189073461248, 375805687529209857, 245675252821000193, 297153970613387264, 333422871567400961, 521430751149948929, 571158028083134465, 512501665719517200,
	533282527323095061, 291772532061765635, 231849525185216512, 406841964542164993, 471542070817849355, 275270122082533378, 454411921060397066, 608954478208483328, 607244646514556929,
	204777316621090816, 581035306002808832, 600726031091105812, 268478587651358721, 438828509591502879, 338897906524225538, 320446653905764362, 602036342679142400, 606239418189217817,
	600176720959635467, 600554904045617152, 171288238659600384, 610352558153793538, 470018481262428160, 453206519308353546, 606488101820301330, 590452995343253515, 606612350853709875,
	377448261138645003, 393200384279052288, 460728494977187841, 460731380863729664, 421245481859940363, 606909966573633546, 482584715673600026, 183749087038930944, 486349031224639488,
	501982335076532224, 531858459512012811, 265161580201771010, 327424261180620801, 583945825516912640, 406830608765943808, 607018819210182666, 389878403547004941, 548642436654563355,
	595690278049021952, 594648003084812289, 501862307870146571, 477114120299085854, 588491489240416256, 603930968579112990, 271394014358405121, 453258927505670165, 195244363339530240,
	576587824284041218, 494796150763683841, 520147661010239500, 389035105227767817, 602098236077113344, 612076970422829058, 367835200916291586, 566187331405742090, 484664339085787136,
	415062217596076033, 367640444185870346, 568756894404182017, 601126843164262447, 497357665928740866, 398601531525562369, 612325209621528608, 538019474599837706, 532611196252061706,
	405893556994310146, 596694947562782742, 373254914551447561, 494875411918880778, 438665977140346890, 613110807621795851, 612827970507243530, 494504495896723486, 466578580449525760,
	496136915427524611, 318312854816161792, 612888269968900104, 392400027781431316, 612757937605181483, 458718632537751552, 613452096192380952, 345789068770148352, 412380586737664020,
	411255104398950411, 357678717301948416, 512413434839695371, 549693186918973442, 554631615565922364, 445160589023641612, 448868461318373377, 554088753195515945, 614220255035785303,
	485962834782453762, 458992175984803850, 562367726207893516, 432610292342587392, 564176350634573849, 170915625722576896, 612688129320681574, 432616859263827988, 335048914719735813,
	577522747123564565, 524508123856240640, 502381121607303168, 466872484684234752, 280726849842053120, 502185551982755840, 278362996349075456, 239775420470394897, 343817643108728832,
	614619148567183383, 440121235410649108
}

# Servers
bannedGuilds = {
	468854180048666625, 577324120371494913, 520669492242677780, 571511712629653514
}
blockedGuilds = {
	264445053596991498, 446425626988249089
}

# Channels
blockedChannels = {
	448609517089980427, 520464618200760320, 529772776282914817, 582169450120216576, 592795709305126936, 591660042747510784, 593853011399409768, 541961275152793630, 588522533557370884,
	586959381413756940, 510170028256002049, 512488848912023562, 601353368769658880, 488416206450327562, 550400700828483585, 498568493604667432, 604079133760290816, 312700697969950720,
	472954259608961034, 489619657238511618
}

# Mentions
mentionWords = [
	" <@401328409499664394>", "alpha", "maco"
]
mutedMentionWords = [
	"centauri", "male", "stage", "beta", "seeking", "gap", "maximum", ":alpha:", "free", "testnet", "mainnet", "romeo", "omega", "share", "my"
]

blockedBotNames = [
	"news", "liq", "telegram", "market", "mkt", "reddit", "rekt", "wall", "cryptopanic", "porn", "alert", "troll", "whale", "nsfw", "finviz", "stock", "github", "gitlab", "bittrex", "bitrex",
	"binance", "twitter", "tweet", "bitmex", "giphy", "gmail", "forex", "coingecko", "r/", "log", "poloniex", "webhook"
]
