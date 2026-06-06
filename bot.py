# -*- coding: utf-8 -*-
"""
WorkHab — Telegram бот контент-завода для Instagram.
Принимает видео/фото → генерирует Reels-пакет через ИИ.
"""
import os
import json
import subprocess
from pathlib import Path

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from telegram.constants import ParseMode, ChatAction

# ─────────────────────────── КОНФИГ ───────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "").strip()
AI_API_KEY   = os.getenv("AI_API_KEY", "").strip()
AI_BASE_URL  = os.getenv("AI_BASE_URL", "https://api.aitunnel.ru/v1/messages").strip()
AI_MODEL     = os.getenv("AI_MODEL", "claude-sonnet-4-6").strip()
ALLOWED_USER = int(os.getenv("ALLOWED_USER_ID", "0").strip() or "0")
MAIN_ACCOUNT = os.getenv("MAIN_ACCOUNT", "@workhab").strip()

WORK_DIR = Path("/tmp/workhab")
WORK_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT = (
    "Ты ИИ-ассистент контент-завода WorkHab для Instagram строительной тематики.\n"
    f"Главный аккаунт: {MAIN_ACCOUNT}. Регион: Московская область.\n"
    "Ниши: кровля, фасады, капремонт МКД, ФКР МО.\n"
    "Цель: 1 млн подписчиков за 12 месяцев через 30 сателлитных аккаунтов.\n"
    "Аудитория русскоязычная. Стиль живой, экспертный, без канцелярита.\n"
    f"Всегда добавляй призыв подписаться на {MAIN_ACCOUNT} в конце.\n"
    "Алгоритм Instagram 2025-2026: семантика важнее хэштегов, закладки = главный сигнал охвата."
)

# ─────────────────────────── ИИ ───────────────────────────
def call_ai(messages, max_tokens=2500):
    """Запрос к ИИ. Возвращает текст или сообщение об ошибке."""
    if not AI_API_KEY:
        return "❌ AI_API_KEY не задан в настройках Railway."

    # Проверка что ключ и модель не содержат не-ASCII символов (частая причина ошибки latin-1)
    try:
        AI_API_KEY.encode("ascii")
    except UnicodeEncodeError:
        return ("❌ В API-ключе есть посторонние символы (возможно русские буквы "
                "или скрытый пробел). Скопируйте ключ заново с aitunnel.ru.")
    try:
        AI_MODEL.encode("ascii")
    except UnicodeEncodeError:
        return "❌ В названии модели (AI_MODEL) посторонние символы. Должно быть: claude-sonnet-4-6"

    # Тело запроса кодируем явно в UTF-8 и передаём как data, чтобы избежать latin-1
    body = json.dumps({
        "model": AI_MODEL,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": messages,
    }, ensure_ascii=False).encode("utf-8")

    try:
        resp = requests.post(
            AI_BASE_URL,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {AI_API_KEY}",
                "anthropic-version": "2023-06-01",
            },
            data=body,
            timeout=90,
        )
        if resp.status_code != 200:
            return f"❌ Ошибка API ({resp.status_code}): {resp.text[:200]}"
        data = resp.json()
        return data["content"][0]["text"]
    except requests.exceptions.Timeout:
        return "❌ Превышено время ожидания ответа ИИ. Попробуйте ещё раз."
    except Exception as exc:
        return f"❌ Ошибка запроса: {exc}"


def ai(prompt, max_tokens=2500):
    return call_ai([{"role": "user", "content": prompt}], max_tokens)


# ─────────────────────────── FFMPEG ───────────────────────────
def ffmpeg(args):
    try:
        r = subprocess.run(["ffmpeg", "-y"] + args, capture_output=True, timeout=300)
        return r.returncode == 0
    except Exception as exc:
        print(f"FFmpeg error: {exc}")
        return False


def video_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 60.0


