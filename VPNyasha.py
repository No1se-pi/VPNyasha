import asyncio
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.strategy import FSMStrategy
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from config import settings
from storage import (
    REFERRAL_BONUS_DAYS,
    apply_referral_bonus,
    cleanup_empty_users,
    create_purchase_order,
    disable_access,
    get_open_purchase_orders,
    get_or_create_user,
    get_purchase_order,
    get_referrals_for,
    get_vpn_key,
    grant_access,
    load_purchase_orders,
    load_users,
    mark_expiration_notice_sent,
    normalize_dt,
    set_order_admin_message,
    set_referral,
    transition_purchase_order,
    users,
    utc_now,
)


load_users()
load_purchase_orders()

logging.basicConfig(level=logging.INFO)
dp = Dispatcher(storage=MemoryStorage(), fsm_strategy=FSMStrategy.GLOBAL_USER)

BASE_DIR = Path(__file__).resolve().parent
PROXY_FILE = BASE_DIR / "proxy_url.txt"
MOSCOW_TZ = timezone(timedelta(hours=3))
GRACE_PERIOD_DAYS = 3
ADMIN_PAGE_SIZE = 8

STICKERS_START = ["CAACAgIAAxkBAAMWaVSJR-Tw6XHH5iKLRgZd0RDoaHgAAmQ6AALgo4IH_LAjcdV4gS04BA"]
STICKERS_CONNECT = [
    "CAACAgIAAxkBAAMSaVSI_FyK_xrCSV4fxR1eDKRJoZcAAls6AALgo4IHz4cwynuVHRc4BA",
    "CAACAgIAAxkBAAPdaVVvpLrqIPC3LO9gIeCBB1Y9ZCwAApI6AALgo4IH-dmrD1mMdNQ4BA",
]

SERVICE_OPTIONS = {
    "vpn": {"title": "VPN", "price": 150, "vpn": True, "proxy": False},
    "proxy": {"title": "Proxy", "price": 50, "vpn": False, "proxy": True},
    "both": {"title": "VPN + Proxy", "price": 200, "vpn": True, "proxy": True},
}

COUNTRY_OPTIONS = {
    "de": {"title": "Германия", "flag": "🇩🇪", "proxy_available": True},
    "us": {"title": "США", "flag": "🇺🇸", "proxy_available": False},
    "bundle": {
        "title": "Комплект",
        "flag": "🇩🇪 + 🇺🇸",
        "proxy_available": False,
    },
}

PROMO_DISCOUNT_PERCENT = Decimal("33.33")
PROMO_PRICE_FACTOR = Decimal("0.6667")
BUNDLE_PRICE_MULTIPLIER = Decimal("1.5")
BUNDLE_COUNTRY_CODE = "bundle"
BUNDLE_VPN_COUNTRIES = ("de", "us")

DURATION_OPTIONS = {
    "1m": {"title": "1 месяц", "days": 30, "months": 1},
    "2m": {"title": "2 месяца", "days": 60, "months": 2},
    "3m": {"title": "3 месяца", "days": 90, "months": 3},
    "6m": {"title": "полгода", "days": 180, "months": 6},
    "12m": {"title": "год", "days": 365, "months": 12},
}

DOWNLOAD_LINKS = {
    "ios": "https://apps.apple.com/us/app/amneziavpn/id1600529900",
    "ios_fallback": "https://apps.apple.com/ru/app/defaultvpn/id6744725017",
    "android": (
        "https://play.google.com/store/apps/details?id=org.amnezia.vpn"
        "&utm_source=amnezia.org&utm_campaign=organic&utm_medium=referral"
    ),
    "apk": "https://github.com/amnezia-vpn/amnezia-client/releases/tag/4.8.19.0",
    "windows": "https://github.com/amnezia-vpn/amnezia-client/releases/download/4.8.19.0/AmneziaVPN_4.8.19.0_x64.exe",
    "macos": "https://github.com/amnezia-vpn/amnezia-client/releases/download/4.8.19.0/AmneziaVPN_4.8.19.0_macos.pkg",
    "linux": (
        "https://github.com/amnezia-vpn/amnezia-client/releases/download/4.8.19.0/"
        "AmneziaVPN_4.8.19.0_linux_x64.tar"
    ),
    "github": "https://github.com/amnezia-vpn/amnezia-client/releases/tag/4.8.19.0",
}


class PurchaseStates(StatesGroup):
    waiting_receipt = State()


class AdminStates(StatesGroup):
    waiting_news = State()
    waiting_vpn_key = State()
    waiting_order_vpn_key = State()
    waiting_user_lookup = State()


class ReferralStates(StatesGroup):
    waiting_code = State()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admins


def h(value: object) -> str:
    return escape(str(value or ""))


def remove_prefix(value: str, prefix: str) -> str:
    if value.startswith(prefix):
        return value[len(prefix):]
    return value


def format_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "не указано"
    return normalize_dt(dt).astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")


def format_user_name(user) -> str:
    if user.username:
        return f"@{h(user.username)}"
    return f"id <code>{user.user_id}</code>"


def plain_user_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return f"id {user.user_id}"


def services_text(user) -> str:
    services = []
    if user.has_vpn:
        services.append("VPN")
    if user.has_proxy:
        services.append("Proxy")
    return " + ".join(services) if services else "нет активной услуги"


def country_label(country_code: str) -> str:
    country = COUNTRY_OPTIONS.get(country_code)
    if not country:
        return "Страна не указана"
    return f"{country['flag']} {country['title']}"


def vpn_country_codes(country_code: str) -> Tuple[str, ...]:
    if country_code == BUNDLE_COUNTRY_CODE:
        return BUNDLE_VPN_COUNTRIES
    if country_code in COUNTRY_OPTIONS:
        return (country_code,)
    return ()


def vpn_keys_for(user, country_code: str) -> Dict[str, str]:
    return {
        key_country_code: get_vpn_key(user, key_country_code)
        for key_country_code in vpn_country_codes(country_code)
        if get_vpn_key(user, key_country_code)
    }


def missing_vpn_country_codes(user, country_code: str) -> List[str]:
    return [
        key_country_code
        for key_country_code in vpn_country_codes(country_code)
        if not get_vpn_key(user, key_country_code)
    ]


def parse_vpn_keys(raw_value: str, country_codes: List[str]) -> Optional[Dict[str, str]]:
    keys = [line.strip() for line in raw_value.splitlines() if line.strip()]
    if len(keys) != len(country_codes) or any(not key.startswith("vpn://") for key in keys):
        return None
    return dict(zip(country_codes, keys))


def service_available(country_code: str, service_code: str) -> bool:
    country = COUNTRY_OPTIONS.get(country_code)
    service = SERVICE_OPTIONS.get(service_code)
    if not country or not service:
        return False
    if country_code == BUNDLE_COUNTRY_CODE:
        return service_code == "vpn"
    return country["proxy_available"] or not service["proxy"]


def original_price_for(
    service_code: str,
    duration_code: str,
    country_code: str = "de",
) -> int:
    original_price = SERVICE_OPTIONS[service_code]["price"] * DURATION_OPTIONS[duration_code]["months"]
    if country_code == BUNDLE_COUNTRY_CODE:
        single_promo_price = int(
            (Decimal(original_price) * PROMO_PRICE_FACTOR).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        return single_promo_price * 2
    return original_price


def price_for(
    service_code: str,
    duration_code: str,
    country_code: str = "de",
) -> int:
    single_original_price = original_price_for(service_code, duration_code)
    single_promo_price = int(
        (Decimal(single_original_price) * PROMO_PRICE_FACTOR).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    if country_code == BUNDLE_COUNTRY_CODE:
        return int(
            (Decimal(single_promo_price) * BUNDLE_PRICE_MULTIPLIER).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
    return single_promo_price


def short_status_text(user, now: Optional[datetime] = None) -> str:
    now = now or utc_now()
    if not user.access_until:
        return "доступ не выдан"

    access_until = normalize_dt(user.access_until)
    if user.has_vpn or user.has_proxy:
        if access_until > now:
            days_left = max((access_until - now).days, 0)
            return f"активен до {format_dt(access_until)} ({days_left} дн.)"

        grace_until = access_until + timedelta(days=GRACE_PERIOD_DAYS)
        if grace_until > now:
            return f"ожидает оплаты до {format_dt(grace_until)}"

    if user.disabled_at:
        return f"отключён {format_dt(user.disabled_at)}"
    return f"истёк {format_dt(access_until)}"


def main_menu_kb(user_id: Optional[int] = None) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="Подключиться к VPN ⚡")],
        [KeyboardButton(text="Мой профиль 😎")],
        [KeyboardButton(text="Акции 🔥")],
    ]
    if user_id and is_admin(user_id):
        keyboard.append([KeyboardButton(text="Админка ⚙️")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def country_choice_kb_user() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇩🇪 Германия", callback_data="buy_country_de"),
                InlineKeyboardButton(text="🇺🇸 США", callback_data="buy_country_us"),
            ],
            [
                InlineKeyboardButton(
                    text="🇩🇪 + 🇺🇸 Комплект ×1,5",
                    callback_data="buy_country_bundle",
                )
            ],
        ]
    )


