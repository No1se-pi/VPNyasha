from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import os
import random
import secrets
import threading


BASE_DIR = Path(__file__).resolve().parent
USERS_FILE_RAW = os.getenv("USERS_FILE")
FILE_PATH = Path(USERS_FILE_RAW) if USERS_FILE_RAW else BASE_DIR / "users.json"
if not FILE_PATH.is_absolute():
    FILE_PATH = BASE_DIR / FILE_PATH

ORDERS_FILE_RAW = os.getenv("ORDERS_FILE")
ORDERS_FILE_PATH = Path(ORDERS_FILE_RAW) if ORDERS_FILE_RAW else BASE_DIR / "orders.json"
if not ORDERS_FILE_PATH.is_absolute():
    ORDERS_FILE_PATH = BASE_DIR / ORDERS_FILE_PATH

LOCK = threading.RLock()
REFERRAL_BONUS_DAYS = 14
EMPTY_ACCOUNT_TTL_DAYS = 30


@dataclass
class UserData:
    user_id: int
    username: str = ""
    nickname: str = ""
    access_until: Optional[datetime] = None
    has_vpn: bool = False
    has_proxy: bool = False
    vpn_key: str = ""
    vpn_keys: Dict[str, str] = field(default_factory=dict)
    last_country: str = ""
    last_plan: str = ""
    last_payment_at: Optional[datetime] = None
    expiration_notice_sent_at: Optional[datetime] = None
    disabled_at: Optional[datetime] = None
    referral_code: str = ""
    referred_by: Optional[int] = None
    referral_rewarded_at: Optional[datetime] = None
    processed_order_ids: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class PurchaseOrder:
    order_id: str
    user_id: int
    country_code: str
    service_code: str
    duration_code: str
    price: int
    receipt_chat_id: int
    receipt_message_id: int
    status: str = "pending"
    admin_message_id: Optional[int] = None
    reviewed_by: Optional[int] = None
    created_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None


users: Dict[int, UserData] = {}
purchase_orders: Dict[str, PurchaseOrder] = {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_dt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return normalize_dt(datetime.fromisoformat(value))
    except ValueError:
        return None


def _format_dt(value: Optional[datetime]) -> Optional[str]:
    return normalize_dt(value).isoformat() if value else None


def _generate_referral_code(used_codes: Optional[set[str]] = None) -> str:
    if used_codes is None:
        used_codes = {u.referral_code for u in users.values() if u.referral_code}
    while True:
        code = f"{random.randint(0, 999999):06d}"
        if code not in used_codes:
            return code


def _user_to_dict(user: UserData) -> dict:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "nickname": user.nickname,
        "access_until": _format_dt(user.access_until),
        "has_vpn": user.has_vpn,
        "has_proxy": user.has_proxy,
        "vpn_key": user.vpn_key,
        "vpn_keys": user.vpn_keys,
        "last_country": user.last_country,
        "last_plan": user.last_plan,
        "last_payment_at": _format_dt(user.last_payment_at),
        "expiration_notice_sent_at": _format_dt(user.expiration_notice_sent_at),
        "disabled_at": _format_dt(user.disabled_at),
        "referral_code": user.referral_code,
        "referred_by": user.referred_by,
        "referral_rewarded_at": _format_dt(user.referral_rewarded_at),
        "processed_order_ids": user.processed_order_ids,
        "created_at": _format_dt(user.created_at),
        "updated_at": _format_dt(user.updated_at),
    }


def save_users():
    with LOCK:
        FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {str(uid): _user_to_dict(user) for uid, user in users.items()}
        tmp_path = FILE_PATH.with_suffix(FILE_PATH.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, FILE_PATH)


