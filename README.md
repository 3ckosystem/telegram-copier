# Telegram Copier

Penyalin media antar grup/channel Telegram dengan pengelompokan blok, album seimbang, template caption, dan kontrol lewat bot. Berjalan 24/7 di VPS dalam satu container Docker.

## Cara kerja
- **Akun pribadi (Telethon, userbot)** membaca grup sumber dan mengirim ulang ke tujuan sebagai pesan baru (bukan forward).
- **Bot BotFather** hanya papan kontrol (menu, status). Hanya pemilik akun yang dilayani.
- Satu proses (`bot.py`) menjalankan keduanya. Progres tersimpan per pasangan sumber-tujuan di `data/`.

## Struktur
| File | Fungsi |
|---|---|
| `bot.py` | Bot kontrol: menu, status, pengaturan, jalankan salin |
| `engine.py` | Mesin penyalin: blok, album, zip/rar/7z, progres, simpan posisi |
| `organizer.py` | Membaca riwayat, mengelompokkan blok, memecah album seimbang |
| `mover.py` | Pindah media per tanggal (hapus asal) |
| `sources.py` | Daftar sumber (riwayat, sembunyikan) |
| `settings.py` | Sumber/tujuan/template yang tersimpan |
| `richtext.py` | Format teks dan emoji premium |
| `copier.py` | Penyalin versi terminal (tanpa bot) |
| `login_test.py`, `pick_sticker.py` | Login Telegram dan pilih stiker pemisah |
| `Dockerfile`, `docker-compose.yml` | Build dan jalankan di Docker |
| `template.txt` | Template caption bawaan (`{judul}`, `{zip}`) |

## Variabel `.env`
| Variabel | Wajib | Keterangan |
|---|---|---|
| `API_ID`, `API_HASH` | ya | Dari my.telegram.org |
| `BOT_TOKEN` | ya | Token bot dari @BotFather |
| `STICKER_MSG_ID` | ya | Nomor pesan stiker pemisah di Pesan Tersimpan (`python pick_sticker.py`) |
| `SOURCE_CHAT_ID`, `TARGET_CHAT_ID` | tidak | Bisa diatur lewat bot; hanya nilai awal |
| `CONVERT_FILES` | tidak | `1` = ubah foto/video berbentuk file menjadi media (default) |
| `GAP_SECONDS` | tidak | Jeda antar media yang memisahkan blok (default 3600) |
| `ZIP_MAX_MB` | tidak | Batas isi arsip yang diekstrak (default 1500) |

`.env` dan `data/` tidak pernah masuk Git.

## Pasang di VPS
    git clone git@github.com:3ckosystem/telegram-copier.git /opt/telegram-copier
    cd /opt/telegram-copier && mkdir -p data
    # buat .env dari .env.example (izin 600)
    docker compose run --rm -it copier python login_test.py   # login Telegram sekali
    docker compose up -d --build

Lalu kirim `/start` ke bot, atur sumber dan tujuan lewat menu, dan tekan Mulai Salin.

## Alur kerja perubahan
1. Edit kode (lokal atau VPS), uji.
2. `git add -A && git commit -m "..." && git push`.
3. Di VPS: `git pull && docker compose up -d --build`.

Jangan membangun ulang saat ada proses salin berjalan, karena prosesnya terputus.

## Perintah penting (di /opt/telegram-copier)
    docker compose logs --tail 20       # log
    docker compose restart              # restart
    docker compose up -d --build        # terapkan perubahan kode
    docker stats --no-stream            # pemakaian

Di bot: `/start` (menu), `/status`, `/limit` (pembatas Telegram), `/cariid kata` (cari ID grup).

## Fitur
- **Blok:** judul (teks atau caption) memulai blok; media setelahnya masuk blok itu; jeda lebih dari GAP_SECONDS memisah blok.
- **Album seimbang:** 17 media jadi 9+8; 52 jadi 10,10,10,10,6,6.
- **Template:** caption memakai `template.txt` (`{judul}`, `{zip}`), bisa diubah lewat bot, emoji premium didukung.
- **Arsip:** zip/rar/7z/tar diunduh, diekstrak, hasilnya jadi album foto/video; password otomatis dari judul dan caption, lalu bertanya ke Anda.
- **Stiker:** stiker sumber tidak disalin; stiker pemisah dikirim di bawah tiap blok.
- **Posisi:** tersimpan per album, lanjut tanpa duplikat; atur lewat link, forward, atau nomor blok.
- **Pindah media:** pindahkan media tanggal tertentu lalu hapus asal (sumber boleh sama dengan tujuan).

## Data (tidak di Git)
Folder `data/` berisi session, progres, pengaturan, dan daftar sumber. Cadangkan secara terenkripsi bersama `.env`. Pindah server = clone repo, pulihkan `data/` dan `.env`, lalu `docker compose up -d --build`. Jangan menjalankan dua instance dengan session yang sama.

## Keamanan
- Jangan commit `.env`, `*.session`, atau `data/`.
- Deploy key GitHub hanya untuk repo ini. Bot hanya melayani pemilik akun.

## Pemecahan masalah
- `database is locked`: ada proses lain memakai session; hentikan yang lain.
- FloodWait pada edit pesan: Telegram membatasi edit oleh bot; bot otomatis memakai pesan baru (lihat `/limit`).
- Arsip dilewati karena batas ukuran: naikkan `ZIP_MAX_MB` di `.env`, lalu `docker compose up -d --force-recreate`.
