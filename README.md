[![Discord Bots](https://discordbots.org/api/widget/401328409499664394.svg)](https://discordbots.org/bot/401328409499664394)

# FAQ

## General

**Can you add other indicators or timeframes to the bot?**

Unfortunately no, TradingView doesn't allow third-party indicators added to the chart widget that Alpha is using. All built-in indicators and timeframes are already supported.

**Can indicator parameters be changed?**

Unfortunately no, TradingView doesn't allow that functionality in the chart widget that Alpha is using.

**Why doesn't Alpha use TradingView Premium?**

TradingView Premium is not available for the widget used to make Alpha work.

**I'm reaching the chart rate limit. Can I increase it?**

Yes, you can increase the limit by purchasing Alpha premium (personal)

**Why does Alpha have specific permission?**

- Send messages, read messages, read message history, attach files, embed links, add reactions: this allows Alpha to properly read and respond to commands, attach charts and add a checkbox after each sent image.
- Manage messages: by clicking on the checkbox under images sent by Alpha, the corresponding message will be removed.
- Change nickname: in case term #3 of Alpha ToS is broken, Alpha can automatically fix the issue.
- Send TTS messages, use external emojis, manage Webhooks: can be turned off, used for future-proofing.


## For Server Owners

**Alpha doesn't post charts in the chat. What to do?**

Check the permissions given to Alpha and make sure it's allowed to read messages, send messages, and send attachments.

**Alpha causes server spam.**

Your only option would be to enable auto-delete mode. To do that, type `a autodelete enable` in your server (administrator privileges are required). If you want to disable it, type `a autodelete disable`.

**I don't want Google Assistant functionality. Can I disable it?**

Yes, type `a assistant disable` in your server (administrator privileges are required). If you want to enable it again, type `a assistant enable`.

**Mex interferes with another bot. How can I disable it?**

Type `a shortcuts disable` in your server (administrator privileges are required). If you want to enable it again, type `a shortcuts enable`.

## For Developers

**Is Alpha open-sourced?**

Alpha as a whole is not open-sourced but our message-handling system is to provide full transparency over how we use your data and what is being stored.

**Alpha won't respond to other bots. Is this normal?**

Yes, this is expected behavior and was put in place due to safety reasons. Alpha only responds to verified bots. To get your bot verified, please contact me for further instructions.

**...#0000 is unverified, why?**

...#0000 is likely not a bot, but a webhook. I cannot verify webhooks due to how they work on Discord.


# Terms of Service

By using Alpha bot, you agree to these terms. Inability to comply will result in Alpha stopping to respond to users requests. Rules are enforced by an automated system. Clever ways to trick the flagging system will result in an instant server ban.
1. Deliberate attempts to break the bot will not be tolerated.
2. The bot should be accessible to all users in a server. Charging for it is forbidden.
3. Alpha bot should not be rebranded or renamed to your server's name. This also includes adding prefixes or suffixes. Neutral nicknames are allowed.
4. Alpha has read access in all channels that you add it to. All messages flowing through the bot are processed by the bot.
5. We are not responsible for any content provided by Alpha.

# Disclaimer

None of the of the information provided here is considered financial advice. Please seek financial guidance before considering making trades in this market, each country is subject to various laws and tax implications.
