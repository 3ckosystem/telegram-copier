import asyncio
import copy
import json
import os
import re
import time

from telethon import Button, errors, events, types

from mover import _bar, _chat_from

LOG = "data/replace_log.json"
CAP_LIMIT = 2048
SKIP = tuple(getattr(types, n) for n in (
    "MessageEntityUrl", "MessageEntityEmail", "MessageEntityMention", "MessageEntityHashtag",
    "MessageEntityCashtag", "MessageEntityBotCommand", "MessageEntityCustomEmoji",
    "MessageEntityPhone", "MessageEntityMentionName") if hasattr(types, n))
END_RE = re.compile(r"akhir|terakhir|selesai|end|semua", re.I)
RP = {"step": None, "chat": None, "name": "", "old": "", "new": "", "whole": True, "case": False,
      "posts": None, "lo": 1, "hi": 1, "items": [], "task": None, "stop": False, "running": False}


def _u16(s, i):
    return len(s[:i].encode("utf-16-le")) // 2


def _rx(old, whole, case):
    p = re.escape(old)
    if whole:
        p = r"(?<!\w)" + p + r"(?!\w)"
    return re.compile(p, 0 if case else re.IGNORECASE)


def _fit(found, new):
    if len(found) > 1 and found.isupper():
        return new.upper()
    if found[:1].isupper():
        return new[:1].upper() + new[1:]
    return new


def _apply(msg, rx, new, case):
    text = msg.message or ""
    ents = list(msg.entities or [])
    blocked = [(e.offset, e.offset + e.length) for e in ents if isinstance(e, SKIP)]
    reps = []
    for m in rx.finditer(text):
        a, b = _u16(text, m.start()), _u16(text, m.end())
        if any(a < y and b > x for x, y in blocked):
            continue
        reps.append((m.start(), m.end(), a, b, new if case else _fit(m.group(0), new)))
    if not reps:
        return None
    out, last = [], 0
    for s, e, a, b, rep in reps:
        out.append(text[last:s])
        out.append(rep)
        last = e
    out.append(text[last:])

    def mp(p):
        d = 0
        for s, e, a, b, rep in reps:
            rl = _u16(rep, len(rep))
            if b <= p:
                d += rl - (b - a)
            elif a < p:
                return a + d + min(p - a, rl)
        return p + d

    new_ents = []
    for e in ents:
        n = copy.copy(e)
        s0, e0 = mp(e.offset), mp(e.offset + e.length)
        n.offset, n.length = s0, e0 - s0
        if n.length > 0:
            new_ents.append(n)
    return "".join(out), new_ents, reps


def _parse_range(text, posts):
    total, t = len(posts), text.strip()
    links = re.findall(r"t\.me/\S+", t)
    end_word = bool(END_RE.search(t))
    if links:
        ids = [int(re.findall(r"/(\d+)", x)[-1]) for x in links]
        lo = next((i + 1 for i, m in enumerate(posts) if m.id >= ids[0]), None)
        if lo is None:
            return "Link awal berada setelah postingan terakhir."
        if len(ids) >= 2:
            hi = max((i + 1 for i, m in enumerate(posts) if m.id <= ids[1]), default=None)
            if hi is None:
                return "Link akhir berada sebelum postingan pertama."
        elif end_word:
            hi = total
        else:
            return "Kirim dua link (awal dan akhir), atau satu link + kata 'akhir'."
    else:
        nums = [int(x) for x in re.findall(r"\d+", t)]
        if end_word:
            lo, hi = (nums[0] if nums else 1), total
        elif len(nums) >= 2:
            lo, hi = nums[0], nums[1]
        else:
            return "Tulis dua angka (contoh: 5 sampai 20), atau '5 sampai akhir', atau 'semua'."
    hi = min(hi, total)
    if lo < 1 or lo > hi:
        return f"Rentang tidak valid. Total postingan: {total}."
    return lo, hi


async def _collect(client, ent, progress):
    for kw in ({"from_user": "me"}, {}):
        posts, n = [], 0
        try:
            async for m in client.iter_messages(ent, reverse=True, **kw):
                n += 1
                if n % 200 == 0:
                    await progress(n)
                if m.out and (m.message or "").strip():
                    posts.append(m)
        except Exception as e:
            print("[ganti] scan", kw, type(e).__name__, e, flush=True)
            posts = []
        if posts:
            return posts
    return []


