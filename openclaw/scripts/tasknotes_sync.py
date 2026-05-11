#!/usr/bin/env python3
"""
TaskNotes (Obsidian) <-> Google Tasks bidirectional sync.

Mapping: TaskNotes/<folder>/*.md  <->  Google Tasks list <folder>
Folders: На сегодня, Недельные планы, Идеи, Жду ответа, Сделанные, На завтра, Финансовые задачи, Продукты

Conflict resolution: newest wins (md mtime vs Google updated).
Completion: move .md -> Сделанные/ + status:done in frontmatter + complete in Google + move to Сделанные list.
Missing in Google (was deleted there): move .md -> Сделанные/ (no actual deletion per Артём rule).

State: ~/scripts/openclaw/data/tasknotes_sync_state.json (по google_task_id).
"""
import os, sys, json, re, urllib.request, urllib.parse, urllib.error
from pathlib import Path
from datetime import datetime

VAULT = Path('/volume1/obsidian')
ROOT = VAULT / 'TaskNotes'
DONE = ROOT / 'Сделанные'
STATE_FILE = '/volume1/homes/artemere-7601341/scripts/openclaw/data/tasknotes_sync_state.json'
LOG_FILE = '/volume1/homes/artemere-7601341/scripts/openclaw/data/tasknotes_sync.log'
CRED = '/volume1/homes/artemere-7601341/scripts/openclaw/config/google_credentials.json'
TOK = '/volume1/homes/artemere-7601341/scripts/openclaw/config/google_token.json'

LISTS = ['На сегодня','Недельные планы','Идеи','Жду ответа','Сделанные','На завтра','Финансовые задачи','Продукты']
DONE_LIST = 'Сделанные'

# ---------- Google API ----------
def _refresh():
    c = json.load(open(CRED))['installed']
    t = json.load(open(TOK))
    data = urllib.parse.urlencode({
        'client_id': c['client_id'],
        'client_secret': c['client_secret'],
        'refresh_token': t['refresh_token'],
        'grant_type': 'refresh_token',
    }).encode()
    req = urllib.request.Request(c['token_uri'], data=data)
    with urllib.request.urlopen(req) as r:
        new = json.loads(r.read())
    t['access_token'] = new['access_token']
    json.dump(t, open(TOK, 'w'), indent=2)
    return t['access_token']

def gapi(path, method='GET', body=None, retried=False):
    tok = json.load(open(TOK))['access_token']
    url = 'https://tasks.googleapis.com/tasks/v1' + path
    headers = {'Authorization': f'Bearer {tok}'}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            _refresh()
            return gapi(path, method, body, True)
        raise

# ---------- Frontmatter parse/write ----------
def parse_md(path):
    txt = path.read_text(encoding='utf-8')
    if not txt.startswith('---\n'):
        return {}, txt
    end = txt.find('\n---\n', 4)
    if end < 0:
        return {}, txt
    fm = {}
    for line in txt[4:end].splitlines():
        if ':' in line and not line.startswith('  '):
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip()
    return fm, txt[end+5:]

def write_md(path, fm, body):
    keys_order = ['title','status','tags','due','created','updated','google_task_id','google_list']
    lines = ['---']
    written = set()
    for k in keys_order:
        if k in fm:
            lines.append(f'{k}: {fm[k]}')
            written.add(k)
    for k, v in fm.items():
        if k not in written:
            lines.append(f'{k}: {v}')
    lines.append('---')
    lines.append('')
    text_body = body if body.startswith('\n') else '\n' + body
    txt = '\n'.join(lines) + text_body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(txt, encoding='utf-8')

# ---------- Sanitize filename ----------
INVALID = re.compile(r'[<>:"/\\|?*\n\r\t]')
def safe_filename(title):
    s = INVALID.sub(' ', title).strip()
    s = re.sub(r'\s+', ' ', s)[:80]
    return s or 'untitled'

# ---------- Logging ----------
def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

