import os
from dataclasses import dataclass
from typing import List
from dotenv import load_dotenv  # pip install python-dotenv

load_dotenv()

@dataclass
class Settings:
    bot_token: str
    admins: List[int]
    log_chat_id: int
    payment_url: str
    support_username: str

def get_settings() -> Settings:
    token = os.getenv("BOT_TOKEN")
    admins_raw = os.getenv("ADMINS", "")
    admins = [int(x) for x in admins_raw.split(",") if x.strip().isdigit()]
    log_chat_id = int(os.getenv("LOG_CHAT_ID"))
    payment_url = os.getenv(
        "PAYMENT_URL",
        "https://www.sberbank.ru/ru/choise_bank?requisiteNumber=+79264680844&bankCode=100000000004",
    )
    support_username = os.getenv("SUPPORT_USERNAME", "Netrunner_0_0").lstrip("@")
    return Settings(
        bot_token=token,
        admins=admins,
        log_chat_id=log_chat_id,
        payment_url=payment_url,
        support_username=support_username,
    )

settings = get_settings()
