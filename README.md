# pyPaperclip

**pyPaperclip** adalah control plane Python ringan untuk mengatur pekerjaan agen AI. Fokusnya adalah koordinasi yang dapat diaudit—company, goal, agent, task, heartbeat, budget, dan audit log—bukan chatbot atau framework model.

> Status saat ini: **Fase 1 selesai dan v0.3 Real Adapters selesai**. Roadmap detail tersedia di [ROADMAP.md](ROADMAP.md), sedangkan rencana teknis tersedia di [PLAN.md](PLAN.md).

## Mengapa pyPaperclip lebih ringan

Upstream Paperclip menggabungkan Node.js, React, PostgreSQL/Drizzle, banyak adapter native/cloud, runner, plugin, observability, chat connectors, dan UI. **pyPaperclip** mempertahankan inti orkestrasi bernilai tinggi dengan satu proses Python, SQLite WAL, FastAPI, dan worker heartbeat internal.

| Konsep | Status pyPaperclip | Implementasi |
|---|---|---|
| Company / organisasi | **Selesai** | Tabel `companies` dengan budget dan spend |
| Goal alignment | **Selesai dasar** | Tabel `goals`, dapat ditautkan ke task |
| Org chart minimum | **Selesai dasar** | Agent dengan role dan adapter |
| Work orchestration | **Selesai** | Task dengan status queued/running/done/failed/blocked |
| Heartbeat | **Selesai MVP** | Worker asyncio yang memproses antrean setiap detik |
| BYO agent | **Selesai fondasi** | `AdapterRegistry`, adapter `echo` bawaan |
| Cost control | **Selesai MVP** | Budget guard dan cost counter sederhana |
| Governance | **Selesai minimum** | Audit log append-only |
| Reliable queue | **Selesai v0.2** | Lease, retry/backoff, idempotency, dan recovery |
| Real adapters | **Selesai v0.3** | Subprocess non-shell, HTTP/webhook, dan OpenAI-compatible |
| Fase 1 domain hygiene | **Selesai** | Validasi relasi, pagination, detail endpoint, migration runner, DLQ, graceful drain |
| Auth multi-user | **Belum** | Direncanakan fase berikutnya |
| UI dashboard | **Belum** | Direncanakan setelah API stabil |
| Remote sandbox / secrets vault | **Belum** | Direncanakan fase produksi |

## Quickstart

```bash
cd paperclip-python
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
pypaperclip
```

Server berjalan di `http://127.0.0.1:8000`; dokumentasi OpenAPI tersedia di `/docs`.

Konfigurasi:

```bash
PYPAPERCLIP_DB=./data/pypaperclip.db \
PYPAPERCLIP_HOST=0.0.0.0 \
PYPAPERCLIP_PORT=8000 \
pypaperclip
```

## Alur API singkat

```bash
COMPANY=$(curl -s localhost:8000/companies -X POST -H 'content-type: application/json' \
  -d '{"name":"Demo AI Co","budget_cents":1000}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
AGENT=$(curl -s localhost:8000/companies/$COMPANY/agents -X POST -H 'content-type: application/json' \
  -d '{"name":"Worker","adapter":"echo"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s localhost:8000/companies/$COMPANY/tasks -X POST -H 'content-type: application/json' \
  -d "{\"title\":\"First task\",\"description\":\"Smoke test\",\"agent_id\":\"$AGENT\"}"
curl -s localhost:8000/companies/$COMPANY/tasks
curl -s localhost:8000/audit?company_id=$COMPANY
```

Task otomatis diproses heartbeat worker. Adapter baru dapat mengimplementasikan protocol `run(task, agent)` dan didaftarkan melalui `adapters.register("nama", adapter)` saat startup.

API list mendukung `limit`, `offset`, serta filter task `status` dan `goal_id`. Endpoint detail tersedia untuk company, agent, dan task. Task yang gagal permanen dapat diperiksa melalui `/companies/{company_id}/dead-letter`; schema SQLite dikelola oleh migration runner dan worker menunggu task aktif selesai saat shutdown.

## Real adapters

Agent menerima `config` JSON saat dibuat. Adapter `subprocess` hanya aktif jika `PYPAPERCLIP_ALLOW_SUBPROCESS=1`, tidak memakai shell, dan memiliki timeout maksimal 300 detik. Contoh konfigurasi: `{"command":["python3","worker.py"],"timeout_seconds":30}`.

Adapter `http` mengirim payload task ke `config.url` melalui POST JSON dengan timeout terbatas. Header tambahan dapat diberikan melalui `config.headers`; jangan menaruh secret langsung di database jika dapat dihindari.

Adapter `openai` memanggil endpoint OpenAI-compatible `/chat/completions`. Konfigurasinya membutuhkan `config.base_url`, `config.model`, dan opsional `config.api_key_env` yang menunjuk ke nama environment variable. Nilai API key tidak pernah disimpan dalam konfigurasi agent atau audit log.

## Pengujian

```bash
PYTHONPATH=. python3 -m unittest discover -s test -v
python3 -m compileall -q pypaperclip test
```

## Prinsip desain

1. **SQLite-first, Postgres-ready:** repository layer harus dipisahkan sebelum migrasi PostgreSQL.
2. **At-least-once execution:** lease kedaluwarsa direcovery, failure transient dijadwalkan ulang dengan backoff, dan duplicate submission dicegah dengan idempotency key.
3. **Budget before dispatch:** semua adapter wajib melewati budget guard.
4. **Audit by default:** perubahan status tercatat tanpa menyimpan secret sensitif.
5. **Adapter boundary:** scheduler tidak boleh mengetahui detail provider model.
6. **Small core first:** fitur besar hanya masuk jika memperbaiki reliabilitas atau produktivitas secara terukur.
