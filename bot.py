import asyncio
import json
import logging
import os
import re
import traceback
from telethon import TelegramClient, events, Button, utils, errors
from telethon.tl.types import Chat, Channel, PeerChannel
from organizer import client, is_media
import engine
import mover
import sources
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import richtext
import settings

bot = TelegramClient(os.path.join(os.environ.get("SESSION_DIR", "."), "bot_session"), int(os.environ["API_ID"]), os.environ["API_HASH"])
state = {"mode": None, "tpl": None, "plan": None, "running": False, "stop": False, "fut": None, "pos": None}
SAMPLE = "Contoh Judul Dari Sumber"


def read_template():
    try:
        with open(settings.template_path(), encoding="utf-8") as f:
            return f.read().strip("\n")
    except FileNotFoundError:
        return "{judul}"


def write_template(text):
    settings.save_template(text)


def bar(frac, width=12):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "█" * n + "░" * (width - n) + f" {int(frac * 100)}%"


def age_text():
    a = engine.cache_info()
    if a is None or a < 60:
        return "Data sumber: baru dibaca"
    return f"Data sumber: dibaca {int(a // 60)} menit lalu (tekan 🔄 untuk membaca ulang)"


def flood_msg(e):
    until = (datetime.now(timezone.utc) + timedelta(seconds=e.seconds)).astimezone(WIB).strftime("%H:%M")
    state["flood_last"] = (time.time(), e.seconds)
    print("[flood-akun] FloodWaitError", e.seconds, "detik", flush=True)
    return f"⏳ Telegram menahan akun {max(1, e.seconds // 60)} menit (sampai sekitar {until} WIB). Coba lagi setelah itu."


def main_menu():
    return [
        [Button.inline("📥 Atur Sumber", b"sl:list"), Button.inline("📤 Atur Tujuan", b"m:tgt")],
        [Button.inline("📝 Template", b"m:tpl"), Button.inline("▶ Mulai Salin", b"m:copy")],
        [Button.inline("📍 Atur Posisi", b"m:pos"), Button.inline("ℹ️ Status", b"m:home")],
        [Button.inline("🚚 Pindahkan media (hapus asal)", b"mv:start")],
    ]


def stop_button():
    return [[Button.inline("⛔ Berhenti sekarang", b"run:kill")],
            [Button.inline("⏸ Berhenti setelah album ini", b"run:stop")]]


async def name_of(chat_id):
    if chat_id is None:
        return "belum diatur"
    try:
        e = await client.get_entity(chat_id)
        return getattr(e, "title", None) or str(chat_id)
    except Exception:
        return str(chat_id)


def progress_line():
    src, tgt = settings.get_ids()
    if not src or not tgt:
        return "Progres: - (sumber/tujuan belum diatur)"
    s = engine.load_state(engine.state_path(src, tgt))
    return f"Progres: blok berikutnya #{s['next_index'] + 1}"


async def status_text():
    src, tgt = settings.get_ids()
    run = "\nStatus: sedang menyalin" if state["running"] else ""
    return ("Telegram Copier\n"
            f"Sumber : {await name_of(src)}{f' ({src})' if src else ''}\n"
            f"Tujuan : {await name_of(tgt)}{f' ({tgt})' if tgt else ''}\n"
            f"{progress_line()}{run}")


async def set_chat(kind, gid):
    try:
        ent = await client.get_entity(gid)
    except Exception:
        return f"Akun Anda tidak bisa mengakses ID {gid}. Pastikan Anda anggota dan ID-nya benar."
    src, tgt = settings.get_ids()
    if (kind == "src" and gid == tgt) or (kind == "tgt" and gid == src):
        return "Sumber dan tujuan tidak boleh sama."
    d = settings.load()
    d["source_id" if kind == "src" else "target_id"] = gid
    settings.save(d)
    state["mode"] = None
    name = getattr(ent, "title", None) or str(gid)
    if kind == "src":
        sources.register(gid, name)
    return f"{'Sumber' if kind == 'src' else 'Tujuan'} diset: {name} ({gid})"


async def show(event, text, buttons, **kw):
    if not buttons:
        buttons = None
    try:
        await event.edit(text, buttons=buttons, **kw)
    except errors.MessageNotModifiedError:
        pass


LINK = re.compile(r"t\.me/(?:c/(\d+)|([A-Za-z][A-Za-z0-9_]{3,}))/(?:\d+/)?(\d+)")


