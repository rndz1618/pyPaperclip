# pyPaperclip — Plan Teknis

## Tujuan

Membangun control plane Python yang ringan, mudah dipasang, dan efektif untuk mengoordinasikan beberapa agen AI dengan batas biaya dan jejak audit yang jelas.

## Ruang lingkup inti

| Area | Target | Status |
|---|---|---|
| Runtime | Satu proses Python dengan FastAPI | **Selesai** |
| Persistence | SQLite WAL dengan schema minimal | **Selesai** |
| Organisasi | Company, goal, agent | **Selesai dasar** |
| Pekerjaan | Task queue dan status lifecycle | **Selesai MVP** |
| Eksekusi | Heartbeat worker dan adapter boundary | **Selesai MVP** |
| Pengendalian biaya | Company budget dan spend counter | **Selesai MVP** |
| Audit | Event append-only | **Selesai minimum** |
| Reliability | Recovery, retry, idempotency | **Selesai Fase 1** |
| Security | Authentication, authorization, secret isolation | **Belum** |
| UX | Dashboard web dan monitoring | **Belum** |

## Tahapan kerja

### Fase 0 — Fondasi MVP

- [x] Package `pypaperclip` dan CLI `pypaperclip`
- [x] FastAPI `/health`, company, agent, goal, task, audit
- [x] SQLite schema dan WAL
- [x] Heartbeat worker internal
- [x] Adapter registry dengan `echo`
- [x] Budget blocking
- [x] Smoke test dan HTTP verification

### Fase 1 — Reliability dan domain hygiene

- [x] Pisahkan model domain, migration runner, dan worker service ke modul terpisah
- [x] Tambahkan task claim lease dengan timeout
- [x] Retry policy dengan exponential backoff
- [x] Idempotency key pada task execution
- [x] Recovery task yang lease-nya kedaluwarsa
- [x] Validasi relasi company/goal/agent secara ketat
- [x] Pagination, filtering, dan endpoint detail
- [x] Migration runner untuk schema SQLite
- [x] Dead-letter queue untuk task permanen gagal
- [x] Graceful worker draining saat shutdown

### Fase 2 — Adapter produksi

- [x] Adapter subprocess lokal tanpa shell dengan opt-in dan timeout
- [x] Adapter HTTP/webhook dengan timeout
- [x] Adapter OpenAI-compatible melalui interface provider
- [ ] Normalisasi token, latency, cost, dan output
- [ ] Redaction secret menyeluruh pada log dan audit
- [ ] Per-agent budget dan rate limit

### Fase 3 — Governance dan security

- [ ] API key authentication
- [ ] Role-based access control
- [ ] Approval gate untuk task berisiko
- [ ] Pause/resume/terminate agent
- [ ] Audit event schema versioning
- [ ] Export/import company dengan secret scrubbing

### Fase 4 — Dashboard dan operasional

- [ ] Dashboard status company, agent, task, budget
- [ ] Live run log dan heartbeat timeline
- [ ] Webhook/event notification
- [ ] Health/readiness metrics
- [ ] Backup dan restore SQLite
- [ ] Docker image dan deployment guide

## Kriteria keberhasilan

- Instalasi lokal selesai dengan satu perintah setelah environment Python tersedia.
- Startup time dan penggunaan memori tetap rendah untuk deployment single-user.
- Task tidak hilang ketika worker restart setelah reliability phase selesai.
- Task yang melewati budget tidak pernah dikirim ke adapter.
- Setiap perubahan lifecycle dapat ditelusuri melalui audit log.
- Adapter dapat ditambahkan tanpa mengubah scheduler.
