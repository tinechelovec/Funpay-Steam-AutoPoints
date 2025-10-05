import os
import sys
import logging
import re
import requests
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

from FunPayAPI import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent

load_dotenv()
FUNPAY_AUTH_TOKEN = os.getenv("FUNPAY_AUTH_TOKEN")
BSP_API_KEY = os.getenv("BSP_API_KEY")
CATEGORY_ID = int(os.getenv("CATEGORY_ID", "714"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "300"))
MIN_POINTS = int(os.getenv("MIN_POINTS", "100"))
DEACTIVATE_CATEGORY_ID = int(os.getenv("DEACTIVATE_CATEGORY_ID", str(CATEGORY_ID)))
LOG_FILE = os.getenv("LOG_FILE", "log.txt")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))

def _env_bool_raw(name: str):
    return os.getenv(name)

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y")

AUTO_REFUND_RAW = _env_bool_raw("AUTO_REFUND")
AUTO_DEACTIVATE_RAW = _env_bool_raw("AUTO_DEACTIVATE")

AUTO_REFUND = _env_bool("AUTO_REFUND", True)
AUTO_DEACTIVATE = _env_bool("AUTO_DEACTIVATE", True)

BSP_MIN_BALANCE_RAW = os.getenv("BSP_MIN_BALANCE")
try:
    BSP_MIN_BALANCE = float(BSP_MIN_BALANCE_RAW) if BSP_MIN_BALANCE_RAW is not None else 5.0
except Exception:
    BSP_MIN_BALANCE = 5.0


CREATOR_NAME = os.getenv("CREATOR_NAME", "@tinechelovec").strip()
CREATOR_URL = os.getenv("CREATOR_URL", "https://t.me/tinechelovec").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/by_thc").strip()
GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/tinechelovec/Funpay-Steam-AutoPoints").strip()
BANNER_NOTE = os.getenv(
    "BANNER_NOTE",
    "Бот бесплатный и с открытым исходным кодом. Автор НЕ продаёт бот; "
    "любая платная продажа — инициатива третьих лиц."
).strip()

try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init(autoreset=True)
except Exception:
    class _Dummy: RESET_ALL = ""
    class _Fore(_Dummy):
        RED = GREEN = YELLOW = CYAN = MAGENTA = BLUE = WHITE = ""
    class _Style(_Dummy):
        BRIGHT = NORMAL = ""
    Fore, Style = _Fore(), _Style()

class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: Fore.BLUE,
        logging.INFO: Fore.CYAN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA + Style.BRIGHT,
    }
    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, "")
        message = super().format(record)
        return f"{color}{message}{Style.RESET_ALL}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d | %(message)s"
)

file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8", delay=False)
file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s:%(lineno)d | %(message)s")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(file_formatter)
logging.getLogger().addHandler(file_handler)

for h in logging.getLogger().handlers:
    try:
        if isinstance(h, logging.FileHandler):
            continue
        fmt = h.formatter._fmt if hasattr(h, "formatter") and h.formatter else "%(message)s"
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (sys.stdout, sys.stderr):
            h.setFormatter(ColorFormatter(fmt))
    except Exception:
        pass

logger = logging.getLogger("SteamPointsBot")

def _log_banner():
    border = "═" * 78
    logger.info(Style.BRIGHT + Fore.WHITE + border)
    logger.info(Style.BRIGHT + Fore.CYAN  + "SteamPointsBot — информация о проекте")
    logger.info(Style.BRIGHT + Fore.WHITE + border)

    if CREATOR_NAME:
        line = f"Создатель: {CREATOR_NAME}"
        if CREATOR_URL:
            line += f"  |  Контакт: {CREATOR_URL}"
        logger.info(Fore.MAGENTA + line)
    elif CREATOR_URL:
        logger.info(Fore.MAGENTA + f"Контакт автора: {CREATOR_URL}")

    if CHANNEL_URL:
        logger.info(Fore.YELLOW + f"Канал с ботами/плагинами: {CHANNEL_URL}")
    if GITHUB_URL:
        logger.info(Fore.GREEN +  f"GitHub проекта: {GITHUB_URL}")

    logger.info(Fore.RED + Style.BRIGHT + "Дисклеймер: " + Fore.RED + BANNER_NOTE)
    logger.info(Style.BRIGHT + Fore.WHITE + border)

