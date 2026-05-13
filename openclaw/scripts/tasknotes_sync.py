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

LISTS = ['На сегодня','На завтра','Недельные планы','Идеи','Жду ответа','Сделанные','Финансовые задачи','Продукты']
DONE_LIST = 'Сделанные'

# Folder ↔ TaskNotes plugin status — теперь identity (status в frontmatter = русское название = folder name)
FOLDER_TO_STATUS = {f: f for f in LISTS}
STATUS_TO_FOLDER = {f: f for f in LISTS}

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
            v = v.strip()
            # Strip surrounding YAML quotes
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            fm[k.strip()] = v
    return fm, txt[end+5:]

def _yaml_value(v):
    """Quote scalar if it contains spaces, colons, or starts with special chars."""
    s = str(v) if v is not None else ''
    if not s:
        return s
    # Already quoted - leave as-is
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s
    # Inline list/object - leave as-is
    if s.startswith('[') or s.startswith('{'):
        return s
    # Quote if it has problematic chars
    if any(ch in s for ch in (' ', ':', '#', '@', '`', '*', '&', '!', '|', '>')):
        # Use double quotes; escape inner quotes
        inner = s.replace('"', '\\"')
        return f'"{inner}"'
    return s

def write_md(path, fm, body):
    keys_order = ['title','status','tags','due','created','updated','google_task_id','google_list']
    lines = ['---']
    written = set()
    for k in keys_order:
        if k in fm:
            lines.append(f'{k}: {_yaml_value(fm[k])}')
            written.add(k)
    for k, v in fm.items():
        if k not in written:
            lines.append(f'{k}: {_yaml_value(v)}')
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

# ---------- Strip Obsidian markup for Google Tasks notes ----------
def strip_obsidian_markup(s):
    if not s:
        return s
    # [[X|Y]] -> Y (display alias)
    s = re.sub(r'\[\[([^\|\]]+?)\|([^\]]+?)\]\]', r'\2', s)
    # [[X]] -> X
    s = re.sub(r'\[\[([^\]]+?)\]\]', r'\1', s)
    # ![[X]] (embed) -> drop entirely (embeds в notes Google не имеют смысла)
    s = re.sub(r'!\[\[[^\]]+?\]\]\s*\n?', '', s)
    # [X](url) markdown link -> X
    s = re.sub(r'\[([^\]]+?)\]\(([^\)]+?)\)', r'\1', s)
    # **X** / __X__ bold -> X
    s = re.sub(r'\*\*([^\*]+?)\*\*', r'\1', s)
    s = re.sub(r'__([^_]+?)__', r'\1', s)
    # ~~X~~ strikethrough -> X
    s = re.sub(r'~~([^~]+?)~~', r'\1', s)
    # *X* italic (избегаем bullet-list "* item")
    s = re.sub(r'(?<!\*)(?<!^)\*([^\*\s][^\*\n]*?)\*(?!\*)', r'\1', s, flags=re.MULTILINE)
    # _X_ italic
    s = re.sub(r'(?<!_)\b_([^_\s][^_\n]*?)_\b(?!_)', r'\1', s)
    # `X` inline code -> X
    s = re.sub(r'`([^`\n]+?)`', r'\1', s)
    # Headings: убрать leading ## / ### / etc, оставить текст
    s = re.sub(r'^#{1,6}\s+', '', s, flags=re.MULTILINE)
    # Tag-обёртки `>[!NOTE]` (Obsidian callout) убираем строку
    s = re.sub(r'^>\s*\[![^\]]+\][^\n]*\n?', '', s, flags=re.MULTILINE)
    # Bullet-quote `> ` в начале строки
    s = re.sub(r'^>\s?', '', s, flags=re.MULTILINE)
    return s

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
def _has_task_tag_raw(text):
    """Check raw md text for #task tag (handles inline list, YAML block list, body)."""
    if not text.startswith('---\n'):
        return '#task' in text
    end = text.find('\n---\n', 4)
    if end < 0:
        return '#task' in text
    fm_text = text[4:end]
    body = text[end+5:]
    in_tags = False
    for line in fm_text.splitlines():
        if line.startswith('tags:'):
            stripped = line[5:].strip()
            if stripped and 'task' in stripped:
                return True
            in_tags = (stripped == '')
            continue
        if in_tags:
            if line.startswith('  - ') or line.startswith('- '):
                if 'task' in line:
                    return True
                continue
            if line.startswith(' '):
                continue
            in_tags = False
    return '#task' in body

