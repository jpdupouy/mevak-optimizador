import anthropic
import os
import json
import smtplib
import time
from mis_publicaciones_ml import resumen_para_agente
from ml_token_manager import buscar_precio_ml_oficial
import gspread
import warnings
warnings.filterwarnings("ignore")
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from tavily import TavilyClient
from google.oauth2.service_account import Credentials

# ── Clientes ──────────────────────────────────────────────────────────────
cliente_claude = anthropic.Anthropic()
cliente_tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file("credenciales_google.json", scopes=SCOPES)
gc = gspread.authorize(creds)
SHEET_ID  = "1gS9pVFuFkTeK4RU6f8yRD8e_SIiU71ZMYdFDYF2a8W4"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"

# ── Config ────────────────────────────────────────────────────────────────
COSTOS   = {"comision_ml": 0.183, "envio": 0.07, "publicidad": 0.106}
USD_CLP  = 950
MAX_CANDIDATOS = 10
MAX_USD  = 15.0
MARGEN_MIN = 0.27

SMTP = {
    "servidor": "smtp.gmail.com", "puerto": 587,
    "usuario": "jpdupouyc@gmail.com",
    "password": os.environ.get("GMAIL_APP_PASSWORD", "")
}
DESTINATARIOS = [
    "jpdupouy@acuaia.com", "jpdupouy@gmail.com",
    "gcabello@acuaia.com", "fgonzalez@acuaia.com", "crodriguez@acuaia.com", "cdiaz@acuaia.com", "jcerda@acuaia.com", "jpdupouy@gmail.com"
]
HISTORIAL_FILE = "historial_hunting.json"

# ── Historial ─────────────────────────────────────────────────────────────
def cargar_historial():
    if os.path.exists(HISTORIAL_FILE):
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"productos_analizados": [], "corridas": []}

def guardar_historial(h):
    with open(HISTORIAL_FILE, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)

def productos_recientes(h):
    corte = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    return [p["nombre"] for p in h["productos_analizados"] if p["fecha"] >= corte]

# ── FASE A: Claude selecciona candidatos (1 llamada) ──────────────────────
def fase_a_seleccionar_candidatos(excluidos, catalogo_mevak=""):
    print("\n  FASE A — Claude selecciona candidatos...")

    # Búsquedas de tendencia — Python las ejecuta directamente
    tendencias = []
    fuentes = [
        ("amazon_us",  "home kitchen gadgets trending"),
        ("tiktok",     "viral products ecommerce 2026"),
        ("amazon_eu",  "kitchen coffee accessories trending"),
        ("europa",     "trendhunter new products 2026"),
        ("pinterest",  "home organization wellness trending")
    ]

    urls_por_fuente = {}
    for fuente, query in fuentes:
        print(f"    -> Explorando {fuente}...")
        try:
            r = cliente_tavily.search(query=f"{query}", max_results=3, include_answer=True)
            contenido = "\n".join([x["content"][:300] for x in r.get("results", [])])
            urls = [x["url"] for x in r.get("results", []) if x.get("url")]
            urls_por_fuente[fuente] = urls
            urls_txt = "\n".join(urls[:3])
            tendencias.append(f"=== {fuente.upper()} ===\n{contenido}\nURLs fuente:\n{urls_txt}")
        except Exception as e:
            tendencias.append(f"=== {fuente.upper()} === Error: {e}")
            urls_por_fuente[fuente] = []
        time.sleep(1)

    tendencias_txt = "\n\n".join(tendencias)
    excluidos_txt  = ", ".join(excluidos) if excluidos else "ninguno"

    prompt = f"""Eres analista comercial de Mevak SpA, ecommerce que importa desde China y vende en MercadoLibre Chile.

Basándote en estas tendencias de hoy {datetime.now().strftime("%d/%m/%Y")}:

{tendencias_txt}

Selecciona exactamente 10 productos candidatos para importar. Criterios:
- Importable desde China, costo estimado ≤ ${MAX_USD} USD
- NO cosméticos con permiso ISP
- NO estos productos (analizados últimos 7 días): {excluidos_txt}
- Precio venta ideal en ML Chile: $10.000-$50.000 CLP
- Preferir productos con tracción real, no modas pasajeras

{catalogo_mevak}

Responde SOLO con JSON válido, sin explicaciones, sin markdown:
{{
  "candidatos": [  // exactamente 10 items
    {{
      "nombre_es": "nombre en español",
      "nombre_en": "nombre en inglés para buscar en AliExpress",
      "categoria": "categoría Amazon",
      "fuente_detectada": "donde lo encontraste (amazon_us/tiktok/pinterest/etc)",
      "link_fuente": "URL exacta donde detectaste el producto (de las URLs fuente provistas arriba)",
      "costo_estimado_usd": 5.0,
      "precio_estimado_ml_clp": 25000,
      "razon": "por qué es buena oportunidad para Mevak en una línea"
    }}
  ]
}}"""

    resp = cliente_claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    tokens = resp.usage.input_tokens + resp.usage.output_tokens
    print(f"    Tokens Fase A: {tokens:,}")

    try:
        texto = resp.content[0].text.strip()
        if "```" in texto:
            texto = texto.split("```")[1].replace("json","").strip()
        data = json.loads(texto)
        return data["candidatos"], tokens
    except Exception as e:
        print(f"    Error parseando JSON: {e}\n    Respuesta: {resp.content[0].text[:200]}")
        return [], tokens

