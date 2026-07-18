from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)


def main_menu_kb() -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="Подключиться к VPN ⚡"),
        ],
        [
            KeyboardButton(text="Мой профиль 😎"),
        ],
        [
            KeyboardButton(text="Акции 🔥"),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
    )


def vpn_choice_kb() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text="🇩🇪 Германия", callback_data="vpn_germany"),
            InlineKeyboardButton(text="🇺🇸 США", callback_data="vpn_usa"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def profile_kb() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(
                text="🔁 Продлить доступ",
                callback_data="extend_access",
            ),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
