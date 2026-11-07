import asyncio
import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telethon import Button, errors, events, utils
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import MessageService, PeerChannel, PeerChat

import richtext
from organizer import is_media, split_albums

WIB = ZoneInfo("Asia/Jakarta")
ALBUM_DELAY = float(os.environ.get("ALBUM_DELAY", "6"))
DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
MV = {"step": None, "src": None, "src_name": "", "tgt": None, "tgt_name": "", "start": None,
      "end": None, "units": None, "can_del": False, "task": None, "stop": False, "running": False}


def _bar(frac, width=12):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "█" * n + "░" * (width - n) + f" {int(frac * 100)}%"


def _link(chat_id, mid):
    s = str(chat_id)
    return f"https://t.me/c/{s[4:]}/{mid}" if s.startswith("-100") else f"pesan #{mid}"


def _size(m):
    try:
        return (m.file.size or 0) if m.file else 0
    except Exception:
        return 0


def _fmt(dt):
    return dt.astimezone(WIB).strftime("%d %b %Y")


async def _chat_from(event):
    fwd = event.message.fwd_from
    if fwd is not None:
        peer = getattr(fwd, "from_id", None)
        return utils.get_peer_id(peer) if isinstance(peer, PeerChannel) else None
    m = re.fullmatch(r"-?\d{5,}", (event.raw_text or "").strip())
    if not m:
        return None
    gid = int(m.group(0))
    return int(f"-100{m.group(0)}") if gid > 0 else gid


async def _scan(client, chat, start, end, progress):
    out, n = [], 0
    s = start.astimezone(timezone.utc)
    async for m in client.iter_messages(chat, offset_date=end.astimezone(timezone.utc)):
        if m.date < s:
            break
        n += 1
        if n % 100 == 0:
            await progress(n)
        if not isinstance(m, MessageService):
            out.append(m)
    out.reverse()
    return out


async def _legacy_id(client, src):
    try:
        ent = await client.get_entity(src)
        if not getattr(ent, "megagroup", False) and not getattr(ent, "broadcast", False):
            return None
        full = await client(GetFullChannelRequest(ent))
        return getattr(full.full_chat, "migrated_from_chat_id", None)
    except Exception:
        return None


def _units(msgs):
    units, cur = [], []

    def flush():
        nonlocal cur
        if cur:
            units.extend(split_albums(cur))
            cur = []

    for m in msgs:
        if m.sticker is not None:
            flush()
            if m.out:
                units.append([m])
        elif is_media(m):
            if cur and cur[0].chat_id != m.chat_id:
                flush()
            cur.append(m)
        else:
            flush()
    flush()
    return units


async def _can_delete_others(client, src):
    try:
        p = await client.get_permissions(src)
        return bool(getattr(p, "is_creator", False) or getattr(p, "delete_messages", False))
    except Exception:
        return False


async def _run_move(client, status_msg, main_menu):
    src, tgt, units, can_del = MV["src"], MV["tgt"], MV["units"], MV["can_del"]
    n_units = len(units)
    items_total = sum(len(a) for a in units)
    mb_total = sum(_size(m) for a in units for m in a) / 1048576
    st = {"i": 0, "items": 0, "mb": 0.0, "moved": 0, "deleted": 0, "nodel": 0, "cur": "", "t": 0.0}
    log = []
    kb = [[Button.inline("⛔ Berhenti sekarang", b"mv:kill")],
          [Button.inline("⏸ Berhenti setelah album ini", b"mv:stop")]]

    def compose(final=False):
        lines = [f"Memindahkan: {MV['src_name']} → {MV['tgt_name']}",
                 f"Tanggal: {_fmt(MV['start'])} - {_fmt(MV['end'] - timedelta(days=1))}",
                 f"Album  {_bar(st['i'] / max(1, n_units))} ({st['i']}/{n_units})",
                 f"Item   {st['items']}/{items_total} file · {st['mb']:.0f}/{mb_total:.0f} MB"]
        if st["cur"] and not final:
            lines.append(f"Sedang: {st['cur']}")
        lines.append(f"Dipindah: {st['moved']} | Dihapus: {st['deleted']} | Tidak terhapus: {st['nodel']}")
        if MV["stop"] and not final:
            lines.append("⏸ Berhenti diminta, menunggu album yang sedang berjalan.")
        lines.append("")
        lines += log[-5:]
        return "\n".join(lines)

    async def refresh(force=False):
        now = time.time()
        if now - st["t"] < 8:
            return
        st["t"] = now
        try:
            await status_msg.edit(compose(), buttons=kb, parse_mode=None)
        except Exception:
            pass

    try:
        for album in units:
            if MV["stop"]:
                log.append("Dihentikan sesuai permintaan.")
                break
            chat = album[0].chat_id
            st["cur"] = _link(chat, album[0].id)
            await refresh(True)
            fresh = [x for x in await client.get_messages(chat, ids=[m.id for m in album])
                     if x is not None and x.media]
            if not fresh:
                st["i"] += 1
                continue
            caption = ""
            for x in fresh:
                if (x.message or "").strip():
                    caption = richtext.msg_markdown(x).strip()
                    break
            medias = [x.media for x in fresh]
            caps = [caption] + [""] * (len(medias) - 1)
            try:
                if len(medias) == 1:
                    await client.send_file(tgt, medias[0], caption=caption, parse_mode=richtext.MODE)
                else:
                    await client.send_file(tgt, medias, caption=caps, parse_mode=richtext.MODE)
            except errors.MediaCaptionTooLongError:
                await client.send_file(tgt, medias[0] if len(medias) == 1 else medias)
                await client.send_message(tgt, caption, parse_mode=richtext.MODE)
            del_ids = [x.id for x in fresh if x.out or (can_del and chat == src)]
            if del_ids:
                try:
                    await client.delete_messages(chat, del_ids, revoke=True)
                    st["deleted"] += len(del_ids)
                except Exception as e:
                    log.append(f"Gagal menghapus ({type(e).__name__})")
                    st["nodel"] += len(del_ids)
            st["nodel"] += len(fresh) - len(del_ids)
            st["moved"] += len(fresh)
            st["items"] += len(fresh)
            st["mb"] += sum(_size(x) for x in fresh) / 1048576
            st["i"] += 1
            await refresh(True)
            await asyncio.sleep(ALBUM_DELAY)
        else:
            log.append("Selesai.")
    except asyncio.CancelledError:
        log.append("Dihentikan paksa.")
    except Exception as e:
        log.append(f"Error: {type(e).__name__}: {e}")
    finally:
        MV["running"] = False
        MV["stop"] = False
    print("[mover]", " | ".join(log[-10:]), flush=True)
    try:
        await status_msg.edit(compose(True) + "\n\nUlangi dengan tanggal yang sama untuk memproses sisa (jika ada).",
                              buttons=main_menu(), parse_mode=None)
    except Exception:
        pass