def load_users():
    global users
    with LOCK:
        users.clear()
        if not FILE_PATH.exists():
            return

        with FILE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)

        now = utc_now()
        changed = False
        used_codes: set[str] = set()
        for uid, raw in data.items():
            user_id = int(raw.get("user_id") or uid)
            referral_code = str(raw.get("referral_code") or "").strip()
            if not referral_code or referral_code in used_codes:
                referral_code = _generate_referral_code(used_codes)
                changed = True
            used_codes.add(referral_code)

            username = raw.get("username") or ""
            nickname = raw.get("nickname") or username
            vpn_key = raw.get("vpn_key") or ""
            raw_vpn_keys = raw.get("vpn_keys")
            vpn_keys = {
                str(country_code): str(key)
                for country_code, key in raw_vpn_keys.items()
                if key
            } if isinstance(raw_vpn_keys, dict) else {}
            if vpn_key and not vpn_keys:
                vpn_keys["de"] = vpn_key
                changed = True
            last_country = raw.get("last_country") or ("de" if vpn_key else "")

            user = UserData(
                user_id=user_id,
                username=username,
                nickname=nickname,
                access_until=_parse_dt(raw.get("access_until")),
                has_vpn=bool(raw.get("has_vpn", False)),
                has_proxy=bool(raw.get("has_proxy", False)),
                vpn_key=vpn_key,
                vpn_keys=vpn_keys,
                last_country=last_country,
                last_plan=raw.get("last_plan") or "",
                last_payment_at=_parse_dt(raw.get("last_payment_at")),
                expiration_notice_sent_at=_parse_dt(raw.get("expiration_notice_sent_at")),
                disabled_at=_parse_dt(raw.get("disabled_at")),
                referral_code=referral_code,
                referred_by=raw.get("referred_by"),
                referral_rewarded_at=_parse_dt(raw.get("referral_rewarded_at")),
                processed_order_ids=[
                    str(order_id)
                    for order_id in (raw.get("processed_order_ids") or [])
                ],
                created_at=_parse_dt(raw.get("created_at")) or now,
                updated_at=_parse_dt(raw.get("updated_at")),
            )
            users[user_id] = user
            if not raw.get("created_at"):
                changed = True

        if changed:
            save_users()


def get_or_create_user(user_id: int, username: Optional[str]) -> UserData:
    with LOCK:
        now = utc_now()
        username = username or ""
        if user_id not in users:
            user = UserData(
                user_id=user_id,
                username=username,
                nickname=username,
                referral_code=_generate_referral_code(),
                created_at=now,
                updated_at=now,
            )
            users[user_id] = user
            save_users()
            return user

        user = users[user_id]
        changed = False
        if username and user.username != username:
            user.username = username
            changed = True
        if not user.nickname and username:
            user.nickname = username
            changed = True
        if not user.referral_code:
            user.referral_code = _generate_referral_code()
            changed = True
        if changed:
            user.updated_at = now
            save_users()
        return user


def set_nickname(user_id: int, nickname: str):
    with LOCK:
        if user_id in users:
            users[user_id].nickname = nickname
            users[user_id].updated_at = utc_now()
            save_users()


def _extend_user_days(user: UserData, days: int, now: Optional[datetime] = None):
    now = now or utc_now()
    base = now
    if user.access_until and normalize_dt(user.access_until) > now:
        base = normalize_dt(user.access_until)
    user.access_until = base + timedelta(days=days)
    user.expiration_notice_sent_at = None
    user.disabled_at = None
    user.updated_at = now


def grant_access(
    user_id: int,
    days: int,
    vpn: bool = False,
    proxy: bool = False,
    vpn_key: str = "",
    country_code: str = "de",
    plan_label: str = "",
    order_id: str = "",
) -> Optional[UserData]:
    with LOCK:
        user = users.get(user_id)
        if not user:
            return None
        if order_id and order_id in user.processed_order_ids:
            return user
        now = utc_now()
        user.has_vpn = vpn
        user.has_proxy = proxy
        if vpn_key:
            user.vpn_keys[country_code] = vpn_key
            if country_code == "de":
                user.vpn_key = vpn_key
        user.last_country = country_code
        user.last_plan = plan_label
        user.last_payment_at = now
        _extend_user_days(user, days, now)
        if order_id:
            user.processed_order_ids.append(order_id)
        save_users()
        return user