def srt_timestamp(sec):
    h, m = int(sec // 3600), int((sec % 3600) // 60)
    s, ms = int(sec % 60), int((sec % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cut_segment(src, dst, start, end):
    return ffmpeg([
        "-ss", str(start), "-i", src, "-t", str(end - start),
        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-b:a", "128k",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        dst,
    ])


def burn_subtitles(src, dst, subs):
    srt = src.replace(".mp4", ".srt")
    with open(srt, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(subs, 1):
            f.write(f"{i}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}\n\n")
    style = ("FontName=Arial,FontSize=18,Bold=1,PrimaryColour=&HFFFFFF,"
             "OutlineColour=&H000000,Outline=2,Alignment=2,MarginV=80")
    ok = ffmpeg(["-i", src, "-vf", f"subtitles={srt}:force_style='{style}'",
                 "-c:a", "copy", dst])
    try:
        os.remove(srt)
    except OSError:
        pass
    return ok


def add_cta(src, dst, text):
    dur = video_duration(src)
    start = max(0, dur - 3)
    safe = text.replace("'", r"\'").replace(":", r"\:")
    return ffmpeg([
        "-i", src,
        "-vf", (f"drawtext=text='{safe}':fontcolor=white:fontsize=24:box=1:"
                f"boxcolor=black@0.6:boxborderw=8:x=(w-text_w)/2:y=h-100:"
                f"enable='between(t,{start},{dur})'"),
        "-c:a", "copy", dst,
    ])


def uniquify(src, dst, variant):
    bitrates = [2800, 2900, 3000, 3100, 3200]
    return ffmpeg([
        "-i", src, "-c:v", "libx264", "-b:v", f"{bitrates[variant % 5]}k",
        "-c:a", "aac", "-b:a", "128k", dst,
    ])


# ─────────────────────────── ДОСТУП ───────────────────────────
def allowed(update):
    return ALLOWED_USER == 0 or update.effective_user.id == ALLOWED_USER


# ─────────────────────────── ОТПРАВКА ───────────────────────────
async def send_long(message, text, markup=None):
    """Отправляет длинный текст частями (лимит Telegram 4096)."""
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [text]
    for idx, chunk in enumerate(chunks):
        kb = markup if idx == len(chunks) - 1 else None
        await message.reply_text(chunk, reply_markup=kb)


# ─────────────────────────── КЛАВИАТУРЫ ───────────────────────────
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Сценарий Reels", callback_data="m:scenario"),
         InlineKeyboardButton("🎣 Хуки", callback_data="m:hook")],
        [InlineKeyboardButton("✍️ SEO Caption", callback_data="m:caption"),
         InlineKeyboardButton("👆 CTA блок", callback_data="m:cta")],
        [InlineKeyboardButton("📅 Контент-план", callback_data="m:plan"),
         InlineKeyboardButton("🎵 Промпт Suno", callback_data="m:suno")],
        [InlineKeyboardButton("📤 Пакет SMMplanner", callback_data="m:smm"),
         InlineKeyboardButton("🛡 Антидубль", callback_data="m:antidupe")],
        [InlineKeyboardButton("🔄 Очистить историю", callback_data="m:clear")],
    ])


def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="m:back")]])


def kb_themes(prefix):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Кровля МКД", callback_data=f"{prefix}:roof"),
         InlineKeyboardButton("🏢 Фасады", callback_data=f"{prefix}:facade")],
        [InlineKeyboardButton("🔨 Капремонт МКД", callback_data=f"{prefix}:kaprem"),
         InlineKeyboardButton("💡 Лайфхаки", callback_data=f"{prefix}:life")],
        [InlineKeyboardButton("📸 До и после", callback_data=f"{prefix}:before"),
         InlineKeyboardButton("❌ Ошибки", callback_data=f"{prefix}:errors")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m:back")],
    ])


def kb_duration():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("15 сек", callback_data="dur:15"),
         InlineKeyboardButton("30 сек", callback_data="dur:30"),
         InlineKeyboardButton("60 сек", callback_data="dur:60")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m:back")],
    ])


def kb_reels_count():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("3 ролика", callback_data="rc:3"),
         InlineKeyboardButton("5 роликов", callback_data="rc:5"),
         InlineKeyboardButton("8 роликов", callback_data="rc:8")],
    ])


THEMES = {
    "roof": "Замена кровли МКД",
    "facade": "Фасадные работы МКД",
    "kaprem": "Капитальный ремонт по программе ФКР МО",
    "life": "Строительные лайфхаки",
    "before": "До и после объекта",
    "errors": "Ошибки подрядчиков — разбор кейса",
}

# ─────────────────────────── СЕССИИ ───────────────────────────
sessions = {}


def get_session(uid):
    return sessions.setdefault(uid, {"history": [], "media": "", "video": None})


