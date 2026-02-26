import vk_api
from vk_api.utils import get_random_id
import os
import sys

TOKEN = os.environ.get("VK_TOKEN", "")
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))
CHAT_ID = int(os.environ.get("CHAT_ID", "210"))

print(f"Добавляю группу {GROUP_ID} в чат {CHAT_ID}...")
sys.stdout.flush()

# Нужен ПОЛЬЗОВАТЕЛЬСКИЙ токен для добавления
# Но сначала попробуем через токен группы

vk_session = vk_api.VkApi(token=TOKEN)
vk = vk_session.get_api()

try:
    # Попытка отправить сообщение
    vk.messages.send(
        peer_id=2000000000 + CHAT_ID,
        message="Тест",
        random_id=get_random_id()
    )
    print("УЖЕ РАБОТАЕТ! Бот в чате!")
except vk_api.exceptions.ApiError as e:
    print(f"Ошибка: {e}")
    print(f"Код: {e.code}")
    
    if e.code == 917:
        print("\nБот НЕ в беседе.")
        print("Нужно добавить вручную (см. инструкцию)")
    
sys.stdout.flush()
