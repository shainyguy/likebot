import vk_api
from vk_api.utils import get_random_id
import os
import sys
import time

print("=" * 50)
print("ПОПЫТКА ВОЙТИ В БЕСЕДУ")
print("=" * 50)
sys.stdout.flush()

TOKEN = os.environ.get("VK_TOKEN", "")
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))
CHAT_ID = int(os.environ.get("CHAT_ID", "210"))
PEER_ID = 2000000000 + CHAT_ID
INVITE_LINK = os.environ.get("INVITE_LINK", "")

vk_session = vk_api.VkApi(token=TOKEN)
vk = vk_session.get_api()

# === Шаг 1: Проверяем группу ===
print("\n[1] Проверяю группу...")
sys.stdout.flush()
try:
    info = vk.groups.getById(group_id=GROUP_ID)
    print(f"    OK: {info[0]['name']}")
    print(f"    ID: {GROUP_ID}")
    sys.stdout.flush()
except Exception as e:
    print(f"    ОШИБКА: {e}")
    sys.stdout.flush()

# === Шаг 2: Пробуем войти по ссылке ===
if INVITE_LINK:
    print(f"\n[2] Пробую войти по ссылке...")
    sys.stdout.flush()
    try:
        result = vk.messages.joinChatByInviteLink(link=INVITE_LINK)
        print(f"    OK! Вошёл в чат: {result}")
        sys.stdout.flush()
    except vk_api.exceptions.ApiError as e:
        print(f"    Ошибка (код {e.code}): {e}")
        if e.code == 935:
            print("    Бот УЖЕ в этой беседе")
        elif e.code == 7:
            print("    Нет прав на вход по ссылке")
        sys.stdout.flush()

# === Шаг 3: Получаем список бесед бота ===
print(f"\n[3] Ищу все беседы где есть бот...")
sys.stdout.flush()
try:
    convos = vk.messages.getConversations(
        count=20,
        filter="all"
    )
    print(f"    Найдено бесед: {convos['count']}")
    
    for item in convos["items"]:
        peer = item["conversation"]["peer"]
        peer_id = peer["id"]
        peer_type = peer["type"]
        
        if peer_type == "chat":
            chat_id = peer_id - 2000000000
            
            # Получаем инфо о беседе
            try:
                chat_info = vk.messages.getConversationsById(
                    peer_ids=peer_id
                )
                chat_title = "?"
                if chat_info["items"]:
                    settings = chat_info["items"][0].get(
                        "chat_settings", {}
                    )
                    chat_title = settings.get("title", "?")
            except Exception:
                chat_title = "?"
            
            marker = " <<<< ВОТ ОН!" if chat_id == CHAT_ID else ""
            print(f"    💬 peer_id={peer_id} chat_id={chat_id} "
                  f"название='{chat_title}'{marker}")
    
    sys.stdout.flush()
except Exception as e:
    print(f"    ОШИБКА: {e}")
    sys.stdout.flush()

# === Шаг 4: Пробуем отправить в каждую найденную беседу ===
print(f"\n[4] Пробую отправить в PEER_ID={PEER_ID}...")
sys.stdout.flush()
try:
    vk.messages.send(
        peer_id=PEER_ID,
        message="🤖 Тест — бот работает!",
        random_id=get_random_id()
    )
    print("    ✅ УСПЕХ!")
    sys.stdout.flush()
except vk_api.exceptions.ApiError as e:
    print(f"    ❌ Ошибка (код {e.code}): {e}")
    sys.stdout.flush()
    
    # Пробуем все найденные беседы
    print(f"\n[5] Пробую отправить во ВСЕ найденные беседы...")
    sys.stdout.flush()
    try:
        convos = vk.messages.getConversations(
            count=20,
            filter="all"
        )
        for item in convos["items"]:
            peer = item["conversation"]["peer"]
            pid = peer["id"]
            if peer["type"] == "chat":
                try:
                    vk.messages.send(
                        peer_id=pid,
                        message="🤖 Тест! Если видите это — "
                                "скопируйте число и "
                                "отправьте разработчику: "
                                f"CHAT_ID = {pid - 2000000000}",
                        random_id=get_random_id()
                    )
                    real_chat_id = pid - 2000000000
                    print(f"    ✅ Отправлено в peer_id={pid} "
                          f"(CHAT_ID={real_chat_id})")
                    sys.stdout.flush()
                except Exception as e2:
                    print(f"    ❌ peer_id={pid}: {e2}")
                    sys.stdout.flush()
    except Exception as e3:
        print(f"    Ошибка: {e3}")
        sys.stdout.flush()

print("\n" + "=" * 50)
print("ГОТОВО. Смотри результаты выше.")
print("=" * 50)
sys.stdout.flush()

# Держим процесс живым чтобы логи не пропали
time.sleep(300)