def _snip(s, n=45):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def _preview():
    rx = _rx(RP["old"], RP["whole"], RP["case"])
    posts, lo, hi = RP["posts"], RP["lo"], RP["hi"]
    items = []
    for i in range(lo, hi + 1):
        r = _apply(posts[i - 1], rx, RP["new"], RP["case"])
        if r:
            items.append((i, posts[i - 1], r))
    RP["items"] = items
    long_n = sum(1 for _, _, r in items if len(r[0]) > CAP_LIMIT)
    reps_n = sum(len(r[2]) for _, _, r in items)
    mode = "kata utuh" if RP["whole"] else "termasuk bagian kata"
    cs = "dibedakan" if RP["case"] else "diabaikan"
    L = [f"Grup: {RP['name']}",
         f"Ganti: \"{RP['old']}\" → \"{RP['new']}\"",
         f"Mode: {mode}, huruf besar-kecil {cs}",
         f"Rentang: postingan {lo} sampai {hi} (dari {len(posts)})",
         f"  awal #{lo}: {_snip(posts[lo - 1].message)}",
         f"  akhir #{hi}: {_snip(posts[hi - 1].message)}",
         f"Caption yang akan berubah: {len(items)} ({reps_n} penggantian)"]
    if long_n:
        L.append(f"Terlalu panjang (dilewati): {long_n}")
    for i, m, (nt, _, reps) in items[:3]:
        s, e, _, _, rep = reps[0]
        a = max(0, s - 25)
        L.append(f"\n#{i} sebelum: …{(m.message or '')[a:e + 25]}…")
        L.append(f"#{i} sesudah: …{nt[a:s + len(rep) + 25]}…")
    if items:
        L.append("\nKetik GANTI (huruf besar) untuk menjalankan.")
    else:
        L.append("\nTidak ada yang cocok. Ubah mode di bawah, atau Batal.")
    btn = [[Button.inline("🔤 Mode: " + mode, b"rp:whole")],
           [Button.inline("Aa Huruf besar-kecil: " + cs, b"rp:case")],
           [Button.inline("✖ Batal", b"rp:cancel")]]
    return "\n".join(L), btn


def _ents_from(ds):
    out = []
    for d in ds:
        d = dict(d)
        out.append(getattr(types, d.pop("_"))(**d))
    return out


def _save_log(chat, name, old, new, ops):
    os.makedirs("data", exist_ok=True)
    tmp = LOG + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"chat": chat, "name": name, "old": old, "new": new, "time": time.time(), "ops": ops}, f)
    os.replace(tmp, LOG)


def _load_log():
    try:
        with open(LOG) as f:
            return json.load(f)
    except Exception:
        return None


async def _edit(client, chat, mid, text, ents):
    for _ in range(3):
        try:
            await client.edit_message(chat, mid, text, formatting_entities=ents, link_preview=False)
            return
        except errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
    raise RuntimeError("FloodWait berulang")


