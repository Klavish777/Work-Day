# Work-Day AI Trader — Android (APK)

Мобильный клиент для системы автотрейдинга. Торговый бэкенд (Python +
MT5 + LLM-агенты) работает на вашем ПК рядом с терминалом MetaTrader 5;
APK — это панель управления, подключающаяся к нему по локальной сети.

## Архитектура

```
[ПК с Windows]                          [Android]
MT5 terminal                            Work-Day APK
  ↕ MetaTrader5 python package            ↕ WebView (http://ПК:8080)
python main.py  ← REST API + дашборд ←──┘
```

Почему так: официальный пакет `MetaTrader5` работает только на Windows и
управляет локальным терминалом — поэтому исполнение ордеров всегда живёт на
ПК, а телефон служит интерфейсом контроля (старт/стоп, параметры,
закрытие позиций, режим DEMO/REAL, журнал).

## Сборка APK

### Вариант 1 — автоматически (рекомендуется)
Запустите GitHub Actions workflow **«Build Android APK»**
(вкладка Actions → Build Android APK → Run workflow). Готовый файл
`app-debug.apk` появится в артефактах запуска.

### Вариант 2 — локально
1. Установите Android Studio (или JDK 17 + Android SDK 34).
2. Откройте папку `android/` как проект.
3. `Build → Build APK` (или `gradle assembleDebug`).
4. Файл: `android/app/build/outputs/apk/debug/app-debug.apk`.

## Подключение
1. На ПК запустите `python main.py` (порт по умолчанию 8080).
2. Узнайте LAN IP ПК (`ipconfig`), например `192.168.1.10`.
3. В APK при первом запуске введите `http://192.168.1.10:8080`.
   В эмуляторе Android адрес хоста — `http://10.0.2.2:8080` (по умолчанию).

## Альтернатива без APK — PWA
Дашборд является устанавливаемым PWA: откройте `http://ПК:8080` в Chrome на
Android → меню → «Добавить на главный экран». Работает как приложение.
