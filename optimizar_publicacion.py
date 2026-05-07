"""
optimizar_publicacion.py
Agente de optimización de publicaciones MercadoLibre - Mevak
"""

import os
import json
import re
import time
import requests
import anthropic
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

# ── Configuración ──────────────────────────────────────────────────────────────
SITE_ID           = "MLC"
ML_CLIENT_ID      = os.getenv("ML_CLIENT_ID")
ML_SECRET_KEY     = os.getenv("ML_SECRET_KEY")
ML_REFRESH_TOKEN  = os.getenv("ML_REFRESH_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GMAIL_USER        = "jpdupouy@gmail.com"
GMAIL_PASSWORD    = os.getenv("GMAIL_APP_PASSWORD")
SHEET_ID          = "14fKlgaq3OZY1agJJWD3RJU4rRprsja36utDPnDzFI-A"
CREDENTIALS_FILE  = "credenciales_google.json"
TOP_N             = 30

DESTINATARIOS = [
    "jpdupouy@acuaia.com",
    "jpdupouy@gmail.com",
    "gcabello@acuaia.com",
    "fgonzalez@acuaia.com",
    "crodriguez@acuaia.com",
    "cdiaz@acuaia.com",
    "jcerda@acuaia.com",
]


# ── 1. Access Token MELI ───────────────────────────────────────────────────────
def get_access_token():
    print("Obteniendo access token MELI...")
    resp = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type":    "refresh_token",
            "client_id":     ML_CLIENT_ID,
            "client_secret": ML_SECRET_KEY,
            "refresh_token": ML_REFRESH_TOKEN,
        },
    )
    resp.raise_for_status()
    print("Access token obtenido")
    return resp.json()["access_token"]


