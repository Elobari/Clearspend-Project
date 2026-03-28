"""
db.py
=====
Shared database engine for the ClearSpend pipeline.

All four pipeline modules (ingest, transform, warehouse, marts) import the
single engine instance from here so there is one consistent connection
configuration across the pipeline.

AUTHOR:     Jonah Knief (i6263747) | Arthem Vysotskyi (i6327809) | Lyan Eleraky
COURSE:     Data Engineering and Data Compliance
UNIVERSITY: Maastricht University
"""

import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

DB_URL = (
    f"postgresql+psycopg2://"
    f"{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME')}"
)

engine = create_engine(DB_URL, echo=False, connect_args={"client_encoding": "utf8"})
