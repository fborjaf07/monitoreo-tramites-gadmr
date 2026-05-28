import asyncio
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright

# ============================================================
# CONFIGURACIÓN
# ============================================================
USUARIO    = os.environ.get("EDOC_USUARIO", "")
CONTRASENA = os.environ.get("EDOC_CONTRASENA", "")
HORAS_ALERTA = 24

ARCHIVO_JSON    = "tramites_todos.json"
ARCHIVO_ALERTAS = "alertas.json"

LOGIN_URL = (
    "https://egob.gadmriobamba.gob.ec:8443/cas/login"
    "?service=https%3A%2F%2Fegobedoc.gadmriobamba.gob.ec%3A8081"
    "%2Fauth%2Fcas%2Fcallback%3Forigin%3Dhttps%253A%252F%252F"
    "egobedoc.gadmriobamba.gob.ec%253A8081%252F"
)
BASE_URL = "https://egobedoc.gadmriobamba.gob.ec:8081"

# URLs reales del e-DOC GADMR
SECCIONES = {
    "Bandeja de entrada": f"{BASE_URL}/my/passig",
    "Enviados":           f"{BASE_URL}/my/pmy",
    "Reasignados":        f"{BASE_URL}/my/preasigned",
}

# ============================================================
# HELPERS
# ============================================================
def calcular_tiempo(fecha_str):
    if not fecha_str:
        return None, "Desconocido"
    try:
        formatos = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                    "%d/%m/%Y %H:%M", "%d/%m/%Y"]
        fecha = None
        for fmt in formatos:
            try:
                fecha = datetime.strptime(str(fecha_str).strip(), fmt)
                break
            except ValueError:
                continue
        if not fecha:
            return None, "Desconocido"
        delta = datetime.now() - fecha
        horas = delta.total_seconds() / 3600
        if horas < 1:
            return horas, f"{int(delta.total_seconds()/60)}min"
        elif horas < 24:
            return horas, f"{int(horas)}h {int((horas%1)*60)}min"
        else:
            dias = int(horas // 24)
            hrs  = int(horas % 24)
            return horas, f"{dias}d {hrs}h"
    except Exception:
        return None, "Desconocido"


def extraer_movimientos(historico_text):
    movimientos = []
    patron = re.compile(
        r'#\d+\s*\n([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ\s\.]+?)\s*\(([^)]+)\)\s*\n'
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})[^\n]*\n(.*?)(?=#\d+\s*\n|\Z)',
        re.DOTALL
    )
    for m in patron.finditer(historico_text):
        nombre    = m.group(1).strip()
        cargo     = m.group(2).strip()
        fecha     = m.group(3).strip()
        contenido = m.group(4)

        nota_match = re.search(
            r'Nota:\s*(.+?)(?:\nAsignado|\nDocumento|\nAñadido|\nEncargo|\Z)',
            contenido, re.DOTALL
        )
        nota = nota_match.group(1).strip()[:300] if nota_match else ""

        enviado_a = ""
        asig = re.search(
            r'Asignado ha cambiado de .+? a ([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\n|$)',
            contenido
        )
        if asig:
            enviado_a = asig.group(1).strip()
        else:
            env = re.search(
                r'Documento Enviado a ([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\n|$)',
                contenido
            )
            if env:
                enviado_a = env.group(1).strip()

        movimientos.append({
            "nombre":    nombre,
            "cargo":     cargo,
            "fecha":     fecha,
            "nota":      nota,
            "enviado_a": enviado_a
        })
    return movimientos


# ============================================================
# SCRAPING DE LISTA (una sección)
# ============================================================
async def extraer_lista_seccion(page, seccion, url_base):
    numeros = []
    vistos  = set()
    pagina  = 1

    while True:
        sep = '&' if '?' in url_base else '?'
        url = f"{url_base}{sep}page={pagina}"
        print(f"  [{seccion}] Página {pagina} -> {url}")
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(3000)

        contenido = await page.content()
        encontrados = re.findall(r'/issues/(\d+)', contenido)
        nuevos = [n for n in dict.fromkeys(encontrados) if n not in vistos]

        if not nuevos:
            print(f"  [{seccion}] Sin trámites en página {pagina}")
            break

        for num in nuevos:
            vistos.add(num)
            numeros.append({
                "numero":      num,
                "seccion":     seccion,
                "descripcion": ""
            })

        print(f"  [{seccion}] Página {pagina}: {len(nuevos)} nuevos (total: {len(numeros)})")

        tiene_siguiente = bool(re.search(
            r'page=' + str(pagina + 1) + r'|rel=["\']next["\']',
            contenido
        ))
        if not tiene_siguiente or pagina >= 100:
            break

        pagina += 1
        await page.wait_for_timeout(1000)

    print(f"  [{seccion}] TOTAL: {len(numeros)} trámites")
    return numeros


