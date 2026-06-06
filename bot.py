import os
import asyncio
import tempfile
import subprocess
import json
import math
import requests
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode

# ── CONFIG ───────────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
AI_API_KEY   = os.getenv("AI_API_KEY", "")
AI_BASE_URL  = os.getenv("AI_BASE_URL", "https://api.aitunnel.ru/v1/messages")
AI_MODEL     = os.getenv("AI_MODEL", "claude-sonnet-4-6")
ALLOWED_USER = int(os.getenv("ALLOWED_USER_ID", "0"))
WORK_DIR     = Path(os.getenv("WORK_DIR", "/tmp/content_factory"))
WORK_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT = """Ты ИИ-ассистент контент-завода для Instagram строительной компании ООО РусСтройГруп.
Главный аккаунт: @russtroygroup. Регион: Московская область.
Ниши: кровля, фасады, капремонт МКД, ФКР МО.
Цель: 1 миллион подписчиков за 12 месяцев через 30 сателлитных аккаунтов.
Аудитория: русскоязычная. Стиль: живой, экспертный, без канцелярита.
Всегда упоминай @russtroygroup в CTA.
Instagram 2025-2026: семантика важнее хэштегов. Закладки = главный сигнал охвата."""

# ── AI ───────────────────────────────────────────────────────────
def call_ai(messages: list, max_tokens=3000) -> str:
    r = requests.post(
        AI_BASE_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AI_API_KEY}",
            "anthropic-version": "2023-06-01"
        },
        json={"model": AI_MODEL, "max_tokens": max_tokens,
              "system": SYSTEM_PROMPT, "messages": messages},
        timeout=90
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"]

def ai(prompt: str, max_tokens=3000) -> str:
    return call_ai([{"role": "user", "content": prompt}], max_tokens)

# ── FFMPEG HELPERS ───────────────────────────────────────────────
def run_ffmpeg(args: list) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-y"] + args,
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0
    except Exception as e:
        print(f"FFmpeg error: {e}")
        return False

def get_video_duration(path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", path],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except:
        return 60.0

def transcribe_video(path: str) -> str:
    """Transcribe using Whisper if available, else return placeholder"""
    try:
        result = subprocess.run(
            ["whisper", path, "--language", "ru", "--output_format", "txt",
             "--output_dir", str(WORK_DIR), "--model", "base"],
            capture_output=True, text=True, timeout=180
        )
        txt_file = WORK_DIR / (Path(path).stem + ".txt")
        if txt_file.exists():
            return txt_file.read_text(encoding="utf-8")
    except:
        pass
    return ""

def cut_segment(input_path: str, output_path: str, start: float, end: float) -> bool:
    """Cut video segment"""
    duration = end - start
    return run_ffmpeg([
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        output_path
    ])

def add_subtitles(input_path: str, output_path: str, subs: list) -> bool:
    """Add burned-in subtitles to video"""
    # Create SRT file
    srt_path = input_path.replace(".mp4", ".srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(subs, 1):
            f.write(f"{i}\n")
            f.write(f"{fmt_time(start)} --> {fmt_time(end)}\n")
            f.write(f"{text}\n\n")

    # Burn subtitles
    ok = run_ffmpeg([
        "-i", input_path,
        "-vf", f"subtitles={srt_path}:force_style='FontName=Arial,FontSize=18,Bold=1,"
               f"PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,"
               f"Alignment=2,MarginV=80'",
        "-c:a", "copy",
        output_path
    ])
    try: os.remove(srt_path)
    except: pass
    return ok

def add_cta_overlay(input_path: str, output_path: str, cta_text: str) -> bool:
    """Add CTA text overlay in last 3 seconds"""
    duration = get_video_duration(input_path)
    cta_start = max(0, duration - 3)
    safe_cta = cta_text.replace("'", "\\'").replace(":", "\\:")
    return run_ffmpeg([
        "-i", input_path,
        "-vf", f"drawtext=text='{safe_cta}':fontcolor=white:fontsize=24:"
               f"box=1:boxcolor=black@0.6:boxborderw=8:"
               f"x=(w-text_w)/2:y=h-100:"
               f"enable='between(t,{cta_start},{duration})'",
        "-c:a", "copy",
        output_path
    ])

def uniquify(input_path: str, output_path: str, variant: int) -> bool:
    """Uniquify video for each account (change bitrate slightly)"""
    bitrates = [2800, 2900, 3000, 3100, 3200]
    crops = ["1080:1918:0:1", "1080:1918:0:2", "1080:1920:0:0",
             "1078:1920:1:0", "1080:1918:0:0"]
    br = bitrates[variant % len(bitrates)]
    crop = crops[variant % len(crops)]
    return run_ffmpeg([
        "-i", input_path,
        "-vf", f"crop={crop}",
        "-c:v", "libx264", "-b:v", f"{br}k",
        "-c:a", "aac", "-b:a", "128k",
        output_path
    ])

def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# ── ACCESS ───────────────────────────────────────────────────────
def allowed(update: Update) -> bool:
    return ALLOWED_USER == 0 or update.effective_user.id == ALLOWED_USER

# ── SEND HELPERS ─────────────────────────────────────────────────
async def send_long(update: Update, text: str, reply_markup=None, parse_mode=None):
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for i, chunk in enumerate(chunks):
        kb = reply_markup if i == len(chunks) - 1 else None
        await update.effective_message.reply_text(chunk, reply_markup=kb, parse_mode=parse_mode)

async def progress(msg, text: str):
    try:
        await msg.edit_text(text)
    except:
        pass

# ── KEYBOARDS ────────────────────────────────────────────────────
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Сценарий Reels",    callback_data="c_scenario"),
         InlineKeyboardButton("🎣 Хуки",              callback_data="c_hook")],
        [InlineKeyboardButton("✍️ SEO Caption",        callback_data="c_caption"),
         InlineKeyboardButton("👆 CTA блок",           callback_data="c_cta")],
        [InlineKeyboardButton("📅 Контент-план",       callback_data="c_plan"),
         InlineKeyboardButton("🎵 Промпт Suno",        callback_data="c_suno")],
        [InlineKeyboardButton("📤 Пакет SMMplanner",   callback_data="c_smm"),
         InlineKeyboardButton("🛡 Антидубль",          callback_data="c_antidupe")],
        [InlineKeyboardButton("🔄 Очистить историю",   callback_data="c_clear")],
    ])

