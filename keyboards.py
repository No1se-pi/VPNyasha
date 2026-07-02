from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)


def main_menu_kb() -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="Регистрация 📝"),
            KeyboardButton(text="Подключиться к VPN ⚡"),
        ],
        [
            KeyboardButton(text="Мой профиль 😎"),
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
            InlineKeyboardButton(text="🇧🇾 Беларусь", callback_data="vpn_belarus"),
        ],
        [
            InlineKeyboardButton(text="🌍 Оба направления", callback_data="vpn_both"),
        ]
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