def _add_task_tag(fm):
    raw = (fm.get('tags', '') or '').strip()
    if not raw:
        fm['tags'] = '[task]'
    elif raw.startswith('[') and raw.endswith(']'):
        items = [x.strip() for x in raw[1:-1].split(',') if x.strip()]
        if 'task' not in items:
            items.insert(0, 'task')
        fm['tags'] = '[' + ', '.join(items) + ']'
    else:
        # Single tag string
        fm['tags'] = '[task, ' + raw + ']'

def scan_vault():
    out = []
    for folder in LISTS:
        d = ROOT / folder
        if not d.is_dir():
            continue
        for f in list(d.iterdir()):
            if not f.is_file() or not f.name.endswith('.md'):
                continue
            try:
                raw = f.read_text(encoding='utf-8')
                fm, body = parse_md(f)
                # Auto-tag: any file in TaskNotes/<list>/ must have #task
                if not _has_task_tag_raw(raw):
                    _add_task_tag(fm)
                    write_md(f, fm, body)
                    log(f'auto_tag: {folder}/{f.name}')
                # Status-driven folder: if user changed status (e.g. Kanban drag) — move file to matching folder
                target_folder = desired_folder_from_status(fm, folder)
                if target_folder != folder:
                    target_dir = ROOT / target_folder
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target = target_dir / f.name
                    if not target.exists():
                        f.rename(target)
                        log(f'status_move: {folder}/{f.name} -> {target_folder}/ (status={fm.get("status")})')
                        f = target
                        folder = target_folder
                # Auto-set status if missing or wrong (keeps file in current folder consistent)
                want_status = FOLDER_TO_STATUS.get(folder)
                if want_status and fm.get('status') != want_status:
                    fm['status'] = want_status
                    write_md(f, fm, body)
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
    s = (fm.get('status', '') or '').strip().lower()
    return s in ('done', 'completed', 'сделанные', DONE_LIST.lower())

def desired_folder_from_status(fm, current_folder):
    """If status in frontmatter maps to a different folder (user moved card in Kanban) — return target."""
    s = (fm.get('status', '') or '').strip().lower()
    return STATUS_TO_FOLDER.get(s, current_folder)

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
    notes = re.sub(r'^\s*#\s+[^\n]*(?:\n+|$)', '', body, count=1)
    notes = strip_obsidian_markup(notes).strip()
    return {
        'title': title,
        'notes': notes,
        'status': 'completed' if fm_status_done(fm) else 'needsAction',
    }

def google_to_md_fm(g):
    list_name = g['list_name']
    status = FOLDER_TO_STATUS.get(list_name, 'idea')
    if g['status'] == 'completed':
        status = 'done'
    fm = {
        'title': g['title'] or 'untitled',
        'status': status,
        'tags': '[task]',
        'created': datetime.now().strftime('%Y-%m-%d'),
        'google_task_id': g['id'],
        'google_list': list_name,
    }
    if g['due']:
        fm['due'] = g['due'][:10]
    return fm


# ---------- Sync Google sidebar order -> Kanban + plugin ----------
PLUGIN_DATA = '/volume1/obsidian/.obsidian/plugins/tasknotes/data.json'
KANBAN_BASE = '/volume1/obsidian/TaskNotes/Views/kanban-default.base'