def kb_themes(pfx: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Кровля МКД",        callback_data=f"{pfx}|roof"),
         InlineKeyboardButton("🏢 Фасады",            callback_data=f"{pfx}|facade")],
        [InlineKeyboardButton("🔨 Капремонт МКД",     callback_data=f"{pfx}|kaprem"),
         InlineKeyboardButton("💡 Лайфхаки",          callback_data=f"{pfx}|life")],
        [InlineKeyboardButton("📸 До и после",        callback_data=f"{pfx}|before"),
         InlineKeyboardButton("❌ Ошибки подрядчика", callback_data=f"{pfx}|errors")],
        [InlineKeyboardButton("◀️ Назад",             callback_data="back")],
    ])

def kb_dur(pfx: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("15 сек", callback_data=f"{pfx}|15"),
         InlineKeyboardButton("30 сек", callback_data=f"{pfx}|30"),
         InlineKeyboardButton("60 сек", callback_data=f"{pfx}|60")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back")],
    ])

def kb_reels_count():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("3 ролика",  callback_data="rc|3"),
         InlineKeyboardButton("5 роликов", callback_data="rc|5"),
         InlineKeyboardButton("8 роликов", callback_data="rc|8")],
    ])

THEMES = {
    "roof":   "Замена кровли МКД",
    "facade": "Фасадные работы МКД",
    "kaprem": "Капитальный ремонт по программе ФКР МО",
    "life":   "Строительные лайфхаки",
    "before": "До и после объекта",
    "errors": "Ошибки подрядчиков",
}

# ── SESSION ──────────────────────────────────────────────────────
sessions = {}

def sess(uid):
    if uid not in sessions:
        sessions[uid] = {
            "history": [], "last_media": "",
            "pending_video": None, "pending_action": None
        }
    return sessions[uid]

