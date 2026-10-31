"""The document library: the stored-documents domain the dashboard, the search route and the assistant share.

    config.py   LibraryConfig, EmbeddingConfig (sections of config/model_config.yml)
    schema.py   API models: documents, forms, checks, reviews, search, stats
    ingest.py   uploaded file -> DocumentDraft (forms, page sizes, chunks)
    store.py    SQLite schema and queries (documents, forms, reviews, chunks)
    search.py   embeddings and hybrid (vector + keyword) search over chunks
    files.py    original uploads on disk, page rendering
    review.py   checks, corrected fields, approval rules
    service.py  `Library`: the use cases routes call

Dependency direction: api -> agent -> library -> core.
"""
