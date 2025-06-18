
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
TON_WALLET = os.getenv("TON_WALLET")
ADMINS = [int(id.strip()) for id in os.getenv("ADMINS", "6281097018").split(",")]