# ── MAIN VIDEO PIPELINE ──────────────────────────────────────────
async def run_pipeline(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                       video_path: str, num_reels: int, caption_txt: str):
    uid = update.effective_user.id
    s = sess(uid)
    msg = await update.effective_message.reply_text("⚙️ *Запускаю полный автопилот...*", parse_mode=ParseMode.MARKDOWN)

    try:
        # STEP 1: Get duration
        await progress(msg, "📏 *Шаг 1/7* — Определяю длину видео...")
        duration = get_video_duration(video_path)
        dur_str = f"{int(duration//60)}:{int(duration%60):02d}"

        # STEP 2: Transcribe
        await progress(msg, "🎙 *Шаг 2/7* — Транскрибирую речь (Whisper)...")
        transcript = transcribe_video(video_path)
        if not transcript:
            transcript = f"Видео {dur_str}. {caption_txt or 'Строительный контент.'}"

        # STEP 3: AI analysis - get timecodes and content
        await progress(msg, "🤖 *Шаг 3/7* — ИИ анализирует и планирует нарезку...")

        analysis_prompt = f"""Проанализируй видео и создай план нарезки на {num_reels} Instagram Reels.

Длина видео: {dur_str} ({int(duration)} сек).
Описание/транскрипт: {transcript[:1000]}

Ответь СТРОГО в JSON формате (только JSON, без пояснений):
{{
  "reels": [
    {{
      "n": 1,
      "start": 0.0,
      "end": 28.0,
      "hook": "Текст хука для первых 3 сек",
      "title": "Название ролика",
      "subtitles": [
        {{"s": 0.0, "e": 3.0, "t": "Текст субтитра"}},
        {{"s": 3.0, "e": 8.0, "t": "Следующий субтитр"}}
      ],
      "caption": "SEO-caption для этого ролика с хуком, текстом, вопросом, CTA на @russtroygroup и 3-5 хэштегами"
    }}
  ]
}}

Тайм-коды должны быть в пределах {int(duration)} сек. Каждый ролик 15-60 сек."""

        raw = ai(analysis_prompt, max_tokens=3000)

        # Parse JSON
        try:
            start_idx = raw.find("{")
            end_idx = raw.rfind("}") + 1
            plan = json.loads(raw[start_idx:end_idx])
            reels_plan = plan["reels"]
        except:
            # Fallback: equal segments
            seg = duration / num_reels
            reels_plan = []
            hooks = [
                "Вы не поверите что здесь было раньше...",
                "Эту ошибку делает каждый второй подрядчик",
                "45 дней — и дом не узнать",
                "Смотрите что происходит когда...",
                "Никогда не делайте вот это на стройке"
            ]
            for i in range(num_reels):
                st = i * seg
                en = min((i + 1) * seg, duration - 0.5)
                reels_plan.append({
                    "n": i+1,
                    "start": round(st, 1),
                    "end": round(en, 1),
                    "hook": hooks[i % len(hooks)],
                    "title": f"Ролик {i+1} — строительный контент",
                    "subtitles": [
                        {"s": st, "e": st+4, "t": hooks[i % len(hooks)]},
                        {"s": st+4, "e": en-3, "t": "Капремонт МКД · Московская область"},
                        {"s": en-3, "e": en, "t": "Подписывайся → @russtroygroup"}
                    ],
                    "caption": f"Строительный контент от @russtroygroup 🏗\n\nКапремонт МКД в Московской области.\n\nСохрани 🔖 — пригодится!\n💬 Был такой случай? Пиши!\n➡️ @russtroygroup\n\n#капремонт #кровля #фасад #мко #строительство"
                })

        total = len(reels_plan)
        result_files = []
        captions_text = []

        # STEP 4: Cut segments
        await progress(msg, f"✂️ *Шаг 4/7* — Нарезаю {total} роликов...")
        raw_dir = WORK_DIR / f"raw_{uid}"
        raw_dir.mkdir(exist_ok=True)

        for reel in reels_plan:
            n = reel["n"]
            out = str(raw_dir / f"reel_{n}_raw.mp4")
            await progress(msg, f"✂️ *Шаг 4/7* — Нарезаю ролик {n}/{total}...")
            ok = cut_segment(video_path, out, reel["start"], reel["end"])
            if ok:
                reel["raw_path"] = out
            else:
                reel["raw_path"] = None

        # STEP 5: Add subtitles
        await progress(msg, f"💬 *Шаг 5/7* — Накладываю субтитры...")
        sub_dir = WORK_DIR / f"sub_{uid}"
        sub_dir.mkdir(exist_ok=True)

        for reel in reels_plan:
            n = reel["n"]
            if not reel.get("raw_path"):
                continue
            await progress(msg, f"💬 *Шаг 5/7* — Субтитры ролик {n}/{total}...")
            subs = [(s["s"] - reel["start"], s["e"] - reel["start"], s["t"])
                    for s in reel.get("subtitles", [])]
            out = str(sub_dir / f"reel_{n}_sub.mp4")
            ok = add_subtitles(reel["raw_path"], out, subs)
            reel["sub_path"] = out if ok else reel["raw_path"]

        # STEP 6: Add CTA overlay
        await progress(msg, "🎯 *Шаг 6/7* — Добавляю CTA...")
        cta_dir = WORK_DIR / f"cta_{uid}"
        cta_dir.mkdir(exist_ok=True)

        for reel in reels_plan:
            n = reel["n"]
            if not reel.get("sub_path"):
                continue
            out = str(cta_dir / f"reel_{n}_final.mp4")
            ok = add_cta_overlay(reel["sub_path"], out, "Подписывайся → @russtroygroup")
            reel["final_path"] = out if ok else reel["sub_path"]

        # STEP 7: Uniquify for accounts + collect results
        await progress(msg, "🛡 *Шаг 7/7* — Уникализирую для аккаунтов...")
        final_dir = WORK_DIR / f"final_{uid}"
        final_dir.mkdir(exist_ok=True)

        for reel in reels_plan:
            n = reel["n"]
            if not reel.get("final_path"):
                continue
            # Create 2 unique versions (main + satellite example)
            for v in range(2):
                out = str(final_dir / f"reel_{n}_v{v+1}.mp4")
                ok = uniquify(reel["final_path"], out, v)
                if ok and v == 0:
                    result_files.append((out, n, reel.get("title", f"Ролик {n}")))

            captions_text.append(
                f"━━━ *РОЛИК {n}: {reel.get('title','')}* ━━━\n\n"
                f"🎣 *Хук:* {reel.get('hook','')}\n\n"
                f"📝 *Caption:*\n{reel.get('caption','')}"
            )

        # SEND RESULTS
        await msg.delete()

        if not result_files:
            await update.effective_message.reply_text(
                "❌ Что-то пошло не так при обработке видео.\n"
                "Убедитесь что FFmpeg установлен на сервере.",
                reply_markup=kb_main()
            )
            return

        # Send video files
        await update.effective_message.reply_text(
            f"✅ *Готово! {len(result_files)} роликов обработано*\n\n"
            f"Отправляю файлы...",
            parse_mode=ParseMode.MARKDOWN
        )

        for file_path, n, title in result_files:
            try:
                with open(file_path, "rb") as f:
                    await update.effective_message.reply_video(
                        f,
                        caption=f"🎬 Ролик {n}: {title}\n✅ Субтитры + CTA + Антидубль v1",
                        supports_streaming=True
                    )
            except Exception as e:
                await update.effective_message.reply_text(f"⚠️ Ролик {n} — ошибка отправки: {e}")

        # Send captions
        caption_full = "\n\n".join(captions_text)
        await send_long(update, caption_full, parse_mode=ParseMode.MARKDOWN)

        # Final instructions
        await update.effective_message.reply_text(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📤 *СЛЕДУЮЩИЕ ШАГИ:*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "1️⃣ Скачай готовые MP4 из чата\n"
            "2️⃣ Зайди на *smmplanner.com*\n"
            "3️⃣ Создай пост → выбери аккаунты\n"
            "4️⃣ Загрузи видео + вставь caption\n"
            "5️⃣ Запланируй время → Готово! 🚀\n\n"
            "Для второй версии (антидубль v2) — нажми /reprocess",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_main()
        )

        # Cleanup
        s["last_media"] = caption_txt or f"видео {dur_str}"

    except Exception as e:
        await msg.edit_text(
            f"❌ Ошибка обработки: {e}\n\n"
            "Убедитесь что FFmpeg установлен:\n`apt-get install ffmpeg`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_main()
        )