# ── FASE B: Python recolecta precios y calcula márgenes ───────────────────
def fase_b_analizar_precios(candidatos):
    print("\n  FASE B — Python recolecta precios y calcula márgenes...")
    resultados = []

    for c in candidatos:
        print(f"\n    Analizando: {c['nombre_es']}")
        datos = {**c, "viable": False, "semaforo": "DESCARTAR",
                 "link_fuente": c.get("link_fuente", ""),
                 "link_aliexpress": "", "link_temu": "",
                 "fuente_compra": "", "costo_real_usd": 0,
                 "precio_real_ml_clp": 0, "margen_pct": 0,
                 "margen_neto_clp": 0, "motivo_descarte": ""}

        # Precio AliExpress
        try:
            print(f"      -> AliExpress...")
            r = cliente_tavily.search(
                query=f"aliexpress.com {c['nombre_en']} price buy",
                max_results=3, include_answer=True
            )
            ali_txt = "\n".join([x["content"][:200] for x in r.get("results", [])])
            ali_url = next((x["url"] for x in r.get("results", []) if "aliexpress" in x.get("url","")), "https://www.aliexpress.com/wholesale?SearchText=" + c["nombre_en"].replace(" ", "+"))
            datos["link_aliexpress"] = ali_url
            time.sleep(1)
        except:
            ali_txt = ""

        # Precio Temu
        try:
            print(f"      -> Temu...")
            r = cliente_tavily.search(
                query=f"temu.com {c['nombre_en']} price",
                max_results=2, include_answer=True
            )
            temu_txt = "\n".join([x["content"][:200] for x in r.get("results", [])])
            temu_url = next((x["url"] for x in r.get("results", []) if "temu" in x.get("url","")), "https://www.temu.com/search_result.html?search_key=" + c["nombre_en"].replace(" ", "+"))
            datos["link_temu"] = temu_url
            time.sleep(1)
        except:
            temu_txt = ""

        # Precio ML Chile
        try:
            print(f"      -> ML Chile...")
            r = cliente_tavily.search(
                query=f"mercadolibre.cl {c['nombre_es']} precio",
                max_results=3, include_answer=True
            )
            ml_txt = "\n".join([x["content"][:200] for x in r.get("results", [])])
            time.sleep(1)
        except:
            ml_txt = ""

        # Usar estimados del candidato si no encontramos precios reales
        costo_usd   = c.get("costo_estimado_usd", 8.0)
        flete_usd   = 5.0
        # Precio real de ML via API oficial
        ml_data = buscar_precio_ml_oficial(c["nombre_es"], pais="MLC")
        precio_clp = ml_data.get("precio_mediana") or ml_data.get("precio_promedio") or c.get("precio_estimado_ml_clp", 25000)

        # Verificar costo máximo
        if costo_usd > MAX_USD:
            datos["motivo_descarte"] = f"Costo ${costo_usd} USD supera máximo ${MAX_USD} USD"
            resultados.append(datos)
            continue

        # Calcular margen
        costo_clp   = (costo_usd + flete_usd) * USD_CLP
        comision    = precio_clp * COSTOS["comision_ml"]
        envio       = precio_clp * COSTOS["envio"]
        publicidad  = precio_clp * COSTOS["publicidad"]
        margen      = precio_clp - costo_clp - comision - envio - publicidad
        margen_pct  = (margen / precio_clp) * 100

        datos.update({
            "costo_real_usd": costo_usd,
            "flete_usd": flete_usd,
            "costo_total_clp": round(costo_clp),
            "precio_real_ml_clp": precio_clp,
            "comision_clp": round(comision),
            "envio_clp": round(envio),
            "publicidad_clp": round(publicidad),
            "margen_neto_clp": round(margen),
            "margen_pct": round(margen_pct, 1),
            "fuente_compra": "AliExpress",
            "link_compra": ali_url if datos["link_aliexpress"] else datos["link_temu"]
        })

        if margen_pct >= 35:
            datos["semaforo"] = "ESCALAR"
            datos["viable"]   = True
        elif margen_pct >= 27:
            datos["semaforo"] = "PROBAR"
            datos["viable"]   = True
        else:
            datos["semaforo"] = "DESCARTAR"
            datos["motivo_descarte"] = f"Margen {margen_pct:.1f}% bajo mínimo 27%"

        resultados.append(datos)
        print(f"      Margen: {margen_pct:.1f}% → {datos['semaforo']}")

    return resultados

