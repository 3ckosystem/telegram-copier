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
    src = int(d.get("source_id") or os.environ["SOURCE_CHAT_ID"])
    tgt = int(d.get("target_id") or os.environ["TARGET_CHAT_ID"])
    return src, tgt


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