BSP_BASE = "https://api.buysteampoints.com"


STATE_BY_CHAT: dict[int, dict] = {}
USER_TO_CHATS: dict[int, set] = {}

RE_STEAM_LINK = re.compile(
    r"^https?://(www\.)?steamcommunity\.com/(id|profiles)/[A-Za-z0-9_./-]+$",
    flags=re.IGNORECASE
)

EXECUTOR = ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS))

def _bind_state(state: dict):
    chat_id = state["chat_id"]
    buyer_id = state["buyer_id"]
    STATE_BY_CHAT[chat_id] = state
    USER_TO_CHATS.setdefault(buyer_id, set()).add(chat_id)
    logger.debug(Fore.BLUE + f"[STATE] bind chat_id={chat_id}, buyer_id={buyer_id}. total_chats_for_user={len(USER_TO_CHATS[buyer_id])}")

def _get_state(chat_id: int | None, user_id: int | None) -> dict | None:
    if chat_id and chat_id in STATE_BY_CHAT:
        return STATE_BY_CHAT[chat_id]

    if user_id and user_id in USER_TO_CHATS and len(USER_TO_CHATS[user_id]) == 1:
        only_chat_id = next(iter(USER_TO_CHATS[user_id]))
        return STATE_BY_CHAT.get(only_chat_id)
    return None

def _pop_state_by_chat(chat_id: int):
    st = STATE_BY_CHAT.pop(chat_id, None)
    if st:
        buyer_id = st.get("buyer_id")
        if buyer_id in USER_TO_CHATS:
            USER_TO_CHATS[buyer_id].discard(chat_id)
            if not USER_TO_CHATS[buyer_id]:
                USER_TO_CHATS.pop(buyer_id, None)
        logger.debug(Fore.BLUE + f"[STATE] pop chat_id={chat_id}, buyer_id={buyer_id}")

def _parse_fixed_lots_env(s: str) -> dict[str, int]:
    mp = {}
    if not s:
        return mp
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        try:
            mp[str(k).strip()] = int(str(v).strip())
        except Exception:
            continue
    return mp

FIXED_LOT_BY_ID = _parse_fixed_lots_env(os.getenv("FIXED_LOT_BY_ID", ""))
ALLOW_TITLE_DETECTION = _env_bool("ALLOW_TITLE_DETECTION", True)

RE_PTS_IN_TITLE = re.compile(r"(?<!\d)(\d{3,7})\s*(?:очк|очков|points)\b", re.IGNORECASE)
RE_FROM_IN_TITLE = re.compile(r"\bот\s*\d+", re.IGNORECASE)
RE_ANY_NUMBER = re.compile(r"(?<!\d)(\d{3,7})(?!\d)")

def _get_lot_id(order) -> str | None:
    candidates = [
        getattr(order, "lot_id", None),
        getattr(order, "lotId", None),
        getattr(getattr(order, "lot", None), "id", None),
        getattr(getattr(order, "good", None), "id", None),
        getattr(getattr(order, "item", None), "id", None),
    ]
    for c in candidates:
        if c is None:
            continue
        try:
            return str(c)
        except Exception:
            return None
    return None