# ---------- Vault scan ----------
def scan_vault():
    out = []
    for folder in LISTS:
        d = ROOT / folder
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file() or not f.name.endswith('.md'):
                continue
            try:
                fm, body = parse_md(f)
                out.append({'path': f, 'folder': folder, 'fm': fm, 'body': body, 'mtime': f.stat().st_mtime})
            except Exception as e:
                log(f'WARN parse {f}: {e}')
    return out

# ---------- Google scan ----------
def scan_google():
    out = []
    list_map = {}
    for L in gapi('/users/@me/lists?maxResults=100').get('items', []):
        list_map[L['title']] = L['id']
        if L['title'] not in LISTS:
            continue
        res = gapi(f'/lists/{L["id"]}/tasks?maxResults=100&showCompleted=true&showHidden=true')
        for t in res.get('items', []):
            out.append({
                'id': t['id'], 'list_id': L['id'], 'list_name': L['title'],
                'title': t.get('title', ''), 'notes': t.get('notes', '') or '',
                'status': t.get('status', 'needsAction'), 'due': t.get('due', ''),
                'updated': t.get('updated', '')
            })
    return out, list_map

# ---------- Helpers ----------
def parse_iso(s):
    if not s:
        return 0
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z'):
        try:
            return datetime.strptime(s.replace('Z', '+0000'), fmt).timestamp()
        except Exception:
            continue
    return 0

def fm_status_done(fm):
    return (fm.get('status', '') or '').strip().lower() in ('done', 'completed')

def md_to_google_body(fm, body, folder, path=None):
    title = (fm.get('title', '').strip() or '')
    if not title:
        m = re.match(r'^#\s+(.+?)\s*$', body, re.M)
        if m:
            title = m.group(1).strip()
    if not title and path is not None:
        title = path.stem
    if not title:
        title = 'untitled'
    notes = re.sub(r'^#\s+.+?\n+', '', body, count=1).strip()
    return {
        'title': title,
        'notes': notes,
        'status': 'completed' if fm_status_done(fm) else 'needsAction',
    }

def google_to_md_fm(g):
    fm = {
        'title': g['title'] or 'untitled',
        'status': 'done' if g['status'] == 'completed' else 'open',
        'tags': '[task]',
        'created': datetime.now().strftime('%Y-%m-%d'),
        'google_task_id': g['id'],
        'google_list': g['list_name'],
    }
    if g['due']:
        fm['due'] = g['due'][:10]
    return fm

