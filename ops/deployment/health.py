"""Local, read-only API health probe. No provider refresh endpoint is called."""
import json
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8000/api/providers/', timeout=4) as response:
    assert response.status == 200
    data = json.load(response)
    assert isinstance(data, (list, dict)), 'Unexpected provider response'
print('HEALTHY /api/providers/ 200')
