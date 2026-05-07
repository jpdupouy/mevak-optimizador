import os
import json
from tavily import TavilyClient

cliente = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

productos = ["molinillo cafe", "filtro agua purificador", "hervidor electrico temperatura"]

for producto in productos:
    print(f"\n=== {producto} ===")
    r = cliente.search(
        query=f"site:mercadolibre.cl {producto} precio CLP",
        max_results=5,
        include_answer=True
    )
    print(f"Answer: {r.get('answer', 'N/A')}")
    for x in r.get("results", []):
        print(f"  URL: {x.get('url')}")
        print(f"  Content: {x.get('content', '')[:300]}")
        print()