async def source_msg_id(event):
    src, _ = settings.get_ids()
    fwd = event.message.fwd_from
    if fwd is not None:
        peer = getattr(fwd, "from_id", None)
        post = getattr(fwd, "channel_post", None)
        if isinstance(peer, PeerChannel) and post and utils.get_peer_id(peer) == src:
            return post, None
        if fwd.date:
            want = (event.message.message or "").strip()
            best = None
            async for m in client.iter_messages(src, offset_date=fwd.date + timedelta(seconds=2), limit=6):
                if abs((m.date - fwd.date).total_seconds()) > 2:
                    continue
                if want and (m.message or "").strip() == want:
                    return m.id, None
                if not want and best is None:
                    best = m
            if best is not None:
                return best.id, None
        return None, "Forward ini tidak bisa dicocokkan dengan postingan di sumber yang aktif. Gunakan link postingan."
    m = LINK.search(event.raw_text or "")
    if not m:
        return None, "Link tidak dikenali. Contoh: https://t.me/c/1575797688/123"
    msg_id = int(m.group(3))
    try:
        if m.group(1):
            chat = int(f"-100{m.group(1)}")
        else:
            chat = utils.get_peer_id(await client.get_input_entity(m.group(2)))
    except Exception:
        return None, "Akun tidak bisa membuka chat dari link itu."
    if chat != src:
        return None, "Link itu dari chat lain, bukan sumber yang aktif."
    return msg_id, None


async def handle_pos(event, mode):
    src, tgt = settings.get_ids()
    if not src or not tgt:
        await event.reply("Atur sumber dan tujuan dulu lewat menu.", parse_mode=None)
        return
    number, msg_id = None, None
    if mode == "posnum":
        t = (event.raw_text or "").strip()
        if not t.isdigit() or int(t) < 1:
            await event.reply("Kirim nomor blok berupa angka, mulai dari 1.", parse_mode=None)
            return
        number = int(t)
    else:
        msg_id, err = await source_msg_id(event)
        if err:
            await event.reply(err, parse_mode=None)
            return
    wait = await event.reply("Membaca riwayat sumber... (bisa beberapa menit)", parse_mode=None)
    sc = {"t": 0.0}

    async def scan_prog(**kw):
        if time.time() - sc["t"] < 2.5:
            return
        sc["t"] = time.time()
        fr = kw["scanned"] / kw["total"] if kw.get("total") else None
        try:
            await wait.edit("Membaca riwayat sumber... " + (bar(fr) if fr is not None else f"{kw['scanned']} pesan"),
                            parse_mode=None)
        except Exception:
            pass

    try:
        blocks = await engine.get_blocks(scan_prog)
        if blocks and ((msg_id is not None and msg_id > engine.last_id(blocks))
                       or (number is not None and number > len(blocks))):
            blocks = await engine.get_blocks(scan_prog, force=True)
    except errors.FloodWaitError as e:
        await wait.edit(flood_msg(e), parse_mode=None)
        return
    except Exception as e:
        await wait.edit(f"Gagal membaca sumber: {type(e).__name__}: {e}", parse_mode=None)
        return
    if msg_id is not None:
        idx = engine.locate(blocks, msg_id)
    else:
        if number > len(blocks):
            await wait.edit(f"Nomor terlalu besar. Total blok: {len(blocks)}.", parse_mode=None)
            return
        idx = number - 1
    b = blocks[idx]
    title = richtext.strip_tokens(b["title"])[:80].replace("\n", " ") if b["title"] else "(tanpa judul)"
    state["pos"] = {"idx": idx, "key": b["key"], "src": src, "tgt": tgt}
    state["mode"] = None
    await wait.edit(
        f"Posisi akan diset ke blok {idx + 1} dari {len(blocks)}:\n{title} ({len(b['media'])} media)\n"
        "Blok sebelumnya dianggap sudah disalin.",
        buttons=[[Button.inline("✅ Set posisi", b"pos:ok"), Button.inline("✖ Batal", b"m:home")]],
        parse_mode=None)


