import sys
sys.path.append('.')
from tools.db_search import _get_connection, _return_connection

conn = _get_connection()
cursor = conn.cursor()
cursor.execute("SELECT DISTINCT lower(categoria) as cat FROM \"produtos-sp-queiroz\" WHERE lower(categoria) LIKE '%fruta%'")
rows = cursor.fetchall()
for r in rows:
    print(r['cat'])
_return_connection(conn)
