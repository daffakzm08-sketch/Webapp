# Y2Mate Web

Web app (Flask) di atas library `y2mate_api` (v1.2.2). Cari video lewat judul/link/ID, pilih video atau audio beserta kualitasnya, lalu simpan file.

## Menjalankan

```sh
pip install -r requirements.txt
python app.py
```

Buka http://127.0.0.1:5000

Opsi lewat environment variable: `HOST`, `PORT`, dan `Y2MATE_CF_CLEARANCE` (cookie awal).

## Cookie cf_clearance otomatis (Silent Refresh)

Cookie diperbarui sendiri di latar belakang lewat [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr), tanpa perlu mengisi manual.

```sh
docker compose up -d                                  # jalankan FlareSolverr
FLARESOLVERR_URL=http://127.0.0.1:8191 python app.py  # jalankan web app
```

Cara kerjanya:

- Saat server menyala, cookie langsung diambil, lalu diperbarui terjadwal sebelum kedaluwarsa (patokan 80% dari masa berlaku, atau tiap `REFRESH_MINUTES`, default 25 menit).
- Bila y2mate tetap menolak permintaan, cookie diperbarui saat itu juga dan permintaan diulang sekali secara otomatis. Pengguna tidak melihat error.
- Pembaruan dibatasi satu kali per 30 detik. Bila gagal, jadwal mencoba lagi tiap 2 menit. Tombol **Cookie** menampilkan "Cookie otomatis"; arahkan kursor ke sana untuk melihat error terakhir.

Syarat penting: FlareSolverr harus berjalan di mesin/IP yang sama dengan web app, karena cookie Cloudflare terikat pada IP dan User-Agent. Web app otomatis memakai User-Agent yang dikembalikan FlareSolverr. Cloudflare sesekali bisa lolos dari FlareSolverr, jadi pembaruan bisa gagal sementara.

## Cookie cf_clearance manual (cadangan)

Tanpa FlareSolverr, atau sebagai cadangan, cookie bisa diisi manual dari Chrome:

1. Buka y2mate.com di Chrome dan lolos verifikasi.
2. DevTools → Application → Cookies → salin nilai `cf_clearance`.
3. Tempel di tombol **Cookie** pada web app.

Cookie cepat kedaluwarsa. Bila muncul pesan "y2mate menolak permintaan", isi ulang.

## API

| Endpoint | Body | Fungsi |
|---|---|---|
| `POST /api/search` | `{query}` | Cari kata kunci, atau baca langsung bila berupa link/ID |
| `POST /api/formats` | `{vid}` | Daftar kualitas video/audio |
| `POST /api/convert` | `{vid, k}` | Konversi dan ambil link unduhan (panggil ulang bila `state` = `CONVERTING`) |
| `POST /api/cookie` | `{cf_clearance}` | Simpan cookie |

## API key untuk project lain

Project lain bisa memakai web app ini lewat endpoint `/v1/*` yang wajib API key.

1. Jalankan `python app.py`. Password admin tercetak di terminal (disimpan di `data/admin_password.txt`). Atur sendiri dengan environment variable `ADMIN_PASSWORD`.
2. Klik **API Key** di web app, masukkan password admin, lalu **Buat key**. Key hanya tampil sekali, jadi salin langsung. Yang tersimpan hanya hash-nya di `data/keys.json`.
3. Kirim key lewat header `X-API-Key: y2m_...` atau `Authorization: Bearer y2m_...`.
4. Key yang bocor bisa dicabut lewat tombol **Cabut**.

| Endpoint | Body | Fungsi |
|---|---|---|
| `POST /v1/download` | `{url, type?, quality?}` | Satu panggilan: link/ID video menjadi link unduhan. `type` = `video` (default) atau `audio`. Tanpa `quality`, dipakai yang terbaik. |
| `POST /v1/search` | `{query}` | Sama seperti `/api/search` |
| `POST /v1/formats` | `{vid}` | Sama seperti `/api/formats` |
| `POST /v1/convert` | `{vid, k}` | Sama seperti `/api/convert` |

Contoh:

```sh
curl -X POST http://127.0.0.1:5000/v1/download \
  -H "X-API-Key: y2m_xxxxx" -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","type":"audio"}'
```

Respons: `{"ok":true,"title":"...","quality":".m4a","dlink":"https://..."}`. Panggilan ini bisa memakan waktu hingga sekitar 30 detik selama konversi.

Variabel tambahan: `DATA_DIR` (lokasi `keys.json`) dan `CORS_ORIGIN` (default `*`, batasi ke domain project-mu bila dipanggil dari browser).

## Catatan

- Cookie disimpan di memori server dan dipakai bersama semua pengunjung, jadi jalankan secara lokal/pribadi. Jangan buka ke publik tanpa autentikasi.
- Link unduhan berasal dari server y2mate dan langsung dibuka di browser, tidak lewat server ini.
- Tidak ada hubungan resmi dengan y2mate.com, dan mengunduh dari YouTube bisa melanggar ketentuan layanannya. Gunakan untuk konten yang boleh kamu unduh.