def sync_lists_order(google_lists_in_order):
    """Update kanban columnOrder + plugin customStatuses order to match Google sidebar."""
    # Filter to known LISTS, preserve Google order
    titles = [t for t in google_lists_in_order if t in LISTS]
    # Append any missing LISTS at the end (safety, shouldn't happen)
    for t in LISTS:
        if t not in titles:
            titles.append(t)

    changed = False

    # --- 1) plugin data.json customStatuses order ---
    try:
        pd_path = Path(PLUGIN_DATA)
        pd = json.loads(pd_path.read_text(encoding='utf-8'))
        statuses = pd.get('customStatuses', [])
        current_order = [s.get('value') for s in statuses]
        if current_order != titles:
            by_value = {s['value']: s for s in statuses}
            new_statuses = []
            for i, title in enumerate(titles):
                s = by_value.get(title)
                if s:
                    s = dict(s)
                    s['order'] = i
                    new_statuses.append(s)
            # Append any extras (preserve unknown)
            for s in statuses:
                if s.get('value') not in titles:
                    new_statuses.append(s)
            pd['customStatuses'] = new_statuses
            pd_path.write_text(json.dumps(pd, ensure_ascii=False, indent=2), encoding='utf-8')
            changed = True
            log(f'sync_order: plugin customStatuses order updated -> {titles}')
    except Exception as e:
        log(f'WARN sync_order plugin: {e}')

    # --- 2) kanban-default.base columnOrder ---
    try:
        kb_path = Path(KANBAN_BASE)
        kb = kb_path.read_text(encoding='utf-8')
        m = re.search(r'columnOrder:\s*\'\{"note\.status":\[([^\]]*)\]\}\'', kb)
        if m:
            current_arr = m.group(1)
            new_arr = ', '.join(f'"{t}"' for t in titles)
            if current_arr.strip() != new_arr:
                kb = re.sub(r'columnOrder:\s*\'\{"note\.status":\[[^\]]*\]\}\'',
                            f'columnOrder: \'{{"note.status":[{new_arr}]}}\'', kb)
                kb_path.write_text(kb, encoding='utf-8')
                changed = True
                log(f'sync_order: kanban columnOrder updated')
    except Exception as e:
        log(f'WARN sync_order kanban: {e}')

    return changed


# ---------- Dedupe: merge fresh convert-empty duplicates ----------
VAULT_ROOT = Path('/volume1/obsidian')

def _is_trivial_body(body):
    """Body considered trivial (just created via convert) if it has no real content."""
    s = (body or '').strip()
    if not s:
        return True
    # Strip out heading line(s) and check rest
    no_heading = re.sub(r'^#{1,6}\s+.+?(\n|$)', '', s, count=1).strip()
    return len(no_heading) < 20

def _norm_title(s):
    return re.sub(r'\s+', ' ', (s or '').strip().lower())

def _replace_wikilinks_to(old_stem, new_stem):
    """Replace [[old_stem]] / [[folder/old_stem]] / [[X|alias]] across vault (skip TaskNotes/)."""
    count = 0
    esc = re.escape(old_stem)
    # Bare or path-style stem, without alias
    pat1 = re.compile(r'\[\[(?:[^\]\|/]+/)*' + esc + r'\]\]')
    # Same but with alias
    pat2 = re.compile(r'\[\[(?:[^\]\|/]+/)*' + esc + r'\|([^\]]+?)\]\]')
    for f in VAULT_ROOT.rglob('*.md'):
        if '/TaskNotes/' in str(f) or '/.trash/' in str(f) or '/#recycle/' in str(f) or '/.obsidian/' in str(f):
            continue
        try:
            txt = f.read_text(encoding='utf-8')
        except Exception:
            continue
        if old_stem not in txt:
            continue
        new_txt = pat1.sub(f'[[{new_stem}]]', txt)
        new_txt = pat2.sub(lambda m: f'[[{new_stem}|{m.group(1)}]]', new_txt)
        if new_txt != txt:
            f.write_text(new_txt, encoding='utf-8')
            count += 1
    return count

def merge_inline_convert_duplicates():
    """Detect fresh convert-created files with empty body, merge into existing canonical."""
    by_title = {}  # norm_title -> list of (path, fm, body, folder)
    for folder in LISTS:
        d = ROOT / folder
        if not d.is_dir():
            continue
        for f in d.glob('*.md'):
            try:
                fm, body = parse_md(f)
            except Exception:
                continue
            title = _norm_title(fm.get('title') or f.stem)
            by_title.setdefault(title, []).append((f, fm, body, folder))

    merged = 0
    for title, items in by_title.items():
        if len(items) < 2:
            continue
        # Pick canonical: prefer (a) has google_task_id, (b) longer body, (c) older mtime
        def score(item):
            f, fm, body, folder = item
            return (
                1 if fm.get('google_task_id') else 0,
                len((body or '').strip()),
                -f.stat().st_mtime,
            )
        ranked = sorted(items, key=score, reverse=True)
        canon_path, canon_fm, canon_body, canon_folder = ranked[0]
        for dup in ranked[1:]:
            d_path, d_fm, d_body, d_folder = dup
            # Only auto-delete trivial bodies (convert-created)
            if not _is_trivial_body(d_body):
                log(f'dup_skip (non-empty body): {d_path.name} <-> canon {canon_path.name}')
                continue
            if d_fm.get('google_task_id'):
                log(f'dup_skip (has google_task_id): {d_path.name} <-> canon {canon_path.name}')
                continue
            # Replace links in vault
            relinked = _replace_wikilinks_to(d_path.stem, canon_path.stem)
            # Delete dup
            try:
                d_path.unlink()
            except Exception as e:
                log(f'FAIL dup delete {d_path.name}: {e}')
                continue
            merged += 1
            log(f'dup_merged: removed {d_folder}/{d_path.name} -> canonical {canon_folder}/{canon_path.name} (relinked {relinked} files)')
    return merged