def _detect_fixed_unit_points(order) -> tuple[int | None, str]:
    lot_id = _get_lot_id(order)
    title = (getattr(order, "title", "") or "").strip()

    if lot_id and lot_id in FIXED_LOT_BY_ID:
        try:
            val = int(FIXED_LOT_BY_ID[lot_id])
            logger.info(Fore.MAGENTA + f"✅ Фикс-лот определён по ID: 1 шт. = {val} очков (lot_id={lot_id}).")
            return val, f"lot_id:{lot_id}"
        except Exception:
            pass

    if not ALLOW_TITLE_DETECTION:
        return None, "not_fixed"

    if RE_FROM_IN_TITLE.search(title):
        logger.info(Fore.BLUE + f"ℹ️ Режим «от N»: заголовок содержит 'от ...'. Покупатель сам задаёт количество. Заголовок: '{title}'")
        return None, "title_has_ot"

    m = RE_PTS_IN_TITLE.search(title)
    if m:
        try:
            val = int(m.group(1))
            if val >= MIN_POINTS and val % 100 == 0:
                logger.info(Fore.MAGENTA + f"✅ Фикс-лот по заголовку: 1 шт. = {val} очков. Заголовок: '{title}'")
                return val, "title_regex"
        except Exception:
            pass

    numbers = []
    try:
        numbers = [int(x) for x in RE_ANY_NUMBER.findall(title)]
    except Exception:
        numbers = []

    if numbers:
        candidates = [n for n in numbers if n >= MIN_POINTS and n % 100 == 0]
        if candidates:
            val = max(candidates)
            logger.info(
                Fore.MAGENTA
                + f"✅ Фикс-лот по числу в названии: 1 шт. = {val} очков | найденные: {numbers} | подходящие: {candidates} | заголовок: '{title}'"
            )
            return val, "title_digits"
        else:
            logger.info(
                Fore.BLUE
                + f"ℹ️ В заголовке найдены числа, но они не подходят (минимум {MIN_POINTS}, кратно 100). "
                  f"Найденные: {numbers} | заголовок: '{title}'"
            )

    return None, "not_fixed"

def get_points_strict(order) -> tuple[int | None, str]:
    buyer_params = getattr(order, "buyer_params", {}) or {}
    for k, v in buyer_params.items():
        try:
            n = int(str(v).strip().replace(" ", ""))
            if n > 0:
                logger.info(Fore.CYAN + f"ℹ️ Количество взято из параметров покупателя: {n} (buyer_params:{k})")
                return n, f"buyer_params:{k}"
        except Exception:
            continue

    amt = getattr(order, "amount", None)
    try:
        amt = int(amt) if amt is not None else None
    except Exception:
        amt = None
    if amt and amt >= 1:
        logger.info(Fore.CYAN + f"ℹ️ Количество определено по числу штук: {amt} (amount)")
        return amt, "amount"

    return None, "not_found"

def get_points(order) -> tuple[int | None, str]:

    unit_points, src = _detect_fixed_unit_points(order)
    if unit_points:
        units = getattr(order, "amount", None)
        try:
            units = int(units) if units is not None else 1
        except Exception:
            units = 1
        if units < 1:
            units = 1
        total = unit_points * units
        logger.info(
            Fore.MAGENTA
            + f"🧮 Расчёт фикс-лота: {unit_points} очков × {units} шт. = {total} очков (источник: {src})"
        )
        return total, f"fixed:{src}:{unit_points}x{units}"

    return get_points_strict(order)

def bsp_create_order(points: int, steam_link: str):
    payload = {
        "api_key": BSP_API_KEY,
        "puan": int(points),
        "steam_link": steam_link.strip()
    }
    try:
        r = requests.post(
            f"{BSP_BASE}/api/buy",
            json=payload,
            timeout=REQUEST_TIMEOUT,
            headers={"content-type": "application/json"}
        )
        data = {}
        try:
            data = r.json()
        except Exception:
            pass
        ok = (r.status_code == 200) and bool(data.get("success"))
        logger.info(
            (Fore.GREEN if ok else Fore.RED)
            + f"[BSP] Создание заказа: HTTP {r.status_code} | {str(data)[:300]}"
        )
        return ok, data, r
    except Exception as e:
        logger.error(Fore.RED + f"[BSP] Ошибка HTTP при создании заказа: {e}")
        return False, {"error": "HTTP error"}, None

