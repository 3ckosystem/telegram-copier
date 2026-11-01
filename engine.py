import asyncio
import json
import shutil
import tempfile
import re
import os
import time
import richtext
from telethon import errors
from organizer import client, build_blocks, split_albums, needs_convert
from settings import get_ids, template_path

CAPTION_LIMIT = 2048
_cache = {"src": None, "t": 0.0, "blocks": None}


def state_path(src, tgt):
    return f"data/progress_{src}_{tgt}.json"


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"next_index": 0, "albums_done": 0, "next_key": None}


def save_state(path, s):
    os.makedirs("data", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, path)


def render(title):
    with open(template_path(), encoding="utf-8") as f:
        tpl = f.read().strip("\n")
    one_line = re.sub(r"\s*\n+\s*", " ", title or "").strip()
    return tpl.replace("{judul}", one_line)


async def load_sticker():
    sid = os.environ.get("STICKER_MSG_ID")
    if not sid:
        return None
    m = await client.get_messages("me", ids=int(sid))
    return m.media if m and m.sticker else None


def advance(path, idx, blocks):
    nxt = idx + 1
    s = {"next_index": nxt, "albums_done": 0,
         "next_key": blocks[nxt]["key"] if nxt < len(blocks) else None}
    save_state(path, s)
    return s


async def get_blocks():
    src = get_ids()[0]
    if _cache["src"] == src and time.time() - _cache["t"] < 600:
        return _cache["blocks"]
    blocks = await build_blocks()
    _cache.update(src=src, t=time.time(), blocks=blocks)
    return blocks


def locate(blocks, msg_id):
    idx = 0
    for i, b in enumerate(blocks):
        if b["key"] <= msg_id:
            idx = i
        else:
            break
    return idx


async def prepare(max_blocks):
    src, tgt = get_ids()
    path = state_path(src, tgt)
    blocks = await get_blocks()
    s = load_state(path)
    i = s["next_index"]
    if i >= len(blocks):
        raise ValueError("Semua blok sudah disalin.")
    if s["next_key"] is not None and blocks[i]["key"] != s["next_key"]:
        j = next((x for x, b in enumerate(blocks) if b["key"] == s["next_key"]), None)
        if j is None or s.get("albums_done", 0) > 0:
            raise ValueError("Susunan blok di sumber berubah. Berhenti demi keamanan.")
        i = j
        s = {"next_index": i, "albums_done": 0, "next_key": s["next_key"]}
        save_state(path, s)
    end = min(i + max_blocks, len(blocks))
    return {"src": src, "tgt": tgt, "path": path, "blocks": blocks, "state": s,
            "i": i, "end": end,
            "n_media": sum(len(b["media"]) for b in blocks[i:end])}


CONVERT = os.environ.get("CONVERT_FILES", "1") == "1"


async def _send(tgt, files, first, caps, force_document):
    kw = dict(parse_mode=richtext.MODE, force_document=force_document, supports_streaming=True)
    if len(files) == 1:
        await client.send_file(tgt, files[0], caption=first, **kw)
    else:
        await client.send_file(tgt, files, caption=caps, **kw)


async def send_album(tgt, msgs, first, caps):
    tmpdir = tempfile.mkdtemp(prefix="tgcopy_")
    files, converted = [], False
    try:
        for m in msgs:
            if CONVERT and needs_convert(m):
                files.append(await client.download_media(m, file=os.path.join(tmpdir, str(m.id))))
                converted = True
            else:
                files.append(m.media)
        try:
            await _send(tgt, files, first, caps, False)
        except errors.FloodWaitError:
            raise
        except errors.RPCError:
            if not converted:
                raise
            await _send(tgt, files, first, caps, True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def execute(plan, ask_text, notify, should_stop):
    blocks, path, tgt, src = plan["blocks"], plan["path"], plan["tgt"], plan["src"]
    s, total = plan["state"], len(plan["blocks"])
    sticker = await load_sticker()
    if sticker is None:
        await notify("Stiker pemisah tidak ditemukan. Jalankan pick_sticker.py dulu.")
        return "gagal"
    for idx in range(plan["i"], plan["end"]):
        if should_stop():
            await notify(f"Dihentikan. Berikutnya: blok {idx + 1}.")
            return "berhenti"
        b = blocks[idx]
        albums = split_albums(b["media"])
        start = s["albums_done"] if idx == s["next_index"] else 0
        if not albums:
            if not b["after_media"]:
                await notify(f"Blok {idx + 1}: teks tidak menempel pada media, dilewati.")
                s = advance(path, idx, blocks)
                continue
            ans = await ask_text(idx, b, richtext.strip_tokens(b["title"]),
                                 richtext.strip_tokens(render(b["title"])))
            if ans == "q":
                await notify(f"Berhenti. Berikutnya: blok {idx + 1}.")
                return "berhenti"
            if ans != "y":
                await notify(f"Blok {idx + 1}: dilewati.")
                s = advance(path, idx, blocks)
                continue
            await client.send_message(tgt, render(b["title"]), parse_mode=richtext.MODE)
        cap = render(b["title"]) if b["title"] else ""
        for ai in range(start, len(albums)):
            if should_stop():
                await notify(f"Dihentikan di blok {idx + 1}. Progres tersimpan.")
                return "berhenti"
            album = albums[ai]
            fresh = await client.get_messages(src, ids=[m.id for m in album])
            fresh = [x for x in fresh if x is not None and x.media]
            medias = fresh
            if not medias:
                await notify(f"Blok {idx + 1}: album {ai + 1} tidak bisa dibaca, dilewati.")
                continue
            first = ""
            if ai == 0 and cap:
                if len(richtext.MODE.parse(cap)[0]) > CAPTION_LIMIT:
                    await client.send_message(tgt, cap, parse_mode=richtext.MODE)
                else:
                    first = cap
            caps = [first] + [""] * (len(medias) - 1)
            await send_album(tgt, fresh, first, caps)
            s = {"next_index": idx, "albums_done": ai + 1, "next_key": b["key"]}
            save_state(path, s)
            await asyncio.sleep(3)
        await client.send_file(tgt, sticker)
        await asyncio.sleep(2)
        s = advance(path, idx, blocks)
        await notify(f"Blok {idx + 1}/{total} selesai ({len(b['media'])} media)")
        await asyncio.sleep(5)
    await notify(f"Selesai. Berikutnya: blok {s['next_index'] + 1}.")
    return "selesai"
