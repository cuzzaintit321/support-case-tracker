"""A local support case tracker backed by SQLite; no third-party packages."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).parent
STATES = {'open', 'in_progress', 'waiting', 'resolved'}
PRIORITIES = {'low', 'normal', 'high', 'urgent'}
LOG = logging.getLogger('case-tracker')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def field(body, name, limit, required=True):
    value = body.get(name, '')
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f'{name} must contain {"1" if required else "0"}–{limit} characters')
    return value.strip()


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL CHECK(priority IN ('low','normal','high','urgent')),
                status TEXT NOT NULL CHECK(status IN ('open','in_progress','waiting','resolved')),
                resolution TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                case_id INTEGER NOT NULL REFERENCES cases(id),
                kind TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_case ON events(case_id, id);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self, query='', status='', priority=''):
        if status and status not in STATES:
            raise ValueError('Unknown status')
        if priority and priority not in PRIORITIES:
            raise ValueError('Unknown priority')
        if len(query) > 200:
            raise ValueError('Search must contain at most 200 characters')
        with self.connect() as db:
            items = db.execute('''SELECT * FROM cases
                WHERE (? = '' OR instr(lower(title || ' ' || description || ' ' || category || ' ' || resolution), lower(?)) > 0)
                AND (? = '' OR status = ?) AND (? = '' OR priority = ?)
                ORDER BY updated_at DESC, id DESC LIMIT 200''',
                (query, query, status, status, priority, priority)).fetchall()
            counts = {state: 0 for state in sorted(STATES)}
            for row in db.execute('SELECT status, count(*) AS n FROM cases GROUP BY status'):
                counts[row['status']] = row['n']
            return {'items': [dict(row) for row in items], 'counts': counts, 'limit': 200}

    def detail(self, case_id):
        with self.connect() as db:
            case = db.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
            if case is None:
                return None
            result = dict(case)
            result['events'] = [dict(row) for row in db.execute(
                'SELECT id, kind, body, created_at FROM events WHERE case_id=? ORDER BY id', (case_id,))]
            return result

    def create(self, body):
        title = field(body, 'title', 160)
        description = field(body, 'description', 8000)
        category = field(body, 'category', 80)
        priority = body.get('priority', 'normal')
        if not isinstance(priority, str) or priority not in PRIORITIES:
            raise ValueError('Unknown priority')
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO cases
                (title, description, category, priority, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'open', ?, ?)''',
                (title, description, category, priority, timestamp, timestamp))
            case_id = cursor.lastrowid
            db.execute("INSERT INTO events(case_id, kind, body, created_at) VALUES (?, 'created', 'Case opened', ?)", (case_id, timestamp))
        return self.detail(case_id)

    def update(self, case_id, body):
        status = body.get('status')
        if not isinstance(status, str) or status not in STATES:
            raise ValueError('Unknown status')
        resolution = field(body, 'resolution', 8000, required=status == 'resolved')
        with self.connect() as db:
            # Acquire the write transaction before reading current status.
            db.execute('BEGIN IMMEDIATE')
            case = db.execute('SELECT status, resolution FROM cases WHERE id=?', (case_id,)).fetchone()
            if case is None:
                return None
            timestamp = now()
            db.execute('UPDATE cases SET status=?, resolution=?, updated_at=? WHERE id=?',
                       (status, resolution, timestamp, case_id))
            if status != case['status']:
                db.execute("INSERT INTO events(case_id,kind,body,created_at) VALUES (?,'status',?,?)",
                           (case_id, f"{case['status']} → {status}", timestamp))
            if resolution != case['resolution']:
                db.execute("INSERT INTO events(case_id,kind,body,created_at) VALUES (?,'resolution',?,?)",
                           (case_id, resolution or 'Resolution cleared', timestamp))
        return self.detail(case_id)

    def note(self, case_id, body):
        text = field(body, 'body', 8000)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM cases WHERE id=?', (case_id,)).fetchone() is None:
                return None
            timestamp = now()
            db.execute("INSERT INTO events(case_id,kind,body,created_at) VALUES (?,'note',?,?)", (case_id, text, timestamp))
            db.execute('UPDATE cases SET updated_at=? WHERE id=?', (timestamp, case_id))
        return self.detail(case_id)

    def export(self):
        # A consistent read transaction includes cases and their events.
        with self.connect() as db:
            db.execute('BEGIN')
            cases = [dict(row) for row in db.execute('SELECT * FROM cases ORDER BY id')]
            for case in cases:
                case['events'] = [dict(row) for row in db.execute('SELECT * FROM events WHERE case_id=? ORDER BY id', (case['id'],))]
        return {'schema_version': 1, 'exported_at': now(), 'cases': cases}


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, value, content_type='application/json; charset=utf-8', download=False):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="support-cases.json"')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            try:
                assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
                if path in assets:
                    filename, kind = assets[path]
                    self.reply(200, (ROOT / filename).read_bytes(), kind)
                elif path == '/api/cases':
                    args = parse_qs(parsed.query)
                    self.reply(200, store.list(args.get('q', [''])[0], args.get('status', [''])[0], args.get('priority', [''])[0]))
                elif path == '/api/export':
                    self.reply(200, store.export(), download=True)
                elif path == '/health':
                    with store.connect() as db:
                        db.execute('SELECT id FROM cases LIMIT 1').fetchall()
                    self.reply(200, {'status': 'ready'})
                elif match := re.fullmatch(r'/api/cases/([1-9][0-9]{0,8})', path):
                    case = store.detail(int(match.group(1)))
                    self.reply(200 if case else 404, case or {'error': 'Case not found'})
                else:
                    self.reply(404, {'error': 'Not found'})
            except ValueError as error:
                self.reply(400, {'error': str(error)})
            except (OSError, sqlite3.Error):
                LOG.exception('Read failed')
                self.reply(503, {'error': 'Storage unavailable'})

        def do_POST(self):
            self.connection.settimeout(5)
            # JSON content-type requirement and absent CORS prevent ordinary cross-origin form writes.
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                self.reply(415, {'error': 'Use application/json'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    self.reply(413, {'error': 'Request body must be 1–65536 bytes'})
                    return
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('JSON object required')
            except (ValueError, UnicodeDecodeError, OSError):
                self.reply(400, {'error': 'Invalid JSON request'})
                return
            try:
                if self.path == '/api/cases':
                    self.reply(201, store.create(body))
                elif match := re.fullmatch(r'/api/cases/([1-9][0-9]{0,8})/(status|notes)', self.path):
                    case = store.update(int(match.group(1)), body) if match.group(2) == 'status' else store.note(int(match.group(1)), body)
                    self.reply(200 if case else 404, case or {'error': 'Case not found'})
                else:
                    self.reply(404, {'error': 'Not found'})
            except ValueError as error:
                self.reply(400, {'error': str(error)})
            except sqlite3.Error:
                LOG.exception('Write failed')
                self.reply(503, {'error': 'Storage unavailable'})

        def log_message(self, fmt, *args):
            # Do not log search queries or customer details.
            LOG.info('HTTP response: %s', args[1] if len(args) > 1 else '')
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='data/cases.sqlite3')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8777)
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    store = Store(args.db)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(store))
    print(f'Support Case Tracker: http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
