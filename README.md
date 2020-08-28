[![Discord Bots](https://discordbots.org/api/widget/401328409499664394.svg)](https://discordbots.org/bot/401328409499664394)

# FAQ

## General

**Can you add other indicators or timeframes to the bot?**

Unfortunately no, our data providers don't allow third-party indicators added to the charts. All built-in indicators and timeframes are already supported.

**Can indicator parameters be changed?**

Unfortunately no, our data providers don't allow that functionality.

**Why doesn't Alpha use TradingView Premium?**

TradingView Premium is not available for the data provided by Alpha.

**I'm reaching the chart rate limit. Can I increase it?**

Yes, you can increase the limit by purchasing [Alpha Pro](https://www.alphabotsystem.com/pro "Alpha Pro").

**Why does Alpha have specific permission?**

- Send messages, read messages, read message history, attach files, embed links, add reactions: this allows Alpha to properly read and respond to commands, attach charts and add a checkbox after each sent image.
- Manage messages: by clicking on the checkbox under images sent by Alpha, the corresponding message will be removed.
- Change nickname: according to our Terms of Service, rebranding is not permitted. With that permission, Alpha can automatically fix the issue.
- Send TTS messages, use external emojis, manage Webhooks: can be turned off, used for future-proofing.


## For Server Owners

**Alpha doesn't post charts in the chat. How to fix that?**

Check the permissions given to Alpha and make sure it's allowed to read messages, send messages, and send attachments.

**Alpha causes server spam. What can be done?**

Your only option would be to enable auto-delete mode. To do that, type `set autodelete on` in your server (administrator privileges are required). If you want to disable it, type `set autodelete off`.

**I don't want the Assistant functionality. Can I disable it?**

Yes, type `set assistant off` in your server (administrator privileges are required). If you want to enable it again, type `set assistant on`.

**Something interferes with another bot. How can I disable it?**

Type `set shortcuts off` in your server (administrator privileges are required). If you want to enable it again, type `set shortcuts on`.
