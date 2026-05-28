"""
FASE 3: Junta todos los lotes en tramites_todos.json y alertas.json
"""
import json
import os
import glob
from datetime import datetime

def main():
    print(f"MERGE - {datetime.now()}")
    todos_tramites = []
    todas_alertas  = []

    for archivo in sorted(glob.glob("tramites_*_lote*.json")):
        print(f"  Leyendo {archivo}...")
        with open(archivo, encoding="utf-8") as f:
            data = json.load(f)
        todos_tramites.extend(data.get("tramites", []))
        todas_alertas.extend(data.get("alertas", []))

    print(f"\nTotal tramites: {len(todos_tramites)}")
    print(f"Total alertas:  {len(todas_alertas)}")

    with open("tramites_todos.json", "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(),
                   "total": len(todos_tramites),
                   "tramites": todos_tramites}, f, ensure_ascii=False, indent=2)

    with open("alertas.json", "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(),
                   "total_alertas": len(todas_alertas),
                   "alertas": sorted(todas_alertas, key=lambda x: x.get("horas",0), reverse=True),
                   "mensaje_resumen": f"{len(todas_alertas)} trámites con más de 24h sin movimiento."
                   }, f, ensure_ascii=False, indent=2)

    print("OK tramites_todos.json y alertas.json generados")
    print(f"COMPLETADO: {datetime.now()}")

if __name__ == "__main__":
    main()