def access_choice_kb_user(country_code: str) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text="VPN", callback_data=f"buy_svc_{country_code}_vpn")]]
    if COUNTRY_OPTIONS[country_code]["proxy_available"]:
        keyboard.extend(
            [
                [InlineKeyboardButton(text="Proxy", callback_data=f"buy_svc_{country_code}_proxy")],
                [InlineKeyboardButton(text="VPN + Proxy", callback_data=f"buy_svc_{country_code}_both")],
            ]
        )
    elif country_code == "us":
        keyboard.append(
            [InlineKeyboardButton(text="Proxy — скоро", callback_data="buy_proxy_unavailable")]
        )
    else:
        keyboard.append(
            [InlineKeyboardButton(text="В комплекте только VPN", callback_data="buy_bundle_vpn_only")]
        )
    keyboard.append([InlineKeyboardButton(text="← К выбору страны", callback_data="buy_back_countries")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def duration_choice_kb_user(country_code: str, service_code: str) -> InlineKeyboardMarkup:
    keyboard = []
    for duration_code, option in DURATION_OPTIONS.items():
        original_price = original_price_for(service_code, duration_code, country_code)
        price = price_for(service_code, duration_code, country_code)
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{option['title']} — {original_price}→{price}₽",
                    callback_data=f"buy_dur_{country_code}_{service_code}_{duration_code}",
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(text="← К услугам", callback_data=f"buy_back_services_{country_code}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def payment_kb(country_code: str, service_code: str, duration_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=settings.payment_url)],
            [
                InlineKeyboardButton(
                    text="✅ Я оплатил — отправить чек",
                    callback_data=f"buy_receipt_{country_code}_{service_code}_{duration_code}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Изменить срок",
                    callback_data=f"buy_svc_{country_code}_{service_code}",
                )
            ],
        ]
    )


def cancel_receipt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить отправку", callback_data="buy_cancel_receipt")]
        ]
    )


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Продлить доступ", callback_data="extend_access")],
            [InlineKeyboardButton(text="🎁 Реферальная программа", callback_data="referral_info")],
        ]
    )


def referral_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ввести код друга", callback_data="referral_enter")],
            [InlineKeyboardButton(text="Назад к профилю", callback_data="profile_back")],
        ]
    )


def promotions_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Выбрать страну и тариф", callback_data="promo_connect")],
            [InlineKeyboardButton(text="🎁 Моя реферальная программа", callback_data="referral_info")],
        ]
    )


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧾 Чеки на проверке", callback_data="adm_orders")],
            [InlineKeyboardButton(text="🧪 Тест сообщений", callback_data="adm_test_messages")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="adm_page_0")],
            [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="adm_add_user")],
            [InlineKeyboardButton(text="📰 News", callback_data="adm_news")],
        ]
    )


def admin_user_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Выдать / продлить", callback_data=f"adm_grant_{user_id}")],
            [InlineKeyboardButton(text="Отключить доступ", callback_data=f"adm_disable_{user_id}")],
            [InlineKeyboardButton(text="Назад к списку", callback_data="adm_page_0")],
        ]
    )


def admin_country_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇩🇪 Германия", callback_data=f"adm_country_{user_id}_de"),
                InlineKeyboardButton(text="🇺🇸 США", callback_data=f"adm_country_{user_id}_us"),
            ],
            [
                InlineKeyboardButton(
                    text="🇩🇪 + 🇺🇸 Комплект ×1,5",
                    callback_data=f"adm_country_{user_id}_bundle",
                )
            ],
            [InlineKeyboardButton(text="Назад", callback_data=f"adm_user_{user_id}")],
        ]
    )


def admin_service_kb(user_id: int, country_code: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="VPN", callback_data=f"adm_svc_{user_id}_{country_code}_vpn")]
    ]
    if COUNTRY_OPTIONS[country_code]["proxy_available"]:
        keyboard.extend(
            [
                [InlineKeyboardButton(text="Proxy", callback_data=f"adm_svc_{user_id}_{country_code}_proxy")],
                [
                    InlineKeyboardButton(
                        text="VPN + Proxy",
                        callback_data=f"adm_svc_{user_id}_{country_code}_both",
                    )
                ],
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(text="Назад", callback_data=f"adm_grant_{user_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_duration_kb(user_id: int, country_code: str, service_code: str) -> InlineKeyboardMarkup:
    keyboard = []
    for duration_code, option in DURATION_OPTIONS.items():
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=option["title"],
                    callback_data=f"adm_dur_{user_id}_{country_code}_{service_code}_{duration_code}",
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(text="Назад", callback_data=f"adm_country_{user_id}_{country_code}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def order_review_kb(order_id: str, back_to_orders: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"order_confirm_{order_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"order_reject_{order_id}"),
            ]
        ]
    if back_to_orders:
        keyboard.append([InlineKeyboardButton(text="← К списку чеков", callback_data="adm_orders")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_test_order_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="adm_test_noop_confirm"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data="adm_test_noop_reject"),
            ]
        ]
    )


def sorted_users_for_admin():
    now = utc_now()

    def rank(user):
        if user.has_vpn or user.has_proxy:
            if user.access_until:
                access_until = normalize_dt(user.access_until)
                if access_until > now:
                    status_rank = 0
                elif access_until + timedelta(days=GRACE_PERIOD_DAYS) > now:
                    status_rank = 1
                else:
                    status_rank = 2
                return (status_rank, access_until, user.username.lower(), user.user_id)
            return (2, datetime.max.replace(tzinfo=timezone.utc), user.username.lower(), user.user_id)
        return (3, datetime.max.replace(tzinfo=timezone.utc), user.username.lower(), user.user_id)

    return sorted(users.values(), key=rank)


def find_user_by_admin_input(raw_value: str):
    raw_target = raw_value.strip().lstrip("@")
    if not raw_target:
        return None
    if raw_target.isdigit():
        return users.get(int(raw_target))
    return next((user for user in users.values() if user.username.lower() == raw_target.lower()), None)


