import urllib.parse
import urllib.request
import json
import os
import base64


def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """
    Obtiene un Access Token usando refresh_token (Authorization Code flow).
    Este token está autorizado por contacto@acuaia.com y permite acceso
    de SOLO LECTURA a datos de MercadoLibre (búsquedas, precios, publicaciones).
    NO realiza acciones sobre la cuenta sin autorización explícita.
    """
    url = "https://api.mercadolibre.com/oauth/token"

    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }).encode()

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")

    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read())
        return result["access_token"]


def buscar_precio_ml_real(producto: str, pais: str = "MLC", client_id: str = None, client_secret: str = None, refresh_token: str = None) -> dict:
    """Busca precios reales en MercadoLibre usando la API oficial autenticada."""

    client_id = client_id or os.environ.get("ML_CLIENT_ID")
    client_secret = client_secret or os.environ.get("ML_SECRET_KEY")
    refresh_token = refresh_token or os.environ.get("ML_REFRESH_TOKEN")

    if not client_id or not client_secret or not refresh_token:
        return {"error": "Faltan credenciales ML_CLIENT_ID, ML_SECRET_KEY y/o ML_REFRESH_TOKEN"}

    try:
        token = get_access_token(client_id, client_secret, refresh_token)

        query = urllib.parse.quote(producto)
        url = f"https://api.mercadolibre.com/sites/{pais}/search?q={query}&limit=20"

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())

        resultados = data.get("results", [])
        if not resultados:
            return {"error": "Sin resultados"}

        precios = sorted([r["price"] for r in resultados if r.get("price")])
        total = len(precios)
        tercio = total // 3

        return {
            "total_publicaciones": data.get("paging", {}).get("total", 0),
            "precio_minimo": min(precios),
            "precio_maximo": max(precios),
            "precio_promedio": round(sum(precios) / total),
            "segmento_bajo": round(sum(precios[:tercio]) / tercio) if tercio > 0 else min(precios),
            "segmento_medio": round(sum(precios[tercio:tercio*2]) / tercio) if tercio > 0 else precios[total//2],
            "segmento_alto": round(sum(precios[tercio*2:]) / len(precios[tercio*2:])) if len(precios[tercio*2:]) > 0 else max(precios),
            "top_productos": [{"titulo": r["title"], "precio": r["price"]} for r in resultados[:5]]
        }

    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    resultado = buscar_precio_ml_real("molinillo cafe")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