# ---------- Link existing tasks in diaries ----------
def _norm_match_title(s):
    return re.sub(r'\s+', ' ', (s or '').strip().lower())

def link_existing_tasks_in_diaries():
    """Scan 1 Входящие/YYYY-MM-DD.md, replace plain '- [ ] text' with '- [[stem]]' if text matches an existing task."""
    # Build title index from TaskNotes/
    title_to_stem = {}
    for folder in LISTS:
        d = ROOT / folder
        if not d.is_dir(): continue
        for f in d.glob('*.md'):
            try:
                fm, _ = parse_md(f)
            except Exception:
                continue
            t = _norm_match_title(fm.get('title') or f.stem)
            if t and t not in title_to_stem:
                title_to_stem[t] = f.stem

    if not title_to_stem:
        return 0

    inbox = VAULT_ROOT / '1 Входящие'
    if not inbox.is_dir():
        return 0

    diary_pat = re.compile(r'^\d{4}-\d{2}-\d{2}\.md$')
    cb_pat = re.compile(r'^(\s*[-*]\s+\[\s\]\s+)(.+?)\s*$')

    relinked = 0
    for f in inbox.iterdir():
        if not f.is_file() or not diary_pat.match(f.name):
            continue
        try:
            txt = f.read_text(encoding='utf-8')
        except Exception:
            continue
        new_lines = []
        changed = False
        for line in txt.splitlines():
            m = cb_pat.match(line)
            if not m:
                new_lines.append(line); continue
            prefix, content = m.group(1), m.group(2).strip()
            # Skip if already a wikilink
            if content.startswith('[[') and content.endswith(']]'):
                new_lines.append(line); continue
            t = _norm_match_title(content)
            stem = title_to_stem.get(t)
            if stem:
                new_lines.append(f'{prefix}[[{stem}]]')
                relinked += 1
                changed = True
            else:
                new_lines.append(line)
        if changed:
            f.write_text('\n'.join(new_lines) + ('\n' if txt.endswith('\n') else ''), encoding='utf-8')
            log(f'diary_relink: {f.name} (+{relinked} links so far)')
    return relinked

# ---------- Route fresh convert-tasks by source location ----------
def _find_link_sources(stem):
    """Return list of vault .md files that contain wikilink to stem (any form)."""
    sources = []
    pat = re.compile(r'\[\[(?:[^\]\|/]+/)*' + re.escape(stem) + r'(?:\|[^\]]+)?\]\]')
    for f in VAULT_ROOT.rglob('*.md'):
        sp = str(f)
        if '/TaskNotes/' in sp or '/.obsidian/' in sp or '/#recycle/' in sp or '/.trash/' in sp:
            continue
        try:
            txt = f.read_text(encoding='utf-8')
        except Exception:
            continue
        if stem in txt and pat.search(txt):
            sources.append(f)
    return sources

def _is_today_diary(path):
    today = datetime.now().strftime('%Y-%m-%d')
    return path.name == f'{today}.md' and '1 Входящие' in str(path)

def _is_meeting_note(path):
    sp = str(path)
    if '7 ДиЛайт Рент' in sp and 'овещани' in path.name:  # "Совещание" / "совещание"
        return True
    try:
        fm, _ = parse_md(path)
    except Exception:
        return False
    tags_raw = (fm.get('tags', '') or '').lower()
    if 'совещан' in tags_raw or 'meeting' in tags_raw:
        return True
    if 'meeting' in (fm.get('type', '') or '').lower():
        return True
    return False