def admin_users_text(page: int) -> Tuple[str, InlineKeyboardMarkup]:
    all_users = sorted_users_for_admin()
    total_pages = max((len(all_users) - 1) // ADMIN_PAGE_SIZE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * ADMIN_PAGE_SIZE
    page_users = all_users[start:start + ADMIN_PAGE_SIZE]

    lines = [f"<b>Пользователи</b> · страница {page + 1}/{total_pages}"]
    if not page_users:
        lines.append("Пока никого нет.")
    else:
        now = utc_now()
        for index, user in enumerate(page_users, start=start + 1):
            lines.append(
                f"{index}. {format_user_name(user)} · {h(services_text(user))} · {h(short_status_text(user, now))}"
            )

    keyboard = []
    for user in page_users:
        label = f"{plain_user_name(user)} · {services_text(user)}"
        keyboard.append([InlineKeyboardButton(text=label[:60], callback_data=f"adm_user_{user.user_id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"adm_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"adm_page_{page + 1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton(text="Назад в админку", callback_data="adm_panel")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_orders_text() -> Tuple[str, InlineKeyboardMarkup]:
    open_orders = sorted(
        get_open_purchase_orders(),
        key=lambda order: order.created_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    lines = ["<b>Чеки на проверке</b>"]
    keyboard = []
    if not open_orders:
        lines.append("\nНовых чеков нет.")
    else:
        lines.append(f"\nВсего заявок: <b>{len(open_orders)}</b>")
        status_labels = {
            "pending": "новый",
            "awaiting_key": "ждёт ключи",
            "processing": "обрабатывается",
        }
        for order in open_orders[:30]:
            user = users.get(order.user_id)
            user_name = plain_user_name(user) if user else f"id {order.user_id}"
            label = (
                f"#{order.order_id} · {user_name} · {country_label(order.country_code)} · "
                f"{status_labels.get(order.status, order.status)}"
            )
            keyboard.append(
                [InlineKeyboardButton(text=label[:64], callback_data=f"adm_order_{order.order_id}")]
            )
        if len(open_orders) > 30:
            lines.append("\nПоказаны последние 30 заявок.")
    keyboard.append([InlineKeyboardButton(text="Назад в админку", callback_data="adm_panel")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_dashboard_text() -> str:
    now = utc_now()
    active = 0
    grace = 0
    disabled = 0
    for user in users.values():
        if not user.access_until:
            continue
        access_until = normalize_dt(user.access_until)
        if (user.has_vpn or user.has_proxy) and access_until > now:
            active += 1
        elif (user.has_vpn or user.has_proxy) and access_until + timedelta(days=GRACE_PERIOD_DAYS) > now:
            grace += 1
        elif user.disabled_at:
            disabled += 1

    return (
        "<b>Админка VPNyasha</b>\n\n"
        f"Всего пользователей: <b>{len(users)}</b>\n"
        f"Чеков на проверке: <b>{len(get_open_purchase_orders())}</b>\n"
        f"Активных подписок: <b>{active}</b>\n"
        f"Ждут оплату в grace-периоде: <b>{grace}</b>\n"
        f"Отключённых после срока: <b>{disabled}</b>"
    )


def user_detail_text(user) -> str:
    referrals = get_referrals_for(user.user_id)
    paid_referrals = sum(1 for referral in referrals if referral.referral_rewarded_at)
    referred_by = users.get(user.referred_by) if user.referred_by else None

    lines = [
        "<b>Карточка пользователя</b>",
        f"Telegram: {format_user_name(user)}",
        f"ID: <code>{user.user_id}</code>",
        f"Услуги: <b>{h(services_text(user))}</b>",
        f"Последняя страна: <b>{h(country_label(user.last_country))}</b>",
        f"Статус: {h(short_status_text(user))}",
        f"Окончание: <b>{format_dt(user.access_until)}</b>",
        f"Последний тариф: {h(user.last_plan or 'не указан')}",
        f"Рефкод: <code>{h(user.referral_code)}</code>",
        f"Приглашён по коду: {format_user_name(referred_by) if referred_by else 'нет'}",
        f"Рефералов: {len(referrals)} · оплаченных: {paid_referrals}",
    ]
    if user.vpn_keys:
        lines.append("\n<b>VPN ключи:</b>")
        for country_code, key in user.vpn_keys.items():
            lines.append(f"{h(country_label(country_code))}:\n<pre>{h(key)}</pre>")
    elif user.vpn_key:
        lines.append(f"\n<b>VPN ключ 🇩🇪:</b>\n<pre>{h(user.vpn_key)}</pre>")
    return "\n".join(lines)


def profile_text(user) -> str:
    referrals = get_referrals_for(user.user_id)
    paid_referrals = sum(1 for referral in referrals if referral.referral_rewarded_at)
    referred_by = users.get(user.referred_by) if user.referred_by else None

    return (
        f"<b>Мой профиль</b>\n\n"
        f"Telegram: {format_user_name(user)}\n"
        f"Услуги: <b>{h(services_text(user))}</b>\n"
        f"Последняя страна: <b>{h(country_label(user.last_country))}</b>\n"
        f"Статус: {h(short_status_text(user))}\n"
        f"Окончание: <b>{format_dt(user.access_until)}</b>\n\n"
        f"Рефкод: <code>{h(user.referral_code)}</code>\n"
        f"Приглашено друзей: {len(referrals)} · бонусов начислено: {paid_referrals}\n"
        f"Код друга: {format_user_name(referred_by) if referred_by else 'не указан'}"
    )


def referral_text(user) -> str:
    referrals = get_referrals_for(user.user_id)
    paid_referrals = sum(1 for referral in referrals if referral.referral_rewarded_at)
    return (
        "<b>Реферальная программа</b>\n\n"
        f"Твой код: <code>{h(user.referral_code)}</code>\n"
        f"За друга, который указал твой код и оплатил подписку, тебе начисляется "
        f"<b>{REFERRAL_BONUS_DAYS} дней</b> бесплатно.\n\n"
        f"Приглашено: {len(referrals)}\n"
        f"Оплатили: {paid_referrals}"
    )


def promotions_text() -> str:
    return (
        "<b>Акции VPNyasha 🔥</b>\n\n"
        "<b>🇺🇸 США на старте: −33,33% на всё</b>\n"
        "Пока знакомим нашу VPNяшу с новым сервером, все тарифы во всех странах дешевле на треть. "
        "Например, VPN на месяц: <s>150 ₽</s> → <b>100 ₽</b>.\n\n"
        "<b>🇩🇪 + 🇺🇸 Комплект двух стран</b>\n"
        "Германия и США вместе стоят не ×2, а всего <b>×1,5</b> от одного VPN-тарифа. "
        "На месяц это <s>200 ₽</s> → <b>150 ₽</b> по текущим акционным ценам.\n\n"
        "<b>🎁 Интернет по дружбе</b>\n"
        f"Пригласи друга по своему коду — после его первой подтверждённой оплаты получишь "
        f"<b>{REFERRAL_BONUS_DAYS} дней</b> доступа. Друг получает VPN, ты — ещё две недели. "
        "Кот доволен, интернет работает 🐾"
    )


def help_text() -> str:
    return (
        "<b>Помощь VPNyasha</b>\n\n"
        "<b>Что такое VPN?</b>\n"
        "Это защищённый туннель для интернета через удалённый сервер. "
        "Сайты видят IP сервера, а не твой реальный IP.\n\n"
        "<b>Какие страны доступны?</b>\n"
        "VPN: 🇩🇪 Германия, 🇺🇸 США или комплект из двух стран за ×1,5 цены. "
        "Proxy пока доступен только в Германии.\n\n"
        "<b>Что такое Proxy для Telegram?</b>\n"
        "Proxy работает только внутри Telegram и помогает открыть Telegram, если он плохо грузится. "
        "Для сайтов и приложений нужен VPN.\n\n"
        "<b>Как оплатить?</b>\n"
        "Нажми «Подключиться к VPN ⚡», выбери страну, услугу и срок, оплати по ссылке, "
        "затем отправь чек прямо в бот. Администратор проверит его и выдаст ключ.\n\n"
        "<b>Как получить VPN по шагам?</b>\n"
        "1. Скачай AmneziaVPN.\n"
        "2. Открой бота и выбери страну, услугу и срок.\n"
        "3. Оплати и отправь чек в бот.\n"
        "4. Дождись подтверждения и получи ключ.\n"
        "5. Импортируй ключ в приложение.\n\n"
        "<b>Сколько устройств можно использовать?</b>\n"
        "Ключ можно добавить на несколько устройств, но одновременно работает только одно активное подключение.\n\n"
        "<b>VPN и Proxy — в чём разница?</b>\n"
        "VPN работает для устройства или приложений через AmneziaVPN. Proxy нужен только для Telegram.\n\n"
        "<b>Если Telegram не открывается?</b>\n"
        "Временно включи любой бесплатный VPN или Telegram Proxy, зайди в Telegram и получи основной ключ в боте.\n\n"
        "<b>Если ключ не импортируется?</b>\n"
        "Проверь, что ключ скопирован целиком и начинается с <code>vpn://</code>. "
        "Если ошибка остаётся, пришли скрин в поддержку.\n\n"
        "<b>Как продлить?</b>\n"
        "Открой «Мой профиль 😎» → «Продлить доступ», выбери страну и отправь новый чек. "
        "При продлении в той же стране VPN ключ остаётся прежним.\n\n"
        "<b>Где мой реферальный код?</b>\n"
        "Открой «Мой профиль 😎» → «Реферальная программа». За оплаченного друга начисляется 14 дней.\n\n"
        "<b>VPN или Proxy не работает?</b>\n"
        f"Напиши @{h(settings.support_username)} и приложи скрин ошибки."
    )


def read_proxy_url() -> str:
    if not PROXY_FILE.exists():
        return ""
    return PROXY_FILE.read_text(encoding="utf-8").strip()


async def safe_send_message(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as exc:
        logging.warning("Failed to send message to %s: %s", chat_id, exc)
        return False


async def safe_send_photo(bot: Bot, chat_id: int, photo: str, **kwargs) -> bool:
    try:
        await bot.send_photo(chat_id, photo=photo, **kwargs)
        return True
    except Exception as exc:
        logging.warning("Failed to send photo to %s: %s", chat_id, exc)
        return False


async def safe_copy_message(bot: Bot, chat_id: int, from_chat_id: int, message_id: int) -> bool:
    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )
        return True
    except Exception as exc:
        logging.warning("Failed to copy message to %s: %s", chat_id, exc)
        return False


def download_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📱 iOS", url=DOWNLOAD_LINKS["ios"]),
                InlineKeyboardButton(text="🤖 Android", url=DOWNLOAD_LINKS["android"]),
            ],
            [
                InlineKeyboardButton(text="🪟 Windows", url=DOWNLOAD_LINKS["windows"]),
                InlineKeyboardButton(text="🍎 macOS", url=DOWNLOAD_LINKS["macos"]),
            ],
            [
                InlineKeyboardButton(text="🐧 Linux", url=DOWNLOAD_LINKS["linux"]),
                InlineKeyboardButton(text="📦 APK / GitHub", url=DOWNLOAD_LINKS["github"]),
            ],
        ]
    )


def build_access_message(
    user,
    country_code: str,
    service_code: str,
    duration_code: str,
    vpn_key: str = "",
    vpn_keys: Optional[Dict[str, str]] = None,
    extension_only: bool = False,
) -> str:
    service = SERVICE_OPTIONS[service_code]
    duration = DURATION_OPTIONS[duration_code]
    resolved_vpn_keys = vpn_keys_for(user, country_code)
    resolved_vpn_keys.update(vpn_keys or {})
    if vpn_key and country_code != BUNDLE_COUNTRY_CODE:
        resolved_vpn_keys[country_code] = vpn_key

    lines = [
        "Доступ продлён ✅" if extension_only else "Доступ выдан ✅",
        f"Страна: <b>{h(country_label(country_code))}</b>",
        f"Тариф: <b>{h(service['title'])}</b>",
        f"Срок: <b>{h(duration['title'])}</b>",
        f"Активен до: <b>{format_dt(user.access_until)}</b>",
        "",
    ]

    if extension_only and service["vpn"]:
        key_status = (
            "VPN ключи остались прежними."
            if country_code == BUNDLE_COUNTRY_CODE
            else "VPN ключ остался прежним."
        )
        lines.extend([key_status, ""])
    elif service["vpn"]:
        import_step = (
            "2. Импортируй оба ключа в приложение по очереди:"
            if country_code == BUNDLE_COUNTRY_CODE
            else "2. Импортируй ключ в приложение:"
        )
        lines.extend(
            [
                "Инструкция для VPN:",
                "1. Скачай AmneziaVPN по кнопкам под сообщением.",
                import_step,
            ]
        )
        for key_country_code in vpn_country_codes(country_code):
            key = resolved_vpn_keys.get(key_country_code, "")
            if country_code == BUNDLE_COUNTRY_CODE:
                lines.append(f"<b>{h(country_label(key_country_code))}</b>")
            lines.append(
                f"<pre>{h(key)}</pre>" if key else "Ключ скоро пришлю отдельным сообщением."
            )
        lines.append("")

    if service["proxy"]:
        proxy_url = read_proxy_url()
        lines.extend(
            [
                "Актуальный Proxy:",
                (
                    f"<code>{h(proxy_url)}</code>"
                    if proxy_url
                    else "Прокси пока не создан. Я пришлю его отдельным сообщением."
                ),
                "",
            ]
        )

    lines.extend(
        [
            "Для следующего продления снова выбери страну и тариф в боте.",
            "Готово! ⚡",
        ]
    )
    return "\n".join(lines)


async def grant_and_notify(
    bot: Bot,
    target_user_id: int,
    country_code: str,
    service_code: str,
    duration_code: str,
    vpn_key: str = "",
    vpn_keys: Optional[Dict[str, str]] = None,
    extension_only: bool = False,
    order_id: str = "",
):
    service = SERVICE_OPTIONS[service_code]
    duration = DURATION_OPTIONS[duration_code]
    plan_label = f"{country_label(country_code)}, {service['title']}, {duration['title']}"

    user = grant_access(
        target_user_id,
        days=duration["days"],
        vpn=service["vpn"],
        proxy=service["proxy"],
        vpn_key=vpn_key,
        vpn_keys=vpn_keys,
        country_code=country_code,
        plan_label=plan_label,
        order_id=order_id,
    )
    if not user:
        return None, None

    await safe_send_message(
        bot,
        user.user_id,
        build_access_message(
            user,
            country_code,
            service_code,
            duration_code,
            vpn_key,
            vpn_keys,
            extension_only,
        ),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=download_links_kb() if service["vpn"] else None,
    )

    referral_bonus = apply_referral_bonus(user.user_id)
    if referral_bonus:
        inviter, paid_user = referral_bonus
        await safe_send_message(
            bot,
            inviter.user_id,
            (
                "🎁 Реферальный бонус начислен!\n\n"
                f"{format_user_name(paid_user)} оплатил подписку по твоему коду. "
                f"+{REFERRAL_BONUS_DAYS} дней к доступу.\n"
                f"Новая дата окончания: <b>{format_dt(inviter.access_until)}</b>"
            ),
            parse_mode=ParseMode.HTML,
        )
        await safe_send_message(
            bot,
            paid_user.user_id,
            "Код друга сработал. Ему начислены бесплатные две недели 🎁",
        )

    return user, referral_bonus


def purchase_order_text(order, user) -> str:
    service = SERVICE_OPTIONS[order.service_code]
    duration = DURATION_OPTIONS[order.duration_code]
    return (
        "🧾 <b>Новый чек на проверку</b>\n\n"
        f"Заявка: <code>#{h(order.order_id)}</code>\n"
        f"Пользователь: {format_user_name(user)}\n"
        f"ID: <code>{user.user_id}</code>\n"
        f"Страна: <b>{h(country_label(order.country_code))}</b>\n"
        f"Услуга: <b>{h(service['title'])}</b>\n"
        f"Срок: <b>{h(duration['title'])}</b>\n"
        f"Сумма по тарифу: <b>{order.price} ₽</b>\n\n"
        "Сверь сумму и реквизиты на чеке, затем подтверди или отклони заявку."
    )


def receipt_accepted_text(order) -> str:
    key_word = "ключи" if order.country_code == BUNDLE_COUNTRY_CODE else "ключ"
    delivery_text = (
        f"пришлёт {key_word} или продлит доступ"
        if SERVICE_OPTIONS[order.service_code]["vpn"]
        else "активирует или продлит Proxy"
    )
    return (
        "Чек принят ✅\n\n"
        f"Заявка: <code>#{h(order.order_id)}</code>\n"
        f"Страна: <b>{h(country_label(order.country_code))}</b>\n"
        f"Сумма: <b>{order.price} ₽</b>\n\n"
        f"Администратор проверит оплату. После подтверждения бот сам {delivery_text}."
    )


def vpn_key_input_format(country_codes: List[str]) -> str:
    if len(country_codes) == 1:
        return (
            f"Нужен ключ: <b>{h(country_label(country_codes[0]))}</b>\n"
            "Отправь его одним сообщением в формате <code>vpn://...</code>."
        )

    lines = [
        "Отправь ключи <b>одним сообщением, каждый с новой строки</b> в указанном порядке:"
    ]
    for index, key_country_code in enumerate(country_codes, start=1):
        lines.append(f"{index}. <b>{h(country_label(key_country_code))}</b> — <code>vpn://...</code>")
    return "\n".join(lines)


def order_vpn_key_request_text(order, country_codes: List[str]) -> str:
    return (
        "🔐 <b>Служебное сообщение для администратора</b>\n\n"
        f"Заявка: <code>#{h(order.order_id)}</code>\n"
        f"Тариф: <b>{h(country_label(order.country_code))}</b>\n\n"
        f"{vpn_key_input_format(country_codes)}\n\n"
        "Покупателю ключи присылать не нужно — после ввода бот отправит их сам."
    )


def manual_vpn_key_request_text(
    user,
    country_code: str,
    service_code: str,
    duration_code: str,
    country_codes: List[str],
) -> str:
    return (
        "🔐 <b>Служебное сообщение для администратора</b>\n\n"
        f"Пользователь: {format_user_name(user)}\n"
        f"Тариф: <b>{h(country_label(country_code))} · "
        f"{h(SERVICE_OPTIONS[service_code]['title'])} · "
        f"{h(DURATION_OPTIONS[duration_code]['title'])}</b>\n\n"
        f"{vpn_key_input_format(country_codes)}"
    )


def processed_order_text(order, user, status_text: str) -> str:
    return f"{purchase_order_text(order, user)}\n\n<b>{h(status_text)}</b>"


async def update_order_admin_card(bot: Bot, order, text: str):
    if not order.admin_message_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=settings.log_chat_id,
            message_id=order.admin_message_id,
            text=text,
        )
    except Exception as exc:
        logging.warning("Failed to update purchase order %s: %s", order.order_id, exc)


async def finalize_purchase_order(
    bot: Bot,
    order_id: str,
    admin_id: int,
    vpn_key: str = "",
    vpn_keys: Optional[Dict[str, str]] = None,
):
    order = get_purchase_order(order_id)
    if not order:
        return None, None, "Заявка не найдена."

    user = users.get(order.user_id)
    if not user:
        return None, None, "Пользователь не найден."

    existing_keys_complete = not missing_vpn_country_codes(user, order.country_code)
    claimed_order = transition_purchase_order(
        order_id,
        {"pending", "awaiting_key", "processing"},
        "processing",
        reviewed_by=admin_id,
    )
    if not claimed_order:
        return None, None, "Эта заявка уже обработана другим администратором."

    try:
        granted_user, referral_bonus = await grant_and_notify(
            bot,
            order.user_id,
            order.country_code,
            order.service_code,
            order.duration_code,
            vpn_key=vpn_key,
            vpn_keys=vpn_keys,
            extension_only=bool(
                SERVICE_OPTIONS[order.service_code]["vpn"] and existing_keys_complete
            ),
            order_id=order.order_id,
        )
    except Exception:
        transition_purchase_order(order_id, {"processing"}, "pending")
        raise

    if not granted_user:
        transition_purchase_order(order_id, {"processing"}, "rejected", reviewed_by=admin_id)
        return None, None, "Пользователь не найден."

    approved_order = transition_purchase_order(
        order_id,
        {"processing"},
        "approved",
        reviewed_by=admin_id,
    )
    return granted_user, referral_bonus, approved_order


async def show_admin_panel(message_or_callback):
    text = admin_dashboard_text()
    keyboard = admin_panel_kb()
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=keyboard)
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(text, reply_markup=keyboard)


async def ensure_admin_callback(callback: CallbackQuery) -> bool:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return False
    return True


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == AdminStates.waiting_order_vpn_key.state:
        data = await state.get_data()
        order_id = data.get("order_id", "")
        order = get_purchase_order(order_id)
        if order and order.status == "awaiting_key" and order.reviewed_by == message.from_user.id:
            transition_purchase_order(order_id, {"awaiting_key"}, "pending")
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=main_menu_kb(message.from_user.id))


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(help_text(), disable_web_page_preview=True)


@dp.message(AdminStates.waiting_news)
async def handle_news_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not (message.text or message.photo or message.caption):
        await message.answer("Пришли новость текстом или фото с подписью. Отмена: /cancel.")
        return

    sent = 0
    failed = 0
    for user in list(users.values()):
        ok = await safe_copy_message(
            message.bot,
            user.user_id,
            message.chat.id,
            message.message_id,
        )
        if ok:
            sent += 1
        else:
            failed += 1

    await state.clear()
    await message.answer(
        f"Новость отправлена ✅\nДоставлено: {sent}\nОшибок: {failed}",
        reply_markup=main_menu_kb(message.from_user.id),
    )


@dp.message(AdminStates.waiting_user_lookup)
async def handle_admin_user_lookup(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("Пришли @username или user_id пользователя. Отмена: /cancel")
        return

    target_user = find_user_by_admin_input(message.text)
    await state.clear()
    if not target_user:
        await message.answer(
            "Пользователь не найден. Пусть сначала напишет боту /start, потом попробуй ещё раз.",
            reply_markup=main_menu_kb(message.from_user.id),
        )
        return

    await message.answer(
        f"Выберите страну для {format_user_name(target_user)}:",
        reply_markup=admin_country_kb(target_user.user_id),
    )


@dp.message(AdminStates.waiting_vpn_key)
async def handle_admin_vpn_key(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    missing_country_codes = list(data.get("missing_country_codes") or [])
    parsed_vpn_keys = (
        parse_vpn_keys(message.text, missing_country_codes)
        if message.text and missing_country_codes
        else None
    )
    if not parsed_vpn_keys:
        await message.answer(
            f"Формат не распознан.\n\n{vpn_key_input_format(missing_country_codes)}\n\n"
            "Отмена: /cancel."
        )
        return

    target_user_id = int(data["target_user_id"])
    country_code = data["country_code"]
    service_code = data["service_code"]
    duration_code = data["duration_code"]

    user, referral_bonus = await grant_and_notify(
        message.bot,
        target_user_id,
        country_code,
        service_code,
        duration_code,
        vpn_keys=parsed_vpn_keys,
    )
    await state.clear()

    if not user:
        await message.answer("Пользователь не найден.")
        return

    bonus_text = "\nРеферальный бонус начислен 🎁" if referral_bonus else ""
    await message.answer(
        f"{format_user_name(user)} получил доступ до <b>{format_dt(user.access_until)}</b> ✅{bonus_text}",
        reply_markup=main_menu_kb(message.from_user.id),
    )


@dp.message(AdminStates.waiting_order_vpn_key)
async def handle_order_vpn_key(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    order_id = data.get("order_id", "")
    missing_country_codes = list(data.get("missing_country_codes") or [])
    parsed_vpn_keys = (
        parse_vpn_keys(message.text, missing_country_codes)
        if message.text and missing_country_codes
        else None
    )
    if not parsed_vpn_keys:
        await message.answer(
            f"Формат не распознан.\n\n{vpn_key_input_format(missing_country_codes)}\n\n"
            "Отмена: /cancel."
        )
        return

    order = get_purchase_order(order_id)
    if not order or order.status != "awaiting_key":
        await state.clear()
        await message.answer("Эта заявка уже обработана или больше не существует.")
        return
    if order.reviewed_by and order.reviewed_by != message.from_user.id:
        await state.clear()
        await message.answer("Ключ по этой заявке вводит другой администратор.")
        return

    try:
        user, referral_bonus, result = await finalize_purchase_order(
            message.bot,
            order_id,
            message.from_user.id,
            vpn_keys=parsed_vpn_keys,
        )
    except Exception as exc:
        logging.exception("Failed to approve purchase order %s", order_id)
        await state.clear()
        await message.answer(f"Не удалось выдать доступ: <code>{h(exc)}</code>")
        return

    await state.clear()
    if not user:
        await message.answer(str(result))
        return

    bonus_text = " Реферальный бонус начислен 🎁" if referral_bonus else ""
    await update_order_admin_card(
        message.bot,
        result,
        processed_order_text(result, user, f"✅ Подтверждено администратором. Доступ выдан.{bonus_text}"),
    )
    await message.answer(
        f"Заявка <code>#{h(order_id)}</code> подтверждена. "
        f"{format_user_name(user)} получил доступ до <b>{format_dt(user.access_until)}</b> ✅"
    )


@dp.message(ReferralStates.waiting_code)
async def handle_referral_code(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пришли 6 цифр реферального кода или /cancel.")
        return
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    ok, text, inviter = set_referral(user.user_id, message.text.strip())
    await state.clear()
    await message.answer(text, reply_markup=profile_kb())
    if ok and inviter:
        await safe_send_message(
            message.bot,
            inviter.user_id,
            f"{format_user_name(user)} указал твой реферальный код. Бонус придёт после оплаты.",
            parse_mode=ParseMode.HTML,
        )


@dp.message(PurchaseStates.waiting_receipt)
async def handle_purchase_receipt(message: Message, state: FSMContext):
    document_is_receipt = bool(
        message.document
        and (
            (message.document.mime_type or "").startswith("image/")
            or message.document.mime_type == "application/pdf"
        )
    )
    if not message.photo and not document_is_receipt:
        await message.answer(
            "Пришли чек фотографией, изображением или PDF. Отмена: /cancel.",
            reply_markup=cancel_receipt_kb(),
        )
        return

    data = await state.get_data()
    country_code = data.get("country_code", "")
    service_code = data.get("service_code", "")
    duration_code = data.get("duration_code", "")
    if (
        country_code not in COUNTRY_OPTIONS
        or service_code not in SERVICE_OPTIONS
        or duration_code not in DURATION_OPTIONS
        or not service_available(country_code, service_code)
    ):
        await state.clear()
        await message.answer(
            "Данные тарифа устарели. Начни подключение заново.",
            reply_markup=main_menu_kb(message.from_user.id),
        )
        return

    user = get_or_create_user(message.from_user.id, message.from_user.username)
    order = create_purchase_order(
        user_id=user.user_id,
        country_code=country_code,
        service_code=service_code,
        duration_code=duration_code,
        price=price_for(service_code, duration_code, country_code),
        receipt_chat_id=message.chat.id,
        receipt_message_id=message.message_id,
    )

    try:
        copied_receipt = await message.bot.copy_message(
            chat_id=settings.log_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        admin_message = await message.bot.send_message(
            settings.log_chat_id,
            purchase_order_text(order, user),
            reply_markup=order_review_kb(order.order_id),
            reply_to_message_id=copied_receipt.message_id,
        )
        set_order_admin_message(order.order_id, admin_message.message_id)
    except Exception as exc:
        logging.exception("Failed to deliver receipt for order %s", order.order_id)
        transition_purchase_order(order.order_id, {"pending"}, "rejected")
        await state.clear()
        await message.answer(
            "Не получилось передать чек администратору. Платёж не потерян: "
            f"напиши @{h(settings.support_username)} и укажи заявку <code>#{h(order.order_id)}</code>.",
            reply_markup=main_menu_kb(message.from_user.id),
        )
        return

    await state.clear()
    await message.answer(
        receipt_accepted_text(order),
        reply_markup=main_menu_kb(message.from_user.id),
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    import random

    user = get_or_create_user(message.from_user.id, message.from_user.username)
    sticker_id = random.choice(STICKERS_START)
    await message.answer_sticker(sticker_id)
    await message.answer(
        "Привет! Я помогу получить тебе доступ к VPN💕\n\n"
        "Нажимай кнопки ниже:\n"
        "• Подключиться к VPN ⚡\n"
        "• Мой профиль 😎\n"
        "• Акции 🔥\n\n"
        f"По техническим вопросам: @{h(settings.support_username)}",
        reply_markup=main_menu_kb(user.user_id),
    )


@dp.message(F.text == "Подключиться к VPN ⚡")
async def btn_connect(message: Message):
    import random

    get_or_create_user(message.from_user.id, message.from_user.username)
    sticker_id = random.choice(STICKERS_CONNECT)
    await message.answer_sticker(sticker_id)
    await message.answer("Сначала выбери страну подключения:", reply_markup=country_choice_kb_user())


@dp.message(F.text == "Мой профиль 😎")
async def btn_profile(message: Message):
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(profile_text(user), reply_markup=profile_kb())


@dp.message(F.text == "Акции 🔥")
async def btn_promotions(message: Message):
    get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(promotions_text(), reply_markup=promotions_kb())


@dp.message(Command("ref"))
async def cmd_ref(message: Message, command: CommandObject):
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    if not command.args:
        await message.answer(referral_text(user), reply_markup=referral_kb())
        return

    ok, text, inviter = set_referral(user.user_id, command.args.strip())
    await message.answer(text, reply_markup=profile_kb())
    if ok and inviter:
        await safe_send_message(
            message.bot,
            inviter.user_id,
            f"{format_user_name(user)} указал твой реферальный код. Бонус придёт после оплаты.",
            parse_mode=ParseMode.HTML,
        )


@dp.callback_query(F.data == "profile_back")
async def callback_profile_back(callback: CallbackQuery):
    user = get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(profile_text(user), reply_markup=profile_kb())
    await callback.answer()


@dp.callback_query(F.data == "referral_info")
async def callback_referral_info(callback: CallbackQuery):
    user = get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(referral_text(user), reply_markup=referral_kb())
    await callback.answer()


@dp.callback_query(F.data == "referral_enter")
async def callback_referral_enter(callback: CallbackQuery, state: FSMContext):
    get_or_create_user(callback.from_user.id, callback.from_user.username)
    await state.set_state(ReferralStates.waiting_code)
    await callback.message.answer("Пришли 6-значный код друга:")
    await callback.answer()


@dp.callback_query(F.data == "extend_access")
async def callback_extend_access(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выбери страну, в которой нужно продлить доступ:",
        reply_markup=country_choice_kb_user(),
    )
    await callback.answer()


@dp.callback_query(F.data == "promo_connect")
async def callback_promo_connect(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выбери страну подключения:",
        reply_markup=country_choice_kb_user(),
    )
    await callback.answer()


@dp.callback_query(F.data == "buy_back_countries")
async def callback_buy_back_countries(callback: CallbackQuery):
    await callback.message.edit_text("Выбери страну подключения:", reply_markup=country_choice_kb_user())
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_country_"))
async def callback_buy_country(callback: CallbackQuery):
    country_code = remove_prefix(callback.data, "buy_country_")
    if country_code not in COUNTRY_OPTIONS:
        await callback.answer("Неизвестная страна", show_alert=True)
        return
    await callback.message.edit_text(
        f"Страна: <b>{h(country_label(country_code))}</b>\nТеперь выбери услугу:",
        reply_markup=access_choice_kb_user(country_code),
    )
    await callback.answer()


@dp.callback_query(F.data == "buy_proxy_unavailable")
async def callback_proxy_unavailable(callback: CallbackQuery):
    await callback.answer("Proxy в США пока недоступен. Выбери VPN или Германию.", show_alert=True)


@dp.callback_query(F.data == "buy_bundle_vpn_only")
async def callback_bundle_vpn_only(callback: CallbackQuery):
    await callback.answer("Комплект включает VPN в Германии и США. Proxy в него не входит.", show_alert=True)


@dp.callback_query(F.data.startswith("buy_back_services_"))
async def callback_buy_back_services(callback: CallbackQuery):
    country_code = remove_prefix(callback.data, "buy_back_services_")
    if country_code not in COUNTRY_OPTIONS:
        await callback.answer("Неизвестная страна", show_alert=True)
        return
    await callback.message.edit_text(
        f"Страна: <b>{h(country_label(country_code))}</b>\nВыбери услугу:",
        reply_markup=access_choice_kb_user(country_code),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_svc_"))
async def callback_buy_service(callback: CallbackQuery):
    parts = callback.data.split("_", 3)
    if len(parts) != 4:
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    _, _, country_code, service_code = parts
    if not service_available(country_code, service_code):
        await callback.answer("Эта услуга в выбранной стране пока недоступна.", show_alert=True)
        return
    service = SERVICE_OPTIONS[service_code]
    promo_text = (
        "Комплект: две страны по цене ×1,5"
        if country_code == BUNDLE_COUNTRY_CODE
        else f"−{PROMO_DISCOUNT_PERCENT}%"
    )
    await callback.message.edit_text(
        f"Страна: <b>{h(country_label(country_code))}</b>\n"
        f"Услуга: <b>{h(service['title'])}</b>\n"
        f"Акция: <b>{h(promo_text)}</b>\n\n"
        "Теперь выбери срок:",
        reply_markup=duration_choice_kb_user(country_code, service_code),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_dur_"))
async def callback_buy_duration(callback: CallbackQuery):
    parts = callback.data.split("_", 4)
    if len(parts) != 5:
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    _, _, country_code, service_code, duration_code = parts
    if (
        duration_code not in DURATION_OPTIONS
        or not service_available(country_code, service_code)
    ):
        await callback.answer("Ошибка выбора", show_alert=True)
        return

    service = SERVICE_OPTIONS[service_code]
    duration = DURATION_OPTIONS[duration_code]
    price = price_for(service_code, duration_code, country_code)
    original_price = original_price_for(service_code, duration_code, country_code)
    original_price_label = (
        "Две страны отдельно"
        if country_code == BUNDLE_COUNTRY_CODE
        else "Обычная цена"
    )

    await callback.message.edit_text(
        (
            "<b>Проверь заказ</b>\n\n"
            f"Страна: <b>{h(country_label(country_code))}</b>\n"
            f"Тариф: <b>{h(service['title'])}</b>\n"
            f"Срок: <b>{h(duration['title'])}</b>\n"
            f"{original_price_label}: <s>{original_price} ₽</s>\n"
            f"По акции: <b>{price} ₽</b>\n\n"
            "Оплати по кнопке ниже, вернись в бот и нажми «Я оплатил — отправить чек»."
        ),
        reply_markup=payment_kb(country_code, service_code, duration_code),
        disable_web_page_preview=True,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_receipt_"))
async def callback_buy_receipt(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 4)
    if len(parts) != 5:
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    _, _, country_code, service_code, duration_code = parts
    if (
        duration_code not in DURATION_OPTIONS
        or not service_available(country_code, service_code)
    ):
        await callback.answer("Данные тарифа устарели. Начни подключение заново.", show_alert=True)
        return
    get_or_create_user(callback.from_user.id, callback.from_user.username)
    await state.set_state(PurchaseStates.waiting_receipt)
    await state.update_data(
        country_code=country_code,
        service_code=service_code,
        duration_code=duration_code,
    )
    await callback.message.answer(
        "Пришли чек одним сообщением — фотографией, изображением или PDF.\n\n"
        "На чеке должны быть видны сумма и успешный статус перевода. Отмена: /cancel.",
        reply_markup=cancel_receipt_kb(),
    )
    await callback.answer("Жду чек")


@dp.callback_query(F.data == "buy_cancel_receipt")
async def callback_cancel_receipt(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Отправка чека отменена. Оплаченный чек можно отправить, заново выбрав тариф.")
    await callback.answer("Отменено")


@dp.callback_query(F.data.startswith("order_confirm_"))
async def callback_order_confirm(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    order_id = remove_prefix(callback.data, "order_confirm_")
    order = get_purchase_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if order.status == "approved":
        await callback.answer("Заявка уже подтверждена.", show_alert=True)
        return
    if order.status == "rejected":
        await callback.answer("Заявка уже отклонена.", show_alert=True)
        return
    user = users.get(order.user_id)
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    service = SERVICE_OPTIONS[order.service_code]
    missing_country_codes = missing_vpn_country_codes(user, order.country_code)
    if service["vpn"] and missing_country_codes:
        if order.status in {"pending", "processing"}:
            order = transition_purchase_order(
                order_id,
                {order.status},
                "awaiting_key",
                reviewed_by=callback.from_user.id,
            )
            if not order:
                await callback.answer("Заявку уже взял другой администратор.", show_alert=True)
                return
        elif order.reviewed_by and order.reviewed_by != callback.from_user.id:
            await callback.answer("Ключ уже вводит другой администратор.", show_alert=True)
            return

        await state.set_state(AdminStates.waiting_order_vpn_key)
        await state.update_data(
            order_id=order_id,
            missing_country_codes=missing_country_codes,
        )
        waiting_text = (
            "⏳ Оплата подтверждается: ожидаются VPN ключи."
            if len(missing_country_codes) > 1
            else "⏳ Оплата подтверждается: ожидается VPN ключ."
        )
        await callback.message.edit_text(
            processed_order_text(order, user, waiting_text),
            reply_markup=order_review_kb(order.order_id),
        )
        try:
            await callback.message.answer(
                order_vpn_key_request_text(order, missing_country_codes)
            )
        except Exception:
            transition_purchase_order(order_id, {"awaiting_key"}, "pending")
            await state.clear()
            await callback.answer(
                "Не удалось отправить запрос ключа в админский чат.",
                show_alert=True,
            )
            return
        await callback.answer("Жду VPN ключ")
        return

    try:
        granted_user, referral_bonus, result = await finalize_purchase_order(
            callback.bot,
            order_id,
            callback.from_user.id,
        )
    except Exception as exc:
        logging.exception("Failed to approve purchase order %s", order_id)
        await callback.answer("Не удалось выдать доступ. Заявка оставлена на проверке.", show_alert=True)
        return

    if not granted_user:
        await callback.answer(str(result), show_alert=True)
        return

    bonus_text = " Реферальный бонус начислен 🎁" if referral_bonus else ""
    await callback.message.edit_text(
        processed_order_text(result, granted_user, f"✅ Подтверждено. Доступ выдан.{bonus_text}")
    )
    await callback.answer("Оплата подтверждена, доступ выдан")


@dp.callback_query(F.data.startswith("order_reject_"))
async def callback_order_reject(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    order_id = remove_prefix(callback.data, "order_reject_")
    order = get_purchase_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if order.status == "awaiting_key" and order.reviewed_by not in {None, callback.from_user.id}:
        await callback.answer("Заявку уже обрабатывает другой администратор.", show_alert=True)
        return

    rejected_order = transition_purchase_order(
        order_id,
        {"pending", "awaiting_key"},
        "rejected",
        reviewed_by=callback.from_user.id,
    )
    if not rejected_order:
        await callback.answer("Заявка уже обработана.", show_alert=True)
        return

    user = users.get(rejected_order.user_id)
    if user:
        await safe_send_message(
            callback.bot,
            user.user_id,
            (
                f"Заявка <code>#{h(order_id)}</code> отклонена: не удалось подтвердить оплату по чеку.\n\n"
                f"Если перевод прошёл, напиши @{h(settings.support_username)} и укажи номер заявки."
            ),
            parse_mode=ParseMode.HTML,
        )
        await callback.message.edit_text(
            processed_order_text(rejected_order, user, "❌ Отклонено администратором.")
        )
    else:
        await callback.message.edit_text(f"Заявка <code>#{h(order_id)}</code> отклонена. Пользователь не найден.")
    await callback.answer("Заявка отклонена")


@dp.message(Command("admin"))
@dp.message(F.text == "Админка ⚙️")
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await show_admin_panel(message)


@dp.callback_query(F.data == "adm_panel")
async def callback_admin_panel(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    await show_admin_panel(callback)


@dp.callback_query(F.data == "adm_test_messages")
async def callback_admin_test_messages(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return

    test_order = SimpleNamespace(
        order_id="test0000",
        user_id=callback.from_user.id,
        country_code=BUNDLE_COUNTRY_CODE,
        service_code="vpn",
        duration_code="1m",
        price=price_for("vpn", "1m", BUNDLE_COUNTRY_CODE),
    )
    test_user = SimpleNamespace(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "test_user",
        access_until=utc_now() + timedelta(days=30),
        vpn_key="vpn://TEST-GERMANY",
        vpn_keys={
            "de": "vpn://TEST-GERMANY",
            "us": "vpn://TEST-USA",
        },
    )

    try:
        await callback.bot.send_message(
            callback.from_user.id,
            "🧪 <b>Тест сообщений VPNyasha</b>\n\n"
            "Ниже бот покажет сообщения покупателя и администратора на примере комплекта. "
            "Это предпросмотр: заявка не создаётся, срок не начисляется, тестовые кнопки ничего не меняют.",
        )
        await callback.bot.send_message(
            callback.from_user.id,
            receipt_accepted_text(test_order),
        )
        await callback.bot.send_message(
            callback.from_user.id,
            purchase_order_text(test_order, test_user),
            reply_markup=admin_test_order_kb(),
        )
        await callback.bot.send_message(
            callback.from_user.id,
            order_vpn_key_request_text(test_order, list(BUNDLE_VPN_COUNTRIES)),
        )
        await callback.bot.send_message(
            callback.from_user.id,
            build_access_message(
                test_user,
                BUNDLE_COUNTRY_CODE,
                "vpn",
                "1m",
                vpn_keys=test_user.vpn_keys,
            ),
            reply_markup=download_links_kb(),
            disable_web_page_preview=True,
        )
    except Exception:
        logging.exception("Failed to send admin message preview to %s", callback.from_user.id)
        await callback.answer(
            "Не могу отправить предпросмотр в личку. Открой бота лично, нажми /start и повтори.",
            show_alert=True,
        )
        return

    await callback.answer("Тестовые сообщения отправлены в личку")


@dp.callback_query(F.data.startswith("adm_test_noop_"))
async def callback_admin_test_noop(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    await callback.answer("Это тестовая кнопка — данные не изменены.", show_alert=True)


@dp.callback_query(F.data == "adm_orders")
async def callback_admin_orders(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    text, keyboard = admin_orders_text()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_order_"))
async def callback_admin_order(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    order_id = remove_prefix(callback.data, "adm_order_")
    order = get_purchase_order(order_id)
    if not order or order.status not in {"pending", "awaiting_key", "processing"}:
        await callback.answer("Заявка уже обработана или не найдена.", show_alert=True)
        return
    user = users.get(order.user_id)
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    status_labels = {
        "pending": "⏳ Ожидает решения администратора.",
        "awaiting_key": "⏳ Оплата подтверждается: ожидаются VPN ключи.",
        "processing": "⏳ Выдача была начата; подтверждение можно безопасно повторить.",
    }
    await callback.message.edit_text(
        processed_order_text(order, user, status_labels[order.status]),
        reply_markup=order_review_kb(order.order_id, back_to_orders=True),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_page_"))
async def callback_admin_users(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    page = int(remove_prefix(callback.data, "adm_page_"))
    text, keyboard = admin_users_text(page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "adm_add_user")
async def callback_admin_add_user(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.set_state(AdminStates.waiting_user_lookup)
    await callback.message.answer(
        "Пришли @username или user_id пользователя, которому нужно выдать доступ. "
        "Отмена: /cancel"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_user_"))
async def callback_admin_user(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    target_user_id = int(remove_prefix(callback.data, "adm_user_"))
    user = users.get(target_user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.message.edit_text(user_detail_text(user), reply_markup=admin_user_kb(user.user_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_grant_"))
async def callback_admin_grant(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    target_user_id = int(remove_prefix(callback.data, "adm_grant_"))
    user = users.get(target_user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"В какой стране выдать доступ {format_user_name(user)}?",
        reply_markup=admin_country_kb(user.user_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_country_"))
async def callback_admin_country(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    _, _, user_id_raw, country_code = callback.data.split("_", 3)
    target_user_id = int(user_id_raw)
    user = users.get(target_user_id)
    if not user or country_code not in COUNTRY_OPTIONS:
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    await callback.message.edit_text(
        f"{format_user_name(user)} · {h(country_label(country_code))}\nЧто выдать?",
        reply_markup=admin_service_kb(target_user_id, country_code),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_svc_"))
async def callback_admin_service(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    _, _, user_id_raw, country_code, service_code = callback.data.split("_", 4)
    target_user_id = int(user_id_raw)
    user = users.get(target_user_id)
    if not user or not service_available(country_code, service_code):
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    await callback.message.edit_text(
        f"{format_user_name(user)} · {h(country_label(country_code))} · "
        f"{h(SERVICE_OPTIONS[service_code]['title'])}\nНа какой срок?",
        reply_markup=admin_duration_kb(target_user_id, country_code, service_code),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_dur_"))
async def callback_admin_duration(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    _, _, user_id_raw, country_code, service_code, duration_code = callback.data.split("_", 5)
    target_user_id = int(user_id_raw)
    user = users.get(target_user_id)
    if (
        not user
        or duration_code not in DURATION_OPTIONS
        or not service_available(country_code, service_code)
    ):
        await callback.answer("Ошибка выбора", show_alert=True)
        return

    service = SERVICE_OPTIONS[service_code]
    duration = DURATION_OPTIONS[duration_code]
    missing_country_codes = missing_vpn_country_codes(user, country_code)
    if service["vpn"] and missing_country_codes:
        await state.set_state(AdminStates.waiting_vpn_key)
        await state.update_data(
            target_user_id=target_user_id,
            country_code=country_code,
            service_code=service_code,
            duration_code=duration_code,
            missing_country_codes=missing_country_codes,
        )
        await callback.message.answer(
            manual_vpn_key_request_text(
                user,
                country_code,
                service_code,
                duration_code,
                missing_country_codes,
            )
        )
        await callback.answer("Жду ключ")
        return

    granted_user, referral_bonus = await grant_and_notify(
        callback.bot,
        target_user_id,
        country_code,
        service_code,
        duration_code,
        extension_only=bool(service["vpn"] and not missing_country_codes),
    )
    if not granted_user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    key_text = ""
    if service["vpn"]:
        key_text = (
            "\nVPN ключи оставлены прежними."
            if country_code == BUNDLE_COUNTRY_CODE
            else "\nVPN ключ оставлен прежним."
        )
    bonus_text = "\nРеферальный бонус начислен 🎁" if referral_bonus else ""
    await callback.message.edit_text(
        f"{format_user_name(granted_user)} получил доступ до "
        f"<b>{format_dt(granted_user.access_until)}</b> ✅{key_text}{bonus_text}",
        reply_markup=admin_user_kb(granted_user.user_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_disable_"))
async def callback_admin_disable(callback: CallbackQuery):
    if not await ensure_admin_callback(callback):
        return
    target_user_id = int(remove_prefix(callback.data, "adm_disable_"))
    user = disable_access(target_user_id)
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await safe_send_message(
        callback.bot,
        user.user_id,
        "Доступ отключён. Если это ошибка, напиши администратору.",
    )
    await callback.message.edit_text(user_detail_text(user), reply_markup=admin_user_kb(user.user_id))
    await callback.answer("Отключено")


@dp.callback_query(F.data == "adm_news")
async def callback_admin_news(callback: CallbackQuery, state: FSMContext):
    if not await ensure_admin_callback(callback):
        return
    await state.set_state(AdminStates.waiting_news)
    await callback.message.answer(
        "Пришли текст новости или фото с подписью. Я скопирую сообщение всем пользователям: "
        "ссылки, форматирование и подпись сохранятся, автор не будет показан. Отмена: /cancel"
    )
    await callback.answer()


@dp.message(Command("add_user"))
async def cmd_add_user(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    if not command.args:
        await message.answer("Использование: /add_user @username или /add_user user_id")
        return

    target_user = find_user_by_admin_input(command.args)
    if not target_user:
        await message.answer("Пользователь не найден. Пусть сначала напишет /start.")
        return

    await message.answer(
        f"Выберите страну для {format_user_name(target_user)}:",
        reply_markup=admin_country_kb(target_user.user_id),
    )


@dp.message(Command("refresh_proxy_url"))
async def refresh_proxy_cmd(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    try:
        result = subprocess.check_output(
            ["bash", str(BASE_DIR / "rotate-mtproxy-secret.sh")],
            cwd=str(BASE_DIR),
            stderr=subprocess.STDOUT,
        ).decode()
        proxy_url = next((line.strip() for line in result.splitlines() if line.startswith("tg://proxy")), None)
        if not proxy_url:
            await message.answer("Не удалось получить ссылку.")
            return

        PROXY_FILE.write_text(proxy_url, encoding="utf-8")
        await message.answer(f"Прокси обновлён:\n{h(proxy_url)}")

        now = utc_now()
        notified = 0
        for user in users.values():
            if user.has_proxy and user.access_until and normalize_dt(user.access_until) > now:
                instr = (
                    "📢 Прокси сервер обновлён:\n\n"
                    f"Используйте актуальный прокси: <code>{h(proxy_url)}</code>\n\n"
                    "Если старый прокси не работает, удалите его в настройках Telegram."
                )
                ok = await safe_send_message(message.bot, user.user_id, instr, parse_mode=ParseMode.HTML)
                if ok:
                    notified += 1
        await message.answer(f"Рассылка активным Proxy-пользователям завершена: {notified}")

    except Exception as exc:
        await message.answer(f"Ошибка:\n<code>{h(exc)}</code>")


@dp.message(Command("proxy"))
async def send_proxy(message: Message):
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    now = utc_now()
    has_proxy_access = bool(
        user.has_proxy
        and user.access_until
        and normalize_dt(user.access_until) + timedelta(days=GRACE_PERIOD_DAYS) > now
    )
    if not has_proxy_access:
        await message.answer(
            "Proxy доступен после подтверждённой оплаты тарифа 🇩🇪 Германия → Proxy.\n\n"
            "Нажми «Подключиться к VPN ⚡» и отправь чек через бот.",
            reply_markup=main_menu_kb(message.from_user.id),
        )
        return
    proxy_url = read_proxy_url()
    if not proxy_url:
        await message.answer("Прокси ещё не создан.")
        return
    instr = (
        "Мини-инструкция подключения:\n"
        "1. Открой настройки Telegram.\n"
        "2. Перейди в Данные и память → Настройки прокси.\n"
        f"3. Используй ссылку: <code>{h(proxy_url)}</code>"
    )
    await message.answer(instr, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def access_watcher(bot: Bot):
    while True:
        now = utc_now()
        removed_empty_users = cleanup_empty_users()
        if removed_empty_users:
            await safe_send_message(
                bot,
                settings.log_chat_id,
                f"🧹 Удалены пустые аккаунты: {len(removed_empty_users)}",
            )

        for user in list(users.values()):
            if not user.access_until or not (user.has_vpn or user.has_proxy):
                continue

            access_until = normalize_dt(user.access_until)
            grace_until = access_until + timedelta(days=GRACE_PERIOD_DAYS)

            if access_until <= now < grace_until and not user.expiration_notice_sent_at:
                await safe_send_message(
                    bot,
                    user.user_id,
                    (
                        "Срок подписки закончился.\n\n"
                        f"Доступ ещё будет работать до <b>{format_dt(grace_until)}</b>. "
                        "Если не продлить подписку в течение трёх дней, ключ отключат.\n\n"
                        "Для продления открой «Мой профиль 😎» → «Продлить доступ», "
                        "выбери страну и тариф, затем отправь чек через бот."
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                await safe_send_message(
                    bot,
                    settings.log_chat_id,
                    f"⏳ Подписка истекла у {format_user_name(user)}. Grace до <b>{format_dt(grace_until)}</b>.",
                    parse_mode=ParseMode.HTML,
                )
                mark_expiration_notice_sent(user.user_id)

            if grace_until <= now:
                disabled_user = disable_access(user.user_id)
                if not disabled_user:
                    continue
                await safe_send_message(
                    bot,
                    disabled_user.user_id,
                    (
                        "Доступ отключён, потому что подписка не была продлена в течение трёх дней.\n\n"
                        "Для восстановления нажми «Подключиться к VPN ⚡», выбери страну и тариф, "
                        "затем отправь чек через бот."
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                key_lines = [
                    f"{h(country_label(country_code))}: <code>{h(key)}</code>"
                    for country_code, key in disabled_user.vpn_keys.items()
                ]
                if not key_lines and disabled_user.vpn_key:
                    key_lines.append(f"🇩🇪 Германия: <code>{h(disabled_user.vpn_key)}</code>")
                joined_keys = "\n".join(key_lines)
                key_text = f"\nКлючи:\n{joined_keys}" if key_lines else ""
                await safe_send_message(
                    bot,
                    settings.log_chat_id,
                    (
                        f"⛔ Доступ отключён в базе для {format_user_name(disabled_user)}.\n"
                        f"Проверь VPN/Proxy на сервере вручную.{key_text}"
                    ),
                    parse_mode=ParseMode.HTML,
                )

        await asyncio.sleep(3600)


async def main():
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(access_watcher(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
