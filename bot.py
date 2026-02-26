import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id
import json
import os
import re
import time
import threading
from datetime import datetime, timedelta


# ======================== НАСТРОЙКИ ========================

TOKEN = "vk1.a.hCNOOFzqh3A8BiHaPi15YoPfZa2i85zLBgJogjGBcCkWVThdoUqO3XoDYv4sUdIxQnau70lnVsURvc_bqCbUYADfBNzBflnTG9ckluyBTCfVDhB-5aizVx5MHYDBKGhq1jpWBPNcKq8tT47xlqMhtbaYucGp_taIxvHuOkX-KPIXiiHp2cW2vEB6q2xON3Z4kb1UKzkEAr9KeSfwZ_HHWw"
GROUP_ID = 236280033          # ID сообщества (без минуса)
CHAT_ID = 210                   # ID беседы (peer_id = 2000000000 + CHAT_ID)
ADMINS = [140519864]  # VK ID администраторов

# Правила лайк-чата
RULES = {
    "link_expire_hours": 24,         # Ссылка активна N часов
    "max_violations": 3,             # Нарушений до кика
    "cooldown_seconds": 300,         # Кулдаун между ссылками (5 мин)
    "max_links_per_user": 2,         # Макс активных ссылок от одного человека
    "delete_violator_msg": True,     # Удалять сообщения нарушителей
    "check_interval_minutes": 30,    # Интервал авто-проверки снятых лайков
    "require_like_percent": 100,     # Процент лайков для допуска (100=все)
}

DATA_FILE = "like_chat_data.json"
PEER_ID = 2000000000 + CHAT_ID

# ======================== ХРАНИЛИЩЕ ========================