def bsp_check_balance() -> float | None:
    endpoints = [
        ("GET", f"{BSP_BASE}/api/balance", {"api_key": BSP_API_KEY}, None),
        ("POST", f"{BSP_BASE}/api/balance", None, {"api_key": BSP_API_KEY}),
        ("POST", f"{BSP_BASE}/api/wallet", None, {"api_key": BSP_API_KEY}),
        ("GET", f"{BSP_BASE}/api/info", {"api_key": BSP_API_KEY}, None),
    ]
    for method, url, params, json_body in endpoints:
        try:
            r = requests.request(method, url, params=params, json=json_body, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                continue
            data = r.json()
            if isinstance(data, dict):
                for k in ("balance", "wallet", "remaining_balance", "amount", "available", "available_balance"):
                    if k in data:
                        try:
                            if isinstance(data[k], dict):
                                for kk in ("amount", "value", "available", "balance"):
                                    if kk in data[k]:
                                        return float(data[k][kk])
                            return float(data[k])
                        except Exception:
                            pass
            try:
                return float(data)
            except Exception:
                pass
        except Exception as e:
            logger.debug(Fore.YELLOW + f"[BSP] Баланс: {method} {url} исключение: {e}")
    logger.warning(Fore.YELLOW + "[BSP] Не удалось получить баланс BSP (эндпоинт не распознан).")
    return None

def _points_to_human(points: int) -> str:
    return f"{points:,}".replace(",", " ")

def _steam_link_valid(link: str) -> bool:
    return bool(RE_STEAM_LINK.match((link or "").strip()))

def _nice_refund(account: Account, chat_id, order_id, user_text: str):
    logger.info(Fore.YELLOW + f"↩️ Возврат по заказу {order_id}: {user_text}")
    if chat_id:
        account.send_message(chat_id, user_text + ("\n\nДеньги вернутся автоматически." if AUTO_REFUND else "\n\nСвяжитесь с админом для возврата."))
    try:
        account.refund(order_id)
        logger.warning(Fore.YELLOW + f"[FUNPAY] Возврат оформлен по заказу {order_id}.")
    except Exception as e:
        logger.error(Fore.RED + f"[FUNPAY] Не удалось оформить возврат {order_id}: {e}")

def deactivate_category(account: Account, category_id: int) -> int:
    deactivated = 0
    my_lots = None

    candidates = [
        ("get_my_subcategory_lots", lambda cid: account.get_my_subcategory_lots(cid)),
        ("get_my_lots", lambda cid: account.get_my_lots(cid) if hasattr(account, "get_my_lots") else None),
        ("get_my_lots_all", lambda cid: account.get_my_lots() if hasattr(account, "get_my_lots") else None),
    ]
    for name, fn in candidates:
        try:
            res = fn(category_id)
            if res:
                my_lots = res
                logger.debug(Fore.CYAN + f"[LOTS] Получили лоты через {name}, count={len(res) if hasattr(res,'__len__') else 'unknown'}")
                break
        except Exception as e:
            logger.debug(Fore.YELLOW + f"[LOTS] Метод {name} выбросил исключение: {e}")

    if my_lots is None:
        logger.error(Fore.RED + f"[LOTS] Не удалось получить список лотов для категории {category_id}.")
        return 0

    for lot in my_lots:
        lot_id = getattr(lot, "id", None) if not isinstance(lot, dict) else lot.get("id") or lot.get("lot_id")
        if not lot_id:
            continue

        field = None
        for fn_name in ("get_lot_fields", "get_lot_field", "get_lot", "get_lot_by_id"):
            try:
                fn = getattr(account, fn_name, None)
                if callable(fn):
                    field = fn(lot_id)
                    if field:
                        break
            except Exception:
                field = None

        if not field:
            logger.warning(Fore.YELLOW + f"[LOTS] Не удалось получить поля лота {lot_id}. Пропуск.")
            continue

        try:
            if isinstance(field, dict):
                field["active"] = False
            else:
                if hasattr(field, "active"):
                    setattr(field, "active", False)
                elif hasattr(field, "is_active"):
                    setattr(field, "is_active", False)
        except Exception as e:
            logger.debug(Fore.YELLOW + f"[LOTS] Не удалось установить active=False для {lot_id}: {e}")

        saved = False
        for sm in ("save_lot", "save_lot_field", "update_lot", "update_lot_field"):
            try:
                fn = getattr(account, sm, None)
                if callable(fn):
                    fn(field)
                    saved = True
                    logger.info(Fore.YELLOW + f"[LOTS] Деактивирован лот {lot_id} через {sm}")
                    deactivated += 1
                    break
            except Exception:
                pass

        if not saved:
            try:
                account.save_lot(field)
                logger.info(Fore.YELLOW + f"[LOTS] Деактивирован лот {lot_id} через fallback save_lot")
                deactivated += 1
            except Exception as e:
                logger.error(Fore.RED + f"[LOTS] Не удалось деактивировать лот {lot_id}: {e}")

    logger.warning(Fore.YELLOW + f"[LOTS] Всего деактивировано: {deactivated}")
    return deactivated

def _after_bsp_failure(account: Account, state: dict, err_text: str):
    chat_id = state.get("chat_id")
    order_id = state.get("order_id")

    if AUTO_REFUND:
        account.send_message(chat_id, "❌ Не удалось оформить пополнение очков.\n" + err_text + "\n\n🔁 Оформляю возврат средств…")
        try:
            account.refund(order_id)
            account.send_message(chat_id, "✅ Средства возвращены. Можно оформить заказ повторно позже.")
            logger.warning(Fore.YELLOW + f"[FUNPAY] Возврат оформлен по заказу {order_id}")
        except Exception as e:
            logger.error(Fore.RED + f"[FUNPAY] Возврат не удался по заказу {order_id}: {e}")
            account.send_message(chat_id, "❌ Не удалось выполнить автоматический возврат. Свяжитесь с админом.")
    else:
        account.send_message(chat_id, "❌ Не удалось оформить пополнение очков.\n" + err_text + "\n\n⚠️ Авто-возврат выключен. Напишите в чат для возврата.")

    bal = bsp_check_balance()
    if bal is None:
        logger.warning(Fore.YELLOW + "[BALANCE] Баланс BSP определить не удалось.")
        return

    logger.info(Fore.MAGENTA + f"[BALANCE] Текущий баланс BSP: {bal}")
    if bal < BSP_MIN_BALANCE:
        logger.warning(Fore.YELLOW + f"[BALANCE] Баланс BSP {bal} ниже порога {BSP_MIN_BALANCE}.")
        if AUTO_DEACTIVATE:
            cnt = deactivate_category(account, DEACTIVATE_CATEGORY_ID)
            logger.warning(Fore.MAGENTA + f"[LOTS] Авто-деактивировано {cnt} лотов (subcategory {DEACTIVATE_CATEGORY_ID}).")
        else:
            logger.warning(Fore.MAGENTA + "[LOTS] AUTO_DEACTIVATE выключен — деактивацию лотов нужно сделать вручную.")

def _process_bsp_order(account: Account, state: dict):
    chat_id = state["chat_id"]
    points = state["points"]
    steam_link = state["steam_link"]
    order_id = state["order_id"]
    try:
        logger.info(Fore.BLUE + f"🧾 [WORKER] Создание BSP: {points} очков -> {steam_link} (order #{order_id})")
        ok, data, r = bsp_create_order(points, steam_link)
        if not ok:
            err = (data.get("error") or (r.text[:200] if r is not None else "Unknown error"))
            logger.error(Fore.RED + f"[BSP] Ошибка оформления: {err} (order #{order_id})")
            _after_bsp_failure(account, state, f"Причина: {err}")
            return

        account.send_message(
            chat_id,
            "🎉 Готово! Пополнение отправлено.\n\n"
            f"Профиль: *{steam_link}*\n"
            f"Очки: *{_points_to_human(points)}*\n\n"
            "Проверьте, пожалуйста, зачисление очков в Steam.\n"
            "Чтобы завершить заказ — **подтвердите его у себя на FunPay** на странице заказа (кнопка «Подтвердить выполнение»).\n"
            "Если есть проблема — опишите ситуацию здесь в чате, администратор ответит как можно быстрее."
        )
        logger.info(Fore.GREEN + f"✅ BSP заказ создан (order #{order_id}). Ждём подтверждение покупателя.")
    except Exception as e:
        logger.exception(Fore.RED + f"[WORKER] Исключение при оформлении BSP (order #{order_id}): {e}")
        try:
            account.send_message(chat_id, "❌ Внутренняя ошибка при оформлении. Свяжитесь с админом.")
        except Exception:
            pass
    finally:
        _pop_state_by_chat(chat_id)

def handle_new_order(account: Account, order):
    subcat = getattr(order, "subcategory", None) or getattr(order, "sub_category", None)
    subcat_id = getattr(subcat, "id", None)
    if subcat_id != CATEGORY_ID:
        logger.info(Fore.BLUE + f"[ORDER] Пропуск заказа {order.id} (subcategory {subcat_id} != {CATEGORY_ID})")
        return

    chat_id = getattr(order, "chat_id", None)
    buyer_id = getattr(order, "buyer_id", None)

    logger.info(Style.BRIGHT + Fore.WHITE + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(Style.BRIGHT + Fore.CYAN + f"🆕 Новый заказ #{getattr(order, 'id', 'unknown')} | Покупатель: {buyer_id}")
    title = getattr(order, "title", None)
    if title:
        logger.info(Fore.CYAN + f"📦 Товар: {title}")
    logger.info(Style.BRIGHT + Fore.WHITE + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    points_raw, src = get_points(order)
    if points_raw is None:
        msg = ("⚠️ Не указано количество очков.\n"
               "Пожалуйста, оформите заказ с выбором количества очков (в поле или количеством штук).")
        if AUTO_REFUND:
            _nice_refund(account, chat_id, getattr(order, "id", None), msg)
        else:
            account.send_message(chat_id, msg + "\n\nАвто-возврат отключён, напишите в чат для возврата.")
        return

    points = int(points_raw)
    if points < MIN_POINTS or (points % 100 != 0):
        msg = (f"⚠️ Некорректное количество очков: {points}.\n"
               f"Минимум — {MIN_POINTS} и кратно 100 (например: 100, 500, 1000).\n"
               f"Оформите, пожалуйста, заказ заново.")
        if AUTO_REFUND:
            _nice_refund(account, chat_id, getattr(order, "id", None), msg)
        else:
            account.send_message(chat_id, msg + "\n\nАвто-возврат отключён, напишите в чат для возврата.")
        return

    state = {
        "step": "waiting_link",
        "order_id": getattr(order, "id", None),
        "chat_id": chat_id,
        "buyer_id": buyer_id,
        "points": points
    }
    _bind_state(state)

    msg = (
        "👋 Спасибо за заказ очков Steam!\n\n"
        f"Количество: *{_points_to_human(points)}*\n"
        "\nПожалуйста, отправьте ссылку на ваш профиль Steam:\n"
        "`https://steamcommunity.com/id/ваш_id` или `https://steamcommunity.com/profiles/7656119...`"
    )
    account.send_message(chat_id, msg)
    logger.info(Fore.BLUE + f"⏳ Ожидаем ссылку Steam от покупателя {buyer_id}… Источник: {src}, очков к зачислению: {points}")

def handle_new_message(account: Account, message):
    user_id = getattr(message, "author_id", None)
    chat_id = getattr(message, "chat_id", None)
    text = (getattr(message, "text", "") or "").strip()

    state = _get_state(chat_id, user_id)
    if not state:
        return

    if state["step"] == "waiting_link":
        link = text
        if not _steam_link_valid(link):
            account.send_message(
                chat_id,
                "⚠️ Невалидная ссылка. Пример:\n"
                "`https://steamcommunity.com/id/gabelogannewell`\n"
                "или\n"
                "`https://steamcommunity.com/profiles/7656119...`"
            )
            logger.info(Fore.YELLOW + f"🚫 Невалидная ссылка Steam: {link}")
            return

        state["steam_link"] = link
        state["step"] = "confirm_order"

        msg = (
            "✅ Профиль принят!\n\n"
            f"Профиль: *{link}*\n"
            f"Очки: *{_points_to_human(state['points'])}*\n"
            "Если всё верно — напишите `+` для оформления пополнения.\n"
            "Если нужен другой профиль — отправьте новую ссылку.\n"
            "Изменить количество очков можно только при оформлении нового заказа."
        )
        account.send_message(chat_id, msg)
        logger.info(Fore.GREEN + f"✅ Ссылка подтверждена (chat {chat_id}): {link}")
        return

    if state["step"] == "confirm_order":
        if text == "+":
            EXECUTOR.submit(_process_bsp_order, account, state.copy())
        else:
            link = text
            if not _steam_link_valid(link):
                account.send_message(
                    chat_id,
                    "⚠️ Невалидная ссылка. Пример:\n"
                    "`https://steamcommunity.com/id/ваш_id`"
                )
                logger.info(Fore.YELLOW + f"🚫 Невалидная ссылка Steam: {link}")
                return
            state["steam_link"] = link
            account.send_message(
                chat_id,
                "♻️ Ссылка обновлена!\n"
                f"Профиль: *{link}*\n"
                f"Очки: *{_points_to_human(state['points'])}*\n"
                "Если всё верно — напишите `+` для оформления пополнения."
            )
            logger.info(Fore.GREEN + f"♻️ Ссылка обновлена (chat {chat_id}): {link}")
        return

    account.send_message(
        chat_id,
        "ℹ️ Если пополнение уже отправлено — подтвердите заказ у себя на FunPay на странице заказа.\n"
        "По вопросам — напишите здесь, поможем."
    )

def main():
    if not FUNPAY_AUTH_TOKEN:
        raise RuntimeError("FUNPAY_AUTH_TOKEN не найден в .env")
    if not BSP_API_KEY:
        raise RuntimeError("BSP_API_KEY не найден в .env")

    if AUTO_REFUND_RAW is None:
        logger.warning(Fore.YELLOW + "⚠️ AUTO_REFUND не задан в .env (по умолчанию выключен). Чтобы включить: AUTO_REFUND=true")
    if AUTO_DEACTIVATE_RAW is None:
        logger.warning(Fore.YELLOW + "⚠️ AUTO_DEACTIVATE не задан в .env (по умолчанию выключен). Чтобы включить: AUTO_DEACTIVATE=true")
    if BSP_MIN_BALANCE_RAW is None:
        logger.warning(Fore.YELLOW + f"⚠️ BSP_MIN_BALANCE не задан в .env. Используется дефолт {BSP_MIN_BALANCE}")

    account = Account(FUNPAY_AUTH_TOKEN)
    account.get()
    logger.info(Fore.GREEN + f"🔐 Авторизован как {getattr(account, 'username', '(unknown)')}")
    logger.info(Fore.CYAN + f"Настройки: AUTO_REFUND={AUTO_REFUND}, AUTO_DEACTIVATE={AUTO_DEACTIVATE}, BSP_MIN_BALANCE={BSP_MIN_BALANCE}, DEACTIVATE_CATEGORY_ID={DEACTIVATE_CATEGORY_ID}")
    logger.info(Fore.CYAN + f"FIXED_LOT_BY_ID={FIXED_LOT_BY_ID}, ALLOW_TITLE_DETECTION={ALLOW_TITLE_DETECTION}")
    logger.info(Fore.CYAN + f"MAX_WORKERS={MAX_WORKERS} (параллельные оформления BSP)")

    _log_banner()

    runner = Runner(account)
    logger.info(Style.BRIGHT + Fore.WHITE + "🚀 SteamPointsBot запущен. Ожидаю события...")

    for event in runner.listen(requests_delay=3.0):
        try:
            if isinstance(event, NewOrderEvent):
                order = account.get_order(event.order.id)
                handle_new_order(account, order)
            elif isinstance(event, NewMessageEvent):
                handle_new_message(account, event.message)
        except Exception:
            logger.exception(Fore.RED + "Ошибка в основном цикле")

if __name__ == "__main__":
     main()
