from flask import Flask, jsonify, request
from flask_cors import CORS
import cloudscraper
import requests
import time
import os

start = Flask(__name__)
CORS(start)

scraper = cloudscraper.create_scraper()

# ==========================
# Настройки кеша
# ==========================
CACHE_TTL = 15  # кеш на 20 секунд
cache = {}  # кеш по username и платформе


# ==========================
# W.TV функции
# ==========================
def get_user_id(username):
    try:
        url = f"https://profiles-service.w.tv/api/v1/profiles/by-nickname/{username}?user_lang=ru"
        r = scraper.get(url, timeout=5)
        if r.status_code != 200:
            print("Статус код профиля WTV:", r.status_code)
            return None
        data = r.json()
        user_id = data.get("profile", {}).get("userId")
        return user_id
    except Exception as e:
        print("Ошибка получения userId WTV:", e)
        return None


def get_viewers_by_id(user_id):
    try:
        url = f"https://streams-search-service.w.tv/api/v1/channels/{user_id}?user_lang=ru"
        r = scraper.get(url, timeout=5)
        if r.status_code != 200:
            print("Статус код канала WTV:", r.status_code)
            return 0
        data = r.json()
        viewers = data.get("channel", {}).get("liveStream", {}).get("viewers", 0)
        return viewers
    except Exception as e:
        print("Ошибка получения viewers WTV:", e)
        return 0


# ==========================
# Twitch функции
# ==========================
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_TOKEN = None
TWITCH_TOKEN_EXPIRES = 0


def get_twitch_token():
    global TWITCH_TOKEN, TWITCH_TOKEN_EXPIRES
    if time.time() < TWITCH_TOKEN_EXPIRES and TWITCH_TOKEN:
        return TWITCH_TOKEN

    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }

    r = requests.post(url, params=params)
    data = r.json()

    TWITCH_TOKEN = data.get("access_token")
    TWITCH_TOKEN_EXPIRES = time.time() + data.get("expires_in", 0) - 60
    return TWITCH_TOKEN


def get_twitch_viewers(username):
    try:
        token = get_twitch_token()
        headers = {
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}"
        }
        url = f"https://api.twitch.tv/helix/streams?user_login={username}"
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code != 200:
            print("Twitch статус:", r.status_code)
            return 0

        data = r.json().get("data", [])
        if not data:
            return 0

        return data[0].get("viewer_count", 0)
    except Exception as e:
        print("Ошибка Twitch:", e)
        return 0


# ==========================
# Kick функции (исправленные)
# ==========================
KICK_IDENTIFIER = os.getenv("KICK_IDENTIFIER")
KICK_API_KEY = os.getenv("KICK_API_KEY")
KICK_TOKEN = None
KICK_TOKEN_EXPIRES = 0


def get_kick_token():
    """Получение токена доступа для Kick API через Client Credentials flow"""
    global KICK_TOKEN, KICK_TOKEN_EXPIRES

    # Проверяем, не истек ли текущий токен
    if time.time() < KICK_TOKEN_EXPIRES and KICK_TOKEN:
        return KICK_TOKEN

    try:
        # Правильный endpoint для токена Kick OAuth
        url = "https://id.kick.com/oauth/token"

        # Данные для получения токена (client credentials flow)
        # Важно: Content-Type должен быть application/x-www-form-urlencoded
        data = {
            "grant_type": "client_credentials",
            "client_id": KICK_IDENTIFIER,  # Исправлено!
            "client_secret": KICK_API_KEY  # Исправлено!
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        print(f"Запрос токена Kick к {url}")
        r = requests.post(url, data=data, headers=headers, timeout=5)

        print(f"Статус получения токена Kick: {r.status_code}")

        if r.status_code != 200:
            print(f"Ответ при ошибке токена: {r.text}")
            return None

        token_data = r.json()
        KICK_TOKEN = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 3600)  # обычно 3600 секунд (1 час)
        KICK_TOKEN_EXPIRES = time.time() + expires_in - 60  # запас в 60 секунд

        print("Токен Kick успешно получен")
        return KICK_TOKEN

    except Exception as e:
        print(f"Ошибка при получении токена Kick: {e}")
        return None


