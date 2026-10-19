"""Create and resolve a labeled exercise case against a running tracker."""
import argparse
import json
from urllib.request import Request, urlopen
import uuid


def run(base):
    def request(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(base.rstrip('/')+path, data=data, headers={'Content-Type':'application/json'})
        with urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    assert request('/health')[0] == 200
    code, case = request('/api/cases', {'title':'Smoke test '+uuid.uuid4().hex[:8],
        'description':'Fictional automated test record.', 'category':'Other', 'priority':'low'})
    assert code == 201
    path = '/api/cases/'+str(case['id'])
    assert request(path+'/notes', {'body':'Verified note persistence'})[0] == 200
    assert request(path+'/status', {'status':'resolved','resolution':'Automated create, note, and resolve checks passed.'})[1]['status'] == 'resolved'
    detail = request(path)[1]
    assert any(event['kind']=='note' for event in detail['events'])
    assert any(item['id']==case['id'] for item in request('/api/export')[1]['cases'])
    print('PASS: create, note, resolve, detail, and export; one labeled test case retained')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8777')
    run(parser.parse_args().base_url)