# ---------- Sync ----------
def main(dry=False):
    state = {}
    if Path(STATE_FILE).exists():
        try:
            state = json.load(open(STATE_FILE))
        except Exception as e:
            log(f'WARN state load failed: {e}')
            state = {}

    vault = scan_vault()
    google, list_map = scan_google()
    log(f'Vault: {len(vault)}, Google: {len(google)}, State: {len(state)}')

    g_by_id = {g['id']: g for g in google}
    v_by_gid = {v['fm'].get('google_task_id'): v for v in vault if v['fm'].get('google_task_id')}

    actions = {'push_new': 0, 'push_update': 0, 'pull_new': 0, 'pull_update': 0, 'complete': 0, 'move_done': 0, 'noop': 0}

    # 1) Vault files without google_task_id -> CREATE in Google
    for v in vault:
        if v['fm'].get('google_task_id'):
            continue
        if v['folder'] == DONE_LIST:
            continue
        list_id = list_map.get(v['folder'])
        if not list_id:
            log(f'WARN no Google list for folder {v["folder"]}')
            continue
        body = md_to_google_body(v['fm'], v['body'], v['folder'], v['path'])
        if dry:
            log(f'[DRY] push_new: {v["path"].name} -> {v["folder"]}')
            actions['push_new'] += 1
            continue
        try:
            new_t = gapi(f'/lists/{list_id}/tasks', 'POST', body)
            if body['status'] == 'completed':
                gapi(f'/lists/{list_id}/tasks/{new_t["id"]}', 'PATCH', {'status': 'completed'})
            v['fm']['google_task_id'] = new_t['id']
            v['fm']['google_list'] = v['folder']
            if 'tags' not in v['fm']:
                v['fm']['tags'] = '[task]'
            write_md(v['path'], v['fm'], v['body'])
            state[new_t['id']] = {'path': str(v['path']), 'list': v['folder'], 'updated': new_t.get('updated', '')}
            actions['push_new'] += 1
            log(f'push_new: {v["path"].name} -> {v["folder"]} (id={new_t["id"]})')
        except Exception as e:
            log(f'FAIL push_new {v["path"].name}: {e}')

    # 2) Google tasks without vault file -> CREATE in vault
    for g in google:
        if g['id'] in v_by_gid:
            continue
        target_folder = g['list_name']
        target_dir = ROOT / target_folder
        if not target_dir.is_dir():
            log(f'WARN target folder missing: {target_folder}')
            continue
        if dry:
            log(f'[DRY] pull_new: {g["title"][:40]} -> {target_folder}/')
            actions['pull_new'] += 1
            continue
        fm = google_to_md_fm(g)
        body = g['notes'] or ''
        fname = safe_filename(g['title']) + '.md'
        path = target_dir / fname
        i = 2
        while path.exists():
            path = target_dir / (safe_filename(g['title']) + f' ({i}).md')
            i += 1
        write_md(path, fm, body)
        state[g['id']] = {'path': str(path), 'list': target_folder, 'updated': g['updated']}
        actions['pull_new'] += 1
        log(f'pull_new: {fname} <- {g["list_name"]}')

    # 3) Paired files: conflict resolution
    for v in list(vault):
        gid = v['fm'].get('google_task_id')
        if not gid:
            continue
        g = g_by_id.get(gid)
        if g is None:
            # Missing in Google now -> deleted there -> move .md to Сделанные/
            if v['folder'] != DONE_LIST:
                if dry:
                    log(f'[DRY] move_done (deleted in google): {v["path"].name}')
                    actions['move_done'] += 1
                    continue
                target = DONE / v['path'].name
                if target.exists():
                    target = DONE / (target.stem + ' (orphan).md')
                v['path'].rename(target)
                fm2, body2 = parse_md(target)
                fm2['status'] = 'done'
                fm2['google_list'] = DONE_LIST
                write_md(target, fm2, body2)
                actions['move_done'] += 1
                log(f'move_done (deleted in google): {v["path"].name} -> Сделанные/')
            continue

        v_done = fm_status_done(v['fm'])
        g_done = (g['status'] == 'completed')

        if v_done and not g_done:
            if dry:
                log(f'[DRY] complete in google: {v["path"].name}')
                actions['complete'] += 1
                continue
            try:
                done_lid = list_map.get(DONE_LIST)
                if done_lid and g['list_id'] != done_lid:
                    moved = gapi(f'/lists/{done_lid}/tasks', 'POST', {'title': g['title'], 'notes': g['notes'], 'status': 'completed'})
                    gapi(f'/lists/{g["list_id"]}/tasks/{gid}', 'DELETE')
                    state.pop(gid, None)
                    new_gid = moved['id']
                    v['fm']['google_task_id'] = new_gid
                else:
                    gapi(f'/lists/{g["list_id"]}/tasks/{gid}', 'PATCH', {'status': 'completed'})
                    new_gid = gid
                target = DONE / v['path'].name
                if target.exists():
                    target = DONE / (target.stem + ' (done).md')
                v['path'].rename(target)
                v['fm']['google_list'] = DONE_LIST
                write_md(target, v['fm'], v['body'])
                state[new_gid] = {'path': str(target), 'list': DONE_LIST, 'updated': datetime.now().isoformat()}
                actions['complete'] += 1
                log(f'complete: {v["path"].name} -> Сделанные/')
            except Exception as e:
                log(f'FAIL complete {v["path"].name}: {e}')
            continue

        if g_done and not v_done:
            if dry:
                log(f'[DRY] move_done from google: {v["path"].name}')
                actions['move_done'] += 1
                continue
            target = DONE / v['path'].name
            if target.exists():
                target = DONE / (target.stem + ' (done).md')
            v['path'].rename(target)
            v['fm']['status'] = 'done'
            v['fm']['google_list'] = DONE_LIST
            write_md(target, v['fm'], v['body'])
            state[gid] = {'path': str(target), 'list': DONE_LIST, 'updated': g['updated']}
            actions['move_done'] += 1
            log(f'move_done (completed in google): {v["path"].name} -> Сделанные/')
            continue

        # Compare timestamps
        v_ts = v['mtime']
        g_ts = parse_iso(g['updated'])
        last_v = state.get(gid, {}).get('last_v_mtime', 0)
        last_g = parse_iso(state.get(gid, {}).get('updated', ''))
        v_changed = v_ts > last_v + 1
        g_changed = g_ts > last_g + 1

        if not v_changed and not g_changed:
            actions['noop'] += 1
            continue

        if v_changed and (not g_changed or v_ts > g_ts):
            if dry:
                log(f'[DRY] push_update: {v["path"].name}')
                actions['push_update'] += 1
                continue
            try:
                body = md_to_google_body(v['fm'], v['body'], v['folder'], v['path'])
                if v['folder'] != g['list_name']:
                    new_lid = list_map.get(v['folder'])
                    if new_lid:
                        new_t = gapi(f'/lists/{new_lid}/tasks', 'POST', body)
                        gapi(f'/lists/{g["list_id"]}/tasks/{gid}', 'DELETE')
                        v['fm']['google_task_id'] = new_t['id']
                        v['fm']['google_list'] = v['folder']
                        write_md(v['path'], v['fm'], v['body'])
                        state.pop(gid, None)
                        state[new_t['id']] = {'path': str(v['path']), 'list': v['folder'], 'updated': new_t.get('updated', '')}
                else:
                    upd = gapi(f'/lists/{g["list_id"]}/tasks/{gid}', 'PATCH', body)
                    state[gid] = {'path': str(v['path']), 'list': v['folder'], 'updated': upd.get('updated', '')}
                actions['push_update'] += 1
                log(f'push_update: {v["path"].name}')
            except Exception as e:
                log(f'FAIL push_update {v["path"].name}: {e}')
        elif g_changed:
            if dry:
                log(f'[DRY] pull_update: {v["path"].name}')
                actions['pull_update'] += 1
                continue
            v['fm']['title'] = g['title']
            if g['due']:
                v['fm']['due'] = g['due'][:10]
            v['fm']['google_list'] = g['list_name']
            new_body = g['notes'] or v['body']
            if g['list_name'] != v['folder']:
                target_dir = ROOT / g['list_name']
                target_dir.mkdir(exist_ok=True)
                target = target_dir / v['path'].name
                if target.exists():
                    target = target_dir / (target.stem + ' (moved).md')
                v['path'].rename(target)
                v['path'] = target
            write_md(v['path'], v['fm'], new_body)
            state[gid] = {'path': str(v['path']), 'list': g['list_name'], 'updated': g['updated']}
            actions['pull_update'] += 1
            log(f'pull_update: {v["path"].name}')

    # Save state with mtime tracking
    for gid, info in list(state.items()):
        p = Path(info.get('path', ''))
        if p.exists():
            info['last_v_mtime'] = p.stat().st_mtime
    if not dry:
        Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
        json.dump(state, open(STATE_FILE, 'w'), ensure_ascii=False, indent=2)

    log(f'Done. {actions}')

if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    try:
        main(dry=dry)
    except Exception as e:
        log(f'FATAL: {type(e).__name__}: {e}')
        raise
