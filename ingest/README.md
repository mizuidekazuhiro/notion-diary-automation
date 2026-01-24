# ingest

収集データの書き込み（ensure/upsert/更新）を扱うモジュール群を配置します。

- `ensure_daily_log_page.py`: Daily_Log の存在保証（Phase A-0）
- `ingest_sources.py`: コネクタを順に実行してDaily_Logへ反映（Phase A-1）
