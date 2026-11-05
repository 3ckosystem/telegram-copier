import json
import os
import re
import time

PATH = "data/sources.json"


def _load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save(d):
    os.makedirs("data", exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, PATH)


def _entry(d, sid):
    return d.setdefault(str(sid), {"name": None, "hidden": False, "last_copy": None})


def ensure(sid):
    d = _load()
    if str(sid) not in d:
        _entry(d, sid)
        _save(d)


def register(sid, name):
    d = _load()
    e = _entry(d, sid)
    e["name"], e["hidden"] = name, False
    _save(d)


def set_name(sid, name):
    d = _load()
    _entry(d, sid)["name"] = name
    _save(d)


def touch_copy(sid):
    d = _load()
    _entry(d, sid)["last_copy"] = time.time()
    _save(d)


def set_hidden(sid, flag):
    d = _load()
    _entry(d, sid)["hidden"] = bool(flag)
    _save(d)


def unhide_all():
    d = _load()
    for e in d.values():
        e["hidden"] = False
    _save(d)


def hidden_count():
    return sum(1 for e in _load().values() if e.get("hidden"))


def entries(hidden=False):
    items = [(int(k), v) for k, v in _load().items() if bool(v.get("hidden")) == hidden]
    items.sort(key=lambda x: x[1].get("last_copy") or 0, reverse=True)
    return items


def seed():
    d = _load()
    changed = False
    if os.path.isdir("data"):
        for fn in os.listdir("data"):
            m = re.fullmatch(r"progress_(-?\d+)_(-?\d+)\.json", fn)
            if m and m.group(1) not in d:
                e = _entry(d, m.group(1))
                e["last_copy"] = os.path.getmtime(os.path.join("data", fn))
                changed = True
    if changed:
        _save(d)
