# Funpay Steam AutoPoints
🚀 Бот для автоматизации продаж очков Steam на FunPay  
📌 Сейчас в стадии бета-тестирования
  
## Что из себя представляет бот?  
Это Python-скрипт, который:  
✔ Автоматически отправляет очки Steam.  
✔ Гибкая настройка бота.  
✔ Курс 11₽ / 1000.  
✔ Деактивирет лоты, если мало денег.  
  
## Что нужно для работы бота?  
1. Установка Python и библиотек
```pip install -r requirements.txt```
2. Настройка .env
```
FUNPAY_AUTH_TOKEN=ваш_золотой_ключ_FunPay  
BSP_API_KEY=ваш_апиключ
CATEGORY_ID=714
REQUEST_TIMEOUT=300
MIN_POINTS=100
AUTO_REFUND=true/false
AUTO_DEACTIVATE=true/false
BSP_MIN_BALANCE=1.0
DEACTIVATE_CATEGORY_ID=714
```
3. Получить API Key в боте [PointsHUB](https://t.me/pointshub_bot)

Более подробная [Инструкция](https://teletype.in/@tinechelovec/Funpay-Steam-AutoPoints)
   
По всем багам, вопросам и предложениям пишите в [Issues](https://github.com/tinechelovec/Funpay-Steam-AutoPoints/issues) или в [Telegram](https://t.me/tinechelovec)

Другие боты и плагины [Channel](https://t.me/by_thc)
