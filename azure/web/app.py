"""Read-only aggregate dashboard for the approved lab tenant."""
from contextlib import closing
from flask import Flask, render_template_string

from database import connect

app = Flask(__name__)
PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Inventory projection</title>
<main><h1>Inventory projection</h1><p>Latest committed state by tenant. Retained tombstones are counted separately.</p>
<table><caption>Current inventory</caption><thead><tr><th>Tenant</th><th>Active products</th>
<th>Stock units</th><th>Retained deletes</th></tr></thead><tbody>
{% for tenant,active,stock,deleted in rows %}<tr><th>{{tenant}}</th><td>{{active}}</td><td>{{stock}}</td><td>{{deleted}}</td></tr>
{% else %}<tr><td colspan="4">No products projected yet.</td></tr>{% endfor %}</tbody></table></main></html>"""


@app.get("/")
def overview():
    with closing(connect()) as db, closing(db.cursor()) as cur:
        rows = cur.execute("SELECT tenant_id,active_products,stock_units,retained_deletes FROM dbo.InventoryTotals ORDER BY tenant_id").fetchall()
    return render_template_string(PAGE, rows=rows)