# ─────────────────────────── ВИДЕО-ПАЙПЛАЙН ───────────────────────────
async def run_pipeline(message, video_path, num_reels):
    status = await message.reply_text("⚙️ Запускаю автопилот...")

    async def upd(text):
        try:
            await status.edit_text(text)
        except Exception:
            pass

    try:
        await upd("📏 Шаг 1/5 — анализирую видео...")
        dur = video_duration(video_path)

        await upd("🤖 Шаг 2/5 — ИИ планирует нарезку...")
        plan_prompt = (
            f"Видео {int(dur)} сек. Составь план нарезки на {num_reels} Reels. "
            "Ответь ТОЛЬКО валидным JSON без пояснений:\n"
            '{"reels":[{"n":1,"start":0.0,"end":28.0,'
            '"hook":"текст хука","title":"название",'
            '"subtitles":[{"s":0.0,"e":3.0,"t":"субтитр"}],'
            f'"caption":"caption с CTA на {MAIN_ACCOUNT} и хэштегами"}}]}}'
            f"\nТайм-коды в пределах {int(dur)} сек, каждый ролик 15-60 сек."
        )
        raw = ai(plan_prompt)
        try:
            plan = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            reels = plan["reels"]
        except Exception:
            seg = dur / num_reels
            hooks = [
                "Вы не поверите что здесь было раньше",
                "Эту ошибку делает каждый второй подрядчик",
                "45 дней и дом не узнать",
                "Смотрите что происходит когда экономят",
                "Никогда не делайте так на стройке",
                "Вот почему важен правильный подрядчик",
                "Этого вы точно не знали о капремонте",
                "Результат который удивил даже нас",
            ]
            reels = []
            for i in range(num_reels):
                st = round(i * seg, 1)
                en = round(min((i + 1) * seg, dur - 0.5), 1)
                reels.append({
                    "n": i + 1, "start": st, "end": en,
                    "hook": hooks[i % len(hooks)],
                    "title": f"Ролик {i + 1}",
                    "subtitles": [
                        {"s": st, "e": st + 4, "t": hooks[i % len(hooks)]},
                        {"s": st + 4, "e": en - 3, "t": "Капремонт МКД · Московская область"},
                        {"s": en - 3, "e": en, "t": f"Подписывайся → {MAIN_ACCOUNT}"},
                    ],
                    "caption": (
                        f"Строительный контент от {MAIN_ACCOUNT}\n\n"
                        "Капремонт МКД в Московской области.\n\n"
                        "Сохрани 🔖 — пригодится!\n"
                        "💬 Был такой случай? Пиши в комментах!\n"
                        f"➡️ {MAIN_ACCOUNT}\n\n"
                        "#капремонт #кровля #фасад #мкд #строительство"
                    ),
                })

        work = WORK_DIR / f"job_{message.chat_id}"
        work.mkdir(exist_ok=True)
        results, captions = [], []
        total = len(reels)

        for reel in reels:
            n = reel["n"]
            await upd(f"✂️ Шаг 3/5 — нарезаю ролик {n}/{total}...")
            raw_path = str(work / f"r{n}_raw.mp4")
            if not cut_segment(video_path, raw_path, reel["start"], reel["end"]):
                continue

            await upd(f"💬 Шаг 4/5 — субтитры {n}/{total}...")
            subs = [(s["s"] - reel["start"], s["e"] - reel["start"], s["t"])
                    for s in reel.get("subtitles", [])]
            sub_path = str(work / f"r{n}_sub.mp4")
            if not burn_subtitles(raw_path, sub_path, subs):
                sub_path = raw_path

            await upd(f"🎯 Шаг 5/5 — CTA + финал {n}/{total}...")
            cta_path = str(work / f"r{n}_cta.mp4")
            if not add_cta(sub_path, cta_path, f"Подписывайся {MAIN_ACCOUNT}"):
                cta_path = sub_path

            final_path = str(work / f"r{n}_final.mp4")
            if not uniquify(cta_path, final_path, n):
                final_path = cta_path

            results.append((final_path, n, reel.get("title", f"Ролик {n}")))
            captions.append(
                f"━━━ РОЛИК {n}: {reel.get('title', '')} ━━━\n\n"
                f"🎣 Хук: {reel.get('hook', '')}\n\n"
                f"📝 Caption:\n{reel.get('caption', '')}"
            )

        await status.delete()

        if not results:
            await message.reply_text(
                "❌ Не удалось обработать видео. Проверьте формат файла.",
                reply_markup=kb_main(),
            )
            return

        await message.reply_text(f"✅ Готово! Обработано роликов: {len(results)}")

        for path, n, title in results:
            try:
                with open(path, "rb") as vf:
                    await message.reply_video(
                        vf,
                        caption=f"🎬 Ролик {n}: {title}\n✅ Субтитры + CTA + Антидубль",
                        supports_streaming=True,
                    )
            except Exception as exc:
                await message.reply_text(f"⚠️ Ролик {n}: ошибка отправки ({exc})")

        await send_long(message, "\n\n".join(captions))
        await message.reply_text(
            "━━━━━━━━━━━━━━━━━━\n"
            "📤 ДАЛЬШЕ:\n"
            "1. Скачай готовые MP4 выше\n"
            "2. Зайди на smmplanner.com\n"
            "3. Создай пост → выбери аккаунты\n"
            "4. Загрузи видео + вставь caption\n"
            "5. Запланируй время 🚀",
            reply_markup=kb_main(),
        )
    except Exception as exc:
        await upd(f"❌ Ошибка обработки: {exc}")


