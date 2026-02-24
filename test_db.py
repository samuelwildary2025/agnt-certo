import sys
sys.path.append('.')
from tools.db_search import _get_connection, _return_connection
conn = _get_connection()
cursor = conn.cursor()
cursor.execute("SELECT id, nome, categoria, preco, estoque, inativo FROM produtos WHERE nome ILIKE '%banana%'")
rows = cursor.fetchall()
for r in rows:
    print(r)
_return_connection(conn)
