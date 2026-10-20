"""One-off: find your Telegram group's chat id and store both secrets in GitHub.

Run it, paste the BotFather token when asked (it's hidden, never printed),
and it does the rest with `gh secret set`.
"""

import getpass
import json
import subprocess
import sys
import time
import urllib.request

REPO = sys.argv[1] if len(sys.argv) > 1 else "yalzerboy/flat-hunter"


def call(token, method, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(url, data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


token = getpass.getpass("Paste the bot token from BotFather (hidden): ").strip()
me = call(token, "getMe")["result"]
print(f"Bot: @{me['username']}")

groups = {}
print(f"Now, in the group, send this message:  /start@{me['username']}")
print("(Bots can't see normal group messages, but they always see commands.) Waiting up to 2 min...")
for _ in range(12):
    updates = call(token, "getUpdates", timeout=10,
                   allowed_updates=["message", "my_chat_member", "chat_member"]).get("result", [])
    for u in updates:
        for key in ("message", "my_chat_member", "chat_member", "edited_message"):
            chat = (u.get(key) or {}).get("chat")
            if chat and chat.get("type") in ("group", "supergroup"):
                groups[chat["id"]] = chat.get("title")
    if groups:
        break
    time.sleep(5)

if not groups:
    sys.exit(f"No group found. Make sure @{me['username']} is in the group, send /start@{me['username']} there, and re-run.")
if len(groups) > 1:
    for i, (cid, title) in enumerate(groups.items()):
        print(f"  {i}: {title}")
    cid = list(groups)[int(input("Which one? "))]
else:
    cid = next(iter(groups))
print(f"Group: {groups[cid]} ({cid})")

call(token, "sendMessage", chat_id=cid,
     text="🏠 Flat hunter connected. New 2-bed flats worth a look will appear here.")

for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", str(cid))):
    subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value, text=True, check=True)
print("Saved TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to", REPO)