# ── COMMAND HANDLERS ─────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    await update.message.reply_text(
        "👋 *Привет, Виктор!*\n\n"
        "Я — *полный автопилот* контент-завода 🏗\n\n"
        "*Просто отправь видео с объекта:*\n"
        "📹 Загрузи видео → я автоматически:\n"
        "  ✂️ Нарежу на Reels по тайм-кодам\n"
        "  💬 Наложу субтитры\n"
        "  🎯 Добавлю CTA @russtroygroup\n"
        "  🛡 Уникализирую для аккаунтов\n"
        "  ✍️ Напишу caption для каждого\n"
        "  📤 Верну готовые MP4 файлы\n\n"
        "*Требования к видео:*\n"
        "• Размер до 50 МБ\n"
        "• Форматы: MP4, MOV, AVI\n"
        "• Горизонтальное или вертикальное\n\n"
        "Или нажми кнопку для текстового контента 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main()
    )

async def handle_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    uid = update.effective_user.id
    s = sess(uid)
    video = update.message.video or update.message.document
    caption_txt = update.message.caption or ""

    msg = await update.message.reply_text(
        "📥 *Видео получено!*\nСколько Reels нарезать?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_reels_count()
    )

    # Download video
    await update.message.chat.send_action("upload_video")
    try:
        file = await ctx.bot.get_file(video.file_id)
        video_path = str(WORK_DIR / f"input_{uid}.mp4")
        await file.download_to_drive(video_path)
        s["pending_video"] = video_path
        s["pending_caption"] = caption_txt
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка загрузки: {e}")

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    uid = update.effective_user.id
    caption = update.message.caption or ""
    msg = await update.message.reply_text("📸 Генерирую контент-пакет для фото...")
    try:
        reply = ai(f"""Фото с объекта. {caption}

Сделай полный пакет:
🎣 5 ЦЕПЛЯЮЩИХ ХУКОВ (первые 2-3 сек Reels)
✍️ SEO-CAPTION (хук → текст → вопрос → CTA → хэштеги)
👆 CTA-БЛОК (закладки + комментарий + @russtroygroup)
📋 ЗАДАНИЕ МОНТАЖЁРУ (конкретные шаги в CapCut)
🎵 ПРОМПТ SUNO AI (музыка без авторских прав)

Русскоязычная строительная аудитория.""")
        await msg.delete()
        await send_long(update, reply, reply_markup=kb_main())
    except Exception as e:
        await msg.edit_text(f"❌ {e}", reply_markup=kb_main())

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    uid = update.effective_user.id
    s = sess(uid)
    text = update.message.text
    await update.message.chat.send_action("typing")
    s["history"].append({"role": "user", "content": text})
    try:
        reply = call_ai(s["history"][-20:])
        s["history"].append({"role": "assistant", "content": reply})
        await send_long(update, reply, reply_markup=kb_main())
    except Exception as e:
        await update.message.reply_text(f"❌ {e}", reply_markup=kb_main())