# ── FASE C: Claude escribe análisis cualitativo (1 llamada) ───────────────
def fase_c_analisis_cualitativo(viables):
    if not viables:
        return {}, 0

    print("\n  FASE C — Claude redacta análisis cualitativo...")

    datos_min = [{"nombre": p["nombre_es"], "fuente": p["fuente_detectada"],
                  "margen": p["margen_pct"], "semaforo": p["semaforo"],
                  "razon_original": p["razon"]} for p in viables]

    prompt = f"""Para cada producto viable de Mevak, escribe exactamente 2 líneas de análisis comercial accionable.

Productos:
{json.dumps(datos_min, ensure_ascii=False)}

Responde SOLO con JSON válido:
{{
  "analisis": {{
    "nombre_producto": "2 líneas de análisis. Primera línea: por qué es oportunidad real. Segunda línea: acción concreta (cuántas unidades probar y a qué precio)."
  }}
}}"""

    resp = cliente_claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    tokens = resp.usage.input_tokens + resp.usage.output_tokens
    print(f"    Tokens Fase C: {tokens:,}")

    try:
        texto = resp.content[0].text.strip()
        if "```" in texto:
            texto = texto.split("```")[1].replace("json","").strip()
        return json.loads(texto).get("analisis", {}), tokens
    except:
        return {}, tokens

# ── FASE D: Python construye HTML y entrega ───────────────────────────────
def color_semaforo(semaforo):
    return {"ESCALAR": "#27ae60", "PROBAR": "#f39c12", "DESCARTAR": "#e74c3c"}.get(semaforo, "#999")

def emoji_semaforo(semaforo):
    return {"ESCALAR": "✅", "PROBAR": "⚠️", "DESCARTAR": "❌"}.get(semaforo, "")

