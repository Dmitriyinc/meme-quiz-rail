# memequiz_bot — фінальна архітектура

## Схема роботи
```
Sendpulse чейн (до квізу)
  → кнопка WebApp → відкриває квіз на Vercel
  → юзер проходить квіз
  → WebApp: tg.sendData() → Telegram → bot.py на Railway
  → bot.py:
      1. Знаходить контакт в Sendpulse по telegram_id
      2. Записує змінні: score, total, passed_quiz, attempt_num
      3. Ставить теги: quiz_passed / quiz_failed
      4. Запускає потрібний флоу в Sendpulse
      5. Пише в Google Sheets
      6. Сповіщає адміна
  → Sendpulse флоу відправляє повідомлення юзеру
```

---

## Частина 1 — WebApp на Vercel (вже задеплоєно)
URL: https://dmitriyinc-meme-quiz.vercel.app

Якщо потрібно перезадеплоїти — папка `webapp/` в цьому архіві.

---

## Частина 2 — Бот на Railway

### Крок 1 — Завантаж на GitHub
Завантаж в репо ці файли (БЕЗ папки webapp/):
- bot.py
- requirements.txt
- Procfile
- runtime.txt
- .gitignore

### Крок 2 — Створи проект на Railway
1. [railway.app](https://railway.app) → New Project
2. Deploy from GitHub repo → вибери репо
3. Railway знайде Procfile і запустить бота

### Крок 3 — Додай змінні (Variables)
В Railway → твій сервіс → Variables:

| Змінна | Значення |
|--------|---------|
| `BOT_TOKEN` | токен від @BotFather |
| `ADMIN_ID` | твій Telegram ID |
| `CHANNEL_ID` | ID каналу (напр. -1001234567890) |
| `CHANNEL_USERNAME` | MEMEcrypted |
| `WEBAPP_URL` | https://dmitriyinc-meme-quiz.vercel.app |
| `SP_CLIENT_ID` | sp_id_4a6ef21f... |
| `SP_CLIENT_SECRET` | sp_sk_72ab44... |
| `SP_BOT_ID` | 69f0a309d8b94020830489fb |
| `SP_FLOW_PASSED_ID` | ID флоу для тих хто пройшов |
| `SP_FLOW_FAILED_ID` | ID флоу для тих хто не пройшов |
| `SHEETS_WEBHOOK` | URL Google Apps Script (опціонально) |
| `DENCHIK_CHAT_ID` | Telegram ID для сповіщень (опціонально) |

### Як знайти Flow ID
Sendpulse → Automation 360 → відкрий флоу → в URL буде:
`/automation/constructor/XXXXX` — це і є Flow ID

### Крок 4 — Deploy
Після додавання змінних Railway перезапустить бота.
Перевір логи — має бути `Bot starting...`

---

## Важливо
- Бот і WebApp — окремо (Railway і Vercel)
- WebApp використовує `tg.sendData()` — дані йдуть через Telegram в бот
- Бот запускає флоу напряму через Sendpulse Chatbot API по contact_id
- SQLite використовується тільки для лічильника спроб і фідбеку
- При рестарті Railway база скидається — для продакшену підключи Railway PostgreSQL
