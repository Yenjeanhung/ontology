import sys, json
sys.path.insert(0, 'services')
from workflow_engine import _parse_structured

answer = '{\n  "cc": "4980mm",\n  "lc": "121km/200km",\n  "dc": "17.6kWh/25.6kWh"\n}\n\n比亚迪汉（包含EV纯电版和DM-i/DM-p插电混动版）...'
fields = [{'name':'cc','type':'string'},{'name':'dc','type':'string'},{'name':'lc','type':'string'}]
print(_parse_structured(answer, fields))
