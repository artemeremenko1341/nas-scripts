#!/usr/bin/env python3
"""
gtasks.py — Pure Google Tasks API CLI for Claude operations.

Replaces tasknotes_sync.py + daemon (deprecated 2026-05-20 after 4 race-conditions
in one morning). Vault `TaskNotes/` is now frozen, Google Tasks is single source.

Commands:
  list                          — show all lists + active tasks (compact)
  lists                         — show only lists (id + title)
  find <query>                  — find tasks by title substring (case-insensitive,
                                  also searches notes; shows status and list)
  create <list> <title> [notes] — POST new task to <list> (active needsAction)
  complete <task_id>            — PATCH status=completed in current list;
                                  if list != "Сделанные", also moves (POST+DELETE)
  move <task_id> <new_list>     — POST in <new_list> + DELETE old (Google has no
                                  cross-list move; this is the canonical pattern)
  delete <task_id>              — DELETE (hard remove)
  reopen <task_id> [<list>]     — PATCH status=needsAction; optional move to <list>

Lists: "На сегодня", "На завтра", "Недельные планы", "Идеи", "Жду ответа",
       "Сделанные", "Финансовые задачи", "Продукты"

Usage from Claude via SSH:
  ssh ... 'python3 /volume1/homes/artemere-7601341/scripts/openclaw/scripts/gtasks.py <cmd> <args>'

Examples:
  gtasks.py find "Сбер дебет"
  gtasks.py complete "RTUxMDBQazZoTGpQS2JYdw"
  gtasks.py create "На сегодня" "Позвонить Шишкову" "Сбер дебет, два стопа"
  gtasks.py move "RTUxMDBQazZoTGpQS2JYdw" "На завтра"
"""
import sys, json, urllib.request, urllib.parse

CRED = '/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json'
TOK = '/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json'

API = 'https://tasks.googleapis.com/tasks/v1'
DONE_LIST = 'Сделанные'


def _access_token():
    tok = json.load(open(TOK))
    cred = json.load(open(CRED))['installed']
    data = urllib.parse.urlencode({
        'client_id': cred['client_id'],
        'client_secret': cred['client_secret'],
        'refresh_token': tok['refresh_token'],
        'grant_type': 'refresh_token',
    }).encode()
    r = urllib.request.urlopen('https://oauth2.googleapis.com/token', data=data, timeout=10)
    return json.load(r)['access_token']


def _req(at, path, method='GET', body=None):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={'Authorization': 'Bearer ' + at, 'Content-Type': 'application/json'},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f'ERROR {e.code} {method} {path}: {body}', file=sys.stderr)
        sys.exit(1)


def _lists(at):
    return _req(at, '/users/@me/lists')['items']


def _list_id(at, name):
    for lst in _lists(at):
        if lst['title'] == name:
            return lst['id']
    print(f'ERROR: list "{name}" not found. Available: {[l["title"] for l in _lists(at)]}', file=sys.stderr)
    sys.exit(1)


def _tasks_in(at, list_id, show_completed=False, show_hidden=False):
    qs = []
    if show_completed:
        qs.append('showCompleted=true')
    if show_hidden:
        qs.append('showHidden=true')
    qs.append('maxResults=200')
    path = f'/lists/{list_id}/tasks?' + '&'.join(qs)
    return _req(at, path).get('items', [])


def _find_task_anywhere(at, task_id):
    """Locate (task, list) by id across all lists. Returns (task_dict, list_id, list_name) or (None, None, None)."""
    for lst in _lists(at):
        # search active + completed + hidden
        for show_c in (False, True):
            tasks = _tasks_in(at, lst['id'], show_completed=show_c, show_hidden=show_c)
            for t in tasks:
                if t.get('id') == task_id:
                    return t, lst['id'], lst['title']
    return None, None, None


def cmd_lists():
    at = _access_token()
    for lst in _lists(at):
        print(f'{lst["id"]}\t{lst["title"]}')


def cmd_list_all():
    at = _access_token()
    for lst in _lists(at):
        active = _tasks_in(at, lst['id'])
        print(f'=== {lst["title"]} ({len(active)} active) ===')
        for t in active:
            print(f'  {t["id"]}\t{t.get("title","")[:60]}')


def cmd_find(query):
    at = _access_token()
    q = query.lower()
    for lst in _lists(at):
        tasks = _tasks_in(at, lst['id'], show_completed=True, show_hidden=True)
        for t in tasks:
            title = t.get('title', '')
            notes = t.get('notes', '')
            if q in title.lower() or q in notes.lower():
                status = t.get('status', '?')
                print(f'[{lst["title"]}] {t["id"]}\tstatus={status}\ttitle={title[:60]}')


def cmd_create(list_name, title, notes=''):
    at = _access_token()
    lid = _list_id(at, list_name)
    body = {'title': title}
    if notes:
        body['notes'] = notes
    t = _req(at, f'/lists/{lid}/tasks', 'POST', body)
    print(f'CREATED id={t["id"]} in list={list_name}: {title[:60]}')