def construir_html(viables, descartados, analisis):
    hoy = datetime.now().strftime("%d/%m/%Y")
    hora = datetime.now().strftime("%H:%M")

    # Sección productos viables
    html_viables = ""
    for p in viables:
        color  = color_semaforo(p["semaforo"])
        emoji  = emoji_semaforo(p["semaforo"])
        texto  = analisis.get(p["nombre_es"], p.get("razon", ""))
        link   = p.get("link_aliexpress") or p.get("link_temu", "#")
        fuente_label = "AliExpress" if p.get("link_aliexpress") else "Temu"

        html_viables += f"""
        <div style="border:1px solid #e0e0e0;border-left:5px solid {color};border-radius:8px;margin-bottom:20px;overflow:hidden;">
          <div style="background:{color}15;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;">
            <div>
              <div style="font-size:16px;font-weight:700;color:#1a1a2e;">{p['nombre_es']}</div>
              <div style="font-size:12px;color:#666;margin-top:3px;">📍 Detectado en: {p['fuente_detectada']} &nbsp;|&nbsp; 🛒 Compra: <a href="{link}" style="color:{color};">{fuente_label}</a></div>
            </div>
            <div style="background:{color};color:white;padding:6px 14px;border-radius:20px;font-weight:700;font-size:13px;">{p['semaforo']} {emoji}</div>
          </div>
          <div style="padding:16px 18px;">
            <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:12px;">
              <tr style="border-bottom:1px solid #f0f0f0;"><td style="padding:5px 0;color:#555;">Precio venta ML Chile</td><td style="text-align:right;font-weight:500;">${p['precio_real_ml_clp']:,.0f} CLP</td></tr>
              <tr style="border-bottom:1px solid #f0f0f0;"><td style="padding:5px 0;color:#555;">Costo {fuente_label} + flete</td><td style="text-align:right;font-weight:500;">${p['costo_total_clp']:,.0f} CLP (${p['costo_real_usd']} + ${p['flete_usd']} USD)</td></tr>
              <tr style="border-bottom:1px solid #f0f0f0;"><td style="padding:5px 0;color:#555;">Comisión ML (18.3%)</td><td style="text-align:right;">${p['comision_clp']:,.0f} CLP</td></tr>
              <tr style="border-bottom:1px solid #f0f0f0;"><td style="padding:5px 0;color:#555;">Envío (7%)</td><td style="text-align:right;">${p['envio_clp']:,.0f} CLP</td></tr>
              <tr style="border-bottom:1px solid #f0f0f0;"><td style="padding:5px 0;color:#555;">Publicidad (10.6%)</td><td style="text-align:right;">${p['publicidad_clp']:,.0f} CLP</td></tr>
              <tr style="border-top:2px solid #e0e0e0;"><td style="padding:8px 0;font-weight:700;">MARGEN NETO</td><td style="text-align:right;font-weight:700;color:{color};font-size:15px;">${p['margen_neto_clp']:,.0f} CLP &nbsp;|&nbsp; {p['margen_pct']}%</td></tr>
            </table>
            <div style="background:#e8f4fd;border-radius:6px;padding:10px 14px;font-size:13px;color:#1565c0;">
              <strong>📋 Análisis:</strong> {texto}
            </div>
          </div>
        </div>"""

    # Sección descartados
    html_descartados = ""
    for p in descartados:
        html_descartados += f"""
        <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f5f5f5;font-size:13px;">
          <span style="color:#444;">{p['nombre_es']}</span>
          <span style="color:#e74c3c;font-size:12px;">{p.get('motivo_descarte','No cumple criterios')}</span>
        </div>"""

    if not html_descartados:
        html_descartados = "<p style='color:#999;font-size:13px;'>Todos los candidatos fueron viables ✅</p>"

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#f0f2f5;font-family:Arial,sans-serif;">
<div style="max-width:620px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.1);">

  <!-- HEADER -->
  <div style="background:#1a1a2e;padding:24px 28px;">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="color:white;font-size:22px;font-weight:700;letter-spacing:1px;">ME<span style="color:#4fc3f7;">VAK</span> <span style="font-size:13px;font-weight:400;color:#90caf9;">Hunting Agent</span></div>
      <div style="color:#90caf9;font-size:12px;">{hoy} · {hora}</div>
    </div>
    <div style="color:#b0bec5;font-size:12px;margin-top:6px;">Reporte diario de oportunidades de producto</div>
  </div>

  <!-- RESUMEN -->
  <div style="background:#f8f9fa;padding:16px 28px;border-bottom:1px solid #e9ecef;display:flex;gap:32px;">
    <div style="text-align:center;"><div style="font-size:24px;font-weight:700;color:#1a1a2e;">3</div><div style="font-size:11px;color:#6c757d;">Analizados</div></div>
    <div style="text-align:center;"><div style="font-size:24px;font-weight:700;color:#27ae60;">{len(viables)}</div><div style="font-size:11px;color:#6c757d;">Viables</div></div>
    <div style="text-align:center;"><div style="font-size:24px;font-weight:700;color:#e74c3c;">{len(descartados)}</div><div style="font-size:11px;color:#6c757d;">Descartados</div></div>
    <div style="text-align:center;"><div style="font-size:24px;font-weight:700;color:#1a1a2e;">${(sum(p['costo_real_usd'] for p in viables)/len(viables) if viables else 0):.1f}</div><div style="font-size:11px;color:#6c757d;">Costo prom USD</div></div>
  </div>

  <!-- VIABLES -->
  <div style="padding:24px 28px;">
    <div style="font-size:11px;font-weight:700;color:#6c757d;text-transform:uppercase;letter-spacing:1px;margin-bottom:16px;">✅ Oportunidades viables</div>
    {html_viables if html_viables else '<p style="color:#999;font-size:13px;">Sin productos viables hoy — ver sección descartados.</p>'}
  </div>

  <!-- DESCARTADOS -->
  <div style="padding:0 28px 24px;">
    <div style="font-size:11px;font-weight:700;color:#6c757d;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">❌ Descartados hoy</div>
    <div style="background:#fafafa;border-radius:8px;padding:14px 16px;border:1px solid #e9ecef;">
      {html_descartados}
    </div>
  </div>

  <!-- FEEDBACK -->
  <div style="padding:0 28px 24px;">
    <div style="background:#e3f2fd;border-radius:8px;padding:16px;text-align:center;">
      <div style="font-weight:700;color:#1565c0;margin-bottom:8px;">📊 Registra tu decisión en el Sheet</div>
      <a href="{SHEET_URL}" style="background:#1565c0;color:white;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;">Abrir Google Sheet →</a>
      <div style="font-size:11px;color:#666;margin-top:8px;">Fernando 🇨🇱 · Cristián 🇨🇴 · Claudia 🇵🇪 — completen su columna</div>
    </div>
  </div>

  <!-- FOOTER -->
  <div style="background:#f8f9fa;padding:16px 28px;text-align:center;font-size:11px;color:#adb5bd;border-top:1px solid #e9ecef;">
    Generado por Mevak Hunting Agent · claude-haiku · {hoy} {hora}<br>
    Próximo reporte: mañana 07:00 AM
  </div>

