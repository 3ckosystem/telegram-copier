import json
import os

PATH = "data/settings.json"


def load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(d):
    os.makedirs("data", exist_ok=True)
    with open(PATH, "w") as f:
        json.dump(d, f)


def get_ids():
    d = load()
    src = d.get("source_id") or os.environ.get("SOURCE_CHAT_ID") or None
    tgt = d.get("target_id") or os.environ.get("TARGET_CHAT_ID") or None
    return (int(src) if src else None), (int(tgt) if tgt else None)


def template_path():
    p = os.environ.get("TEMPLATE_PATH", "template.txt")
    return p if os.path.exists(p) else "template.txt"


def save_template(text):
    p = os.environ.get("TEMPLATE_PATH", "template.txt")
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text.strip("\n") + "\n")


def default_template_path():
    p = os.environ.get("TEMPLATE_PATH") or "template.txt"
    return os.path.join(os.path.dirname(p), "template_default.txt")


def load_default_template():
    try:
        with open(default_template_path(), encoding="utf-8") as f:
            return f.read().strip("\n") or None
    except FileNotFoundError:
        return None


def save_default_template(text):
    p = default_template_path()
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text.strip("\n") + "\n")
