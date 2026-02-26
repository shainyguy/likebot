import vk_api
import os
import sys

TOKEN = os.environ.get("VK_TOKEN", "")
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))

vk_session = vk_api.VkApi(token=TOKEN)
vk = vk_session.get_api()

print("Получаю ссылку-приглашение для бота...")
sys.stdout.flush()

try:
    # Получаем информацию о группе
    info = vk.groups.getById(group_id=GROUP_ID, fields="invite_link")
    print(f"Группа: {info[0]['name']}")
    
    # Получаем callback confirmation code
    result = vk.groups.getCallbackConfirmationCode(group_id=GROUP_ID)
    print(f"Код подтверждения: {result['code']}")
    
    print("\n=== ИНСТРУКЦИЯ ===")
    print("1. Открой беседу в VK")
    print("2. Получи ссылку-приглашение в беседу:")
    print("   Название беседы → Ссылка-приглашение → Скопировать")
    print("3. Открой эту ссылку в новом окне")
    print("   (убедись что ты залогинен как сообщество)")
    print("=================")
    sys.stdout.flush()
    
except Exception as e:
    print(f"Ошибка: {e}")
    sys.stdout.flush()