def route_fresh_tasks_by_source():
    """Move fresh convert-tasks (in Идеи, no google_task_id, trivial body) to proper folder based on source link."""
    src_dir = ROOT / 'Идеи'
    if not src_dir.is_dir():
        return 0
    moved = 0
    for f in list(src_dir.glob('*.md')):
        try:
            fm, body = parse_md(f)
        except Exception:
            continue
        if fm.get('google_task_id'):
            continue
        if not _is_trivial_body(body):
            continue
        sources = _find_link_sources(f.stem)
        if not sources:
            continue
        target_folder = None
        for s in sources:
            if _is_today_diary(s):
                target_folder = 'На сегодня'
                break
            if _is_meeting_note(s):
                target_folder = 'Жду ответа'
                break
        # Heuristic: title starts with "Фамилия [+ Фамилия]* - " -> delegated -> Жду ответа
        if not target_folder:
            title = (fm.get('title','').strip() or f.stem)
            if re.match(r'^[A-ZА-ЯЁ][a-zа-яё]+(\s*[+&]\s*[A-ZА-ЯЁ][a-zа-яё]+)*\s*[-–—]\s+', title):
                target_folder = 'Жду ответа'
        if not target_folder or target_folder == 'Идеи':
            continue
        target_dir = ROOT / target_folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f.name
        if target.exists():
            log(f'route_skip (target exists): {f.name} -> {target_folder}')
            continue
        f.rename(target)
        moved += 1
        log(f'route: {f.name} Идеи -> {target_folder} (source: {sources[0].name})')
    return moved

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
    # Sync sidebar order Google -> Obsidian
    sync_lists_order(list(list_map.keys()))
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
            # FIX 2026-05-13: keep g_by_id consistent after push_new so step 3 (paired) does not treat just-created task as deleted-in-google
            g_by_id[new_t['id']] = {
                'id': new_t['id'],
                'list_id': list_id,
                'list_name': v['folder'],
                'title': v['fm'].get('title', ''),
                'notes': '',
                'status': body.get('status', 'needsAction'),
                'updated': new_t.get('updated', ''),
            }
            actions['push_new'] += 1
            log(f'push_new: {v["path"].name} -> {v["folder"]} (id={new_t["id"]})')
        except Exception as e:
            log(f'FAIL push_new {v["path"].name}: {e}')

    # 2) Google tasks without vault file -> CREATE in vault
    for g in google:
        if g['id'] in v_by_gid:
            continue
        # If completed in non-Done list, route to Сделанные/ + relocate in Google
        if g['status'] == 'completed' and g['list_name'] != DONE_LIST:
            done_lid = list_map.get(DONE_LIST)
            if done_lid and not dry:
                try:
                    moved = gapi(f'/lists/{done_lid}/tasks', 'POST', {'title': g['title'], 'notes': g['notes']})
                    gapi(f'/lists/{done_lid}/tasks/{moved["id"]}', 'PATCH', {'status': 'completed'})
                    gapi(f'/lists/{g["list_id"]}/tasks/{g["id"]}', 'DELETE')
                    g['id'] = moved['id']
                    g['list_id'] = done_lid
                    g['list_name'] = DONE_LIST
                except Exception as e:
                    log(f'WARN pull_new relocate-to-done failed: {e}')
        target_folder = g['list_name'] if g['status'] != 'completed' else DONE_LIST
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
            # Also move Google task to "Сделанные" list (it stayed in original list, just completed)
            done_lid = list_map.get(DONE_LIST)
            new_gid = gid
            if done_lid and g['list_id'] != done_lid:
                try:
                    moved = gapi(f'/lists/{done_lid}/tasks', 'POST', {
                        'title': g['title'], 'notes': g['notes'],
                    })
                    gapi(f'/lists/{done_lid}/tasks/{moved["id"]}', 'PATCH', {'status': 'completed'})
                    gapi(f'/lists/{g["list_id"]}/tasks/{gid}', 'DELETE')
                    state.pop(gid, None)
                    new_gid = moved['id']
                    v['fm']['google_task_id'] = new_gid
                except Exception as e:
                    log(f'WARN move_done: google relocate failed: {e}')
            write_md(target, v['fm'], v['body'])
            state[new_gid] = {'path': str(target), 'list': DONE_LIST, 'updated': g['updated']}
            actions['move_done'] += 1
            log(f'move_done (completed in google): {v["path"].name} -> Сделанные/ (+ Google relocated)')
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
