import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id
import os
import sys
import time

print("=" * 50)
print("ЗАПУСК БОТА")
print("=" * 50)
sys.stdout.flush()

# Читаем переменные
TOKEN = os.environ.get("VK_TOKEN", "")
GROUP_ID = os.environ.get("GROUP_ID", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
ADMINS_STR = os.environ.get("ADMINS", "")

# Проверяем что все есть
print(f"VK_TOKEN: {'ЕСТЬ (' + TOKEN[:15] + '...)' if TOKEN else 'НЕТ!!!'}")
print(f"GROUP_ID: {GROUP_ID if GROUP_ID else 'НЕТ!!!'}")
print(f"CHAT_ID: {CHAT_ID if CHAT_ID else 'НЕТ!!!'}")
print(f"ADMINS: {ADMINS_STR if ADMINS_STR else 'НЕТ!!!'}")
sys.stdout.flush()

if not TOKEN:
    print("ОШИБКА: Добавь переменную VK_TOKEN в Railway Variables!")
    sys.exit(1)

if not GROUP_ID:
    print("ОШИБКА: Добавь переменную GROUP_ID в Railway Variables!")
    sys.exit(1)

GROUP_ID = int(GROUP_ID)
CHAT_ID = int(CHAT_ID) if CHAT_ID else 1
PEER_ID = 2000000000 + CHAT_ID
ADMINS = []
if ADMINS_STR:
    ADMINS = [int(x.strip()) for x in ADMINS_STR.split(",") if x.strip()]

print(f"\nPEER_ID: {PEER_ID}")
print(f"ADMINS: {ADMINS}")
sys.stdout.flush()

# Подключение
print("\n[1] Подключаюсь к VK...")
sys.stdout.flush()
try:
    vk_session = vk_api.VkApi(token=TOKEN)
    vk = vk_session.get_api()
    print("    OK - подключился")
    sys.stdout.flush()
except Exception as e:
    print(f"    ОШИБКА: {e}")
    sys.exit(1)

# Проверка группы
print("\n[2] Проверяю группу...")
sys.stdout.flush()
try:
    info = vk.groups.getById(group_id=GROUP_ID)
    print(f"    OK - группа: {info[0]['name']}")
    sys.stdout.flush()
except Exception as e:
    print(f"    ОШИБКА: {e}")
    sys.stdout.flush()
    sys.exit(1)

# Отправка тестового сообщения
print("\n[3] Отправляю тестовое сообщение в чат...")
sys.stdout.flush()
try:
    vk.messages.send(
        peer_id=PEER_ID,
        message="🤖 Бот запущен и работает!",
        random_id=get_random_id()
    )
    print("    OK - сообщение отправлено!")
    sys.stdout.flush()
except vk_api.exceptions.ApiError as e:
    print(f"    ОШИБКА (код {e.code}): {e}")
    if e.code == 901:
        print("    >>> Бот НЕ добавлен в беседу!")
        print("    >>> Или CHAT_ID неправильный!")
    elif e.code == 917:
        print("    >>> Включи 'Сообщения сообщества'!")
    elif e.code == 925:
        print("    >>> Включи 'Возможности ботов'!")
    elif e.code == 7:
        print("    >>> Нет прав! Пересоздай токен!")
    sys.stdout.flush()
    sys.exit(1)

# Long Poll
print("\n[4] Запускаю Long Poll...")
sys.stdout.flush()
try:
    longpoll = VkBotLongPoll(vk_session, GROUP_ID)
    print("    OK - Long Poll работает")
    sys.stdout.flush()
except Exception as e:
    print(f"    ОШИБКА: {e}")
    print("    >>> Включи Long Poll API в настройках группы!")
    sys.stdout.flush()
    sys.exit(1)

print("\n" + "=" * 50)
print("ВСЁ РАБОТАЕТ! ЖДУ СООБЩЕНИЯ...")
print("=" * 50)
sys.stdout.flush()

# Главный цикл
while True:
    try:
        for event in longpoll.listen():
            if event.type == VkBotEventType.MESSAGE_NEW:
                msg = event.object.get("message", event.object)
                peer = msg.get("peer_id", 0)
                from_id = msg.get("from_id", 0)
                text = msg.get("text", "")

                print(f"\nПОЛУЧЕНО: peer={peer} from={from_id} text='{text}'")
                sys.stdout.flush()

                if peer == PEER_ID:
                    try:
                        vk.messages.send(
                            peer_id=PEER_ID,
                            message=f"✅ Я работаю! Получил: «{text}»\n\nОт: id{from_id}",
                            random_id=get_random_id()
                        )
                        print("ОТВЕТ ОТПРАВЛЕН")
                        sys.stdout.flush()
                    except Exception as e:
                        print(f"ОШИБКА ОТВЕТА: {e}")
                        sys.stdout.flush()
                else:
                    print(f"Сообщение не из нашего чата (наш: {PEER_ID})")
                    sys.stdout.flush()

    except Exception as e:
        print(f"ОШИБКА LONGPOLL: {e}")
        sys.stdout.flush()
        time.sleep(5)
