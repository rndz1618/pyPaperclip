# pyPaperclip — Roadmap

Roadmap ini membedakan fitur yang sudah tersedia dari pekerjaan yang belum dimulai. Status dapat berubah setelah setiap milestone tervalidasi melalui test dan dokumentasi.

## Ringkasan status

| Status | Arti |
|---|---|
| **Selesai** | Sudah diimplementasikan dan diverifikasi |
| **Fondasi** | Kerangka tersedia, tetapi belum siap production-grade |
| **Belum** | Belum diimplementasikan |

## Sudah selesai

- **Branding pyPaperclip:** nama proyek, package, CLI, FastAPI title, health response, dan dokumentasi sudah distandarkan.
- **Control plane API:** company, goal, agent, task, dan audit endpoint tersedia.
- **SQLite persistence:** schema minimal dengan foreign key dan WAL.
- **Heartbeat execution:** worker internal memproses task queued secara otomatis.
- **Adapter boundary:** registry dan adapter `echo` tersedia.
- **Budget guard:** task diblokir jika budget company sudah habis.
- **Cost accounting minimum:** spend dicatat per task pada company dan agent.
- **Audit trail:** event company, goal, agent, task completed/failed/blocked tercatat.
- **Smoke test:** lifecycle utama telah diuji dengan `unittest`.
- **HTTP verification:** health endpoint dan alur task telah diuji melalui server nyata.
- **Reliable queue v0.2:** lease timeout, retry/backoff, idempotency key, dan recovery test telah selesai.
- **Real adapters v0.3:** subprocess non-shell, HTTP/webhook, OpenAI-compatible, bounded timeout, dan konfigurasi agent telah selesai.
- **Fase 1 domain hygiene:** validasi relasi, pagination/filtering, detail endpoint, migration runner, dead-letter queue, dan graceful worker draining telah selesai.
- **Secure control plane v0.4:** auth API key secure-by-default, RBAC company scope, approval gate, agent lifecycle, dan secret redaction telah selesai.

## Fondasi tersedia, tetapi belum production-ready

- **Goal alignment:** task dapat menyimpan `goal_id`, tetapi belum ada progress rollup ke goal.
- **BYO agent:** interface adapter dan tiga adapter nyata sudah tersedia; sandboxing dan provider cost normalization masih belum production-ready.
- **Cost control:** counter bersifat sederhana; belum ada token accounting provider dan rate limit.
- **Concurrency:** SQLite lock dan lease recovery sudah tersedia; multi-process coordination masih belum production-ready.
- **Audit:** event tersedia, tetapi belum ada schema versioning, retention, atau redaction menyeluruh.
- **Packaging:** package metadata dan CLI tersedia, tetapi belum ada release automation dan artifact publishing.

## Belum dikerjakan

### Reliability

- Retry/backoff — **selesai di v0.2**
- Idempotency key — **selesai di v0.2**
- Crash recovery task — **selesai di v0.2**
- Dead-letter queue — **selesai di Fase 1**
- Graceful worker draining — **selesai di Fase 1**

### Security dan governance

- Authentication dan API keys — **selesai di v0.4**
- RBAC company/agent — **selesai di v0.4**
- Secret vault dan secret redaction menyeluruh — **redaction selesai; vault belum**
- Approval gates — **selesai di v0.4**
- Agent pause/resume/terminate — **selesai di v0.4**

### Adapter dan integrasi

- Local subprocess adapter tanpa shell — **selesai di v0.3**
- HTTP/webhook adapter — **selesai di v0.3**
- OpenAI-compatible adapter — **selesai di v0.3**
- Git workspace integration
- Webhook notifications

### Produk dan operasi

- Dashboard web
- Live logs
- Multi-user support
- Export/import company
- Backup/restore
- Docker image
- Metrics, tracing, dan alerting

## Urutan milestone berikutnya

1. **v0.2 — Reliable queue:** lease, retry, idempotency, recovery test — **selesai**.
2. **v0.3 — Real adapters:** subprocess dan HTTP adapter dengan timeout serta OpenAI-compatible endpoint — **selesai**.
3. **v0.4 — Secure control plane:** authentication, RBAC, secret handling, approval — **selesai**.
4. **v0.5 — Operator dashboard:** monitoring company, agent, task, budget, dan audit — **berikutnya**.
5. **v1.0 — Production baseline:** migration, backup/restore, deployment, metrics, security review, dan dokumentasi operasional.
