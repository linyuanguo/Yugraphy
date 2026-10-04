import psycopg2
from app.core.config import settings

c = psycopg2.connect(
    dbname=settings.PG_DB, user=settings.PG_USER, password=settings.PG_PASSWORD,
    host=settings.PG_HOST, port=settings.PG_PORT,
)
cur = c.cursor()
cur.execute("SELECT status, count(*) FROM import_tasks GROUP BY status")
print(cur.fetchall())
c.close()