# ── 2. Top 30 publicaciones por ventas ────────────────────────────────────────
def get_top_publicaciones(token, top_n=30):
    print(f"Trayendo publicaciones activas de {SITE_ID}...")
    resp = requests.get(
        "https://api.mercadolibre.com/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    user_id = resp.json()["id"]

    all_items = []
    offset = 0
    limit = 50
    while True:
        resp = requests.get(
            f"https://api.mercadolibre.com/users/{user_id}/items/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"status": "active", "limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        data = resp.json()
        item_ids = data.get("results", [])
        if not item_ids:
            break
        all_items.extend(item_ids)
        total = data.get("paging", {}).get("total", 0)
        offset += limit
        if offset >= total:
            break

    print(f"Total publicaciones activas: {len(all_items)}")

    items_detalle = []
    for i in range(0, len(all_items), 20):
        batch = all_items[i:i+20]
        ids_str = ",".join(batch)
        resp = requests.get(
            "https://api.mercadolibre.com/items",
            headers={"Authorization": f"Bearer {token}"},
            params={"ids": ids_str},
        )
        if resp.status_code == 200:
            for entry in resp.json():
                if entry.get("code") == 200:
                    items_detalle.append(entry["body"])

    items_detalle.sort(key=lambda x: x.get("sold_quantity", 0), reverse=True)
    top = items_detalle[:top_n]
    print(f"Top {top_n} publicaciones obtenidas")
    return top


# ── 3. Descripción ─────────────────────────────────────────────────────────────
def get_description(item_id, item_data, token):
    resp = requests.get(
        f"https://api.mercadolibre.com/items/{item_id}/description",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code == 200:
        texto = resp.json().get("plain_text", "").strip()
        if texto:
            return texto, "propia"

    catalog_product_id = item_data.get("catalog_product_id")
    if catalog_product_id:
        resp_cat = requests.get(
            f"https://api.mercadolibre.com/products/{catalog_product_id}/description",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp_cat.status_code == 200:
            texto = resp_cat.json().get("plain_text", "").strip()
            if texto:
                return texto, "catalogo"

    return "", "ninguna"


# ── 4. Keywords trending con cache ─────────────────────────────────────────────
_keywords_cache = {}

def get_trending_keywords(category_id, token):
    # category_id siempre es string — seguro como key
    key = str(category_id)
    if key in _keywords_cache:
        return _keywords_cache[key]
    resp = requests.get(
        f"https://api.mercadolibre.com/trends/{SITE_ID}/{key}",
        headers={"Authorization": f"Bearer {token}"},
    )
    keywords = []
    if resp.status_code == 200:
        data = resp.json()
        if isinstance(data, list):
            keywords = [t["keyword"] for t in data if isinstance(t, dict) and "keyword" in t]
    _keywords_cache[key] = keywords
    return keywords


# ── 5. Analizar con Claude ─────────────────────────────────────────────────────
def analizar_con_claude(titulo, descripcion, tipo_descripcion, keywords_trending, atributos, es_catalogo):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    nota_catalogo = ""
    if es_catalogo:
        nota_catalogo = "ATENCION: Publicacion de CATALOGO. El titulo y atributos los controla ML. El vendedor SI puede editar descripcion, precio y stock.\n"

    nota_descripcion = ""
    if tipo_descripcion == "ninguna":
        nota_descripcion = "ADVERTENCIA: Sin descripcion. Esto penaliza el posicionamiento.\n"
    elif tipo_descripcion == "catalogo":
        nota_descripcion = "NOTA: Descripcion es del catalogo ML, no del vendedor.\n"

    # Extraer atributos de forma segura
    atributos_simples = []
    for a in atributos:
        if isinstance(a, dict) and a.get("value_name"):
            atributos_simples.append(f"{a.get('id','')}: {a.get('value_name','')}")

    prompt = f"""Eres experto en optimizacion de publicaciones MercadoLibre Chile.
{nota_catalogo}{nota_descripcion}
TITULO ACTUAL: {titulo}
DESCRIPCION ACTUAL: {descripcion if descripcion else "Sin descripcion"}
KEYWORDS TRENDING: {", ".join(keywords_trending) if keywords_trending else "No disponibles"}
ATRIBUTOS: {"; ".join(atributos_simples[:15])}

INSTRUCCIONES CRITICAS:
- Responde SOLO con el formato indicado, sin texto adicional
- NO uses markdown, emojis, asteriscos ni formato especial
- SCORE_ACTUAL y SCORE_PROYECTADO: solo un numero entero sin texto adicional
- TITULO_SUGERIDO: maximo 60 caracteres, solo texto plano
- DESCRIPCION_SUGERIDA: texto plano sin emojis, sin asteriscos, sin HTML
- ACCION_PRIORITARIA: una sola oracion corta

SCORE_ACTUAL: [numero]
SCORE_PROYECTADO: [numero]
KEYWORDS_FALTANTES: [palabras separadas por comas]
TITULO_SUGERIDO: [maximo 60 caracteres]
DESCRIPCION_SUGERIDA: [texto plano, minimo 3 parrafos]
LIMITACIONES_CATALOGO: [texto plano o "No aplica"]
ACCION_PRIORITARIA: [una oracion]"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── 6. Parsear respuesta ───────────────────────────────────────────────────────
def limpiar_texto(texto):
    texto = re.sub(r'\*+', '', texto)
    texto = re.sub(r'#+\s*', '', texto)
    texto = re.sub(r'`+', '', texto)
    return texto.strip()

def extraer_numero(texto):
    match = re.search(r"(10|[1-9]|0)", texto)
    return int(match.group()) if match else 0

def parsear_analisis(texto):
    campos = ["SCORE_ACTUAL", "SCORE_PROYECTADO", "KEYWORDS_FALTANTES",
              "TITULO_SUGERIDO", "DESCRIPCION_SUGERIDA", "LIMITACIONES_CATALOGO",
              "ACCION_PRIORITARIA"]

    resultado = {}
    for i, campo in enumerate(campos):
        siguiente = campos[i+1] if i+1 < len(campos) else None
        if siguiente:
            patron = rf"{campo}:\s*(.+?)(?={siguiente}:)"
        else:
            patron = rf"{campo}:\s*(.+?)$"
        match = re.search(patron, texto, re.DOTALL)
        valor = limpiar_texto(match.group(1)) if match else ""
        resultado[campo.lower()] = valor

    resultado["score_actual"]     = str(extraer_numero(resultado.get("score_actual", "0")))
    resultado["score_proyectado"] = str(extraer_numero(resultado.get("score_proyectado", "0")))
    return resultado


# ── 7. Google Sheets ───────────────────────────────────────────────────────────
def get_sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds)

def escribir_fila(service, fila_data, pestana):
    fila = [[
        fila_data["item_id"],
        fila_data["sku"],
        fila_data["titulo_actual"],
        fila_data["titulo_sugerido"],
        fila_data["score_actual"],
        fila_data["score_proyectado"],
        fila_data["keywords_faltantes"],
        fila_data["descripcion_actual"],
        fila_data["descripcion_sugerida"],
        fila_data["limitaciones_catalogo"],
        fila_data["fecha"],
        "",
        "",
    ]]
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{pestana}!A:M",
        valueInputOption="RAW",
        body={"values": fila},
    ).execute()


# ── 8. Email ───────────────────────────────────────────────────────────────────
def enviar_email(resumen_items, fecha):
    print("Enviando email al equipo...")

    if not GMAIL_PASSWORD:
        print("ERROR: ACUAIA_EMAIL_PASSWORD no configurado — email omitido")
        return

    filas_html = ""
    for item in resumen_items:
        try:
            score = int(item["score_actual"])
        except:
            score = 0
        color = "#d4edda" if score >= 7 else "#fff3cd" if score >= 5 else "#f8d7da"
        titulo = item["titulo_actual"][:50] + "..." if len(item["titulo_actual"]) > 50 else item["titulo_actual"]
        filas_html += f"""
        <tr style="background:{color}">
            <td style="padding:8px;border:1px solid #ddd">{titulo}</td>
            <td style="padding:8px;border:1px solid #ddd;text-align:center">{item['score_actual']}/10</td>
            <td style="padding:8px;border:1px solid #ddd;text-align:center">{item['score_proyectado']}/10</td>
            <td style="padding:8px;border:1px solid #ddd">{item['accion_prioritaria']}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:900px;margin:0 auto;padding:20px">
    <h2 style="color:#e37222">Reporte Optimizacion Publicaciones Mevak - Chile</h2>
    <p>Fecha: <strong>{fecha}</strong> | Publicaciones analizadas: <strong>{len(resumen_items)}</strong></p>
    <p>Verde: score 7 o mas | Amarillo: entre 5 y 7 | Rojo: menos de 5</p>
    <table style="width:100%;border-collapse:collapse;margin-top:20px">
        <tr style="background:#e37222;color:white">
            <th style="padding:10px;border:1px solid #ddd;text-align:left">Publicacion</th>
            <th style="padding:10px;border:1px solid #ddd">Score Actual</th>
            <th style="padding:10px;border:1px solid #ddd">Score Proyectado</th>
            <th style="padding:10px;border:1px solid #ddd;text-align:left">Accion Prioritaria</th>
        </tr>
        {filas_html}
    </table>
    <p style="margin-top:20px">
        Ver analisis completo: https://docs.google.com/spreadsheets/d/{SHEET_ID}
    </p>
    <p style="color:#999;font-size:12px">Reporte generado automaticamente por Mevak Agente.</p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Mevak] Optimizacion Publicaciones Chile - {fecha}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = ", ".join(DESTINATARIOS)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASSWORD)
        server.sendmail(GMAIL_USER, DESTINATARIOS, msg.as_string())

    print("Email enviado al equipo")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("\nIniciando agente de optimizacion Mevak - Chile\n" + "="*55)
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")

    token      = get_access_token()
    top_items  = get_top_publicaciones(token, TOP_N)
    sheets_svc = get_sheets_service()
    resumen    = []

    print(f"\nAnalizando {len(top_items)} publicaciones...\n")

    for i, item in enumerate(top_items, 1):
        item_id     = item["id"]
        titulo      = item.get("title", "")
        category_id = str(item.get("category_id", ""))
        atributos   = item.get("attributes", [])
        ventas      = item.get("sold_quantity", 0)
        es_catalogo = bool(item.get("catalog_product_id"))
        sku         = next((a.get("value_name","") for a in atributos if isinstance(a, dict) and a.get("id") == "SELLER_SKU"), "")

        print(f"[{i}/{len(top_items)}] {titulo[:55]}... ({ventas} ventas)")

        try:
            descripcion, tipo_desc = get_description(item_id, item, token)
            keywords = get_trending_keywords(category_id, token)
            analisis = analizar_con_claude(titulo, descripcion, tipo_desc, keywords, atributos, es_catalogo)
            campos   = parsear_analisis(analisis)

            escribir_fila(sheets_svc, {
                "item_id":               item_id,
                "sku":                   sku,
                "titulo_actual":         titulo,
                "titulo_sugerido":       campos["titulo_sugerido"],
                "score_actual":          campos["score_actual"],
                "score_proyectado":      campos["score_proyectado"],
                "keywords_faltantes":    campos["keywords_faltantes"],
                "descripcion_actual":    descripcion,
                "descripcion_sugerida":  campos["descripcion_sugerida"],
                "limitaciones_catalogo": campos["limitaciones_catalogo"],
                "fecha":                 fecha,
            }, "Chile")

            resumen.append({
                "titulo_actual":      titulo,
                "score_actual":       campos["score_actual"],
                "score_proyectado":   campos["score_proyectado"],
                "accion_prioritaria": campos["accion_prioritaria"],
            })

            print(f"   Score: {campos['score_actual']}/10 -> {campos['score_proyectado']}/10")
            time.sleep(2)

        except Exception as e:
            print(f"   Error en {item_id}: {e}")
            continue

    print(f"\n{len(resumen)} publicaciones analizadas y escritas en Sheet")
    enviar_email(resumen, fecha)
    print("\nProceso completado!")


if __name__ == "__main__":
    main()
