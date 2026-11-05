import asyncio
import json
import zipfile
import shutil
import tempfile
import re
import os
import time
import richtext
import sources
from telethon import errors
from organizer import client, build_blocks, split_albums, needs_convert, is_archive, album_sizes
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


def render(title, zips=None):
    with open(template_path(), encoding="utf-8") as f:
        tpl = f.read().strip("\n")
    one_line = re.sub(r"\s*\n+\s*", " ", title or "").strip()
    z = ", ".join(zips) if zips else ""
    if "{zip}" in tpl:
        return tpl.replace("{judul}", one_line).replace("{zip}", z)
    if z:
        one_line = (one_line + "\n" + z) if one_line else z
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


async def get_blocks(progress=None):
    src = get_ids()[0]
    if _cache["src"] == src and time.time() - _cache["t"] < 600:
        return _cache["blocks"]
    blocks = await build_blocks(progress)
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


async def prepare(max_blocks, progress=None):
    src, tgt = get_ids()
    if not src or not tgt:
        raise ValueError("Sumber atau tujuan belum diatur. Atur dulu lewat menu.")
    path = state_path(src, tgt)
    blocks = await get_blocks(progress)
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
_PROG = {"cb": None}


async def _p(**kw):
    cb = _PROG["cb"]
    if cb:
        await cb(**kw)


def _fire(**kw):
    cb = _PROG["cb"]
    if cb:
        asyncio.get_running_loop().create_task(cb(**kw))


def _upload_cb(n_files, label="Mengunggah"):
    st = {"i": 0, "last": 0}

    def cb(sent, total):
        if sent < st["last"]:
            st["i"] += 1
        st["last"] = sent
        frac = (st["i"] + (sent / total if total else 0)) / max(1, n_files)
        _fire(kind="step", label=f"{label} {min(st['i'] + 1, n_files)}/{n_files} file", frac=frac)

    return cb


def _prep_image(path):
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((2560, 2560))
            out = path.rsplit(".", 1)[0] + "_t.jpg"
            im.save(out, "JPEG", quality=90, optimize=True)
        return out
    except Exception:
        return path


async def _send(tgt, files, first, caps, force_document):
    kw = dict(parse_mode=richtext.MODE, force_document=force_document, supports_streaming=True,
              progress_callback=_upload_cb(len(files)))
    if len(files) == 1:
        await client.send_file(tgt, files[0], caption=first, **kw)
    else:
        await client.send_file(tgt, files, caption=caps, **kw)


async def send_album(tgt, msgs, first, caps):
    tmpdir = tempfile.mkdtemp(prefix="tgcopy_")
    files, converted = [], False
    try:
        for k, m in enumerate(msgs, 1):
            if CONVERT and needs_convert(m):
                await _p(kind="step", label=f"Mengonversi file {k}/{len(msgs)}", frac=k / len(msgs))
                pth = await client.download_media(m, file=os.path.join(tmpdir, str(m.id)))
                if pth and pth.lower().endswith(IMG_EXT):
                    pth = await asyncio.to_thread(_prep_image, pth)
                files.append(pth)
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


ZIP_MAX_MB = int(os.environ.get("ZIP_MAX_MB", "1500"))
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
VID_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")
_last_pw = {"v": None}