# ─────────────────────────── ОБРАБОТЧИКИ ───────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "👋 Привет! Я бот контент-завода WorkHab 🏗\n\n"
        "📹 Отправь видео с объекта → я автоматически:\n"
        "  ✂️ нарежу на Reels\n"
        "  💬 наложу субтитры\n"
        f"  🎯 добавлю CTA на {MAIN_ACCOUNT}\n"
        "  🛡 уникализирую файлы\n"
        "  ✍️ напишу caption\n\n"
        "📸 Отправь фото → дам хуки, caption и CTA\n\n"
        "✍️ Или напиши вопрос текстом\n\n"
        "Можно нажать кнопку ниже 👇",
        reply_markup=kb_main(),
    )


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    s = get_session(update.effective_user.id)
    await update.message.chat.send_action(ChatAction.TYPING)
    s["history"].append({"role": "user", "content": update.message.text})
    reply = call_ai(s["history"][-20:])
    s["history"].append({"role": "assistant", "content": reply})
    await send_long(update.message, reply, kb_main())


async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    s = get_session(update.effective_user.id)
    video = update.message.video or update.message.document
    await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)
    msg = await update.message.reply_text("📥 Загружаю видео...")
    try:
        tg_file = await ctx.bot.get_file(video.file_id)
        path = str(WORK_DIR / f"in_{update.effective_user.id}.mp4")
        await tg_file.download_to_drive(path)
        s["video"] = path
        await msg.edit_text("📥 Видео загружено! Сколько Reels нарезать?",
                            reply_markup=kb_reels_count())
    except Exception as exc:
        await msg.edit_text(f"❌ Ошибка загрузки: {exc}")


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    caption = update.message.caption or ""
    await update.message.chat.send_action(ChatAction.TYPING)
    msg = await update.message.reply_text("📸 Генерирую контент-пакет...")
    reply = ai(
        f"Фото с объекта. {caption}\n\n"
        "Дай пакет:\n"
        "🎣 5 ХУКОВ (первые 2-3 сек Reels)\n"
        "✍️ SEO-CAPTION (хук, текст, вопрос, CTA, хэштеги)\n"
        f"👆 CTA-БЛОК (закладки + комментарий + {MAIN_ACCOUNT})\n"
        "📋 ЗАДАНИЕ МОНТАЖЁРУ (шаги в CapCut)\n"
        "🎵 ПРОМПТ SUNO AI (без авторских прав)"
    )
    await msg.delete()
    await send_long(update.message, reply, kb_main())


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    doc = update.message.document
    if doc and doc.mime_type and doc.mime_type.startswith("video"):
        await on_video(update, ctx)
    else:
        await update.message.reply_text("Отправь видео или фото!", reply_markup=kb_main())


