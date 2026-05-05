# mlss-contracts

Shared pydantic schemas used by both the MLSS server (`mlss_monitor/`) and the
Plant Grow Unit firmware (`grow_unit/`). Single source of truth — both packages
import from this one to guarantee message-shape compatibility.

Install (path dep, dev mode):
    poetry install