async def handle_pos_cb(event, data):
    if state["running"]:
        await event.answer("Sedang menyalin. Hentikan dulu sebelum mengubah posisi.", alert=True)
        return
    src, tgt = settings.get_ids()
    cancel = [[Button.inline("✖ Batal", b"m:home")]]
    if data == "m:pos":
        state["mode"] = None
        rows = [[Button.inline("🔗 Dari link / forward postingan", b"pos:link")],
                [Button.inline("🔢 Dari nomor blok", b"pos:num")],
                [Button.inline("⏮ Mulai dari awal (reset)", b"pos:reset")],
                [Button.inline("« Kembali", b"m:home")]]
        await show(event, progress_line() + "\nAtur dari mana penyalinan dimulai:", rows, parse_mode=None)
    elif data == "pos:link":
        state["mode"] = "poslink"
        await show(event, "Kirim link postingan di channel sumber (tekan lama pesan, lalu Salin Tautan), "
                          "atau forward postingannya ke sini.", cancel, parse_mode=None)
    elif data == "pos:num":
        state["mode"] = "posnum"
        await show(event, "Kirim nomor blok (angka, mulai dari 1).", cancel, parse_mode=None)
    elif data == "pos:reset":
        await show(event, "Mulai dari blok 1? Jika tujuan sudah berisi hasil sebelumnya, "
                          "isinya akan terduplikasi.",
                   [[Button.inline("Ya, reset", b"pos:reset_ok"), Button.inline("✖ Batal", b"m:home")]],
                   parse_mode=None)
    elif data == "pos:reset_ok":
        try:
            os.remove(engine.state_path(src, tgt))
        except FileNotFoundError:
            pass
        await show(event, "Progres direset.\n" + progress_line(), main_menu(), parse_mode=None)
    elif data == "pos:ok":
        p = state.get("pos")
        if not p:
            await show(event, "Tidak ada posisi yang menunggu konfirmasi.", main_menu(), parse_mode=None)
            return
        engine.save_state(engine.state_path(p["src"], p["tgt"]),
                          {"next_index": p["idx"], "albums_done": 0, "next_key": p["key"]})
        state["pos"] = None
        await show(event, "Posisi diset.\n" + progress_line(), main_menu(), parse_mode=None)


WIB = ZoneInfo("Asia/Jakarta")
_media_cache = {}


def fmt_dt(dt):
    if dt is None:
        return "-"
    return dt.astimezone(WIB).strftime("%d %b %H:%M")


async def last_media_date(src_id):
    c = _media_cache.get(src_id)
    if c and time.time() - c[0] < 600:
        return c[1]
    try:
        d = None
        async for m in client.iter_messages(src_id, limit=60):
            if is_media(m):
                d = m.date
                break
    except Exception:
        d = "ERR"
    _media_cache[src_id] = (time.time(), d)
    return d


async def source_screen(hide_mode):
    sources.seed()
    cur = settings.get_ids()[0]
    if cur:
        sources.ensure(cur)
    items = sources.entries(False)[:12]
    for sid, e in items:
        if not e.get("name"):
            e["name"] = await name_of(sid)
            sources.set_name(sid, e["name"])
    dates = await asyncio.gather(*[last_media_date(sid) for sid, _ in items])
    if not items:
        head = "Belum ada sumber. Tekan Tambah sumber."
    elif hide_mode:
        head = "Pilih sumber yang mau disembunyikan dari daftar:"
    else:
        head = "Pilih sumber:"
    lines = [head]
    for n, ((sid, e), md) in enumerate(zip(items, dates), 1):
        mark = "✅ " if sid == cur else ""
        mtxt = "tidak bisa diakses" if md == "ERR" else fmt_dt(md)
        lc = e.get("last_copy")
        ctxt = fmt_dt(datetime.fromtimestamp(lc, timezone.utc)) if lc else "belum pernah"
        lines.append(f"{n}. {mark}{e['name']}\n   media terakhir: {mtxt} | copy terakhir: {ctxt}")
    rows = []
    if hide_mode:
        for n, (sid, e) in enumerate(items, 1):
            rows.append([Button.inline(f"🙈 {n} · {e['name'][:24]}", f"sl:hide:{sid}".encode())])
        rows.append([Button.inline(f"♻️ Pulihkan semua ({sources.hidden_count()})", b"sl:unhide"),
                     Button.inline("✔ Selesai", b"sl:list")])
    else:
        pair = []
        for n, (sid, e) in enumerate(items, 1):
            pair.append(Button.inline(f"{n} · {e['name'][:18]}", f"sl:use:{sid}".encode()))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append([Button.inline("➕ Tambah sumber", b"sl:add"),
                     Button.inline("🙈 Sembunyikan", b"sl:hidemode")])
        rows.append([Button.inline("« Kembali", b"m:home")])
    return "\n".join(lines), rows


async def handle_sl(event, data):
    parts = data.split(":")
    act = parts[1]
    if state["running"] and act in ("use", "add"):
        await event.answer("Sedang menyalin. Hentikan dulu sebelum mengganti sumber.", alert=True)
        return
    if act in ("list", "hidemode", "hide", "unhide"):
        if act == "hide":
            sources.set_hidden(int(parts[2]), True)
        elif act == "unhide":
            sources.unhide_all()
        await show(event, "Memuat daftar sumber...", None, parse_mode=None)
        text, rows = await source_screen(act in ("hidemode", "hide", "unhide"))
        await show(event, text, rows, parse_mode=None)
    elif act == "add":
        state["mode"] = "src"
        await show(event, "Tambah SUMBER:\nforward satu pesan dari channel, atau kirim angka ID.\n"
                          "Cari ID: /cariid kata",
                   [[Button.inline("✖ Batal", b"sl:list")]], parse_mode=None)
    elif act == "use":
        msg = await set_chat("src", int(parts[2]))
        await show(event, msg, main_menu(), parse_mode=None)