def cmd_complete(task_id):
    at = _access_token()
    t, src_lid, src_name = _find_task_anywhere(at, task_id)
    if not t:
        print(f'ERROR: task {task_id} not found', file=sys.stderr)
        sys.exit(1)
    if src_name == DONE_LIST and t.get('status') == 'completed':
        print(f'NOOP: already completed in {DONE_LIST}: {t.get("title","")[:60]}')
        return
    # Step 1: mark completed in current list (so Google records the completion)
    _req(at, f'/lists/{src_lid}/tasks/{task_id}', 'PATCH', {'status': 'completed'})
    # Step 2: if not already in Done list, move there (POST + DELETE; Google has no cross-list move)
    if src_name != DONE_LIST:
        done_lid = _list_id(at, DONE_LIST)
        new_body = {'title': t.get('title', ''), 'notes': t.get('notes', '')}
        new_t = _req(at, f'/lists/{done_lid}/tasks', 'POST', new_body)
        _req(at, f'/lists/{done_lid}/tasks/{new_t["id"]}', 'PATCH', {'status': 'completed'})
        _req(at, f'/lists/{src_lid}/tasks/{task_id}', 'DELETE')
        print(f'COMPLETED+MOVED: was [{src_name}] {task_id} -> [{DONE_LIST}] {new_t["id"]} ({t.get("title","")[:60]})')
    else:
        print(f'COMPLETED in place [{src_name}] {task_id}: {t.get("title","")[:60]}')


def cmd_move(task_id, new_list_name):
    at = _access_token()
    t, src_lid, src_name = _find_task_anywhere(at, task_id)
    if not t:
        print(f'ERROR: task {task_id} not found', file=sys.stderr)
        sys.exit(1)
    if src_name == new_list_name:
        print(f'NOOP: already in {new_list_name}')
        return
    new_lid = _list_id(at, new_list_name)
    body = {'title': t.get('title', ''), 'notes': t.get('notes', '')}
    if t.get('status') == 'completed':
        body['status'] = 'completed'
    new_t = _req(at, f'/lists/{new_lid}/tasks', 'POST', body)
    if t.get('status') == 'completed':
        _req(at, f'/lists/{new_lid}/tasks/{new_t["id"]}', 'PATCH', {'status': 'completed'})
    _req(at, f'/lists/{src_lid}/tasks/{task_id}', 'DELETE')
    print(f'MOVED: [{src_name}] {task_id} -> [{new_list_name}] {new_t["id"]} ({t.get("title","")[:60]})')


def cmd_delete(task_id):
    at = _access_token()
    t, src_lid, src_name = _find_task_anywhere(at, task_id)
    if not t:
        print(f'ERROR: task {task_id} not found', file=sys.stderr)
        sys.exit(1)
    _req(at, f'/lists/{src_lid}/tasks/{task_id}', 'DELETE')
    print(f'DELETED from [{src_name}]: {t.get("title","")[:60]}')


def cmd_reopen(task_id, new_list=None):
    at = _access_token()
    t, src_lid, src_name = _find_task_anywhere(at, task_id)
    if not t:
        print(f'ERROR: task {task_id} not found', file=sys.stderr)
        sys.exit(1)
    if new_list and new_list != src_name:
        # Move via POST + DELETE
        new_lid = _list_id(at, new_list)
        body = {'title': t.get('title', ''), 'notes': t.get('notes', ''), 'status': 'needsAction'}
        new_t = _req(at, f'/lists/{new_lid}/tasks', 'POST', body)
        _req(at, f'/lists/{src_lid}/tasks/{task_id}', 'DELETE')
        print(f'REOPENED+MOVED: was [{src_name}] {task_id} -> [{new_list}] {new_t["id"]}: {t.get("title","")[:60]}')
    else:
        _req(at, f'/lists/{src_lid}/tasks/{task_id}', 'PATCH', {'status': 'needsAction'})
        print(f'REOPENED in place [{src_name}] {task_id}: {t.get("title","")[:60]}')


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == 'lists':
        cmd_lists()
    elif cmd == 'list':
        cmd_list_all()
    elif cmd == 'find' and len(args) == 1:
        cmd_find(args[0])
    elif cmd == 'create' and len(args) >= 2:
        cmd_create(args[0], args[1], args[2] if len(args) >= 3 else '')
    elif cmd == 'complete' and len(args) == 1:
        cmd_complete(args[0])
    elif cmd == 'move' and len(args) == 2:
        cmd_move(args[0], args[1])
    elif cmd == 'delete' and len(args) == 1:
        cmd_delete(args[0])
    elif cmd == 'reopen' and 1 <= len(args) <= 2:
        cmd_reopen(args[0], args[1] if len(args) == 2 else None)
    else:
        print(f'Unknown or malformed command: {cmd} {args}', file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