def _natural(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def _pw_candidates(text):
    text = (text or "").strip()
    out = []

    def add(x):
        x = x.strip()
        if x and x not in out:
            out.append(x)

    for m in re.findall(r"(?i)\bpass(?:word)?\s*[:=\-]\s*(\S+)", text):
        add(m)
    add(text)
    add(re.sub(r"\s*\n+\s*", " ", text))
    for line in text.splitlines():
        add(line)
        for sep in (":", "="):
            if sep in line:
                add(line.split(sep, 1)[1])
    for w in re.findall(r"\S+", text):
        add(w)
        add(w.strip(".,;:!?()[]{}<>\"'`*_"))
    return out[:40]


def _pw_ok(zf, infos, pw):
    zf.setpassword(pw.encode("utf-8"))
    probe = min(infos, key=lambda i: i.file_size)
    try:
        zf.read(probe)
        return True
    except Exception:
        return False


async def _run(args, timeout=3600):
    p = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
        return p.returncode, out.decode("utf-8", "replace")
    except BaseException:
        try:
            p.kill()
        except ProcessLookupError:
            pass
        raise


def _parse_slt(text):
    parts = text.split("\n----------\n", 1)
    if len(parts) < 2:
        return []
    entries = []
    for block in parts[1].split("\n\n"):
        d = {}
        for line in block.splitlines():
            if " = " in line:
                k, v = line.split(" = ", 1)
                d[k.strip()] = v
        if "Path" in d:
            entries.append(d)
    return entries


async def _password_loop(name, test, ask_password, notify, hints):
    queue = list(hints) + ([_last_pw["v"]] if _last_pw["v"] else [])
    if queue:
        await notify(f"{name}: mencoba {len(queue)} kemungkinan password dari teks blok...")
    tried = 0
    while True:
        if queue:
            pw = queue.pop(0)
        else:
            if ask_password is None:
                await notify(f"{name}: butuh password tetapi tidak bisa ditanyakan di mode ini, dilewati.")
                return None
            pw = await ask_password(name, tried)
            tried += 1
            if pw is None:
                await notify(f"{name}: dilewati.")
                return None
        if await test(pw):
            _last_pw["v"] = pw
            if tried == 0:
                await notify(f"{name}: password cocok otomatis.")
            return pw
        if tried >= 4:
            await notify(f"{name}: password salah 4 kali, dilewati.")
            return None


async def _extract_cli(zpath, outdir, name, ask_password, notify, hints):
    rc, out = await _run(["7z", "l", "-slt", "-spd", "-y", zpath], 900)
    pw = None
    entries = _parse_slt(out) if rc == 0 else []
    if rc != 0:
        if "password" not in out.lower():
            await notify(f"{name}: format arsip tidak didukung atau rusak, dilewati.")
            return []

        async def t_hdr(p):
            r, _o = await _run(["7z", "l", "-slt", "-spd", "-y", f"-p{p}", zpath], 900)
            return r == 0

        pw = await _password_loop(name, t_hdr, ask_password, notify, hints)
        if pw is None:
            return []
        rc, out = await _run(["7z", "l", "-slt", "-spd", "-y", f"-p{pw}", zpath], 900)
        entries = _parse_slt(out)
    files = [e for e in entries if e.get("Folder") != "+" and e.get("Path")]
    if not files:
        await notify(f"{name}: arsip kosong atau tidak terbaca, dilewati.")
        return []
    total = sum(int(e.get("Size") or 0) for e in files)
    if total > ZIP_MAX_MB * 1024 * 1024:
        await notify(f"{name}: isi arsip {total // 1048576} MB melebihi batas {ZIP_MAX_MB} MB, dilewati.")
        return []
    if shutil.disk_usage(tempfile.gettempdir()).free < total * 2:
        await notify(f"{name}: ruang disk tidak cukup, dilewati.")
        return []
    enc = [e for e in files if e.get("Encrypted") == "+"]
    if enc and pw is None:
        probe = min(enc, key=lambda e: int(e.get("Size") or 0))

        async def t_file(p):
            r, _o = await _run(["7z", "t", "-spd", "-y", f"-p{p}", zpath, probe["Path"]], 1800)
            return r == 0

        pw = await _password_loop(name, t_file, ask_password, notify, hints)
        if pw is None:
            return []
    args = ["7z", "x", "-y", "-spd", f"-o{outdir}"] + ([f"-p{pw}"] if pw else []) + [zpath]
    await _p(kind="step", label=f"Ekstrak: {name} ({len(files)} file)", frac=None)
    rc, out = await _run(args, 7200)
    if rc != 0:
        await notify(f"{name}: ekstraksi gagal (kode {rc}), dilewati.")
        return []

    def collect(d):
        res = []
        for root, _dirs, names in os.walk(d):
            for n in names:
                res.append(os.path.join(root, n))
        return res

    paths = collect(outdir)
    if len(paths) == 1 and paths[0].lower().endswith(".tar"):
        d2 = outdir + "_tar"
        rc2, _o = await _run(["7z", "x", "-y", "-spd", f"-o{d2}", paths[0]], 7200)
        if rc2 == 0:
            _rm(paths[0])
            paths, outdir = collect(d2), d2

    def keep(p):
        base = os.path.basename(p)
        low = base.lower()
        return ("__MACOSX" not in p and not base.startswith("._") and low not in (".ds_store", "thumbs.db")
                and not low.endswith((".zip", ".rar", ".7z")))

    kept = sorted([p for p in paths if keep(p)], key=lambda p: _natural(os.path.relpath(p, outdir)))
    await notify(f"{name}: {len(kept)} file diproses, {len(paths) - len(kept)} dilewati (sampah sistem atau arsip bersarang).")
    return kept


async def extract_zip(zpath, outdir, name, ask_password, notify, hints=()):
    if not zipfile.is_zipfile(zpath):
        return await _extract_cli(zpath, outdir, name, ask_password, notify, hints)
    import pyzipper
    try:
        zf = pyzipper.AESZipFile(zpath)
    except Exception as e:
        await notify(f"{name}: bukan zip yang valid ({type(e).__name__}), dilewati.")
        return []
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            await notify(f"{name}: zip kosong, dilewati.")
            return []
        total = sum(i.file_size for i in infos)
        if total > ZIP_MAX_MB * 1024 * 1024:
            await notify(f"{name}: isi zip {total // 1048576} MB melebihi batas {ZIP_MAX_MB} MB, dilewati.")
            return []
        if shutil.disk_usage(tempfile.gettempdir()).free < total * 2:
            await notify(f"{name}: ruang disk tidak cukup, dilewati.")
            return []
        if any(i.flag_bits & 0x1 for i in infos):
            queue = list(hints) + ([_last_pw["v"]] if _last_pw["v"] else [])
            if queue:
                await notify(f"{name}: mencoba {len(queue)} kemungkinan password dari teks blok...")
            tried, ok = 0, False
            while not ok:
                if queue:
                    pw = queue.pop(0)
                else:
                    if ask_password is None:
                        await notify(f"{name}: butuh password tetapi tidak bisa ditanyakan di mode ini, dilewati.")
                        return []
                    pw = await ask_password(name, tried)
                    tried += 1
                    if pw is None:
                        await notify(f"{name}: dilewati.")
                        return []
                ok = await asyncio.to_thread(_pw_ok, zf, infos, pw)
                if ok:
                    _last_pw["v"] = pw
                    if tried == 0:
                        await notify(f"{name}: password cocok otomatis.")
                elif tried >= 4:
                    await notify(f"{name}: password salah 4 kali, dilewati.")
                    return []

        def keep(i):
            base = os.path.basename(i.filename)
            return (not i.filename.startswith("__MACOSX") and not base.startswith("._")
                    and base.lower().endswith(IMG_EXT + VID_EXT))

        media = sorted([i for i in infos if keep(i)], key=lambda i: _natural(i.filename))
        skipped = len(infos) - len(media)
        await asyncio.sleep(0.3)
        await _p(kind="step", label=f"Ekstrak: {name} ({len(media)} file)", frac=None)
        paths = await asyncio.to_thread(lambda: [zf.extract(i, outdir) for i in media])
    await notify(f"{name}: {len(paths)} gambar/video diproses, {skipped} file lain dilewati.")
    return paths


async def _send_paths(tgt, files, first, caps, notify=None):
    try:
        await _send(tgt, files, first, caps, False)
    except errors.FloodWaitError:
        raise
    except errors.RPCError as e:
        if notify:
            await notify(f"Album gagal sebagai foto ({type(e).__name__}), dikirim satu per satu.")
        for k, f in enumerate(files):
            cap = first if k == 0 else ""
            try:
                await _send(tgt, [f], cap, [cap], False)
            except errors.FloodWaitError:
                raise
            except errors.RPCError as e2:
                if notify:
                    await notify(f"{os.path.basename(f)}: gagal sebagai foto/video ({type(e2).__name__}), dikirim sebagai dokumen.")
                await _send(tgt, [f], cap, [cap], True)


def _rm(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


class ZipStop(Exception):
    pass


async def handle_zip(tgt, msg, caption, ask_password, notify, hint="", skip=0, on_chunk=None, should_stop=None):
    name = msg.file.name if msg.file and msg.file.name else f"{msg.id}.zip"
    tmp = tempfile.mkdtemp(prefix="tgzip_")
    try:
        await notify(f"Mengunduh zip: {name}")
        zpath = await client.download_media(
            msg, file=os.path.join(tmp, "a.zip"),
            progress_callback=lambda r, t: _fire(kind="step", label=f"Unduh zip: {name}", frac=(r / t) if t else None))
        hints = _pw_candidates(richtext.strip_tokens(hint or "") + "\n" + (msg.message or ""))
        files = await extract_zip(zpath, os.path.join(tmp, "x"), name, ask_password, notify, hints=hints)
        _rm(zpath)
        for k, p in enumerate(files):
            if p.lower().endswith(IMG_EXT):
                await _p(kind="step", label=f"Menyiapkan gambar {k + 1}/{len(files)}", frac=(k + 1) / len(files))
                files[k] = await asyncio.to_thread(_prep_image, p)
                if files[k] != p:
                    _rm(p)
        sent, pos = 0, 0
        for size in (album_sizes(len(files)) if files else []):
            chunk = files[pos:pos + size]
            pos += size
            first = caption if sent == 0 else ""
            await _send_paths(tgt, chunk, first, [first] + [""] * (len(chunk) - 1))
            sent += 1
            await asyncio.sleep(3)
        return sent
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def msg_link(src, mid):
    s = str(src)
    return f"https://t.me/c/{s[4:]}/{mid}" if s.startswith("-100") else f"pesan #{mid}"


def _size(m):
    try:
        return (m.file.size or 0) if m.file else 0
    except Exception:
        return 0


async def execute(plan, ask_text, notify, should_stop, ask_password=None, progress=None):
    _PROG["cb"] = progress

    async def prog(**kw):
        if progress:
            await progress(**kw)

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
        await prog(kind="block", idx=idx, total=total, done=idx - plan["i"],
                   run_n=plan["end"] - plan["i"], albums_n=len(albums),
                   link=msg_link(src, b["key"]), items_n=len(b["media"]),
                   bytes_n=sum(_size(m) for m in b["media"]))
        start = s["albums_done"] if idx == s["next_index"] else 0
        items_done = sum(len(a) for a in albums[:start])
        bytes_done = sum(_size(m) for a in albums[:start] for m in a)
        await prog(kind="counts", items_done=items_done, bytes_done=bytes_done)
        if not albums:
            if not b["after_media"]:
                await notify(f"Blok {idx + 1}: teks tidak menempel pada media, dilewati.")
                s = advance(path, idx, blocks)
                continue
            await client.send_message(tgt, render(b["title"]), parse_mode=richtext.QUOTE)
        zips = [m.file.name for m in b["media"] if is_archive(m) and m.file and m.file.name]
        cap = render(b["title"], zips) if (b["title"] or zips) else ""
        for ai in range(start, len(albums)):
            if should_stop():
                await notify(f"Dihentikan di blok {idx + 1}. Progres tersimpan.")
                return "berhenti"
            album = albums[ai]
            await prog(kind="album_start", i=ai + 1, n=len(albums), link=msg_link(src, album[0].id))
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
            if is_archive(fresh[0]):
                zs = s.get("zip_sent", 0) if (idx == s["next_index"] and ai == start) else 0

                async def on_chunk(k, _ai=ai):
                    save_state(path, {"next_index": idx, "albums_done": _ai, "next_key": b["key"], "zip_sent": k})

                try:
                    n = await handle_zip(tgt, fresh[0], first, ask_password, notify, hint=b["title"] or "",
                                         skip=zs, on_chunk=on_chunk, should_stop=should_stop)
                except ZipStop:
                    await notify(f"Dihentikan di tengah zip (blok {idx + 1}). Progres zip tersimpan, bisa dilanjutkan.")
                    return "berhenti"
                if n == 0 and first and zs == 0:
                    await client.send_message(tgt, first, parse_mode=richtext.MODE)
            else:
                await send_album(tgt, fresh, first, caps)
            s = {"next_index": idx, "albums_done": ai + 1, "next_key": b["key"]}
            save_state(path, s)
            items_done += len(album)
            bytes_done += sum(_size(m) for m in album)
            await prog(kind="album", i=ai + 1, n=len(albums), items_done=items_done, bytes_done=bytes_done)
            await asyncio.sleep(3)
        await client.send_file(tgt, sticker)
        await asyncio.sleep(2)
        s = advance(path, idx, blocks)
        sources.touch_copy(src)
        await notify(f"Blok {idx + 1}/{total} selesai ({len(b['media'])} media)")
        await asyncio.sleep(5)
    await notify(f"Selesai. Berikutnya: blok {s['next_index'] + 1}.")
    return "selesai"