async def main():
    await client.start()
    await bot.start(bot_token=os.environ["BOT_TOKEN"])
    owner = (await client.get_me()).id

    _orig_edit = bot.edit_message
    _fb = {}

    async def _safe_edit(entity, message=None, text=None, **kw):
        if time.time() >= state.get("edit_until", 0):
            try:
                return await _orig_edit(entity, message, text, **kw)
            except errors.FloodWaitError as e:
                state["edit_until"] = time.time() + e.seconds + 5
                print("[flood] edit diblokir Telegram selama", e.seconds, "detik", flush=True)
                state["flood_last"] = (time.time(), e.seconds)
                try:
                    os.makedirs("data", exist_ok=True)
                    with open("data/flood.json", "w") as f:
                        json.dump({"edit_until": state["edit_until"]}, f)
                    until = (datetime.now(timezone.utc) + timedelta(seconds=e.seconds)).astimezone(WIB).strftime("%H:%M")
                    await bot.send_message(entity, f"⏳ Telegram membatasi edit pesan oleh bot selama {e.seconds // 60} menit (sampai sekitar {until} WIB). Bot tetap bekerja dengan pesan baru.", parse_mode=None)
                except Exception:
                    pass
        key = (str(entity), str(message))
        if time.time() - _fb.get(key, 0) < 30:
            return None
        _fb[key] = time.time()
        kw2 = {k: v for k, v in kw.items() if k in ("buttons", "parse_mode", "link_preview")}
        return await bot.send_message(entity, text or "", **kw2)

    bot.edit_message = _safe_edit

    try:
        with open("data/flood.json") as f:
            state["edit_until"] = float(json.load(f).get("edit_until", 0))
    except Exception:
        pass

    class _FloodNotifier(logging.Handler):
        def emit(self, record):
            try:
                msg = record.getMessage()
                mm = re.search(r"Sleeping for (\d+)s", msg)
                if not mm or "flood wait" not in msg.lower():
                    return
                secs = int(mm.group(1))
                print("[flood-akun]", msg, flush=True)
                state["flood_last"] = (time.time(), secs)
                if time.time() - state.get("flood_note_t", 0) < 60:
                    return
                state["flood_note_t"] = time.time()
                until = (datetime.now(timezone.utc) + timedelta(seconds=secs)).astimezone(WIB).strftime("%H:%M")
                dur = f"{secs} detik" if secs < 120 else f"{secs // 60} menit (sampai sekitar {until} WIB)"
                asyncio.get_event_loop().create_task(
                    bot.send_message(owner, f"⏳ Telegram menahan akun selama {dur}. Proses lanjut otomatis sesudahnya.", parse_mode=None))
            except Exception:
                pass

    logging.getLogger("telethon").setLevel(logging.INFO)
    logging.getLogger("telethon").addHandler(_FloodNotifier())
    _h = logging.StreamHandler()
    _h.setLevel(logging.WARNING)
    logging.getLogger("telethon").addHandler(_h)

    async def run_job(msg, plan):
        log = []

        view = {"block": None, "album": None, "step": None, "t": 0.0}

        def compose():
            lines = []
            bk = view["block"]
            if bk:
                lines.append(f"Blok {bk['idx'] + 1}/{bk['total']}  ·  sesi: {bk['done']}/{bk['run_n']} blok")
                if bk.get("link"):
                    lines.append(f"Sumber: {bk['link']}")
                d = view.get("done_counts", (0, 0))
                lines.append(f"Item   {d[0]}/{bk.get('items_n', 0)} file · {d[1] / 1048576:.0f}/{bk.get('bytes_n', 0) / 1048576:.0f} MB")
                if view.get("cur"):
                    lines.append(f"Sedang: {view['cur']}")
                lines.append("Total  " + bar(bk["done"] / max(1, bk["run_n"])))
                if view["album"]:
                    a = view["album"]
                    lines.append(f"Album  {bar(a[0] / max(1, a[1]))} ({a[0]}/{a[1]})")
                if view["step"]:
                    st = view["step"]
                    lines.append(st[0] + (f"  {bar(st[1])}" if st[1] is not None else " ..."))
                lines.append("")
            if state.get("stop"):
                lines.append("⏸ Berhenti diminta: menunggu bagian yang sedang berjalan selesai (atau tekan ⛔).")
            lines += log[-6:]
            return "\n".join(lines)

        def _compact():
            bk = view["block"]
            if not bk:
                return None
            a = view["album"] or (0, 0)
            d = view.get("done_counts", (0, 0))
            s = (f"▶ Blok {bk['idx'] + 1}/{bk['total']} · album {a[0]}/{a[1]} · {d[0]}/{bk.get('items_n', 0)} file · "
                 f"{d[1] / 1048576:.0f}/{bk.get('bytes_n', 0) / 1048576:.0f} MB")
            if view.get("step"):
                s += " · " + view["step"][0]
            return s

        async def _note(text):
            try:
                await bot.send_message(owner, text, parse_mode=None)
            except Exception:
                pass

        async def refresh(force=False):
            if view.get("done"):
                return
            view["dirty"] = True

        state["refresh"] = refresh
        async def notify(text):
            print("[status]", text, flush=True)
            log.append(text)
            if time.time() < state.get("edit_until", 0):
                asyncio.create_task(_note(text))
            await refresh(True)

        async def progress(**kw):
            k = kw.get("kind")
            if k == "block":
                view["block"] = kw
                view["album"] = (0, kw["albums_n"]) if kw["albums_n"] else None
                view["step"] = None
                view["cur"] = None
                view["done_counts"] = (0, 0)
            elif k == "counts":
                view["done_counts"] = (kw["items_done"], kw["bytes_done"])
            elif k == "album_start":
                view["cur"] = kw["link"]
                view["step"] = None
            elif k == "album":
                view["album"] = (kw["i"], kw["n"])
                view["done_counts"] = (kw["items_done"], kw["bytes_done"])
                view["step"] = None
            elif k == "step":
                view["step"] = (kw["label"], kw.get("frac"))
            await refresh(k in ("block", "album", "album_start"))

        async def ask(idx, b, raw, rendered):
            state["fut"] = asyncio.get_running_loop().create_future()
            await bot.send_message(
                owner,
                f"Blok {idx + 1}: teks murni tepat setelah media:\n\n{raw[:1500]}\n\n"
                f"Hasil dengan template:\n\n{rendered[:1500]}",
                buttons=[[Button.inline("✅ Kirim", b"tx:y"), Button.inline("⏭ Lewati", b"tx:n"),
                          Button.inline("⏹ Berhenti", b"tx:q")]],
                parse_mode=None)
            ans = await state["fut"]
            state["fut"] = None
            return ans

        async def ask_pw(name, tried):
            state["pw_fut"] = asyncio.get_running_loop().create_future()
            salah = " (salah, coba lagi)" if tried else ""
            await bot.send_message(
                owner, f"File zip \"{name}\" butuh password{salah}.\nKetik password di chat ini.",
                buttons=[[Button.inline("⏭ Lewati zip ini", b"pw:skip")]], parse_mode=None)
            pw = await state["pw_fut"]
            state["pw_fut"] = None
            return pw

        stop_tick = {"v": False}

        async def ticker():
            while not stop_tick["v"]:
                await asyncio.sleep(10)
                if stop_tick["v"] or view.get("done"):
                    break
                text = compose()
                if text == view.get("last"):
                    continue
                if time.time() < state.get("edit_until", 0):
                    if time.time() - view.get("sent_new", 0) >= 120:
                        view["sent_new"] = time.time()
                        view["last"] = text
                        try:
                            await bot.send_message(owner, _compact() or text, buttons=stop_button(), parse_mode=None)
                        except Exception:
                            pass
                    continue
                view["last"] = text
                try:
                    await msg.edit(text, buttons=stop_button(), parse_mode=None)
                except Exception:
                    pass

        asyncio.create_task(ticker())
        try:
            await engine.execute(plan, ask, notify, lambda: state["stop"], ask_password=ask_pw, progress=progress)
        except asyncio.CancelledError:
            log.append("Dihentikan paksa. Progres tersimpan, bisa dilanjutkan.")
        except errors.FloodWaitError as e:
            until = (datetime.now(timezone.utc) + timedelta(seconds=e.seconds)).astimezone(WIB).strftime("%H:%M")
            log.append(f"⏳ Telegram menahan akun {e.seconds // 60} menit (sampai sekitar {until} WIB). Progres tersimpan, lanjutkan setelah itu.")
            state["flood_last"] = (time.time(), e.seconds)
        except Exception as e:
            traceback.print_exc()
            log.append(f"Error: {type(e).__name__}: {e}. Progres tersimpan, bisa dilanjutkan.")
        finally:
            stop_tick["v"] = True
            view["done"] = True
            state["running"], state["stop"], state["plan"], state["fut"] = False, False, None, None
        try:
            await msg.edit("\n".join(log[-8:]) + "\n\n" + progress_line(),
                           buttons=main_menu(), parse_mode=None)
        except Exception:
            pass

    @bot.on(events.NewMessage(from_users=owner, pattern=r"^/(start|menu|status|batal)\b"))
    async def on_cmd(event):
        print("[cmd]", (event.raw_text or "")[:30], flush=True)
        state["mode"] = None
        await event.reply(await status_text(), buttons=main_menu(), parse_mode=None)

    @bot.on(events.NewMessage(from_users=owner, pattern=r"^/cariid\b"))
    async def on_cariid(event):
        q = event.raw_text.partition(" ")[2].strip().lower()
        lines = []
        async for d in client.iter_dialogs():
            if isinstance(d.entity, (Chat, Channel)) and (not q or q in (d.name or "").lower()):
                lines.append(f"{d.id}  {d.name}")
            if len(lines) >= 30:
                break
        await event.reply("\n".join(lines) or "Tidak ada yang cocok.", parse_mode=None)

    @bot.on(events.NewMessage(from_users=owner, func=lambda e: not (e.raw_text or "").startswith("/")))
    async def on_msg(event):
        mode = state["mode"]
        if mode == "mv":
            return
        fwd = event.message.fwd_from
        pf = state.get("pw_fut")
        if pf is not None and not pf.done() and fwd is None and event.raw_text:
            pf.set_result(event.raw_text.strip())
            try:
                await event.delete()
            except Exception:
                pass
            return
        if mode in ("poslink", "posnum"):
            await handle_pos(event, mode)
            return
        if mode == "tpld" and fwd is None:
            text = richtext.msg_markdown(event.message)
            state["tpl_d"] = text
            await event.reply("Pratinjau template default:\n\n" + richtext.strip_tokens(text).replace("{zip}", "nama-arsip.zip"),
                              buttons=[[Button.inline("✅ Simpan", b"tpd:save"), Button.inline("✖ Batal", b"m:home")]])
            return
        if mode == "tpl" and fwd is None:
            text = richtext.msg_markdown(event.message)
            warn = "" if "{judul}" in text else "\n\n⚠ Template ini tidak memuat {judul}, jadi judul asli dari sumber tidak akan ditampilkan."
            state["tpl"] = text
            await event.reply("Pratinjau:\n\n" + richtext.strip_tokens(text).replace("{judul}", SAMPLE) + warn,
                              buttons=[[Button.inline("✅ Simpan", b"tpl:save"),
                                        Button.inline("✖ Batal", b"m:home")]])
            return
        gid = None
        if fwd is not None:
            peer = getattr(fwd, "from_id", None)
            if isinstance(peer, PeerChannel):
                gid = utils.get_peer_id(peer)
            else:
                await event.reply(
                    "Asal pesan tidak terdeteksi sebagai channel (pesan dari grup hanya membawa nama pengirim).\n"
                    "Kirim ID grup/channel-nya langsung. Untuk mencari ID: /cariid kata",
                    parse_mode=None)
                return
        else:
            m = re.fullmatch(r"-?\d{5,}", event.raw_text.strip())
            if m:
                gid = int(m.group(0))
                if gid > 0:
                    gid = int(f"-100{m.group(0)}")
        if gid is None:
            await event.reply("Gunakan menu di bawah.", buttons=main_menu())
            return
        if mode in ("src", "tgt"):
            await event.reply(await set_chat(mode, gid), buttons=main_menu(), parse_mode=None)
        else:
            await event.reply(f"ID terdeteksi: {gid}\nJadikan apa?", parse_mode=None,
                              buttons=[[Button.inline("Jadikan SUMBER", f"src:{gid}".encode()),
                                        Button.inline("Jadikan TUJUAN", f"tgt:{gid}".encode())]])

    @bot.on(events.CallbackQuery(func=lambda e: e.sender_id == owner))
    async def on_cb(event):
        data = event.data.decode()
        if data == "m:home":
            state["mode"] = None
            await show(event, await status_text(), main_menu(), parse_mode=None)
        elif data in ("m:src", "m:tgt"):
            state["mode"] = data[2:]
            label = "SUMBER" if data == "m:src" else "TUJUAN"
            await show(event,
                       f"Atur {label}:\nforward satu pesan dari channel, atau kirim angka ID.\n"
                       "Cari ID: /cariid kata",
                       [[Button.inline("✖ Batal", b"m:home")]], parse_mode=None)
        elif data == "m:tpl":
            state["mode"] = None
            await show(event, "Template saat ini:\n\n" + richtext.strip_tokens(read_template()),
                       [[Button.inline("✏️ Ubah", b"tpl:edit")], [Button.inline("🧩 Template default (tanpa caption)", b"tpd:view")], [Button.inline("« Kembali", b"m:home")]],
                       parse_mode=None)
        elif data == "tpl:edit":
            state["mode"] = "tpl"
            await show(event,
                       "Kirim teks template baru. Wajib memuat {judul} (diganti judul asli dari sumber).",
                       [[Button.inline("✖ Batal", b"m:home")]], parse_mode=None)
        elif data == "tpl:save":
            if not state["tpl"]:
                await show(event, "Tidak ada template yang menunggu disimpan.", main_menu(), parse_mode=None)
                return
            write_template(state["tpl"])
            state["tpl"], state["mode"] = None, None
            await show(event, "Template disimpan.", main_menu(), parse_mode=None)
        elif data == "run:kill":
            tk = state.get("task")
            state["stop"] = True
            pf = state.get("pw_fut")
            if pf is not None and not pf.done():
                pf.set_result(None)
            if tk is not None and not tk.done():
                tk.cancel()
                await event.answer("Menghentikan sekarang...")
            else:
                await event.answer("Tidak ada proses yang berjalan.")
        elif data == "tpd:view":
            state["mode"] = None
            cur = richtext.strip_tokens(settings.load_default_template() or "") or "(belum diatur: dipakai template utama tanpa judul)"
            await show(event, "Template default (untuk blok tanpa caption):\n\n" + cur,
                       [[Button.inline("✏️ Ubah default", b"tpd:edit")], [Button.inline("« Kembali", b"m:tpl")]],
                       parse_mode=None)
        elif data == "tpd:edit":
            state["mode"] = "tpld"
            await show(event, "Kirim teks template default. Dipakai untuk blok yang tidak punya judul atau caption. "
                              "Tidak perlu {judul}. Boleh memuat {zip} untuk nama arsip.",
                       [[Button.inline("✖ Batal", b"m:home")]], parse_mode=None)
        elif data == "tpd:save":
            if not state.get("tpl_d"):
                await show(event, "Tidak ada template yang menunggu disimpan.", main_menu(), parse_mode=None)
                return
            settings.save_default_template(state["tpl_d"])
            state["tpl_d"], state["mode"] = None, None
            await show(event, "Template default disimpan.", main_menu(), parse_mode=None)
        elif data == "rf:go":
            if state["running"]:
                await event.answer("Sedang berjalan.", alert=True)
                return
            engine.invalidate()
            state["plan"] = None
            rows = [[Button.inline("1 blok", b"n:1"), Button.inline("5 blok", b"n:5"), Button.inline("10 blok", b"n:10")],
                    [Button.inline("25 blok", b"n:25"), Button.inline("50 blok", b"n:50"), Button.inline("Semua sisa", b"n:0")],
                    [Button.inline("« Kembali", b"m:home")]]
            await show(event, "Data sumber dibuang. Pilih jumlah blok; sumber akan dibaca ulang.", rows, parse_mode=None)
        elif data.startswith("mv:"):
            return
        elif data == "pw:skip":
            pf = state.get("pw_fut")
            if pf is not None and not pf.done():
                pf.set_result(None)
            await show(event, "Zip dilewati.", None, parse_mode=None)
        elif data.startswith("sl:"):
            await handle_sl(event, data)
        elif data == "m:pos" or data.startswith("pos:"):
            await handle_pos_cb(event, data)
        elif data == "m:copy":
            if state["running"]:
                await show(event, "Sedang menyalin. Tekan Berhenti untuk menghentikan.", stop_button(), parse_mode=None)
                return
            rows = [[Button.inline("1 blok", b"n:1"), Button.inline("5 blok", b"n:5"),
                     Button.inline("10 blok", b"n:10")],
                    [Button.inline("25 blok", b"n:25"), Button.inline("50 blok", b"n:50"),
                     Button.inline("Semua sisa", b"n:0")],
                    [Button.inline("« Kembali", b"m:home")]]
            await show(event, "Berapa blok yang disalin sekarang?", rows, parse_mode=None)
        elif data.startswith("n:"):
            if state["running"]:
                await event.answer("Sedang berjalan.", alert=True)
                return
            n = int(data[2:]) or 10 ** 9
            await show(event, "Membaca riwayat sumber... (bisa memakan waktu beberapa menit)", [], parse_mode=None)
            sc = {"t": 0.0}

            async def scan_prog(**kw):
                if time.time() - sc["t"] < 2.5:
                    return
                sc["t"] = time.time()
                fr = kw["scanned"] / kw["total"] if kw.get("total") else None
                await show(event, "Membaca riwayat sumber... " + (bar(fr) if fr is not None else f"{kw['scanned']} pesan"),
                           None, parse_mode=None)

            try:
                plan = await engine.prepare(n, scan_prog)
            except ValueError as e:
                await show(event, str(e), main_menu(), parse_mode=None)
                return
            except errors.FloodWaitError as e:
                await show(event, flood_msg(e), main_menu(), parse_mode=None)
                return
            except Exception as e:
                await show(event, f"Gagal membaca sumber: {type(e).__name__}: {e}", main_menu(), parse_mode=None)
                return
            state["plan"] = plan
            await show(event,
                       f"Akan menyalin blok {plan['i'] + 1}-{plan['end']} dari {len(plan['blocks'])} "
                       f"({plan['n_media']} media).\n"
                       f"Sumber : {await name_of(plan['src'])}\nTujuan : {await name_of(plan['tgt'])}\n{age_text()}",
                       [[Button.inline("▶ Mulai", b"run:go"), Button.inline("✖ Batal", b"m:home")],
                        [Button.inline("🔄 Baca ulang sumber", b"rf:go")]],
                       parse_mode=None)
        elif data == "run:go":
            if state["running"] or not state["plan"]:
                await event.answer("Tidak ada rencana, atau sedang berjalan.", alert=True)
                return
            state["running"], state["stop"] = True, False
            msg = await event.get_message()
            await show(event, "Memulai penyalinan...", stop_button(), parse_mode=None)
            state["task"] = asyncio.create_task(run_job(msg, state["plan"]))
        elif data == "run:stop":
            state["stop"] = True
            fut = state.get("fut")
            if fut and not fut.done():
                fut.set_result("q")
            pf = state.get("pw_fut")
            if pf is not None and not pf.done():
                pf.set_result(None)
            await event.answer("Akan berhenti setelah album yang sedang dikirim selesai.")
            rf = state.get("refresh")
            if rf:
                await rf(True)
        elif data.startswith("tx:"):
            fut = state.get("fut")
            if fut and not fut.done():
                fut.set_result(data[3:])
            label = {"y": "Dikirim.", "n": "Dilewati.", "q": "Berhenti."}.get(data[3:], "OK.")
            await show(event, label, [], parse_mode=None)
        else:
            kind, _, gid = data.partition(":")
            msg = await set_chat(kind, int(gid))
            await show(event, msg, main_menu(), parse_mode=None)

    @bot.on(events.NewMessage(from_users=owner, pattern=r"^/limit\b"))
    async def on_limit(event):
        now = time.time()
        eu = state.get("edit_until", 0)
        lines = []
        if eu > now:
            end = datetime.fromtimestamp(eu, timezone.utc).astimezone(WIB).strftime("%d %b %H:%M")
            lines.append(f"Edit pesan oleh bot: DIBLOKIR sampai {end} WIB ({int((eu - now) // 60)} menit lagi). Bot memakai pesan baru.")
        else:
            lines.append("Edit pesan oleh bot: normal.")
        fl = state.get("flood_last")
        if fl:
            when = datetime.fromtimestamp(fl[0], timezone.utc).astimezone(WIB).strftime("%d %b %H:%M")
            lines.append(f"Penahanan terakhir: {fl[1]} detik, pada {when} WIB.")
        else:
            lines.append("Akun (pengiriman): belum ada penahanan sejak bot menyala.")
        await event.reply("\n".join(lines), parse_mode=None)

    @bot.on(events.NewMessage(from_users=owner, pattern=r"^/tes\b"))
    async def on_tes(event):
        try:
            await client.send_message("me", engine.render("Contoh Judul Dari Sumber", ["contoh.zip"]), parse_mode=richtext.MODE)
            await client.send_message("me", engine.render_default(["contoh.zip"]), parse_mode=richtext.MODE)
            await event.reply("Terkirim ke Pesan Tersimpan: (1) template utama, (2) template default. "
                              "Cek apakah emoji premium tampil di sana.", parse_mode=None)
        except Exception as e:
            await event.reply(f"Gagal: {type(e).__name__}: {e}", parse_mode=None)

    mover.register(bot, client, owner, state, show, main_menu)
    print("Bot berjalan. Tekan Ctrl+C untuk berhenti.")
    await bot.run_until_disconnected()


client.loop.run_until_complete(main())
