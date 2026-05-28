"""
FASE 2: Extrae detalle de un rango de trámites
Uso: python 2_extraer_detalle.py <seccion> <lote> <total_lotes>
Ejemplo: python 2_extraer_detalle.py enviados 3 10
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from playwright.async_api import async_playwright

USUARIO    = os.environ.get("EDOC_USUARIO", "")
CONTRASENA = os.environ.get("EDOC_CONTRASENA", "")
CAS_LOGIN  = "https://egob.gadmriobamba.gob.ec:8443/cas/login"
BASE_URL   = "https://egobedoc.gadmriobamba.gob.ec:8081"
SERVICE    = "https://egobedoc.gadmriobamba.gob.ec:8081/auth/cas/callback?origin=https%3A%2F%2Fegobedoc.gadmriobamba.gob.ec%3A8081%2F"
HORAS_ALERTA = 24

NOMBRE_SECCION = {
    "entrada":     "Bandeja de entrada",
    "enviados":    "Enviados",
    "reasignados": "Reasignados"
}

def calcular_tiempo(fecha_str):
    if not fecha_str: return None, "Desconocido"
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"]:
            try:
                fecha = datetime.strptime(str(fecha_str).strip(), fmt)
                delta = datetime.now() - fecha
                h = delta.total_seconds() / 3600
                if h < 1:    return h, f"{int(delta.total_seconds()/60)}min"
                elif h < 24: return h, f"{int(h)}h {int((h%1)*60)}min"
                else:        return h, f"{int(h//24)}d {int(h%24)}h"
            except ValueError: continue
    except: pass
    return None, "Desconocido"

def extraer_movimientos(texto):
    movimientos = []
    patron = re.compile(
        r'#\d+\s*\n([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ\s\.]+?)\s*\(([^)]+)\)\s*\n'
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})[^\n]*\n(.*?)(?=#\d+\s*\n|\Z)', re.DOTALL)
    for m in patron.finditer(texto):
        contenido = m.group(4)
        nota_m = re.search(r'Nota:\s*(.+?)(?:\nAsignado|\nDocumento|\nAnadido|\nEncargo|\Z)', contenido, re.DOTALL)
        nota = nota_m.group(1).strip()[:300] if nota_m else ""
        enviado_a = ""
        for pat in [r'Asignado ha cambiado de .+? a ([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\n|$)',
                    r'Documento Enviado a ([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\n|$)']:
            mm = re.search(pat, contenido)
            if mm: enviado_a = mm.group(1).strip(); break
        movimientos.append({"nombre": m.group(1).strip(), "cargo": m.group(2).strip(),
                            "fecha": m.group(3).strip(), "nota": nota, "enviado_a": enviado_a})
    return movimientos

async def login(page):
    await page.goto(f"{CAS_LOGIN}?service={SERVICE}", timeout=60000, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.fill('input[name="username"]', USUARIO)
    await page.fill('input[name="password"]', CONTRASENA)
    await page.click('button[type="submit"], input[type="submit"]')
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(5000)
    if "egobedoc" not in page.url:
        raise Exception(f"Login fallido: {page.url}")

async def extraer_detalle(page, numero, seccion):
    r = {"numero": numero, "seccion": seccion, "descripcion": "", "estado_edoc": "",
         "movimientos": [], "horas_sin_movimiento": 0, "tiempo_texto": "—",
         "ultimo_movimiento": None, "error": None}
    try:
        await page.goto(f"{BASE_URL}/issues/{numero}", timeout=60000, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        for _ in range(4):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)
        texto = await page.inner_text("body")
        m = re.search(r"Asunto:\s*(.+?)(?:\n)", texto)
        if m: r["descripcion"] = m.group(1).strip()
        m = re.search(r"Estado:\s*(\w[\w\s]*?)(?:\n)", texto)
        if m: r["estado_edoc"] = m.group(1).strip()
        idx = max(texto.find("Historico"), texto.find("Hist"))
        if idx > 0: r["movimientos"] = extraer_movimientos(texto[idx:])
        if r["movimientos"]:
            u = r["movimientos"][-1]
            h, t = calcular_tiempo(u["fecha"])
            r["horas_sin_movimiento"] = round(h, 2) if h else 0
            r["tiempo_texto"] = t
            r["ultimo_movimiento"] = u
    except Exception as e:
        r["error"] = str(e)
    return r

async def main(seccion_key, lote, total_lotes):
    # Cargar números de esta sección
    with open(f"numeros_{seccion_key}.json") as f:
        data = json.load(f)
    numeros = data["numeros"]
    seccion = NOMBRE_SECCION[seccion_key]

    # Calcular rango de este lote
    total   = len(numeros)
    tam     = (total + total_lotes - 1) // total_lotes
    inicio  = (lote - 1) * tam
    fin     = min(lote * tam, total)
    mi_lote = numeros[inicio:fin]

    print(f"\n{'='*60}")
    print(f"{seccion_key.upper()} lote {lote}/{total_lotes}")
    print(f"Trámites {inicio+1}-{fin} de {total} ({len(mi_lote)} en este lote)")
    print(f"INICIO: {datetime.now()}")
    print(f"{'='*60}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors","--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await login(page)

        detalles = []
        alertas  = []
        for i, numero in enumerate(mi_lote, 1):
            print(f"[{i}/{len(mi_lote)}] #{numero}...")
            d = await extraer_detalle(page, numero, seccion)
            if not d["error"] and d["movimientos"]:
                u = d["movimientos"][-1]
                print(f"  {len(d['movimientos'])} movs | {d['tiempo_texto']}")
                if d["horas_sin_movimiento"] >= HORAS_ALERTA:
                    alertas.append({
                        "tramite": numero, "seccion": seccion,
                        "descripcion": d["descripcion"],
                        "responsable_actual": u.get("enviado_a") or u["nombre"],
                        "cargo": u["cargo"], "desde_cuando": u["fecha"],
                        "nota": u["nota"], "horas": round(d["horas_sin_movimiento"],1)
                    })
            detalles.append(d)

        await browser.close()

    archivo = f"tramites_{seccion_key}_lote{lote}.json"
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump({"seccion": seccion, "lote": lote, "total_lotes": total_lotes,
                   "tramites": detalles, "alertas": alertas}, f, ensure_ascii=False, indent=2)
    print(f"\nOK {archivo}: {len(detalles)} trámites, {len(alertas)} alertas")
    print(f"COMPLETADO: {datetime.now()}")

if __name__ == "__main__":
    seccion_key  = sys.argv[1]
    lote         = int(sys.argv[2])
    total_lotes  = int(sys.argv[3])
    asyncio.run(main(seccion_key, lote, total_lotes))
