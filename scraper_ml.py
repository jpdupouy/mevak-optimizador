import re
import os
import time
from tavily import TavilyClient

_cliente = None

def get_cliente():
    global _cliente
    if _cliente is None:
        _cliente = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _cliente


def buscar_precio_ml_real(producto: str, pais: str = "cl") -> dict:
    """
    Obtiene precios reales de MercadoLibre usando Tavily con site: operator.
    No requiere autenticación ML. Solo lectura de datos públicos.
    
    Args:
        producto: nombre del producto en español
        pais: cl=Chile, co=Colombia, pe=Perú
    """
    dominios = {
        "cl": "mercadolibre.cl",
        "co": "mercadolibre.com.co", 
        "pe": "mercadolibre.com.pe"
    }
    dominio = dominios.get(pais, "mercadolibre.cl")
    
    try:
        cliente = get_cliente()
        r = cliente.search(
            query=f"site:{dominio} {producto} precio CLP",
            max_results=5,
            include_answer=True
        )
        
        # Extraer precios del contenido
        precios = []
        todo_texto = ""
        
        # Incluir answer y contenido de resultados
        if r.get("answer"):
            todo_texto += r["answer"] + " "
        for x in r.get("results", []):
            todo_texto += x.get("content", "") + " "
        
        # Patrones de precio en CLP
        # Formato: $XX.XXX o XX.XXX CLP o XX,XXX
        patrones = [
            r'\$\s*([\d]{1,3}(?:[.,]\d{3})+)',  # $XX.XXX o $XX,XXX
            r'([\d]{1,3}(?:\.\d{3})+)\s*(?:CLP|clp)',  # XXXXX CLP
            r'desde\s*\$?\s*([\d]{1,3}(?:[.,]\d{3})+)',  # desde $XX.XXX
        ]
        
        for patron in patrones:
            matches = re.findall(patron, todo_texto)
            for m in matches:
                try:
                    precio = int(m.replace(".", "").replace(",", ""))
                    if 1000 <= precio <= 5000000:
                        precios.append(precio)
                except:
                    pass
        
        # También extraer números simples que parecen precios CLP
        numeros = re.findall(r'\b(\d{4,7})\b', todo_texto)
        for n in numeros:
            try:
                precio = int(n)
                if 3000 <= precio <= 500000:
                    precios.append(precio)
            except:
                pass

        precios = sorted(list(set(precios)))
        
        if not precios:
            return {
                "error": "Sin precios encontrados",
                "producto": producto,
                "answer": r.get("answer", "")
            }
        
        total = len(precios)
        tercio = max(total // 3, 1)
        
        return {
            "producto": producto,
            "pais": pais,
            "total_precios": total,
            "precio_minimo": min(precios),
            "precio_maximo": max(precios),
            "precio_promedio": round(sum(precios) / total),
            "precio_mediana": precios[total // 2],
            "segmento_bajo": round(sum(precios[:tercio]) / tercio),
            "segmento_medio": round(sum(precios[tercio:tercio*2]) / tercio) if total > 3 else precios[total//2],
            "segmento_alto": round(sum(precios[tercio*2:]) / len(precios[tercio*2:])) if len(precios[tercio*2:]) > 0 else max(precios),
        }

    except Exception as e:
        return {"error": str(e), "producto": producto}


if __name__ == "__main__":
    import json
    productos = ["molinillo cafe", "filtro agua purificador", "hervidor electrico temperatura"]
    for p in productos:
        resultado = buscar_precio_ml_real(p)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        print()
        time.sleep(2)
