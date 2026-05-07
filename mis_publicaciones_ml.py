import urllib.parse
import urllib.request
import json
import os


def get_access_token(client_id, client_secret, refresh_token):
    """Obtiene access token usando refresh_token. Solo lectura."""
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }).encode()
    req = urllib.request.Request("https://api.mercadolibre.com/oauth/token", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]


def obtener_mis_publicaciones(user_id="1808552964", limit=50):
    """
    Obtiene las publicaciones activas de Mevak en MercadoLibre.
    Retorna resumen con título, precio, stock y ventas de cada producto.
    Solo lectura — no modifica nada en la cuenta.
    """
    client_id = os.environ.get("ML_CLIENT_ID")
    client_secret = os.environ.get("ML_SECRET_KEY")
    refresh_token = os.environ.get("ML_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        return {"error": "Faltan credenciales ML"}

    try:
        token = get_access_token(client_id, client_secret, refresh_token)
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Obtener IDs de publicaciones activas
        url = f"https://api.mercadolibre.com/users/{user_id}/items/search?status=active&limit={limit}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())

        item_ids = data.get("results", [])
        total = data.get("paging", {}).get("total", 0)

        if not item_ids:
            return {"total": 0, "publicaciones": []}

        # 2. Obtener detalle de cada publicación (en lotes de 20)
        publicaciones = []
        for i in range(0, len(item_ids), 20):
            lote = item_ids[i:i+20]
            ids_str = ",".join(lote)
            url_items = f"https://api.mercadolibre.com/items?ids={ids_str}&attributes=id,title,price,available_quantity,sold_quantity,status,category_id"
            req2 = urllib.request.Request(url_items)
            req2.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req2, timeout=10) as r:
                items_data = json.loads(r.read())

            for item in items_data:
                body = item.get("body", {})
                if body:
                    publicaciones.append({
                        "id": body.get("id"),
                        "titulo": body.get("title"),
                        "precio_clp": body.get("price"),
                        "stock": body.get("available_quantity"),
                        "vendidos": body.get("sold_quantity"),
                        "estado": body.get("status"),
                        "categoria": body.get("category_id")
                    })

        return {
            "total_activas": total,
            "consultadas": len(publicaciones),
            "publicaciones": publicaciones
        }

    except Exception as e:
        return {"error": str(e)}


def resumen_para_agente():
    """
    Retorna un texto compacto con el catálogo actual de Mevak,
    listo para incluir como contexto en el prompt del hunting agent.
    """
    data = obtener_mis_publicaciones()
    if "error" in data:
        return f"[Catálogo ML no disponible: {data['error']}]"

    lines = [f"CATÁLOGO ACTUAL MEVAK EN ML ({data['total_activas']} publicaciones activas):"]
    for p in data["publicaciones"]:
        lines.append(f"- {p['titulo']} | ${p['precio_clp']:,.0f} CLP | Stock: {p['stock']} | Vendidos: {p['vendidos']}")

    return "\n".join(lines)


if __name__ == "__main__":
    resultado = obtener_mis_publicaciones(limit=10)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
