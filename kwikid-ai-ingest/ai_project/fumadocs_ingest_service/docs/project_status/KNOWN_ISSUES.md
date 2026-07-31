# Known Issues & Technical Debt

## 🔴 Security
- [ ] Hardcoded Supabase service role key in `supabasesuccess.py`.
- [ ] Missing authentication layer on FastAPI endpoints.

## 🟠 Reliability
- [ ] Synchronous ingestion blocks API workers.
- [ ] Fixed-sleep rate limiting for Freshdesk (inefficient).

## 🟡 Technical Debt
- [ ] Dual reranking implementations (Python vs JS) are out of sync.
- [ ] Hardcoded developer paths in utility scripts.
```
