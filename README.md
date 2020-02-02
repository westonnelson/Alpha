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

Your only option would be to enable auto-delete mode. To do that, type `toggle autodelete on` in your server (administrator privileges are required). If you want to disable it, type `toggle autodelete off`.

**I don't want Google Assistant functionality. Can I disable it?**

Yes, type `toggle assistant off` in your server (administrator privileges are required). If you want to enable it again, type `toggle assistant on`.

**Mex interferes with another bot. How can I disable it?**

Type `toggle shortcuts off` in your server (administrator privileges are required). If you want to enable it again, type `toggle shortcuts on`.