def register(bot, client, owner, state, show, main_menu):
    cancel_btn = [[Button.inline("✖ Batal", b"mv:cancel")]]

    @bot.on(events.CallbackQuery(func=lambda e: e.sender_id == owner and e.data.startswith(b"mv:")))
    async def on_cb(event):
        data = event.data.decode()
        if data == "mv:start":
            if state.get("running") or MV["running"]:
                await event.answer("Ada proses yang sedang berjalan.", alert=True)
                return
            MV.update(step="src", src=None, tgt=None, start=None, end=None, units=None)
            state["mode"] = "mv"
            await show(event, "Pindahkan media (SEMENTARA). Pesan asli DIHAPUS setelah terkirim.\n"
                              "Langkah 1/3: kirim grup SUMBER (forward pesan dari channel, atau angka ID).\n"
                              "Cari ID: /cariid kata", cancel_btn, parse_mode=None)
        elif data == "mv:cancel":
            state["mode"] = None
            MV["step"] = None
            await show(event, "Dibatalkan.", main_menu(), parse_mode=None)
        elif data == "mv:stop":
            MV["stop"] = True
            await event.answer("Akan berhenti setelah album yang sedang dikirim selesai.")
        elif data == "mv:kill":
            tk = MV.get("task")
            MV["stop"] = True
            if tk is not None and not tk.done():
                tk.cancel()
                await event.answer("Menghentikan sekarang...")
            else:
                await event.answer("Tidak ada proses yang berjalan.")

    @bot.on(events.NewMessage(from_users=owner,
                              func=lambda e: state.get("mode") == "mv" and not (e.raw_text or "").startswith("/")))
    async def on_msg(event):
        step = MV["step"]
        if step in ("src", "tgt"):
            gid = await _chat_from(event)
            if gid is None:
                await event.reply("Tidak terdeteksi. Kirim forward dari channel atau angka ID.", parse_mode=None)
                return
            try:
                ent = await client.get_entity(gid)
            except Exception:
                await event.reply(f"Akun tidak bisa mengakses ID {gid}.", parse_mode=None)
                return
            name = getattr(ent, "title", None) or str(gid)
            if step == "src":
                if getattr(ent, "noforwards", False):
                    await event.reply("Grup ini memakai proteksi konten, medianya tidak bisa dikirim ulang. Dibatalkan.",
                                      buttons=main_menu(), parse_mode=None)
                    state["mode"], MV["step"] = None, None
                    return
                MV.update(src=gid, src_name=name, step="tgt")
                await event.reply(f"Sumber: {name}\nLangkah 2/3: kirim grup TUJUAN.", buttons=cancel_btn, parse_mode=None)
            else:
                if gid == MV["src"]:
                    await event.reply("Sumber dan tujuan sama: media akan dipindah ke posisi paling bawah di grup itu.",
                                      parse_mode=None)
                MV.update(tgt=gid, tgt_name=name, step="date")
                await event.reply(f"Tujuan: {name}\nLangkah 3/3: kirim tanggal (WIB). Contoh:\n"
                                  "2026-11-02\natau rentang: 2026-11-01 sampai 2026-11-03\natau dari yang paling awal: awal sampai 2026-11-03",
                                  buttons=cancel_btn, parse_mode=None)
        elif step == "date":
            raw = event.raw_text or ""
            ds = DATE_RE.findall(raw)
            use_awal = bool(re.match(r"\s*awal\b", raw.lower()))
            if not ds and not use_awal:
                await event.reply("Format tanggal tidak dikenali. Contoh: 2026-11-02, atau: awal sampai 2026-11-03",
                                  parse_mode=None)
                return
            today = datetime.now(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
            try:
                if use_awal:
                    first = None
                    async for m in client.iter_messages(MV["src"], reverse=True, limit=1):
                        first = m
                    if first is None:
                        await event.reply("Grup sumber kosong.", parse_mode=None)
                        return
                    d1 = first.date.astimezone(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
                    d2 = datetime(*map(int, ds[-1]), tzinfo=WIB) if ds else today - timedelta(days=1)
                else:
                    d1 = datetime(*map(int, ds[0]), tzinfo=WIB)
                    d2 = datetime(*map(int, ds[-1]), tzinfo=WIB)
            except ValueError:
                await event.reply("Tanggal tidak valid.", parse_mode=None)
                return
            if d2 < d1:
                d1, d2 = d2, d1
            if MV["src"] == MV["tgt"] and d2 + timedelta(days=1) > today:
                await event.reply("Sumber dan tujuan sama: tanggal tidak boleh mencakup hari ini, karena media yang baru "
                                  "dipindahkan akan terbaca lagi. Kirim tanggal sebelum hari ini.", parse_mode=None)
                return
            MV.update(start=d1, end=d2 + timedelta(days=1), step="scan")
            wait = await event.reply("Membaca pesan pada tanggal itu...", parse_mode=None)
            sc = {"t": 0.0}

            async def prog(n):
                if time.time() - sc["t"] < 2.5:
                    return
                sc["t"] = time.time()
                try:
                    await wait.edit(f"Membaca pesan... {n} pesan diperiksa", parse_mode=None)
                except Exception:
                    pass

            try:
                msgs = await _scan(client, MV["src"], MV["start"], MV["end"], prog)
                legacy = await _legacy_id(client, MV["src"])
                n_legacy = 0
                if legacy:
                    old = await _scan(client, PeerChat(legacy), MV["start"], MV["end"], prog)
                    n_legacy = sum(1 for m in old if is_media(m))
                    msgs = sorted(old + msgs, key=lambda m: m.date)
                units = _units(msgs)
                can_del = await _can_delete_others(client, MV["src"])
            except Exception as e:
                MV["step"] = "date"
                await wait.edit(f"Gagal membaca: {type(e).__name__}: {e}", parse_mode=None)
                return
            if not units:
                MV["step"] = "date"
                note = (f" Grup ini hasil upgrade dari grup biasa (ID lama {legacy}), pesan lamanya ikut dibaca."
                        if legacy else "")
                await wait.edit(f"Tidak ada media pada tanggal itu. Pesan diperiksa: {len(msgs)}.{note} "
                                "Kirim tanggal lain, atau Batal.",
                                buttons=cancel_btn, parse_mode=None)
                return
            items = [m for a in units for m in a]
            deletable = sum(1 for m in items if m.out or (can_del and m.chat_id == MV["src"]))
            leg = f"\nTermasuk {n_legacy} media dari grup lama (sebelum jadi supergrup)" if n_legacy else ""
            mb = sum(_size(m) for m in items) / 1048576
            MV.update(units=units, can_del=can_del, step="confirm")
            stk = sum(1 for m in items if m.sticker is not None)
            seq = " ".join(("S" if a[0].sticker is not None else f"A{len(a)}") for a in units[:15])
            warn = ("\n⚠ Sebagian TIDAK bisa dihapus: medianya akan muncul dobel di grup."
                    if (MV["src"] == MV["tgt"] and deletable < len(items)) else "")
            await wait.edit(
                f"Sumber : {MV['src_name']}\nTujuan : {MV['tgt_name']}\n"
                f"Tanggal: {_fmt(MV['start'])} - {_fmt(MV['end'] - timedelta(days=1))} (WIB)\n"
                f"Media  : {len(items)} file dalam {len(units)} album/stiker, {mb:.0f} MB{leg}\n"
                f"Stiker : {stk} (ikut dipindah dan dihapus)\n"
                f"Urutan awal: {seq}\n"
                f"Bisa dihapus dari sumber: {deletable} dari {len(items)}{warn}\n\n"
                "⚠ Setelah terkirim, pesan asli DIHAPUS PERMANEN dari sumber.\n"
                "Ketik persis HAPUS untuk memulai, atau tekan Batal.",
                buttons=cancel_btn, parse_mode=None)
        elif step == "confirm":
            if (event.raw_text or "").strip() != "HAPUS":
                await event.reply("Ketik persis HAPUS untuk memulai, atau tekan Batal.", parse_mode=None)
                return
            state["mode"] = None
            MV.update(step=None, running=True, stop=False)
            status = await event.reply("Memulai...", parse_mode=None)
            MV["task"] = asyncio.create_task(_run_move(client, status, main_menu))