# ============================================================
# SCRAPING DE DETALLE (un trámite)
# ============================================================
async def extraer_detalle(page, numero, seccion):
    resultado = {
        "numero":               numero,
        "seccion":              seccion,
        "descripcion":          "",
        "estado_edoc":          "",
        "movimientos":          [],
        "horas_sin_movimiento": 0,
        "tiempo_texto":         "—",
        "ultimo_movimiento":    None,
        "error":                None
    }

    try:
        url = f"{BASE_URL}/issues/{numero}"
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2500)

        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(800)

        texto = await page.inner_text("body")

        asunto_m = re.search(r"Asunto:\s*(.+?)(?:\n)", texto)
        if asunto_m:
            resultado["descripcion"] = asunto_m.group(1).strip()

        estado_m = re.search(r"Estado:\s*(\w[\w\s]*?)(?:\n)", texto)
        if estado_m:
            resultado["estado_edoc"] = estado_m.group(1).strip()

        idx_hist = texto.find("Histórico")
        if idx_hist == -1:
            idx_hist = texto.find("Historico")

        if idx_hist > 0:
            historico_text = texto[idx_hist:]
            movimientos = extraer_movimientos(historico_text)
            resultado["movimientos"] = movimientos

        if resultado["movimientos"]:
            ultimo = resultado["movimientos"][-1]
            horas, tiempo_txt = calcular_tiempo(ultimo["fecha"])
            resultado["horas_sin_movimiento"] = round(horas, 2) if horas else 0
            resultado["tiempo_texto"]         = tiempo_txt
            resultado["ultimo_movimiento"]    = ultimo

    except Exception as e:
        resultado["error"] = str(e)

    return resultado


# ============================================================
# EJECUCIÓN PRINCIPAL
# ============================================================
async def ejecutar():
    print(f"\n{'='*60}")
    print(f"INICIO: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    if not USUARIO or not CONTRASENA:
        print("ERROR: Variables EDOC_USUARIO y EDOC_CONTRASENA no configuradas.")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--ignore-certificate-errors",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )
        context = await browser.new_context(ignore_https_errors=True)
        page    = await context.new_page()

        # LOGIN
        print("Iniciando sesión...")
        await page.goto(LOGIN_URL, timeout=60000)
        await page.wait_for_load_state("networkidle")
        await page.fill('input[name="username"]', USUARIO)
        await page.fill('input[name="password"]', CONTRASENA)
        await page.click('input[type="submit"], button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)

        # Verificar login exitoso
        url_actual = page.url
        print(f"URL después del login: {url_actual}")
        print("Sesión iniciada.\n")

        # FASE 1: DESCUBRIMIENTO
        print("FASE 1 — Descubriendo trámites por sección...\n")
        todos_lista = []
        vistos      = set()

        for seccion, url in SECCIONES.items():
            items = await extraer_lista_seccion(page, seccion, url)
            for item in items:
                if item["numero"] not in vistos:
                    vistos.add(item["numero"])
                    todos_lista.append(item)

        print(f"\nTotal único de trámites descubiertos: {len(todos_lista)}\n")

        # FASE 2: DETALLE
        print("FASE 2 — Extrayendo detalle de cada trámite...\n")
        tramites_detalle = []
        alertas          = []

        for i, item in enumerate(todos_lista, 1):
            print(f"[{i}/{len(todos_lista)}] #{item['numero']} ({item['seccion']})...")
            detalle = await extraer_detalle(page, item["numero"], item["seccion"])

            if detalle["error"]:
                print(f"  ERROR: {detalle['error']}")
            else:
                movs = detalle["movimientos"]
                if movs:
                    ultimo = movs[-1]
                    print(f"  OK {len(movs)} movs | {ultimo['nombre']} | {detalle['tiempo_texto']}")
                    if detalle["horas_sin_movimiento"] >= HORAS_ALERTA:
                        alertas.append({
                            "tramite":            item["numero"],
                            "seccion":            item["seccion"],
                            "descripcion":        detalle["descripcion"],
                            "responsable_actual": ultimo.get("enviado_a") or ultimo["nombre"],
                            "cargo":              ultimo["cargo"],
                            "desde_cuando":       ultimo["fecha"],
                            "nota":               ultimo["nota"],
                            "horas":              round(detalle["horas_sin_movimiento"], 1)
                        })
                else:
                    print(f"  OK sin movimientos")

            tramites_detalle.append(detalle)

        await browser.close()

    # GUARDAR JSON
    payload = {
        "timestamp": datetime.now().isoformat(),
        "total":     len(tramites_detalle),
        "tramites":  tramites_detalle
    }
    with open(ARCHIVO_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nOK {ARCHIVO_JSON} guardado ({len(tramites_detalle)} trámites)")

    alertas_payload = {
        "timestamp":     datetime.now().isoformat(),
        "total_alertas": len(alertas),
        "alertas":       alertas,
        "mensaje_resumen": (
            f"ALERTA TRAMITES — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            f"{len(alertas)} trámite(s) sin movimiento por más de {HORAS_ALERTA}h."
        ) if alertas else "Sin alertas activas."
    }
    with open(ARCHIVO_ALERTAS, "w", encoding="utf-8") as f:
        json.dump(alertas_payload, f, ensure_ascii=False, indent=2)
    print(f"OK {ARCHIVO_ALERTAS} guardado ({len(alertas)} alertas)")
    print(f"\nCOMPLETADO: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(ejecutar())
