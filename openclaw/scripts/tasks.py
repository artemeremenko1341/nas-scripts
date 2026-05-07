#!/usr/bin/env python3
"""Работа с Google Tasks: список списков, задачи, добавление, завершение, удаление."""
import json
import sys
import urllib.request
import urllib.parse

CREDENTIALS_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json"
TOKEN_FILE = "/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json"
BASE_URL = "https://tasks.googleapis.com/tasks/v1"


def refresh_token():
    with open(CREDENTIALS_FILE) as f:
        creds = json.load(f)["installed"]
    with open(TOKEN_FILE) as f:
        token = json.load(f)

    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(creds["token_uri"], data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        new_token = json.loads(r.read())

    token["access_token"] = new_token["access_token"]
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    return token["access_token"]


def get_access_token():
    with open(TOKEN_FILE) as f:
        return json.load(f).get("access_token")


def api(path, method="GET", body=None):
    token = get_access_token()
    url = BASE_URL + path
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            if r.status == 204:
                return {}
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            new_token = refresh_token()
            req.headers["Authorization"] = f"Bearer {new_token}"
            with urllib.request.urlopen(req) as r:
                if r.status == 204:
                    return {}
                return json.loads(r.read())
        body_text = e.read().decode()
        print(f"Ошибка {e.code}: {body_text}", file=sys.stderr)
        raise


def list_tasklists():
    """Список всех списков задач."""
    result = api("/users/@me/lists")
    return result.get("items", [])


def find_list_by_name(name):
    """Найти список по имени (частичное совпадение, без учёта регистра)."""
    lists = list_tasklists()
    name_lower = name.lower()
    for lst in lists:
        if name_lower in lst["title"].lower():
            return lst["id"], lst["title"]
    return None, None


def get_default_list_id():
    """Получить ID списка 'На сегодня' или первого доступного."""
    lists = list_tasklists()
    for lst in lists:
        if "сегодня" in lst["title"].lower():
            return lst["id"]
    return lists[0]["id"] if lists else "@default"


def get_done_list_id():
    """Получить ID списка 'Сделанные'."""
    lists = list_tasklists()
    for lst in lists:
        if "сделан" in lst["title"].lower():
            return lst["id"]
    return None


def list_tasks(tasklist_id="@default", show_completed=False):
    """Список задач в списке."""
    params = {"showCompleted": "true" if show_completed else "false", "maxResults": 100}
    result = api(f"/lists/{tasklist_id}/tasks?" + urllib.parse.urlencode(params))
    return result.get("items", [])


def find_task_in_all_lists(task_id):
    """Найти задачу по ID во всех списках. Возвращает (task, list_id) или (None, None)."""
    lists = list_tasklists()
    for lst in lists:
        tasks = list_tasks(lst["id"])
        for t in tasks:
            if t["id"] == task_id:
                return t, lst["id"]
    return None, None


def add_task(title, notes="", due=None, tasklist_id=None):
    """Добавить задачу."""
    if tasklist_id is None:
        tasklist_id = get_default_list_id()
    body = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = due  # формат: 2026-03-06T00:00:00.000Z
    result = api(f"/lists/{tasklist_id}/tasks", method="POST", body=body)
    return result


def update_task(task_id, tasklist_id, due=None, title=None):
    """Обновить задачу (дата, название)."""
    body = {}
    if due:
        body["due"] = due
    if title:
        body["title"] = title
    result = api(f"/lists/{tasklist_id}/tasks/{task_id}", method="PATCH", body=body)
    return result


def complete_task(task_id, tasklist_id=None):
    """Отметить задачу выполненной. Если list_id не указан — ищет во всех списках."""
    if tasklist_id is None:
        _, tasklist_id = find_task_in_all_lists(task_id)
        if not tasklist_id:
            raise ValueError(f"Задача {task_id} не найдена ни в одном списке")
    body = {"status": "completed"}
    result = api(f"/lists/{tasklist_id}/tasks/{task_id}", method="PATCH", body=body)
    return result


def delete_task(task_id, tasklist_id=None):
    """Удалить задачу. Если list_id не указан — ищет во всех списках."""
    if tasklist_id is None:
        _, tasklist_id = find_task_in_all_lists(task_id)
        if not tasklist_id:
            raise ValueError(f"Задача {task_id} не найдена ни в одном списке")
    api(f"/lists/{tasklist_id}/tasks/{task_id}", method="DELETE")


def move_to_done(task_id, from_list=None, done_list=None):
    """Перенести задачу в список Сделанные и пометить выполненной. Удалить из исходного."""
    if from_list is None:
        _, from_list = find_task_in_all_lists(task_id)
        if not from_list:
            raise ValueError(f"Задача {task_id} не найдена ни в одном списке")
    if done_list is None:
        done_list = get_done_list_id()

    task = api(f"/lists/{from_list}/tasks/{task_id}")
    title = task.get("title", "")
    notes = task.get("notes", "")
    body = {"title": title, "status": "completed"}
    if notes:
        body["notes"] = notes
    new_task = api(f"/lists/{done_list}/tasks", method="POST", body=body)
    api(f"/lists/{done_list}/tasks/{new_task['id']}", method="PATCH", body={"status": "completed"})
    api(f"/lists/{from_list}/tasks/{task_id}", method="DELETE")
    return title


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "lists"

    if cmd == "lists":
        lists = list_tasklists()
        for lst in lists:
            print(f"  [{lst['id']}] {lst['title']}")

    elif cmd == "tasks":
        # tasks [list_id_or_name]
        if len(sys.argv) > 2:
            arg = sys.argv[2]
            # Проверяем — это ID или имя?
            lists = list_tasklists()
            list_ids = {lst["id"] for lst in lists}
            if arg in list_ids:
                tasklist = arg
            else:
                # Ищем по имени
                found_id, found_name = find_list_by_name(arg)
                if found_id:
                    tasklist = found_id
                    print(f"  Список: {found_name}")
                else:
                    print(f"❌ Список '{arg}' не найден")
                    sys.exit(1)
        else:
            tasklist = get_default_list_id()

        tasks = list_tasks(tasklist)
        if not tasks:
            print("Задач нет")
        for t in tasks:
            status = "✅" if t.get("status") == "completed" else "⬜"
            due = t.get("due", "")[:10] if t.get("due") else ""
            print(f"  {status} [{due}] {t.get('title')} (id: {t['id']})")

    elif cmd == "add":
        # add <list_id_or_name> <название> [заметка] [дата]
        arg = sys.argv[2] if len(sys.argv) > 2 else None
        title = sys.argv[3] if len(sys.argv) > 3 else ""
        notes = sys.argv[4] if len(sys.argv) > 4 else ""
        due = sys.argv[5] if len(sys.argv) > 5 else None

        if arg:
            lists = list_tasklists()
            list_ids = {lst["id"] for lst in lists}
            if arg in list_ids:
                tasklist = arg
            else:
                found_id, found_name = find_list_by_name(arg)
                tasklist = found_id if found_id else get_default_list_id()
        else:
            tasklist = get_default_list_id()

        task = add_task(title, notes, due, tasklist)
        print(f"✅ Задача добавлена: {title} (id: {task['id']})")

    elif cmd == "done":
        # done <task_id> [list_id] — автоматически переносит в Сделанные
        task_id = sys.argv[2]
        from_list = sys.argv[3] if len(sys.argv) > 3 else None
        done_list = get_done_list_id()
        if done_list:
            try:
                title = move_to_done(task_id, from_list, done_list)
                print(f"✅ Перенесено в Сделанные и закрыто: {title}")
            except Exception:
                # Если задача уже закрыта/не найдена — просто помечаем
                complete_task(task_id, from_list)
                print(f"✅ Задача выполнена")
        else:
            complete_task(task_id, from_list)
            print(f"✅ Задача выполнена")

    elif cmd == "move":
        # move <task_id> [from_list] [done_list]
        task_id = sys.argv[2]
        from_list = sys.argv[3] if len(sys.argv) > 3 else None
        done_list = sys.argv[4] if len(sys.argv) > 4 else None
        title = move_to_done(task_id, from_list, done_list)
        print(f"✅ Перенесено в Сделанные: {title}")

    elif cmd == "delete":
        # delete <task_id> [list_id]
        task_id = sys.argv[2]
        tasklist = sys.argv[3] if len(sys.argv) > 3 else None
        delete_task(task_id, tasklist)
        print(f"✅ Задача удалена")

    elif cmd == "all":
        # all — показать все задачи во всех списках
        lists = list_tasklists()
        for lst in lists:
            tasks = list_tasks(lst["id"])
            if tasks:
                print(f"\n📋 {lst['title']} [{lst['id']}]")
                for t in tasks:
                    status = "✅" if t.get("status") == "completed" else "⬜"
                    due = t.get("due", "")[:10] if t.get("due") else ""
                    print(f"  {status} [{due}] {t.get('title')} (id: {t['id']})")

    else:
        print("Команды: lists | tasks [list_or_name] | all | add <list> <название> [заметка] [дата] | done <task_id> [list_id] | move <task_id> [from_list] | delete <task_id> [list_id]")