async def _worker(client, status, jobs, chat, name, old, new, undo, main_menu):
    stop_btn = [[Button.inline("⏸ Berhenti setelah ini", b"rp:stop")],
                [Button.inline("⛔ Hentikan paksa", b"rp:kill")]]
    ops, done, skipped, failed = [], 0, [], []
    t0 = last = time.time()
    verb = "Mengembalikan" if undo else "Mengganti"
    RP.update(running=True, stop=False)
    try:
        for k, (label, mid, text, ents, rec) in enumerate(jobs, 1):
            if RP["stop"]:
                break
            if len(text) > CAP_LIMIT:
                skipped.append(label)
                continue
            if not undo:
                ops.append(rec)
            try:
                await _edit(client, chat, mid, text, ents)
                done += 1
            except errors.MessageNotModifiedError:
                done += 1
            except (errors.MessageTooLongError, errors.MediaCaptionTooLongError):
                skipped.append(label)
                if not undo:
                    ops.pop()
            except Exception as e:
                failed.append(f"{label}:{type(e).__name__}")
                if not undo:
                    ops.pop()
            if not undo and k % 20 == 0:
                _save_log(chat, name, old, new, ops)
            await asyncio.sleep(1.2)
            if time.time() - last > 8:
                last = time.time()
                try:
                    await status.edit(f"{verb} caption…\n{_bar(k / len(jobs))}  {k}/{len(jobs)}\n"
                                      f"Berhasil: {done}  Dilewati: {len(skipped)}  Gagal: {len(failed)}",
                                      buttons=stop_btn, parse_mode=None)
                except Exception:
                    pass
        note = "Selesai." if not RP["stop"] else "Dihentikan."
    except asyncio.CancelledError:
        note = "Dihentikan paksa."
    except Exception as e:
        note = f"Error: {type(e).__name__}: {e}"
    finally:
        if not undo and ops:
            _save_log(chat, name, old, new, ops)
        if undo and not failed and not skipped:
            try:
                os.remove(LOG)
            except OSError:
                pass
        RP.update(running=False, stop=False, task=None)
    msg = (f"{note}\n{'Dikembalikan' if undo else 'Diubah'}: {done} caption dalam "
           f"{int(time.time() - t0)} detik.")
    if skipped:
        msg += f"\nDilewati (terlalu panjang): {', '.join(map(str, skipped[:15]))}"
    if failed:
        msg += f"\nGagal: {', '.join(failed[:10])}"
    if not undo and done:
        msg += "\nTombol ✏️ Ganti kata → ↩ Batalkan perubahan terakhir bisa mengembalikan."
    try:
        await status.edit(msg, buttons=main_menu(), parse_mode=None)
    except Exception:
        pass