def get_vpn_key(user: UserData, country_code: str) -> str:
    key = user.vpn_keys.get(country_code, "")
    if not key and country_code == "de":
        key = user.vpn_key
    return key


def _order_to_dict(order: PurchaseOrder) -> dict:
    return {
        "order_id": order.order_id,
        "user_id": order.user_id,
        "country_code": order.country_code,
        "service_code": order.service_code,
        "duration_code": order.duration_code,
        "price": order.price,
        "receipt_chat_id": order.receipt_chat_id,
        "receipt_message_id": order.receipt_message_id,
        "status": order.status,
        "admin_message_id": order.admin_message_id,
        "reviewed_by": order.reviewed_by,
        "created_at": _format_dt(order.created_at),
        "reviewed_at": _format_dt(order.reviewed_at),
    }


def save_purchase_orders():
    with LOCK:
        ORDERS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            order_id: _order_to_dict(order)
            for order_id, order in purchase_orders.items()
        }
        tmp_path = ORDERS_FILE_PATH.with_suffix(ORDERS_FILE_PATH.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, ORDERS_FILE_PATH)


def load_purchase_orders():
    with LOCK:
        purchase_orders.clear()
        if not ORDERS_FILE_PATH.exists():
            return

        with ORDERS_FILE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)

        for order_id, raw in data.items():
            order = PurchaseOrder(
                order_id=str(raw.get("order_id") or order_id),
                user_id=int(raw["user_id"]),
                country_code=str(raw["country_code"]),
                service_code=str(raw["service_code"]),
                duration_code=str(raw["duration_code"]),
                price=int(raw["price"]),
                receipt_chat_id=int(raw["receipt_chat_id"]),
                receipt_message_id=int(raw["receipt_message_id"]),
                status=str(raw.get("status") or "pending"),
                admin_message_id=raw.get("admin_message_id"),
                reviewed_by=raw.get("reviewed_by"),
                created_at=_parse_dt(raw.get("created_at")),
                reviewed_at=_parse_dt(raw.get("reviewed_at")),
            )
            purchase_orders[order.order_id] = order


def create_purchase_order(
    user_id: int,
    country_code: str,
    service_code: str,
    duration_code: str,
    price: int,
    receipt_chat_id: int,
    receipt_message_id: int,
) -> PurchaseOrder:
    with LOCK:
        while True:
            order_id = secrets.token_hex(4)
            if order_id not in purchase_orders:
                break
        order = PurchaseOrder(
            order_id=order_id,
            user_id=user_id,
            country_code=country_code,
            service_code=service_code,
            duration_code=duration_code,
            price=price,
            receipt_chat_id=receipt_chat_id,
            receipt_message_id=receipt_message_id,
            created_at=utc_now(),
        )
        purchase_orders[order_id] = order
        save_purchase_orders()
        return order


def get_purchase_order(order_id: str) -> Optional[PurchaseOrder]:
    return purchase_orders.get(order_id)


def set_order_admin_message(order_id: str, message_id: int) -> Optional[PurchaseOrder]:
    with LOCK:
        order = purchase_orders.get(order_id)
        if not order:
            return None
        order.admin_message_id = message_id
        save_purchase_orders()
        return order


def transition_purchase_order(
    order_id: str,
    expected_statuses: set[str],
    new_status: str,
    reviewed_by: Optional[int] = None,
) -> Optional[PurchaseOrder]:
    with LOCK:
        order = purchase_orders.get(order_id)
        if not order or order.status not in expected_statuses:
            return None
        order.status = new_status
        if reviewed_by is not None:
            order.reviewed_by = reviewed_by
        if new_status in {"approved", "rejected"}:
            order.reviewed_at = utc_now()
        save_purchase_orders()
        return order


def get_open_purchase_orders() -> List[PurchaseOrder]:
    return [
        order
        for order in purchase_orders.values()
        if order.status in {"pending", "awaiting_key", "processing"}
    ]