class DataManager:
    """Хранение данных в JSON-файле"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                print("⚠️ Файл данных повреждён, создаю новый")
        return {
            "queue": [],
            "users": {},
            "banned": [],
            "next_id": 1
        }

    def save(self):
        with self.lock:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)

    # --- Очередь ссылок ---

    def add_link(self, link_info):
        with self.lock:
            link_info["id"] = self.data["next_id"]
            self.data["next_id"] += 1
            self.data["queue"].append(link_info)
        self.save()
        return link_info["id"]

    def remove_link(self, link_id):
        with self.lock:
            before = len(self.data["queue"])
            self.data["queue"] = [
                l for l in self.data["queue"] if l["id"] != link_id
            ]
            removed = len(self.data["queue"]) < before
        if removed:
            self.save()
        return removed

    def get_active_links(self, expire_hours=24):
        cutoff = (
            datetime.now() - timedelta(hours=expire_hours)
        ).isoformat()
        return [
            l for l in self.data["queue"]
            if l.get("timestamp", "") > cutoff and l.get("active", True)
        ]

    def expire_old_links(self, expire_hours=24):
        cutoff = (
            datetime.now() - timedelta(hours=expire_hours)
        ).isoformat()
        with self.lock:
            before = len(self.data["queue"])
            self.data["queue"] = [
                l for l in self.data["queue"]
                if l.get("timestamp", "") > cutoff
            ]
            removed = before - len(self.data["queue"])
        if removed > 0:
            self.save()
        return removed

    def clear_queue(self):
        with self.lock:
            count = len(self.data["queue"])
            self.data["queue"] = []
        self.save()
        return count

    def link_exists(self, content_type, owner_id, item_id):
        for l in self.data["queue"]:
            if (l["content_type"] == content_type
                    and l["owner_id"] == owner_id
                    and l["item_id"] == item_id
                    and l.get("active", True)):
                return True
        return False

    # --- Пользователи ---

    def get_user(self, user_id):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {
                "links_posted": 0,
                "likes_given": 0,
                "violations": 0,
                "last_link_time": None,
                "warned": False
            }
            self.save()
        return self.data["users"][uid]

    def update_user(self, user_id, **kwargs):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.get_user(user_id)
        with self.lock:
            self.data["users"][uid].update(kwargs)
        self.save()

    def add_violation(self, user_id):
        user = self.get_user(user_id)
        user["violations"] = user.get("violations", 0) + 1
        self.save()
        return user["violations"]

    def reset_violations(self, user_id):
        self.update_user(user_id, violations=0)

    def get_all_users(self):
        return self.data["users"]

    # --- Бан-лист ---

    def ban_user(self, user_id):
        if user_id not in self.data["banned"]:
            self.data["banned"].append(user_id)
            self.save()
            return True
        return False

    def unban_user(self, user_id):
        if user_id in self.data["banned"]:
            self.data["banned"].remove(user_id)
            self.save()
            return True
        return False

    def is_banned(self, user_id):
        return user_id in self.data["banned"]

    def get_banned(self):
        return self.data["banned"]


# ======================== ПАРСЕР ССЫЛОК ========================


class LinkParser:
    """Извлечение VK-ссылок из сообщений"""

    # wall/photo/video/clip + owner_id + _ + item_id
    LINK_PATTERN = re.compile(
        r'(wall|photo|video|clip)(-?\d+)_(\d+)'
    )

    # Маппинг типов для likes.isLiked
    TYPE_MAP = {
        "wall": "post",
        "photo": "photo",
        "video": "video",
        "clip": "video",
    }

    @classmethod
    def parse_text(cls, text):
        """Найти все VK-ссылки в тексте"""
        results = []
        seen = set()

        for match in cls.LINK_PATTERN.finditer(text):
            vk_type = match.group(1)
            owner_id = int(match.group(2))
            item_id = int(match.group(3))
            key = (vk_type, owner_id, item_id)

            if key in seen:
                continue
            seen.add(key)

            content_type = cls.TYPE_MAP.get(vk_type, "post")
            url = f"https://vk.com/{vk_type}{owner_id}_{item_id}"

            results.append({
                "content_type": content_type,
                "vk_type": vk_type,
                "owner_id": owner_id,
                "item_id": item_id,
                "url": url
            })

        return results

    @classmethod
    def parse_attachments(cls, attachments):
        """Извлечь ссылки из вложений сообщения"""
        results = []

        for att in attachments:
            att_type = att.get("type", "")
            obj = att.get(att_type, {})

            if att_type == "wall":
                owner_id = obj.get("owner_id") or obj.get("to_id")
                item_id = obj.get("id")
                if owner_id and item_id:
                    results.append({
                        "content_type": "post",
                        "vk_type": "wall",
                        "owner_id": owner_id,
                        "item_id": item_id,
                        "url": f"https://vk.com/wall{owner_id}_{item_id}"
                    })

            elif att_type in ("photo", "video"):
                owner_id = obj.get("owner_id")
                item_id = obj.get("id")
                if owner_id and item_id:
                    results.append({
                        "content_type": att_type,
                        "vk_type": att_type,
                        "owner_id": owner_id,
                        "item_id": item_id,
                        "url": f"https://vk.com/{att_type}{owner_id}_{item_id}"
                    })

            elif att_type == "link":
                url = obj.get("url", "")
                text_links = cls.parse_text(url)
                results.extend(text_links)

        return results

    @classmethod
    def parse_message(cls, text, attachments):
        """Полный парсинг сообщения"""
        links = cls.parse_text(text or "")

        if attachments:
            att_links = cls.parse_attachments(attachments)
            # Убираем дубли
            seen = {
                (l["content_type"], l["owner_id"], l["item_id"])
                for l in links
            }
            for al in att_links:
                key = (al["content_type"], al["owner_id"], al["item_id"])
                if key not in seen:
                    links.append(al)
                    seen.add(key)

        return links


# ======================== ОСНОВНОЙ БОТ ========================


class LikeChatBot:
    def __init__(self):
        self.vk_session = vk_api.VkApi(token=TOKEN)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkBotLongPoll(self.vk_session, GROUP_ID)
        self.db = DataManager(DATA_FILE)
        self.rules = RULES
        self._name_cache = {}

        print("=" * 50)
        print("✅ Лайк-чат бот запущен!")
        print(f"📍 Чат: peer_id = {PEER_ID}")
        print(f"👑 Админы: {ADMINS}")
        print("=" * 50)

    # ─────────── УТИЛИТЫ ───────────

    def send_chat(self, text):
        """Сообщение в чат"""
        try:
            chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                self.vk.messages.send(
                    peer_id=PEER_ID,
                    message=chunk,
                    random_id=get_random_id()
                )
                time.sleep(0.1)
        except Exception as e:
            print(f"Ошибка отправки в чат: {e}")

    def send_private(self, user_id, text):
        """Личное сообщение"""
        try:
            self.vk.messages.send(
                user_id=user_id,
                message=text,
                random_id=get_random_id()
            )
        except Exception as e:
            print(f"Ошибка ЛС для {user_id}: {e}")

    def delete_message(self, cmid):
        """Удалить сообщение из чата"""
        try:
            self.vk.messages.delete(
                cmids=cmid,
                peer_id=PEER_ID,
                delete_for_all=1
            )
            return True
        except Exception as e:
            print(f"Не удалось удалить сообщение: {e}")
            return False

    def kick_user(self, user_id):
        """Кикнуть из чата"""
        try:
            self.vk.messages.removeChatUser(
                chat_id=CHAT_ID,
                member_id=user_id
            )
            return True
        except Exception as e:
            print(f"Не удалось кикнуть {user_id}: {e}")
            return False

    def get_name(self, user_id):
        """Имя пользователя (с кешем)"""
        if user_id in self._name_cache:
            return self._name_cache[user_id]
        try:
            u = self.vk.users.get(user_ids=user_id)[0]
            name = f"{u['first_name']} {u['last_name']}"
            self._name_cache[user_id] = name
            return name
        except Exception:
            return f"id{user_id}"

    def mention(self, user_id):
        """Упоминание пользователя"""
        name = self.get_name(user_id)
        return f"[id{user_id}|{name}]"

    def is_admin(self, user_id):
        return user_id in ADMINS

    def resolve_user(self, text):
        """Распознать пользователя: id, ссылка, @username"""
        text = text.strip().lstrip("@").lstrip("[").rstrip("]")

        # [id123|Имя] — из упоминаний VK
        match = re.match(r'id(\d+)', text)
        if match:
            return int(match.group(1))

        if text.isdigit():
            return int(text)

        match = re.search(r'vk\.com/id(\d+)', text)
        if match:
            return int(match.group(1))

        match = re.search(r'vk\.com/([a-zA-Z0-9_.]+)', text)
        screen = match.group(1) if match else text

        if re.match(r'^[a-zA-Z0-9_.]+$', screen):
            try:
                result = self.vk.utils.resolveScreenName(
                    screen_name=screen
                )
                if result and result.get("type") == "user":
                    return result["object_id"]
            except Exception:
                pass

        return None

    # ─────────── ПРОВЕРКА ЛАЙКОВ ───────────

    def check_like(self, user_id, content_type, owner_id, item_id):
        """
        Проверить лайк. Возвращает: True / False / None (ошибка)
        """
        try:
            r = self.vk.likes.isLiked(
                user_id=user_id,
                type=content_type,
                owner_id=owner_id,
                item_id=item_id
            )
            return r.get("liked", 0) == 1
        except vk_api.exceptions.ApiError as e:
            # 15 = Access denied, 100 = invalid params
            print(f"  API ошибка likes.isLiked: {e}")
            return None
        except Exception as e:
            print(f"  Ошибка check_like: {e}")
            return None

    def check_user_all_links(self, user_id):
        """
        Проверить все активные ссылки для пользователя.
        Пропускает его собственные ссылки.
        Возвращает: (liked_list, not_liked_list, error_list)
        """
        active = self.db.get_active_links(self.rules["link_expire_hours"])
        liked = []
        not_liked = []
        errors = []

        for link in active:
            # Свои ссылки не проверяем
            if link["user_id"] == user_id:
                continue

            result = self.check_like(
                user_id,
                link["content_type"],
                link["owner_id"],
                link["item_id"]
            )
            time.sleep(0.35)

            if result is True:
                liked.append(link)
            elif result is False:
                not_liked.append(link)
            else:
                errors.append(link)

        return liked, not_liked, errors

    # ─────────── ОБРАБОТКА ССЫЛКИ ───────────

    def handle_new_link(self, user_id, links, msg):
        """
        Главная логика: пользователь кинул ссылку в чат.
        Проверяем все условия.
        """
        cmid = msg.get("conversation_message_id", 0)

        # 1. Бан
        if self.db.is_banned(user_id):
            if self.rules["delete_violator_msg"] and cmid:
                self.delete_message(cmid)
            self.send_chat(
                f"🚫 {self.mention(user_id)}, "
                f"вы заблокированы в лайк-чате."
            )
            return

        user_data = self.db.get_user(user_id)

        # 2. Кулдаун
        last_time_str = user_data.get("last_link_time")
        if last_time_str:
            try:
                last_time = datetime.fromisoformat(last_time_str)
                cd = self.rules["cooldown_seconds"]
                if datetime.now() - last_time < timedelta(seconds=cd):
                    remaining = cd - (
                        datetime.now() - last_time
                    ).total_seconds()
                    mins = int(remaining // 60)
                    secs = int(remaining % 60)

                    if self.rules["delete_violator_msg"] and cmid:
                        self.delete_message(cmid)

                    self.send_chat(
                        f"⏳ {self.mention(user_id)}, кулдаун! "
                        f"Подождите ещё {mins}м {secs}с"
                    )
                    return
            except (ValueError, TypeError):
                pass

        # 3. Лимит активных ссылок
        active = self.db.get_active_links(self.rules["link_expire_hours"])
        user_active = [l for l in active if l["user_id"] == user_id]
        max_links = self.rules["max_links_per_user"]

        if len(user_active) >= max_links:
            if self.rules["delete_violator_msg"] and cmid:
                self.delete_message(cmid)
            self.send_chat(
                f"📛 {self.mention(user_id)}, у вас уже "
                f"{len(user_active)}/{max_links} активных ссылок.\n"
                f"Дождитесь пока предыдущие истекут."
            )
            return

        # 4. Дубликаты
        for link in links:
            if self.db.link_exists(
                link["content_type"], link["owner_id"], link["item_id"]
            ):
                if self.rules["delete_violator_msg"] and cmid:
                    self.delete_message(cmid)
                self.send_chat(
                    f"🔄 {self.mention(user_id)}, "
                    f"эта ссылка уже в очереди: {link['url']}"
                )
                return

        # 5. ГЛАВНАЯ ПРОВЕРКА — лайки на чужие ссылки
        other_links = [l for l in active if l["user_id"] != user_id]

        if other_links:
            self.send_chat(
                f"🔍 {self.mention(user_id)}, проверяю ваши лайки..."
            )

            liked, not_liked, errors = self.check_user_all_links(user_id)

            total_required = len(other_links)
            total_liked = len(liked)

            require_pct = self.rules["require_like_percent"]
            actual_pct = (
                (total_liked / total_required * 100)
                if total_required > 0 else 100
            )

            if actual_pct < require_pct:
                # ❌ НЕ ПРОШЁЛ ПРОВЕРКУ
                violations = self.db.add_violation(user_id)
                max_v = self.rules["max_violations"]

                if self.rules["delete_violator_msg"] and cmid:
                    self.delete_message(cmid)

                msg_text = (
                    f"❌ {self.mention(user_id)}, вы не лайкнули "
                    f"все ссылки!\n\n"
                    f"📊 Лайкнуто: {total_liked}/{total_required}\n"
                    f"⚠️ Нарушение {violations}/{max_v}\n\n"
                    f"👇 Нужно лайкнуть:\n"
                )

                for i, nl in enumerate(not_liked[:10], 1):
                    poster = self.mention(nl["user_id"])
                    msg_text += f"  {i}. {nl['url']} (от {poster})\n"

                if len(not_liked) > 10:
                    msg_text += (
                        f"  ... и ещё {len(not_liked) - 10}\n"
                    )

                msg_text += (
                    "\n💡 Лайкните всё и попробуйте снова."
                )

                # Кик при превышении лимита
                if violations >= max_v:
                    msg_text += (
                        f"\n\n🚫 Достигнут лимит нарушений! "
                        f"Вы кикнуты из чата."
                    )
                    self.send_chat(msg_text)
                    self.kick_user(user_id)
                    return

                self.send_chat(msg_text)

                # ЛС с подробностями
                pm_text = (
                    f"⚠️ Лайк-чат: вы не прошли проверку!\n\n"
                    f"Вам нужно лайкнуть {len(not_liked)} "
                    f"ссылок:\n\n"
                )
                for nl in not_liked:
                    pm_text += f"🔗 {nl['url']}\n"
                pm_text += (
                    f"\nНарушений: {violations}/{max_v}\n"
                    f"После лайков отправьте ссылку снова."
                )
                self.send_private(user_id, pm_text)
                return

        # ✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ — добавляем ссылки
        added_links = []
        for link in links:
            link_data = {
                "content_type": link["content_type"],
                "vk_type": link["vk_type"],
                "owner_id": link["owner_id"],
                "item_id": link["item_id"],
                "url": link["url"],
                "user_id": user_id,
                "timestamp": datetime.now().isoformat(),
                "active": True
            }
            link_id = self.db.add_link(link_data)
            added_links.append((link_id, link["url"]))

        # Обновляем статистику
        self.db.update_user(
            user_id,
            last_link_time=datetime.now().isoformat(),
            links_posted=self.db.get_user(user_id).get(
                "links_posted", 0
            ) + len(added_links)
        )

        # Уведомление
        active_after = self.db.get_active_links(
            self.rules["link_expire_hours"]
        )
        total_in_queue = len(active_after)

        msg_text = (
            f"✅ {self.mention(user_id)}, "
            f"{'ссылка добавлена' if len(added_links) == 1 else 'ссылки добавлены'}"
            f" в очередь!\n\n"
        )
        for lid, url in added_links:
            msg_text += f"  🔗 #{lid}: {url}\n"

        expire_h = self.rules["link_expire_hours"]
        msg_text += (
            f"\n📋 Всего в очереди: {total_in_queue}\n"
            f"⏰ Действует {expire_h}ч\n"
            f"👥 Все участники должны поставить лайк!"
        )

        self.send_chat(msg_text)

    # ─────────── ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ───────────

    def cmd_my_status(self, user_id):
        """Что мне нужно лайкнуть"""
        liked, not_liked, errors = self.check_user_all_links(user_id)
        user_data = self.db.get_user(user_id)

        if not not_liked and not errors:
            text = (
                f"✅ {self.mention(user_id)}, "
                f"вы лайкнули всё! Можете кидать ссылку.\n"
                f"📊 Нарушений: "
                f"{user_data.get('violations', 0)}/"
                f"{self.rules['max_violations']}"
            )
        else:
            text = (
                f"📋 {self.mention(user_id)}, "
                f"вам нужно лайкнуть:\n\n"
            )
            for i, nl in enumerate(not_liked, 1):
                poster = self.mention(nl["user_id"])
                text += f"  {i}. {nl['url']} (от {poster})\n"

            text += (
                f"\n📊 Лайкнуто: {len(liked)}/"
                f"{len(liked) + len(not_liked)}\n"
                f"⚠️ Нарушений: "
                f"{user_data.get('violations', 0)}/"
                f"{self.rules['max_violations']}"
            )

        self.send_chat(text)

    def cmd_queue(self, user_id):
        """Показать очередь"""
        active = self.db.get_active_links(self.rules["link_expire_hours"])

        if not active:
            self.send_chat("📋 Очередь пуста.")
            return

        text = f"📋 Активная очередь ({len(active)}):\n\n"

        for link in active:
            ts = link.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                expire_dt = dt + timedelta(
                    hours=self.rules["link_expire_hours"]
                )
                remaining = expire_dt - datetime.now()
                hours_left = max(0, remaining.total_seconds() / 3600)
                time_str = f"{hours_left:.1f}ч"
            except (ValueError, TypeError):
                time_str = "?"

            poster = self.mention(link["user_id"])
            text += (
                f"  #{link['id']} | {link['url']}\n"
                f"    👤 {poster} | ⏰ осталось {time_str}\n\n"
            )

        self.send_chat(text)

    def cmd_top(self, user_id):
        """Рейтинг участников"""
        users = self.db.get_all_users()
        if not users:
            self.send_chat("📊 Статистика пока пуста.")
            return

        sorted_users = sorted(
            users.items(),
            key=lambda x: x[1].get("likes_given", 0),
            reverse=True
        )

        text = "🏆 Рейтинг участников:\n\n"

        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, data) in enumerate(sorted_users[:15]):
            try:
                uid_int = int(uid)
            except ValueError:
                continue

            medal = medals[i] if i < 3 else f"{i+1}."
            name = self.get_name(uid_int)
            posted = data.get("links_posted", 0)
            given = data.get("likes_given", 0)
            violations = data.get("violations", 0)

            text += (
                f"  {medal} {name}\n"
                f"      📤 Ссылок: {posted} | "
                f"❤️ Лайков: {given} | "
                f"⚠️ Нарушений: {violations}\n"
            )

        self.send_chat(text)

    # ─────────── АДМИН-КОМАНДЫ ───────────

    def cmd_check_user(self, admin_id, target_text):
        """Админ: полная проверка пользователя"""
        target_id = self.resolve_user(target_text)
        if not target_id:
            self.send_chat(f"❌ Не найден: {target_text}")
            return

        self.send_chat(
            f"🔍 Проверяю {self.mention(target_id)}..."
        )

        liked, not_liked, errors = self.check_user_all_links(target_id)
        user_data = self.db.get_user(target_id)

        text = (
            f"📊 Отчёт по {self.mention(target_id)}:\n\n"
            f"📤 Ссылок отправлено: "
            f"{user_data.get('links_posted', 0)}\n"
            f"❤️ Лайков поставлено: "
            f"{user_data.get('likes_given', 0)}\n"
            f"⚠️ Нарушений: "
            f"{user_data.get('violations', 0)}/"
            f"{self.rules['max_violations']}\n\n"
        )

        if liked:
            text += f"✅ Лайкнул ({len(liked)}):\n"
            for l in liked[:10]:
                text += f"  • {l['url']}\n"
            if len(liked) > 10:
                text += f"  ... и ещё {len(liked) - 10}\n"
            text += "\n"

        if not_liked:
            text += f"❌ НЕ лайкнул ({len(not_liked)}):\n"
            for l in not_liked:
                text += f"  • {l['url']}\n"
            text += "\n"

        if errors:
            text += f"⚠️ Ошибки проверки ({len(errors)}):\n"
            for l in errors:
                text += f"  • {l['url']}\n"

        total = len(liked) + len(not_liked)
        if total > 0:
            pct = round(len(liked) / total * 100)
            text += f"\n📈 Процент лайков: {pct}%"

        self.send_chat(text)

    def cmd_check_post(self, admin_id, post_text):
        """Админ: кто лайкнул конкретный пост"""
        links = LinkParser.parse_text(post_text)
        if not links:
            self.send_chat(f"❌ Не распознана ссылка: {post_text}")
            return

        link = links[0]
        self.send_chat(
            f"🔍 Проверяю лайки на {link['url']}..."
        )

        # Проверяем всех пользователей из чата/базы
        active = self.db.get_active_links(self.rules["link_expire_hours"])
        all_user_ids = set()
        for l in active:
            all_user_ids.add(l["user_id"])
        for uid_str in self.db.get_all_users():
            try:
                all_user_ids.add(int(uid_str))
            except ValueError:
                pass

        liked_users = []
        not_liked_users = []

        for uid in all_user_ids:
            result = self.check_like(
                uid,
                link["content_type"],
                link["owner_id"],
                link["item_id"]
            )
            if result is True:
                liked_users.append(uid)
            elif result is False:
                not_liked_users.append(uid)
            time.sleep(0.35)

        text = f"📊 Лайки на {link['url']}:\n\n"

        if liked_users:
            text += f"✅ Лайкнули ({len(liked_users)}):\n"
            for uid in liked_users:
                text += f"  • {self.mention(uid)}\n"
            text += "\n"

        if not_liked_users:
            text += f"❌ НЕ лайкнули ({len(not_liked_users)}):\n"
            for uid in not_liked_users:
                text += f"  • {self.mention(uid)}\n"

        total = len(liked_users) + len(not_liked_users)
        if total > 0:
            pct = round(len(liked_users) / total * 100)
            text += f"\n📈 {len(liked_users)}/{total} ({pct}%)"

        self.send_chat(text)

    def cmd_ban(self, admin_id, target_text):
        target_id = self.resolve_user(target_text)
        if not target_id:
            self.send_chat(f"❌ Не найден: {target_text}")
            return
        self.db.ban_user(target_id)
        self.send_chat(
            f"🚫 {self.mention(target_id)} заблокирован "
            f"в лайк-чате."
        )

    def cmd_unban(self, admin_id, target_text):
        target_id = self.resolve_user(target_text)
        if not target_id:
            self.send_chat(f"❌ Не найден: {target_text}")
            return
        if self.db.unban_user(target_id):
            self.send_chat(
                f"✅ {self.mention(target_id)} разблокирован."
            )
        else:
            self.send_chat("ℹ️ Пользователь не в бан-листе.")

    def cmd_reset(self, admin_id, target_text):
        target_id = self.resolve_user(target_text)
        if not target_id:
            self.send_chat(f"❌ Не найден: {target_text}")
            return
        self.db.reset_violations(target_id)
        self.send_chat(
            f"✅ Нарушения {self.mention(target_id)} сброшены."
        )

    def cmd_remove_link(self, admin_id, link_id_text):
        try:
            link_id = int(link_id_text)
        except ValueError:
            self.send_chat("❌ Укажите номер ссылки: /удалить 5")
            return

        if self.db.remove_link(link_id):
            self.send_chat(f"✅ Ссылка #{link_id} удалена из очереди.")
        else:
            self.send_chat(f"❌ Ссылка #{link_id} не найдена.")

    def cmd_clear_queue(self, admin_id):
        count = self.db.clear_queue()
        self.send_chat(f"🗑 Очередь очищена ({count} ссылок удалено).")

    def cmd_settings(self, admin_id):
        text = (
            "⚙️ Текущие настройки:\n\n"
            f"⏰ Срок жизни ссылки: "
            f"{self.rules['link_expire_hours']}ч\n"
            f"⏳ Кулдаун: "
            f"{self.rules['cooldown_seconds'] // 60} мин\n"
            f"📤 Макс ссылок от 1 чел: "
            f"{self.rules['max_links_per_user']}\n"
            f"⚠️ Макс нарушений: "
            f"{self.rules['max_violations']}\n"
            f"📊 Требуется лайков: "
            f"{self.rules['require_like_percent']}%\n"
            f"🗑 Удалять сообщения: "
            f"{'да' if self.rules['delete_violator_msg'] else 'нет'}\n"
        )
        banned = self.db.get_banned()
        if banned:
            text += f"\n🚫 В бане: {len(banned)} чел."
        self.send_chat(text)

    def cmd_banlist(self, admin_id):
        banned = self.db.get_banned()
        if not banned:
            self.send_chat("🚫 Бан-лист пуст.")
            return
        text = f"🚫 Бан-лист ({len(banned)}):\n\n"
        for uid in banned:
            text += f"  • {self.mention(uid)}\n"
        self.send_chat(text)

    def cmd_kick_user(self, admin_id, target_text):
        target_id = self.resolve_user(target_text)
        if not target_id:
            self.send_chat(f"❌ Не найден: {target_text}")
            return
        if self.kick_user(target_id):
            self.send_chat(
                f"👢 {self.get_name(target_id)} кикнут из чата."
            )
        else:
            self.send_chat("❌ Не удалось кикнуть (бот — админ чата?).")

    def cmd_mass_check(self, admin_id):
        """Массовая проверка всех пользователей"""
        active = self.db.get_active_links(self.rules["link_expire_hours"])
        if not active:
            self.send_chat("📋 Очередь пуста, нечего проверять.")
            return

        all_user_ids = set()
        for l in active:
            all_user_ids.add(l["user_id"])

        self.send_chat(
            f"🔍 Массовая проверка {len(all_user_ids)} "
            f"участников..."
        )

        violators = []
        good_users = []

        for uid in all_user_ids:
            liked, not_liked, _ = self.check_user_all_links(uid)
            if not_liked:
                violators.append((uid, len(liked), len(not_liked)))
            else:
                good_users.append(uid)

        text = f"📊 Результаты массовой проверки:\n\n"

        if good_users:
            text += f"✅ Всё лайкнули ({len(good_users)}):\n"
            for uid in good_users:
                text += f"  • {self.mention(uid)}\n"
            text += "\n"

        if violators:
            text += f"❌ Не долайкали ({len(violators)}):\n"
            for uid, liked_c, not_c in violators:
                text += (
                    f"  • {self.mention(uid)} — "
                    f"не хватает {not_c} лайков\n"
                )
        else:
            text += "🎉 Все участники лайкнули всё!"

        self.send_chat(text)

    def show_help(self, user_id):
        """Справка"""
        text = (
            "📖 ЛАЙК-ЧАТ БОТ\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "📌 ПРАВИЛА:\n"
            "1. Лайкните ВСЕ ссылки в очереди\n"
            "2. Только после этого кидайте свою\n"
            "3. Бот проверит автоматически\n\n"

            "👤 КОМАНДЫ ДЛЯ ВСЕХ:\n\n"

            "  /статус — что мне нужно лайкнуть\n"
            "  /очередь — активные ссылки\n"
            "  /топ — рейтинг участников\n"
            "  /помощь — эта справка\n"
            "  /правила — правила чата\n\n"
        )

        if self.is_admin(user_id):
            text += (
                "👑 АДМИН-КОМАНДЫ:\n\n"

                "  /проверить @user — проверка лайков\n"
                "  /проверить_пост ссылка\n"
                "      — кто лайкнул пост\n"
                "  /массовая — проверить всех\n\n"

                "  /бан @user — заблокировать\n"
                "  /разбан @user — разблокировать\n"
                "  /кик @user — кикнуть\n"
                "  /сброс @user — сбросить нарушения\n"
                "  /банлист — список забаненных\n\n"

                "  /удалить N — удалить ссылку #N\n"
                "  /очистить — очистить очередь\n"
                "  /настройки — показать настройки\n"
            )

        self.send_chat(text)

    def show_rules(self):
        """Правила чата"""
        text = (
            "📜 ПРАВИЛА ЛАЙК-ЧАТА:\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"

            "1️⃣ Перед тем как кинуть свою ссылку — "
            "лайкните ВСЕ ссылки в очереди\n\n"

            "2️⃣ Бот автоматически проверяет лайки. "
            "Если не лайкнули — ваша ссылка будет "
            "отклонена\n\n"

            f"3️⃣ Кулдаун между ссылками: "
            f"{self.rules['cooldown_seconds'] // 60} мин\n\n"

            f"4️⃣ Максимум {self.rules['max_links_per_user']} "
            f"активных ссылок от одного человека\n\n"

            f"5️⃣ Ссылка активна "
            f"{self.rules['link_expire_hours']}ч, "
            f"потом удаляется\n\n"

            f"6️⃣ После {self.rules['max_violations']} "
            f"нарушений — кик из чата\n\n"

            "7️⃣ Команда /статус покажет что вам "
            "нужно лайкнуть\n\n"

            "❤️ Взаимный лайкинг — залог успеха!"
        )
        self.send_chat(text)

    # ─────────── ФОНОВЫЕ ЗАДАЧИ ───────────

    def background_worker(self):
        """Фоновый поток: очистка + проверка"""
        while True:
            try:
                interval = self.rules["check_interval_minutes"] * 60
                time.sleep(interval)

                # Очистка устаревших
                removed = self.db.expire_old_links(
                    self.rules["link_expire_hours"]
                )
                if removed > 0:
                    self.send_chat(
                        f"🗑 Авто-очистка: удалено "
                        f"{removed} устаревших ссылок."
                    )

                # Обновление likes_given для всех
                active = self.db.get_active_links(
                    self.rules["link_expire_hours"]
                )
                if active:
                    all_uids = set()
                    for l in active:
                        all_uids.add(l["user_id"])

                    for uid in all_uids:
                        liked, _, _ = self.check_user_all_links(uid)
                        self.db.update_user(
                            uid,
                            likes_given=self.db.get_user(
                                uid
                            ).get("likes_given", 0) + len(liked)
                        )

            except Exception as e:
                print(f"Ошибка background_worker: {e}")
                time.sleep(60)

    # ─────────── ГЛАВНЫЙ ОБРАБОТЧИК ───────────

    def process_message(self, msg):
        """Обработка одного сообщения"""
        peer_id = msg.get("peer_id", 0)
        from_id = msg.get("from_id", 0)
        text = msg.get("text", "").strip()
        attachments = msg.get("attachments", [])

        # Игнорируем сообщения от сообществ
        if from_id <= 0:
            return

        # Обработка только нашего чата
        if peer_id != PEER_ID:
            # Личные сообщения — краткая справка
            if peer_id == from_id:
                self.send_private(
                    from_id,
                    "🤖 Я работаю в лайк-чате.\n"
                    "Напишите /статус в чате чтобы "
                    "узнать что нужно лайкнуть."
                )
            return

        # Обработка действий чата (вход/выход)
        action = msg.get("action")
        if action:
            if action.get("type") == "chat_invite_user":
                member = action.get("member_id", 0)
                if member > 0:
                    if self.db.is_banned(member):
                        self.kick_user(member)
                        self.send_chat(
                            f"🚫 {self.get_name(member)} "
                            f"в бан-листе."
                        )
                    else:
                        self.send_chat(
                            f"👋 Добро пожаловать, "
                            f"{self.mention(member)}!\n"
                            f"Напишите /правила "
                            f"чтобы узнать как всё работает."
                        )
            return

        low = text.lower()

        # === КОМАНДЫ (начинаются с /) ===
        if low.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            # Общие команды
            if cmd in ["/помощь", "/help", "/start", "/начать"]:
                self.show_help(from_id)
            elif cmd in ["/статус", "/status", "/мой"]:
                self.cmd_my_status(from_id)
            elif cmd in ["/очередь", "/queue"]:
                self.cmd_queue(from_id)
            elif cmd in ["/топ", "/рейтинг", "/top"]:
                self.cmd_top(from_id)
            elif cmd in ["/правила", "/rules"]:
                self.show_rules()

            # Админ-команды
            elif cmd in ["/проверить", "/check"] and self.is_admin(from_id):
                if arg:
                    self.cmd_check_user(from_id, arg)
                else:
                    self.send_chat("❌ /проверить @user")

            elif cmd in ["/проверить_пост", "/checkpost"] and self.is_admin(from_id):
                if arg:
                    self.cmd_check_post(from_id, arg)
                else:
                    self.send_chat("❌ /проверить_пост wall-123_456")

            elif cmd in ["/массовая", "/masscheck"] and self.is_admin(from_id):
                self.cmd_mass_check(from_id)

            elif cmd in ["/бан", "/ban"] and self.is_admin(from_id):
                if arg:
                    self.cmd_ban(from_id, arg)
                else:
                    self.send_chat("❌ /бан @user")

            elif cmd in ["/разбан", "/unban"] and self.is_admin(from_id):
                if arg:
                    self.cmd_unban(from_id, arg)
                else:
                    self.send_chat("❌ /разбан @user")

            elif cmd in ["/кик", "/kick"] and self.is_admin(from_id):
                if arg:
                    self.cmd_kick_user(from_id, arg)
                else:
                    self.send_chat("❌ /кик @user")

            elif cmd in ["/сброс", "/reset"] and self.is_admin(from_id):
                if arg:
                    self.cmd_reset(from_id, arg)
                else:
                    self.send_chat("❌ /сброс @user")

            elif cmd in ["/удалить", "/remove"] and self.is_admin(from_id):
                if arg:
                    self.cmd_remove_link(from_id, arg)
                else:
                    self.send_chat("❌ /удалить <номер>")

            elif cmd in ["/очистить", "/clear"] and self.is_admin(from_id):
                self.cmd_clear_queue(from_id)

            elif cmd in ["/настройки", "/settings"] and self.is_admin(from_id):
                self.cmd_settings(from_id)

            elif cmd in ["/банлист", "/banlist"] and self.is_admin(from_id):
                self.cmd_banlist(from_id)

            else:
                if cmd.startswith("/") and not self.is_admin(from_id):
                    # Может быть админ-команда от не-админа
                    admin_cmds = [
                        "/проверить", "/бан", "/разбан",
                        "/кик", "/сброс", "/удалить",
                        "/очистить", "/настройки",
                        "/массовая", "/банлист",
                        "/проверить_пост"
                    ]
                    if cmd in admin_cmds:
                        self.send_chat("⛔ Только для админов.")
                    else:
                        self.send_chat(
                            "❓ Неизвестная команда. /помощь"
                        )
                else:
                    self.send_chat(
                        "❓ Неизвестная команда. /помощь"
                    )
            return

        # === ПРОВЕРКА НА ССЫЛКИ ===
        links = LinkParser.parse_message(text, attachments)
        if links:
            print(
                f"🔗 Ссылки от {from_id}: "
                f"{[l['url'] for l in links]}"
            )
            self.handle_new_link(from_id, links, msg)

    # ─────────── ЗАПУСК ───────────

    def run(self):
        """Запуск бота"""

        # Фоновый поток
        worker = threading.Thread(
            target=self.background_worker,
            daemon=True
        )
        worker.start()
        print("🔄 Фоновый поток запущен")

        # Основной цикл
        print("🔄 Ожидание сообщений...")
        while True:
            try:
                for event in self.longpoll.listen():
                    if event.type == VkBotEventType.MESSAGE_NEW:
                        # Безопасное извлечение сообщения
                        raw = event.raw.get("object", {})
                        msg = raw.get("message", raw)

                        try:
                            self.process_message(msg)
                        except Exception as e:
                            print(f"❌ Ошибка: {e}")
                            import traceback
                            traceback.print_exc()

            except KeyboardInterrupt:
                print("\n🛑 Бот остановлен.")
                break
            except Exception as e:
                print(f"⚠️ LongPoll ошибка: {e}")
                time.sleep(5)


# ======================== ЗАПУСК ========================

if __name__ == "__main__":
    bot = LikeChatBot()
    bot.run()