def register(bot, client, owner, state, show, main_menu):
    cancel_btn = [[Button.inline("✖ Batal", b"rp:cancel")]]

    def reset(event_mode=None):
        RP.update(step=None, posts=None, items=[], whole=True, case=False)
        state["mode"] = event_mode

    @bot.on(events.CallbackQuery(func=lambda e: e.sender_id == owner and e.data.startswith(b"rp:")))
    async def on_cb(event):
        data = event.data.decode()
        busy = state.get("running") or RP["running"]
        if data == "rp:start":
            await show(event, "✏️ Ganti kata di caption (hanya kata itu yang berubah).",
                       [[Button.inline("➕ Mulai ganti kata", b"rp:new")],
                        [Button.inline("↩ Batalkan perubahan terakhir", b"rp:undo")],
                        [Button.inline("✖ Tutup", b"rp:cancel")]], parse_mode=None)
        elif data == "rp:new":
            if busy:
                await event.answer("Ada proses yang sedang berjalan.", alert=True)
                return
            reset("rp")
            RP["step"] = "grp"
            await show(event, "Langkah 1/5: kirim GRUP yang captionnya mau diedit "
                              "(forward pesan dari grup/channel itu, atau angka ID).",
                       cancel_btn, parse_mode=None)
        elif data == "rp:cancel":
            reset(None)
            await show(event, "Ditutup.", main_menu(), parse_mode=None)
        elif data in ("rp:whole", "rp:case"):
            if RP["step"] != "confirm":
                await event.answer()
                return
            RP["whole" if data == "rp:whole" else "case"] ^= True
            txt, btn = _preview()
            await show(event, txt, btn, parse_mode=None)
        elif data == "rp:stop":
            RP["stop"] = True
            await event.answer("Berhenti setelah caption ini selesai.")
        elif data == "rp:kill":
            tk = RP.get("task")
            RP["stop"] = True
            if tk is not None and not tk.done():
                tk.cancel()
                await event.answer("Menghentikan sekarang...")
            else:
                await event.answer("Tidak ada proses yang berjalan.")
        elif data == "rp:undo":
            lg = _load_log()
            if not lg or not lg.get("ops"):
                await show(event, "Tidak ada perubahan yang bisa dibatalkan.", main_menu(), parse_mode=None)
                return
            await show(event, f"Perubahan terakhir: \"{lg['old']}\" → \"{lg['new']}\" di {lg['name']}, "
                              f"{len(lg['ops'])} caption.\nKembalikan semuanya ke teks semula?",
                       [[Button.inline("Ya, kembalikan", b"rp:undo_go")],
                        [Button.inline("✖ Batal", b"rp:cancel")]], parse_mode=None)
        elif data == "rp:undo_go":
            if busy:
                await event.answer("Ada proses yang sedang berjalan.", alert=True)
                return
            lg = _load_log()
            if not lg:
                await event.answer("Catatan tidak ditemukan.", alert=True)
                return
            jobs = [(o["id"], o["id"], o["text"], _ents_from(o["ents"]), None) for o in reversed(lg["ops"])]
            status = await event.respond("Mengembalikan caption…", parse_mode=None)
            RP["task"] = asyncio.create_task(_worker(client, status, jobs, lg["chat"], lg["name"],
                                                     lg["old"], lg["new"], True, main_menu))

    @bot.on(events.NewMessage(from_users=owner,
                              func=lambda e: state.get("mode") == "rp" and not (e.raw_text or "").startswith("/")))
    async def on_msg(event):
        step, raw = RP["step"], (event.raw_text or "").strip()
        if step == "grp":
            gid = await _chat_from(event)
            if gid is None:
                await event.reply("Tidak terdeteksi. Kirim forward dari grup/channel atau angka ID.", parse_mode=None)
                return
            try:
                ent = await client.get_entity(gid)
            except Exception:
                await event.reply(f"Akun tidak bisa mengakses ID {gid}.", parse_mode=None)
                return
            st = await event.reply("Membaca postingan…", parse_mode=None)
            last = [0.0]

            async def prog(n):
                if time.time() - last[0] > 8:
                    last[0] = time.time()
                    try:
                        await st.edit(f"Membaca postingan… {n} pesan diperiksa.", parse_mode=None)
                    except Exception:
                        pass

            posts = await _collect(client, ent, prog)
            if not posts:
                await st.edit("Tidak ada pesan bercaption dari akun Anda di grup itu.",
                              buttons=main_menu(), parse_mode=None)
                reset(None)
                return
            RP.update(chat=gid, name=getattr(ent, "title", None) or str(gid), posts=posts, step="old")
            await st.edit(f"Ditemukan {len(posts)} postingan bercaption milik akun Anda.\n"
                          "Langkah 2/5: kirim KATA YANG DICARI.", buttons=cancel_btn, parse_mode=None)
        elif step == "old":
            if not raw or len(raw) > 100:
                await event.reply("Kirim satu kata/frasa (maksimal 100 huruf).", parse_mode=None)
                return
            RP.update(old=raw, step="new")
            await event.reply("Langkah 3/5: kirim KATA PENGGANTI.", buttons=cancel_btn, parse_mode=None)
        elif step == "new":
            if not raw or len(raw) > 100:
                await event.reply("Kirim kata pengganti (maksimal 100 huruf).", parse_mode=None)
                return
            if raw == RP["old"]:
                await event.reply("Kata pengganti sama dengan kata lama.", parse_mode=None)
                return
            RP.update(new=raw, step="range")
            await event.reply(f"Langkah 4/5: RENTANG postingan (total {len(RP['posts'])}). Contoh:\n"
                              "1 sampai 50\n120 sampai akhir\nsemua\natau dua link postingan (awal dan akhir).",
                              buttons=cancel_btn, parse_mode=None)
        elif step == "range":
            r = _parse_range(raw, RP["posts"])
            if isinstance(r, str):
                await event.reply(r, parse_mode=None)
                return
            RP.update(lo=r[0], hi=r[1], step="confirm")
            txt, btn = _preview()
            await event.reply(txt, buttons=btn, parse_mode=None)
        elif step == "confirm":
            if raw != "GANTI":
                await event.reply("Ketik persis GANTI, atau tekan Batal.", parse_mode=None)
                return
            if state.get("running") or RP["running"]:
                await event.reply("Ada proses yang sedang berjalan.", parse_mode=None)
                return
            if not RP["items"]:
                await event.reply("Tidak ada yang cocok untuk diganti.", parse_mode=None)
                return
            jobs = [(i, m.id, nt, ents, {"id": m.id, "text": m.message,
                                         "ents": [e.to_dict() for e in (m.entities or [])]})
                    for i, m, (nt, ents, _) in RP["items"]]
            chat, name, old, new = RP["chat"], RP["name"], RP["old"], RP["new"]
            reset(None)
            status = await event.reply(f"Mengganti {len(jobs)} caption…", parse_mode=None)
            RP["task"] = asyncio.create_task(_worker(client, status, jobs, chat, name, old, new, False, main_menu))
