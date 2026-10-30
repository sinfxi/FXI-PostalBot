import asyncio
import logging
import os
import time
from typing import Any

import httpx
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("fxi_postal_bot")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "FXI-PostalBot/1.0 (Telegram postal-code lookup bot)"
REQUEST_TIMEOUT = 12.0

# Nominatim's public service policy requires a maximum of one request per second.
_nominatim_lock = asyncio.Lock()
_last_nominatim_request = 0.0
_cache: dict[tuple[float, float], dict[str, Any]] = {}
_CACHE_LIMIT = 500


def location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 ارسال موقعیت من", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def reverse_geocode(latitude: float, longitude: float) -> dict[str, Any]:
    """Look up an address for coordinates using OpenStreetMap Nominatim."""
    global _last_nominatim_request

    # Round coordinates slightly for cache reuse without changing the requested location materially.
    cache_key = (round(latitude, 5), round(longitude, 5))
    if cache_key in _cache:
        return _cache[cache_key]

    async with _nominatim_lock:
        # Re-check after waiting for the lock in case another request filled the cache.
        if cache_key in _cache:
            return _cache[cache_key]

        wait_for = 1.05 - (time.monotonic() - _last_nominatim_request)
        if wait_for > 0:
            await asyncio.sleep(wait_for)

        params = {
            "format": "jsonv2",
            "lat": latitude,
            "lon": longitude,
            "addressdetails": 1,
            "zoom": 18,
        }
        headers = {"User-Agent": USER_AGENT}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
                response = await client.get(NOMINATIM_URL, params=params)
                _last_nominatim_request = time.monotonic()
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise RuntimeError("timeout") from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("Nominatim returned HTTP %s", exc.response.status_code)
            raise RuntimeError("service_error") from exc
        except (httpx.RequestError, ValueError) as exc:
            logger.warning("Reverse geocoding request failed: %s", exc)
            raise RuntimeError("service_error") from exc

        if not isinstance(data, dict) or "error" in data:
            raise RuntimeError("not_found")

        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[cache_key] = data
        return data


def format_location(data: dict[str, Any]) -> str:
    address = data.get("address") or {}
    postcode = address.get("postcode")

    parts = [
        address.get("road"),
        address.get("neighbourhood") or address.get("suburb"),
        address.get("city") or address.get("town") or address.get("village")
        or address.get("municipality"),
        address.get("state"),
        address.get("country"),
    ]
    address_text = "، ".join(dict.fromkeys(part for part in parts if part))

    if postcode:
        result = f"📮 کد پستی: <b>{postcode}</b>"
    else:
        result = (
            "⚠️ برای این نقطه، کد پستی در داده‌های نقشه ثبت نشده است.\n"
            "این به معنی نداشتن کد پستی نیست."
        )

    if address_text:
        result += f"\n\n📍 نشانی تقریبی: {address_text}"

    result += "\n\n<i>اطلاعات از OpenStreetMap دریافت شده و ممکن است کامل یا دقیق نباشد.</i>"
    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "سلام! 👋\n"
        "موقعیت جغرافیایی‌ات را با دکمه زیر بفرست تا کد پستیِ موجود برای آن نقطه را بررسی کنم.\n\n"
        "توجه: برای همه نقاط، کد پستی در نقشه موجود نیست.",
        reply_markup=location_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "برای بررسی کد پستی، روی «📍 ارسال موقعیت من» بزن و موقعیت را ارسال کن.\n"
            "می‌توانی موقعیت یک نقطه را از نقشه تلگرام هم انتخاب کنی.",
            reply_markup=location_keyboard(),
        )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.location:
        return

    latitude = update.message.location.latitude
    longitude = update.message.location.longitude

    await update.message.reply_text("🔎 دارم اطلاعات این موقعیت را بررسی می‌کنم...")

    try:
        data = await reverse_geocode(latitude, longitude)
        await update.message.reply_text(
            format_location(data),
            parse_mode="HTML",
            reply_markup=location_keyboard(),
            disable_web_page_preview=True,
        )
    except RuntimeError as exc:
        if str(exc) == "timeout":
            message = "⏳ سرویس نقشه دیر پاسخ داد. چند لحظه دیگر دوباره امتحان کن."
        elif str(exc) == "not_found":
            message = "❌ برای این موقعیت، اطلاعاتی از سرویس نقشه پیدا نشد."
        else:
            message = "⚠️ فعلاً ارتباط با سرویس نقشه ممکن نیست. کمی بعد دوباره امتحان کن."
        await update.message.reply_text(message, reply_markup=location_keyboard())


async def handle_other_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "برای بررسی کد پستی، لطفاً موقعیت جغرافیایی‌ات را با دکمه زیر بفرست. 📍",
            reply_markup=location_keyboard(),
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled bot error", exc_info=context.error)


def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "BOT_TOKEN تنظیم نشده است. توکن را در متغیر محیطی BOT_TOKEN قرار بده."
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_other_message))
    app.add_error_handler(on_error)

    logger.info("FXI PostalBot is starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