</div>
</body>
</html>"""

def escribir_sheet(viables):
    try:
        sh = gc.open_by_key(SHEET_ID)
        ws = sh.get_worksheet(0)
        hoy = datetime.now().strftime("%d/%m/%Y")
        for p in viables:
            ws.append_row([
                hoy, p["nombre_es"], p["categoria"],
                f"${p['precio_real_ml_clp']:,.0f}",
                f"${p['costo_real_usd']:.2f}",
                f"{p['margen_pct']:.1f}%",
                p.get("link_fuente", ""),        # G: Link Oficial (fuente de la oportunidad)
                p.get("link_aliexpress", ""),    # H: Link AliExpress
                "", "", "", "", ""               # I..M: columnas de feedback del equipo
            ])
        return f"OK: {len(viables)} productos escritos"
    except Exception as e:
        return f"Error: {e}"

def enviar_email_html(html, viables):
    try:
        n = len(viables)
        asunto = f"🤖 Mevak Hunting {datetime.now().strftime('%d/%m')} — {n} oportunidad{'es' if n!=1 else ''} encontrada{'s' if n!=1 else ''}"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = SMTP["usuario"]
        msg["To"]      = ", ".join(DESTINATARIOS)
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(SMTP["servidor"], SMTP["puerto"]) as s:
            s.starttls()
            s.login(SMTP["usuario"], SMTP["password"])
            s.sendmail(SMTP["usuario"], DESTINATARIOS, msg.as_string())
        return True
    except Exception as e:
        print(f"    Error email: {e}")
        return False

def guardar_backup_local(viables, descartados):
    nombre = f"hunting_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    with open(nombre, "w", encoding="utf-8") as f:
        f.write(f"MEVAK HUNTING — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")
        f.write("="*50 + "\n\nVIABLES:\n")
        for p in viables:
            f.write(f"- {p['nombre_es']} | Margen: {p['margen_pct']}% | {p['semaforo']}\n")
        f.write("\nDESCARTADOS:\n")
        for p in descartados:
            f.write(f"- {p['nombre_es']} | {p.get('motivo_descarte','')}\n")
    return nombre

# ── MAIN ──────────────────────────────────────────────────────────────────
def correr_hunting():
    tokens_total = 0
    print(f"\n{'='*55}")
    print(f"  MEVAK HUNTING v3 — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"  Flujo: A(Claude) → B(Python) → C(Claude) → D(Python)")
    print(f"{'='*55}")

    historial = cargar_historial()
    excluidos = productos_recientes(historial)
    print(f"  Excluidos últimos 7 días: {len(excluidos)}\n")

    # Obtener catálogo actual de Mevak
    catalogo_mevak = resumen_para_agente()
    print(f"  Catálogo ML cargado")

    # Obtener catálogo actual de Mevak
    catalogo_mevak = resumen_para_agente()
    print(f"  Catálogo ML cargado")

    # FASE A — Claude selecciona candidatos
    candidatos, t_a = fase_a_seleccionar_candidatos(excluidos, catalogo_mevak)
    tokens_total += t_a
    if not candidatos:
        print("  Sin candidatos — abortando.")
        return

    print(f"\n  Candidatos seleccionados: {len(candidatos)}")
    for c in candidatos:
        print(f"    - {c['nombre_es']} ({c['fuente_detectada']})")

    # FASE B — Python analiza precios y márgenes
    resultados = fase_b_analizar_precios(candidatos)
    viables    = [r for r in resultados if r["viable"]]
    descartados = [r for r in resultados if not r["viable"]]

    print(f"\n  Viables: {len(viables)} | Descartados: {len(descartados)}")

    # FASE C — Claude escribe análisis cualitativo
    analisis, t_c = fase_c_analisis_cualitativo(viables)
    tokens_total += t_c

    # FASE D — Python construye y entrega
    print("\n  FASE D — Python construye y entrega...")

    html = construir_html(viables, descartados, analisis)

    # Sheet
    res_sheet = escribir_sheet(viables)
    print(f"    -> Sheet: {res_sheet}")

    # Email
    ok_email = enviar_email_html(html, viables)
    print(f"    -> Email: {'enviado ✅' if ok_email else 'error ❌'}")

    # Backup
    backup = guardar_backup_local(viables, descartados)
    print(f"    -> Backup: {backup}")

    # Actualizar historial
    hoy = datetime.now().strftime("%Y-%m-%d")
    for c in candidatos:
        historial["productos_analizados"].append({"nombre": c["nombre_es"], "fecha": hoy})
    historial["corridas"].append({
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tokens": tokens_total, "viables": len(viables)
    })
    guardar_historial(historial)

    print(f"\n{'='*55}")
    print(f"  Tokens totales: {tokens_total:,} (Fase A: {t_a:,} | Fase C: {t_c:,})")
    print(f"  Hunting completado — {datetime.now().strftime('%H:%M')}")
    print(f"{'='*55}\n")

correr_hunting()
