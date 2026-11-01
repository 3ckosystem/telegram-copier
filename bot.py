import asyncio
import os
import re
import traceback
from telethon import TelegramClient, events, Button, utils, errors
from telethon.tl.types import Chat, Channel, PeerChannel
from organizer import client
import engine
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


def main_menu():
    return [
        [Button.inline("📥 Atur Sumber", b"m:src"), Button.inline("📤 Atur Tujuan", b"m:tgt")],
        [Button.inline("📝 Template", b"m:tpl"), Button.inline("▶ Mulai Salin", b"m:copy")],
        [Button.inline("📍 Atur Posisi", b"m:pos"), Button.inline("ℹ️ Status", b"m:home")],
    ]


def stop_button():
    return [[Button.inline("⏹ Berhenti", b"run:stop")]]


async def name_of(chat_id):
    try:
        e = await client.get_entity(chat_id)
        return getattr(e, "title", None) or str(chat_id)
    except Exception:
        return str(chat_id)


def progress_line():
    src, tgt = settings.get_ids()
    s = engine.load_state(engine.state_path(src, tgt))
    return f"Progres: blok berikutnya #{s['next_index'] + 1}"


async def status_text():
    src, tgt = settings.get_ids()
    run = "\nStatus: sedang menyalin" if state["running"] else ""
    return ("Telegram Copier\n"
            f"Sumber : {await name_of(src)} ({src})\n"
            f"Tujuan : {await name_of(tgt)} ({tgt})\n"
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
        return None, "Forward ini bukan dari channel sumber yang aktif. Gunakan link postingan."
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
    try:
        blocks = await engine.get_blocks()
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


async def main():
    await client.start()
    await bot.start(bot_token=os.environ["BOT_TOKEN"])
    owner = (await client.get_me()).id

    async def run_job(msg, plan):
        log = []

        async def notify(text):
            log.append(text)
            try:
                await msg.edit("\n".join(log[-8:]), buttons=stop_button(), parse_mode=None)
            except Exception:
                pass

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

        try:
            await engine.execute(plan, ask, notify, lambda: state["stop"])
        except Exception as e:
            traceback.print_exc()
            log.append(f"Error: {type(e).__name__}: {e}. Progres tersimpan, bisa dilanjutkan.")
        finally:
            state["running"], state["stop"], state["plan"], state["fut"] = False, False, None, None
        try:
            await msg.edit("\n".join(log[-8:]) + "\n\n" + progress_line(),
                           buttons=main_menu(), parse_mode=None)
        except Exception:
            pass

    @bot.on(events.NewMessage(from_users=owner, pattern=r"^/(start|menu|status|batal)\b"))
    async def on_cmd(event):
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
        fwd = event.message.fwd_from
        if mode in ("poslink", "posnum"):
            await handle_pos(event, mode)
            return
        if mode == "tpl" and fwd is None:
            text = richtext.msg_markdown(event.message)
            if "{judul}" not in text:
                await event.reply("Template harus memuat {judul}. Kirim ulang, atau /batal.", parse_mode=None)
                return
            state["tpl"] = text
            await event.reply("Pratinjau:\n\n" + richtext.strip_tokens(text).replace("{judul}", SAMPLE),
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
                       [[Button.inline("✏️ Ubah", b"tpl:edit")], [Button.inline("« Kembali", b"m:home")]],
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
            try:
                plan = await engine.prepare(n)
            except ValueError as e:
                await show(event, str(e), main_menu(), parse_mode=None)
                return
            except Exception as e:
                await show(event, f"Gagal membaca sumber: {type(e).__name__}: {e}", main_menu(), parse_mode=None)
                return
            state["plan"] = plan
            await show(event,
                       f"Akan menyalin blok {plan['i'] + 1}-{plan['end']} dari {len(plan['blocks'])} "
                       f"({plan['n_media']} media).\n"
                       f"Sumber : {await name_of(plan['src'])}\nTujuan : {await name_of(plan['tgt'])}",
                       [[Button.inline("▶ Mulai", b"run:go"), Button.inline("✖ Batal", b"m:home")]],
                       parse_mode=None)
        elif data == "run:go":
            if state["running"] or not state["plan"]:
                await event.answer("Tidak ada rencana, atau sedang berjalan.", alert=True)
                return
            state["running"], state["stop"] = True, False
            msg = await event.get_message()
            await show(event, "Memulai penyalinan...", stop_button(), parse_mode=None)
            asyncio.create_task(run_job(msg, state["plan"]))
        elif data == "run:stop":
            state["stop"] = True
            fut = state.get("fut")
            if fut and not fut.done():
                fut.set_result("q")
            await event.answer("Akan berhenti setelah album yang sedang dikirim selesai.")
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

    print("Bot berjalan. Tekan Ctrl+C untuk berhenti.")
    await bot.run_until_disconnected()


client.loop.run_until_complete(main())