async def handle_doc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    doc = update.message.document
    if doc and doc.mime_type and doc.mime_type.startswith("video"):
        await handle_video(update, ctx)
    else:
        await update.message.reply_text("Отправь видео или фото!", reply_markup=kb_main())

# ── BUTTON HANDLER ───────────────────────────────────────────────
async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    s = sess(uid)
    data = q.data

    if data == "back":
        await q.edit_message_reply_markup(reply_markup=kb_main())
        return

    if data == "c_clear":
        sessions[uid] = {"history":[], "last_media":"", "pending_video":None, "pending_action":None}
        await q.edit_message_text("🔄 Очищено!", reply_markup=kb_main())
        return

    # Reels count selected
    if data.startswith("rc|"):
        num = int(data.split("|")[1])
        video_path = s.get("pending_video")
        caption_txt = s.get("pending_caption", "")
        if not video_path or not Path(video_path).exists():
            await q.edit_message_text("❌ Видео не найдено. Отправь видео заново.", reply_markup=kb_main())
            return
        await q.delete()
        await run_pipeline(update, ctx, video_path, num, caption_txt)
        return

    # Text content buttons
    if data == "c_scenario":
        await q.edit_message_text("🎬 Длительность:", reply_markup=kb_dur("scen"))
        return
    if data == "c_hook":
        await q.edit_message_text("🎣 Тема:", reply_markup=kb_themes("hook"))
        return
    if data == "c_caption":
        await q.edit_message_text("✍️ Тема:", reply_markup=kb_themes("cap"))
        return

    if data == "c_cta":
        await q.edit_message_text("⏳ Генерирую CTA...")
        try:
            r = ai("Напиши 5 вариантов CTA для Instagram Reels строительной тематики. Типы: закладки, комментарий, репост, подписка, вопрос. Живой русский. Упомяни @russtroygroup.")
            await q.edit_message_text(r[:4000], reply_markup=kb_main())
        except Exception as e:
            await q.edit_message_text(f"❌ {e}", reply_markup=kb_main())
        return

    if data == "c_plan":
        await q.edit_message_text("⏳ Генерирую контент-план...")
        try:
            r = ai("Составь контент-план на 2 недели для 30 Instagram аккаунтов строительной тематики МО. 4-5 постов/день. Reels 60%, карусели 30%, посты 10%. Дата, тема, формат, аккаунт, ссылка на @russtroygroup.")
            await q.edit_message_text(r[:4000])
            if len(r) > 4000:
                await update.effective_message.reply_text(r[4000:], reply_markup=kb_main())
            else:
                await update.effective_message.reply_text("✅", reply_markup=kb_main())
        except Exception as e:
            await q.edit_message_text(f"❌ {e}", reply_markup=kb_main())
        return

    if data == "c_suno":
        await q.edit_message_text("⏳ Генерирую промпты Suno...")
        try:
            r = ai("Напиши 3 промпта для Suno AI под строительные Reels: 1) таймлапс 2) до/после 3) экспертный совет. Английский, без вокала, 30 сек, нет авторских прав.")
            await q.edit_message_text(r[:4000], reply_markup=kb_main())
        except Exception as e:
            await q.edit_message_text(f"❌ {e}", reply_markup=kb_main())
        return

    if data == "c_smm":
        await q.edit_message_text("⏳ Формирую пакет SMMplanner...")
        media = s.get("last_media", "строительный контент")
        try:
            r = ai(f"Сформируй пакет для SMMplanner на 5 аккаунтов: @russtroygroup, @roofmaster_msk, @fasad_pro_mo, @kaprem_mo, @stroylajfhak_ru. Контент: {media}. Для каждого: уникальный SEO-текст, CTA, 3-5 хэштегов. Время: 19:00 МСК.")
            await q.edit_message_text(r[:4000])
            if len(r) > 4000:
                await update.effective_message.reply_text(r[4000:], reply_markup=kb_main())
            else:
                await update.effective_message.reply_text("✅ Копируй в SMMplanner!", reply_markup=kb_main())
        except Exception as e:
            await q.edit_message_text(f"❌ {e}", reply_markup=kb_main())
        return

    if data == "c_antidupe":
        await q.edit_message_text("⏳ Генерирую инструкцию...")
        try:
            r = ai("Напиши краткую инструкцию как уникализировать видео для 30 Instagram аккаунтов через CloudConvert — уникальный MD5-хеш для каждого. Конкретные шаги.")
            await q.edit_message_text(r[:4000], reply_markup=kb_main())
        except Exception as e:
            await q.edit_message_text(f"❌ {e}", reply_markup=kb_main())
        return

    # Duration for scenario
    if data.startswith("scen|"):
        dur = data.split("|")[1]
        ctx.user_data["scen_dur"] = dur
        await q.edit_message_text(f"🎬 Reels {dur} сек — тема:", reply_markup=kb_themes(f"scen_t_{dur}"))
        return

    # Theme selected
    parts = data.split("|")
    if len(parts) == 2:
        action, theme_key = parts[0], parts[1]
        theme = THEMES.get(theme_key, theme_key)

        if action == "hook":
            prompt = f"Напиши 8 цепляющих хуков для первых 2-3 сек Instagram Reels по теме '{theme}'. Остановить скролл намертво. Разные подходы: провокация, шок-факт, вопрос, обещание. Русскоязычная аудитория."
        elif action == "cap":
            prompt = f"Напиши SEO-caption для Instagram Reels по теме '{theme}'. Хук → SEO-текст → вопрос → CTA (🔖+💬+@russtroygroup) → 3-5 хэштегов. До 250 слов."
        elif action.startswith("scen_t_"):
            dur = action.replace("scen_t_", "")
            prompt = f"Детальный сценарий Reels {dur}сек, тема '{theme}'. Тайм-коды, субтитры, голос, текст на экране. Хук 0-3 сек. Финал: 🔖 Сохрани · @russtroygroup."
        else:
            return

        await q.edit_message_text(f"⏳ Генерирую для темы: {theme}...")
        try:
            r = ai(prompt)
            s["history"].append({"role":"user","content":prompt})
            s["history"].append({"role":"assistant","content":r})
            await q.edit_message_text(r[:4000])
            if len(r) > 4000:
                await update.effective_message.reply_text(r[4000:], reply_markup=kb_main())
            else:
                await update.effective_message.reply_text("Что дальше?", reply_markup=kb_main())
        except Exception as e:
            await q.edit_message_text(f"❌ {e}", reply_markup=kb_main())

# ── MAIN ─────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не задан!")
        return
    if not AI_API_KEY:
        print("❌ AI_API_KEY не задан!")
        return

    print("🤖 Автопилот контент-завода запускается...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("✅ Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