def extend_access_days(user_id: int, days: int) -> Optional[UserData]:
    with LOCK:
        user = users.get(user_id)
        if not user:
            return None
        if not (user.has_vpn or user.has_proxy):
            user.has_vpn = True
        _extend_user_days(user, days)
        save_users()
        return user


def disable_access(user_id: int) -> Optional[UserData]:
    with LOCK:
        user = users.get(user_id)
        if not user:
            return None
        user.has_vpn = False
        user.has_proxy = False
        user.disabled_at = utc_now()
        user.updated_at = user.disabled_at
        save_users()
        return user


def mark_expiration_notice_sent(user_id: int) -> Optional[UserData]:
    with LOCK:
        user = users.get(user_id)
        if not user:
            return None
        user.expiration_notice_sent_at = utc_now()
        user.updated_at = user.expiration_notice_sent_at
        save_users()
        return user


def set_referral(user_id: int, referral_code: str) -> Tuple[bool, str, Optional[UserData]]:
    with LOCK:
        user = users.get(user_id)
        if not user:
            return False, "Пользователь не найден. Нажми /start и попробуй ещё раз.", None
        if user.referred_by:
            inviter = users.get(user.referred_by)
            inviter_text = f"@{inviter.username}" if inviter and inviter.username else "другого пользователя"
            return False, f"Ты уже указал код {inviter_text}.", inviter

        normalized_code = referral_code.strip()
        if not normalized_code.isdigit() or len(normalized_code) != 6:
            return False, "Реферальный код должен состоять из 6 цифр.", None

        inviter = next((u for u in users.values() if u.referral_code == normalized_code), None)
        if not inviter:
            return False, "Код не найден. Проверь 6 цифр и попробуй ещё раз.", None
        if inviter.user_id == user.user_id:
            return False, "Свой собственный код использовать нельзя.", inviter

        user.referred_by = inviter.user_id
        user.updated_at = utc_now()
        save_users()
        return True, "Код принят. Бонус другу начислится после твоей первой оплаты.", inviter


def apply_referral_bonus(paid_user_id: int) -> Optional[Tuple[UserData, UserData]]:
    with LOCK:
        paid_user = users.get(paid_user_id)
        if not paid_user or not paid_user.referred_by or paid_user.referral_rewarded_at:
            return None

        inviter = users.get(paid_user.referred_by)
        if not inviter:
            return None

        now = utc_now()
        if not (inviter.has_vpn or inviter.has_proxy):
            inviter.has_vpn = True
        _extend_user_days(inviter, REFERRAL_BONUS_DAYS, now)
        paid_user.referral_rewarded_at = now
        paid_user.updated_at = now
        save_users()
        return inviter, paid_user


def get_referrals_for(user_id: int) -> List[UserData]:
    return [user for user in users.values() if user.referred_by == user_id]


def get_all_users() -> List[UserData]:
    return list(users.values())


def get_active_proxy_users() -> List[UserData]:
    now = utc_now()
    return [
        user
        for user in users.values()
        if user.has_proxy and user.access_until and normalize_dt(user.access_until) > now
    ]


def is_empty_user(user: UserData) -> bool:
    has_referrals = any(other.referred_by == user.user_id for other in users.values())
    return (
        not user.username
        and not user.nickname
        and not user.access_until
        and not user.has_vpn
        and not user.has_proxy
        and not user.vpn_key
        and not user.last_plan
        and not user.last_payment_at
        and not user.referred_by
        and not user.referral_rewarded_at
        and not has_referrals
    )


def cleanup_empty_users(ttl_days: int = EMPTY_ACCOUNT_TTL_DAYS) -> List[UserData]:
    with LOCK:
        now = utc_now()
        removed: List[UserData] = []
        cutoff = now - timedelta(days=ttl_days)

        for user_id, user in list(users.items()):
            created_at = normalize_dt(user.created_at) if user.created_at else now
            if is_empty_user(user) and created_at <= cutoff:
                removed.append(user)
                del users[user_id]

        if removed:
            save_users()
        return removed
