from fastapi import FastAPI
from fastapi.responses import JSONResponse
import json

app = FastAPI()

@app.get("/tramite/{numero}")
async def consultar_tramite(numero: str):
    try:
        with open("alertas.json", "r", encoding="utf-8") as f:
            datos = json.load(f)
        
        tramite = next((t for t in datos if str(t.get("tramite")) == str(numero)), None)
        
        if not tramite:
            return JSONResponse({"encontrado": False, "mensaje": f"Tramite #{numero} no encontrado"})
        
        return JSONResponse({"encontrado": True, "datos": tramite})
    
    except FileNotFoundError:
        return JSONResponse({"error": "Sin datos disponibles"}, status_code=503)

@app.get("/health")
async def health():
    return {"status": "ok"}
