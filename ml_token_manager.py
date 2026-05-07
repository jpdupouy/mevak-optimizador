"""
ml_token_manager.py — Auto-rotación del refresh token de MercadoLibre
Autor: generado para Jean Pierre Dupouy / Mevak SpA

Cada vez que se usa el refresh token, ML devuelve uno nuevo.
Este módulo obtiene el access token Y guarda el nuevo refresh token
en GitHub Secrets automáticamente, para que nunca expire.

Requiere en GitHub Secrets:
  ML_CLIENT_ID       — App ID de MercadoLibre
  ML_SECRET_KEY      — Client Secret de MercadoLibre
  ML_REFRESH_TOKEN   — Refresh token vigente (se actualiza solo)
  ML_TOKEN_ROTATOR   — GitHub Personal Access Token con permisos repo
  GITHUB_REPO        — nombre del repo, ej: jpdupouy/mevak-agente
"""

import urllib.request
import urllib.parse
import json
import os
import ssl
import base64

# ── Configuración ──────────────────────────────────────────────────────────

GITHUB_REPO = os.environ.get("GITHUB_REPO", "jpdupouy/mevak-agente")

def _ssl_context():
    """Contexto SSL compatible con Mac y Linux."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── Obtener Access Token con auto-rotación ─────────────────────────────────

def get_access_token_ml() -> tuple[str, str]:
    """
    Obtiene un access token de ML usando el refresh token.
    Retorna (access_token, nuevo_refresh_token).
    Guarda el nuevo refresh token en GitHub Secrets automáticamente.
    """
    client_id     = os.environ.get("ML_CLIENT_ID")
    client_secret = os.environ.get("ML_SECRET_KEY")
    refresh_token = os.environ.get("ML_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise EnvironmentError("Faltan credenciales ML: ML_CLIENT_ID, ML_SECRET_KEY o ML_REFRESH_TOKEN")

    data = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }).encode()

    req = urllib.request.Request(
        "https://api.mercadolibre.com/oauth/token",
        data=data, method="POST"
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")

    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
        result = json.loads(r.read())

    access_token   = result.get("access_token")
    new_refresh    = result.get("refresh_token")

    if not access_token:
        raise ValueError(f"ML no devolvió access_token: {result}")

    print(f"  ✅ ML access token obtenido.")

    # Guardar nuevo refresh token en GitHub Secrets
    if new_refresh and new_refresh != refresh_token:
        _actualizar_github_secret("ML_REFRESH_TOKEN", new_refresh)
        # Actualizar en el entorno actual para que el resto del script lo use
        os.environ["ML_REFRESH_TOKEN"] = new_refresh
        print(f"  🔄 ML refresh token rotado y guardado en GitHub Secrets.")
    
    return access_token, new_refresh


# ── Actualizar secret en GitHub ────────────────────────────────────────────

def _actualizar_github_secret(secret_name: str, secret_value: str):
    """Actualiza un secret en GitHub usando la API."""
    github_pat = os.environ.get("ML_TOKEN_ROTATOR")
    if not github_pat:
        print(f"  ⚠️  ML_TOKEN_ROTATOR no encontrado — refresh token no guardado en GitHub.")
        return

    # 1. Obtener la public key del repo para encriptar el secret
    pub_key_data = _github_get(f"https://api.github.com/repos/{GITHUB_REPO}/actions/secrets/public-key", github_pat)
    key_id  = pub_key_data["key_id"]
    pub_key = pub_key_data["key"]

    # 2. Encriptar el valor con la public key
    encrypted = _encrypt_secret(pub_key, secret_value)

    # 3. Hacer PUT para actualizar el secret
    payload = json.dumps({
        "encrypted_value": encrypted,
        "key_id": key_id
    }).encode()

    _github_put(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/secrets/{secret_name}",
        github_pat, payload
    )


def _github_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
        return json.loads(r.read())


def _github_put(url: str, token: str, payload: bytes):
    req = urllib.request.Request(url, data=payload, method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
        return r.status


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encripta el secret con la public key de GitHub usando PyNaCl."""
    from nacl import encoding, public
    pk = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder)
    box = public.SealedBox(pk)
    encrypted = box.encrypt(secret_value.encode())
    return base64.b64encode(encrypted).decode()


# ── Búsqueda de precios en ML con API oficial ──────────────────────────────

def buscar_precio_ml_oficial(producto: str, pais: str = "MLC") -> dict:
    """
    Busca precios reales en MercadoLibre usando la API oficial.
    Reemplaza scraper_ml.py (Tavily) para precios precisos.
    
    pais: MLC = Chile, MCO = Colombia, MPE = Perú
    """
    try:
        access_token, _ = get_access_token_ml()

        query = urllib.parse.quote(producto)
        url   = f"https://api.mercadolibre.com/sites/{pais}/search?q={query}&limit=20"

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Accept", "application/json")

        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
            data = json.loads(r.read())

        resultados = data.get("results", [])
        if not resultados:
            return {"error": "Sin resultados en ML"}

        precios = sorted([r["price"] for r in resultados if r.get("price")])
        total   = len(precios)
        tercio  = max(total // 3, 1)

        # Top 3 publicaciones para referencia
        top = [{"titulo": r["title"], "precio": r["price"], "url": r.get("permalink", "")}
               for r in resultados[:3]]

        return {
            "total_publicaciones": data.get("paging", {}).get("total", 0),
            "precio_minimo":   min(precios),
            "precio_maximo":   max(precios),
            "precio_promedio": round(sum(precios) / total),
            "precio_mediana":  precios[total // 2],
            "segmento_bajo":   round(sum(precios[:tercio]) / tercio),
            "segmento_medio":  round(sum(precios[tercio:tercio*2]) / tercio) if total > 3 else precios[total//2],
            "segmento_alto":   round(sum(precios[tercio*2:]) / len(precios[tercio*2:])) if len(precios[tercio*2:]) > 0 else max(precios),
            "top_publicaciones": top,
        }

    except Exception as e:
        return {"error": str(e)}


# ── Test directo ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    resultado = buscar_precio_ml_oficial("molinillo cafe")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
