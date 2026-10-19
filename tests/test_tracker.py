from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import server


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'cases.sqlite3'
        self.store = server.Store(self.path)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.handler_for(self.store))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.tmp.cleanup()

    def request(self, path, payload=None, raw=None, kind='application/json'):
        data = json.dumps(payload).encode() if payload is not None else raw
        request = Request(self.base+path, data=data, headers={'Content-Type': kind})
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.code, json.loads(body) if 'application/json' in response.headers['Content-Type'] else body

    def create(self, **extra):
        case = {'title': 'DNS lookup failure', 'description': 'IP works; hostname does not.', 'category': 'Networking', 'priority': 'high'}
        case.update(extra)
        code, item = self.request('/api/cases', case)
        self.assertEqual(code, 201)
        return item

    def test_create_note_resolve_and_history(self):
        case = self.create()
        path = f"/api/cases/{case['id']}"
        self.assertEqual(self.request(path+'/notes', {'body': 'Compared DNS server settings.'})[0], 200)
        self.assertEqual(self.request(path+'/status', {'status': 'in_progress'})[0], 200)
        code, item = self.request(path+'/status', {'status': 'resolved', 'resolution': 'Corrected DNS setting and verified lookup.'})
        self.assertEqual(code, 200)
        self.assertEqual(item['status'], 'resolved')
        self.assertEqual([e['kind'] for e in item['events']], ['created', 'note', 'status', 'status', 'resolution'])

    def test_resolution_required_and_invalid_state_unchanged(self):
        case = self.create()
        path = f"/api/cases/{case['id']}/status"
        self.assertEqual(self.request(path, {'status': 'resolved', 'resolution': '  '})[0], 400)
        self.assertEqual(self.request(path, {'status': 'deleted'})[0], 400)
        self.assertEqual(self.store.detail(case['id'])['status'], 'open')

    def test_search_filters_and_global_counts(self):
        self.create()
        self.create(title='Slow boot', category='Linux', priority='low')
        code, result = self.request('/api/cases?q=DNS&priority=high&status=open')
        self.assertEqual(code, 200)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['counts']['open'], 2)
        self.assertEqual(self.request('/api/cases?status=broken')[0], 400)

    def test_search_is_literal_and_sql_is_parameterized(self):
        self.create(title="Literal % quote ' and <script>text</script>")
        self.assertEqual(len(self.request('/api/cases?q=%25')[1]['items']), 1)
        self.assertEqual(len(self.request('/api/cases?q=%27%20OR%201%3D1--')[1]['items']), 0)
        self.assertEqual(len(self.store.list()['items']), 1)

    def test_persistence_and_export_include_notes(self):
        case = self.create()
        self.store.note(case['id'], {'body': 'Verified settings'})
        reopened = server.Store(self.path)
        self.assertEqual(reopened.detail(case['id'])['title'], case['title'])
        code, exported = self.request('/api/export')
        self.assertEqual(code, 200)
        self.assertEqual(exported['schema_version'], 1)
        self.assertEqual(exported['cases'][0]['events'][-1]['body'], 'Verified settings')

    def test_input_limits_content_type_and_json(self):
        self.assertEqual(self.request('/api/cases', raw=b'{')[0], 400)
        self.assertEqual(self.request('/api/cases', payload=[])[0], 400)
        self.assertEqual(self.request('/api/cases', raw=b'x'*65537)[0], 413)
        self.assertEqual(self.request('/api/cases', payload={}, kind='text/plain')[0], 415)
        self.assertEqual(self.request('/api/cases', payload={'title':'a'*161})[0], 400)
        self.assertEqual(len(self.store.list()['items']), 0)

    def test_missing_cases_and_file_paths(self):
        self.assertEqual(self.request('/api/cases/42')[0], 404)
        self.assertEqual(self.request('/api/cases/42/notes', {'body': 'note'})[0], 404)
        self.assertEqual(self.request('/server.py')[0], 404)
        self.assertEqual(self.request('/data/cases.sqlite3')[0], 404)

    def test_reopen_keeps_original_resolution_in_history(self):
        case = self.create()
        self.store.update(case['id'], {'status': 'resolved', 'resolution': 'Temporary fix'})
        reopened = self.store.update(case['id'], {'status': 'open', 'resolution': ''})
        self.assertEqual(reopened['resolution'], '')
        self.assertIn('Temporary fix', [x['body'] for x in reopened['events']])

    def test_health_reads_database(self):
        self.assertEqual(self.request('/health')[0], 200)
        with self.store.connect() as db:
            db.execute('DROP TABLE cases')
        self.assertEqual(self.request('/health')[0], 503)


if __name__ == '__main__':
    unittest.main()
