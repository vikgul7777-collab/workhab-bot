# 🤖 Контент-завод — Полный Автопилот

## Что делает бот автоматически

1. ✂️ Нарезает видео на Reels по ИИ-тайм-кодам
2. 💬 Накладывает субтитры (FFmpeg)
3. 🎯 Добавляет CTA @russtroygroup в финале
4. 🛡 Уникализирует для разных аккаунтов
5. ✍️ Генерирует SEO-caption для каждого ролика
6. 📤 Возвращает готовые MP4 в Telegram

## Переменные окружения (Railway → Variables)

| Переменная | Значение |
|---|---|
| BOT_TOKEN | Токен от @BotFather в Telegram |
| AI_API_KEY | Ключ от aitunnel.ru |
| AI_BASE_URL | https://api.aitunnel.ru/v1/messages |
| AI_MODEL | claude-sonnet-4-6 |
| ALLOWED_USER_ID | Ваш Telegram ID (узнать у @userinfobot) |

## Деплой на Railway (шаг за шагом)

### 1. Создай бота
- Telegram → @BotFather → /newbot
- Скопируй токен

### 2. Узнай свой Telegram ID
- Напиши @userinfobot → скопируй ID

### 3. Загрузи на GitHub
- github.com → New repository → Upload files
- Загрузи все файлы из этой папки

### 4. Задеплой на Railway
- railway.app → New Project → Deploy from GitHub
- Выбери репозиторий
- Variables → добавь все переменные выше
- Deploy!

## Ограничения

- Видео до 50 МБ (лимит Telegram Bot API)
- Для больших файлов используй /sendvideo через сжатие
- Публикация в Instagram — вручную через SMMplanner

## Цепочка публикации

Видео → Бот → Готовые Reels → SMMplanner → 30 аккаунтов Instagram
