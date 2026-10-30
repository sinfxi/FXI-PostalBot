const API = "https://api.telegram.org";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/") {
      return new Response("FXI PostalBot is running.", { headers: { "content-type": "text/plain; charset=utf-8" } });
    }

    // One-time/manual setup endpoint. Protect it with a long random SETUP_SECRET.
    if (request.method === "GET" && url.pathname === "/setup") {
      if (!env.SETUP_SECRET || url.searchParams.get("key") !== env.SETUP_SECRET) {
        return new Response("Unauthorized", { status: 401 });
      }
      if (!env.BOT_TOKEN || !env.WEBHOOK_SECRET) {
        return new Response("Missing BOT_TOKEN or WEBHOOK_SECRET", { status: 500 });
      }
      const webhookUrl = `${url.origin}/telegram/${env.WEBHOOK_SECRET}`;
      const result = await telegram(env, "setWebhook", {
        url: webhookUrl,
        secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message"],
        drop_pending_updates: true
      });
      return Response.json(result);
    }

    if (request.method !== "POST" || url.pathname !== `/telegram/${env.WEBHOOK_SECRET}`) {
      return new Response("Not found", { status: 404 });
    }
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("Bad request", { status: 400 });
    }

    // Telegram expects a fast acknowledgement; handle the small update inline.
    try {
      if (update.message) await handleMessage(update.message, env);
    } catch (error) {
      console.error("Update processing failed:", error?.message || error);
    }
    return new Response("OK");
  }
};

async function handleMessage(message, env) {
  const chatId = message.chat?.id;
  if (!chatId) return;

  if (message.text === "/start" || message.text === "/help") {
    await telegram(env, "sendMessage", {
      chat_id: chatId,
      text: "سلام! 👋\nبرای پیدا کردن کد پستی، دکمهٔ «📍 ارسال موقعیت» را بزن و موقعیت مکانی را بفرست.\n\nکد پستی فقط وقتی نمایش داده می‌شود که منبع نقشه آن را برای آن نقطه ثبت کرده باشد.",
      reply_markup: {
        keyboard: [[{ text: "📍 ارسال موقعیت", request_location: true }]],
        resize_keyboard: true,
        is_persistent: true
      }
    });
    return;
  }

  if (message.location) {
    const { latitude, longitude } = message.location;
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude) ||
        latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
      await sendText(env, chatId, "موقعیت ارسال‌شده معتبر نیست. دوباره امتحان کن.");
      return;
    }

    await sendText(env, chatId, "🔎 دارم اطلاعات این موقعیت را بررسی می‌کنم...");
    try {
      const info = await reverseGeocode(latitude, longitude);
      const address = info?.address || {};
      const postal = address.postcode;
      const place = [
        address.road,
        address.suburb || address.neighbourhood,
        address.city || address.town || address.village || address.county,
        address.state,
        address.country
      ].filter(Boolean).join("، ");

      const text = postal
        ? `📮 کد پستی: ${postal}${place ? `\n📍 محدوده: ${place}` : ""}\n\nℹ️ کد پستی از داده‌های نقشه دریافت شده و ممکن است برای نشانی دقیق نیاز به بررسی داشته باشد.`
        : `❗ برای این نقطه کد پستی در داده‌های نقشه پیدا نشد.${place ? `\n📍 محدوده: ${place}` : ""}\n\nاین لزوماً به معنی نداشتن کد پستی نیست؛ ممکن است اطلاعات نقشه کامل نباشد.`;
      await sendText(env, chatId, text);
    } catch (error) {
      console.error("Geocoding error:", error?.message || error);
      await sendText(env, chatId, "⚠️ فعلاً نتونستم اطلاعات نقشه را دریافت کنم. کمی بعد دوباره امتحان کن.");
    }
    return;
  }

  if (message.text) {
    await telegram(env, "sendMessage", {
      chat_id: chatId,
      text: "برای دریافت کد پستی، موقعیت جغرافیایی‌ات را با دکمهٔ «📍 ارسال موقعیت» بفرست.",
      reply_markup: {
        keyboard: [[{ text: "📍 ارسال موقعیت", request_location: true }]],
        resize_keyboard: true
      }
    });
  }
}

async function reverseGeocode(lat, lon) {
  const params = new URLSearchParams({
    format: "jsonv2",
    lat: String(lat),
    lon: String(lon),
    zoom: "18",
    addressdetails: "1"
  });
  const response = await fetch(`https://nominatim.openstreetmap.org/reverse?${params}`, {
    headers: {
      "Accept": "application/json",
      "Accept-Language": "fa,en",
      "User-Agent": "FXI-PostalBot/1.0 (Telegram location postal lookup)"
    },
    signal: AbortSignal.timeout(9000)
  });
  if (!response.ok) throw new Error(`Nominatim returned HTTP ${response.status}`);
  return response.json();
}

async function sendText(env, chatId, text) {
  return telegram(env, "sendMessage", { chat_id: chatId, text });
}

async function telegram(env, method, payload) {
  if (!env.BOT_TOKEN) throw new Error("BOT_TOKEN is not configured");
  const response = await fetch(`${API}/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(10000)
  });
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.description || `Telegram API HTTP ${response.status}`);
  return data;
}
