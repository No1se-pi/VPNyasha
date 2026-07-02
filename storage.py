from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import os
import random
import threading


BASE_DIR = Path(__file__).resolve().parent
USERS_FILE_RAW = os.getenv("USERS_FILE")
FILE_PATH = Path(USERS_FILE_RAW) if USERS_FILE_RAW else BASE_DIR / "users.json"
if not FILE_PATH.is_absolute():
    FILE_PATH = BASE_DIR / FILE_PATH

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
    last_plan: str = ""
    last_payment_at: Optional[datetime] = None
    expiration_notice_sent_at: Optional[datetime] = None
    disabled_at: Optional[datetime] = None
    referral_code: str = ""
    referred_by: Optional[int] = None
    referral_rewarded_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


users: Dict[int, UserData] = {}


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
        "last_plan": user.last_plan,
        "last_payment_at": _format_dt(user.last_payment_at),
        "expiration_notice_sent_at": _format_dt(user.expiration_notice_sent_at),
        "disabled_at": _format_dt(user.disabled_at),
        "referral_code": user.referral_code,
        "referred_by": user.referred_by,
        "referral_rewarded_at": _format_dt(user.referral_rewarded_at),
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

            user = UserData(
                user_id=user_id,
                username=username,
                nickname=nickname,
                access_until=_parse_dt(raw.get("access_until")),
                has_vpn=bool(raw.get("has_vpn", False)),
                has_proxy=bool(raw.get("has_proxy", False)),
                vpn_key=raw.get("vpn_key") or "",
                last_plan=raw.get("last_plan") or "",
                last_payment_at=_parse_dt(raw.get("last_payment_at")),
                expiration_notice_sent_at=_parse_dt(raw.get("expiration_notice_sent_at")),
                disabled_at=_parse_dt(raw.get("disabled_at")),
                referral_code=referral_code,
                referred_by=raw.get("referred_by"),
                referral_rewarded_at=_parse_dt(raw.get("referral_rewarded_at")),
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
    plan_label: str = "",
) -> Optional[UserData]:
    with LOCK:
        user = users.get(user_id)
        if not user:
            return None
        now = utc_now()
        user.has_vpn = vpn
        user.has_proxy = proxy
        if vpn_key:
            user.vpn_key = vpn_key
        user.last_plan = plan_label
        user.last_payment_at = now
        _extend_user_days(user, days, now)
        save_users()
        return user


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