# ─────────────────────────── КНОПКИ ───────────────────────────
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    s = get_session(uid)
    data = q.data
    kind, _, value = data.partition(":")

    # Навигация
    if data == "m:back":
        await q.edit_message_reply_markup(reply_markup=kb_main())
        return
    if data == "m:clear":
        sessions[uid] = {"history": [], "media": "", "video": None}
        await q.edit_message_text("🔄 История очищена!", reply_markup=kb_main())
        return

    # Меню, требующие выбора темы/длительности
    if data == "m:scenario":
        await q.edit_message_text("🎬 Длительность ролика:", reply_markup=kb_duration())
        return
    if data == "m:hook":
        await q.edit_message_text("🎣 Выбери тему:", reply_markup=kb_themes("hook"))
        return
    if data == "m:caption":
        await q.edit_message_text("✍️ Выбери тему:", reply_markup=kb_themes("cap"))
        return

    # Длительность выбрана → выбор темы
    if kind == "dur":
        ctx.user_data["dur"] = value
        await q.edit_message_text(f"🎬 Reels {value} сек — выбери тему:",
                                  reply_markup=kb_themes("scen"))
        return

    # Количество роликов выбрано → запуск пайплайна
    if kind == "rc":
        if not s.get("video") or not Path(s["video"]).exists():
            await q.edit_message_text("❌ Видео не найдено. Отправь заново.",
                                      reply_markup=kb_main())
            return
        await q.delete()
        await run_pipeline(q.message, s["video"], int(value))
        return

    # Прямые генерации без темы
    direct = {
        "m:cta": ("👆 Генерирую CTA...",
                  "Напиши 5 вариантов CTA для Instagram Reels строительной "
                  f"тематики: закладки, комментарий, репост, подписка, вопрос. "
                  f"Живой русский. Призыв подписаться на {MAIN_ACCOUNT}."),
        "m:plan": ("📅 Генерирую контент-план...",
                   "Составь контент-план на 2 недели для 30 Instagram аккаунтов "
                   "строительной тематики МО. 4-5 постов/день. Reels 60%, "
                   f"карусели 30%, посты 10%. Дата, тема, формат, аккаунт, ссылка на {MAIN_ACCOUNT}."),
        "m:suno": ("🎵 Генерирую промпты Suno...",
                   "Напиши 3 промпта для Suno AI под строительные Reels: таймлапс, "
                   "до/после, экспертный совет. На английском, без вокала, 30 сек, "
                   "без проблем с авторскими правами."),
        "m:smm": ("📤 Формирую пакет SMMplanner...",
                  f"Сформируй пакет для SMMplanner на 5 аккаунтов: {MAIN_ACCOUNT}, "
                  "@roofmaster_msk, @fasad_pro_mo, @kaprem_mo, @stroylajfhak_ru. "
                  "Для каждого: уникальный SEO-текст, CTA, 3-5 хэштегов. Время 19:00 МСК."),
        "m:antidupe": ("🛡 Генерирую инструкцию...",
                       "Напиши краткую пошаговую инструкцию как уникализировать видео "
                       "для 30 Instagram аккаунтов через CloudConvert — уникальный "
                       "MD5-хеш для каждого. Конкретные шаги."),
    }
    if data in direct:
        wait_text, prompt = direct[data]
        await q.edit_message_text(wait_text)
        reply = ai(prompt)
        # edit_message_text не принимает длинный текст хорошо — отправим частями
        if len(reply) <= 4000:
            await q.edit_message_text(reply, reply_markup=kb_main())
        else:
            await q.edit_message_text(reply[:4000])
            await send_long(q.message, reply[4000:], kb_main())
        return

    # Тема выбрана
    if kind in ("hook", "cap", "scen"):
        theme = THEMES.get(value, value)
        if kind == "hook":
            prompt = (f"Напиши 8 цепляющих хуков для первых 2-3 секунд Instagram "
                      f"Reels по теме '{theme}'. Остановить скролл. Русскоязычная аудитория.")
        elif kind == "cap":
            prompt = (f"Напиши SEO-caption для Instagram Reels по теме '{theme}'. "
                      f"Хук, SEO-текст, вопрос к аудитории, CTA (закладки + комментарий "
                      f"+ {MAIN_ACCOUNT}), 3-5 хэштегов. До 250 слов.")
        else:  # scen
            dur = ctx.user_data.get("dur", "30")
            prompt = (f"Детальный сценарий Instagram Reels {dur} сек, тема '{theme}'. "
                      f"Тайм-коды, субтитры, голос за кадром, текст на экране. "
                      f"Хук 0-3 сек. Финал: сохрани + подписка на {MAIN_ACCOUNT}.")
        await q.edit_message_text(f"⏳ Генерирую: {theme}...")
        reply = ai(prompt)
        s["history"].append({"role": "user", "content": prompt})
        s["history"].append({"role": "assistant", "content": reply})
        if len(reply) <= 4000:
            await q.edit_message_text(reply, reply_markup=kb_main())
        else:
            await q.edit_message_text(reply[:4000])
            await send_long(q.message, reply[4000:], kb_main())
        return


# ─────────────────────────── ЗАПУСК ───────────────────────────
def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не задан!")
        return
    if not AI_API_KEY:
        print("❌ AI_API_KEY не задан!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, on_video))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("✅ WorkHab бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