def get_kick_viewers(username):
    try:
        # Получаем токен
        token = get_kick_token()
        if not token:
            print("Не удалось получить токен Kick")
            return 0

        # Формируем запрос к API для получения информации о канале
        # Используем параметр slug для поиска по имени пользователя
        url = f"https://api.kick.com/public/v1/channels"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }

        params = {
            "slug": username.lower()  # Kick использует slug (обычно в нижнем регистре)
        }

        print(f"Запрос к Kick API: {url} с slug={username}")
        r = requests.get(url, headers=headers, params=params, timeout=5)

        print(f"Kick статус: {r.status_code}")

        if r.status_code != 200:
            print(f"Ответ Kick при ошибке: {r.text}")
            return 0

        data = r.json()

        # Проверяем структуру ответа согласно документации Kick API [citation:1]
        if "data" in data and len(data["data"]) > 0:
            channel_data = data["data"][0]

            # Проверяем, есть ли информация о стриме
            if "stream" in channel_data:
                stream = channel_data["stream"]
                # Проверяем, идет ли стрим
                if stream.get("is_live", False):
                    return stream.get("viewer_count", 0)

        # Если стрим не найден или не активен
        return 0

    except Exception as e:
        print(f"Ошибка Kick: {e}")
        return 0


# ==========================
# VK Video Live функции
# ==========================
def get_vk_viewers(slug):
    try:
        url = f"https://live.vkvideo.ru/api/web/channel/{slug}"
        r = requests.get(url, timeout=5)

        if r.status_code != 200:
            print(f"VK Live статус {r.status_code} для {slug}")
            return 0

        data = r.json()

        channel = data.get("channel")
        if not channel:
            print(f"Канал {slug} не найден или оффлайн")
            return 0

        stream = channel.get("stream")
        # Проверяем, что стрим идёт
        if not stream or not stream.get("is_live", False):
            return 0

        return stream.get("viewers", 0)

    except Exception as e:
        print(f"Ошибка VK Live для {slug}: {e}")
        return 0


# ==========================
# Универсальный API маршрут
# ==========================
@start.route("/viewers")
def viewers():
    username = request.args.get("username")
    platform = request.args.get("platform", "wtv")

    if not username:
        return jsonify({"error": "username parameter required"})

    now = time.time()
    cache_key = f"{platform}:{username}"

    # Проверка кеша
    if cache_key in cache:
        cached_time, cached_value = cache[cache_key]
        if now - cached_time < CACHE_TTL:
            return jsonify({platform: cached_value})

    # ======================
    # W.TV
    # ======================
    if platform == "wtv":
        user_id = get_user_id(username)
        if not user_id:
            cache[cache_key] = (now, 0)
            return jsonify({"wtv": 0})

        viewers_count = get_viewers_by_id(user_id)
        cache[cache_key] = (now, viewers_count)
        return jsonify({"wtv": viewers_count})

    # ======================
    # Twitch
    # ======================
    elif platform == "twitch":
        viewers_count = get_twitch_viewers(username)
        cache[cache_key] = (now, viewers_count)
        return jsonify({"twitch": viewers_count})

    # ======================
    # Kick
    # ======================
    elif platform == "kick":
        viewers_count = get_kick_viewers(username)
        cache[cache_key] = (now, viewers_count)
        return jsonify({"kick": viewers_count})

    # ======================
    # VK Video Live
    # ======================
    elif platform == "vk":
        viewers_count = get_vk_viewers(username)  # username = slug из URL
        cache[cache_key] = (now, viewers_count)
        return jsonify({"vk": viewers_count})

    else:
        return jsonify({"error": "unknown platform"})

# ==========================
# Запуск сервера
# ==========================
if __name__ == "__main__":
    start.run()





