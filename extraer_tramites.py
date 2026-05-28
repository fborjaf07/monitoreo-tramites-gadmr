import asyncio
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright

USUARIO    = os.environ.get("EDOC_USUARIO", "")
CONTRASENA = os.environ.get("EDOC_CONTRASENA", "")
HORAS_ALERTA = 24
ARCHIVO_JSON    = "tramites_todos.json"
ARCHIVO_ALERTAS = "alertas.json"

CAS_LOGIN = "https://egob.gadmriobamba.gob.ec:8443/cas/login"
BASE_URL  = "https://egobedoc.gadmriobamba.gob.ec:8081"
SERVICE   = "https://egobedoc.gadmriobamba.gob.ec:8081/auth/cas/callback?origin=https%3A%2F%2Fegobedoc.gadmriobamba.gob.ec%3A8081%2F"

SECCIONES = {
    "Bandeja de entrada": f"{BASE_URL}/my/passig",
    "Enviados":           f"{BASE_URL}/my/pmy",
    "Reasignados":        f"{BASE_URL}/my/preasigned",
}

# ============================================================
def calcular_tiempo(fecha_str):
    if not fecha_str:
        return None, "Desconocido"
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"]:
            try:
                fecha = datetime.strptime(str(fecha_str).strip(), fmt)
                delta = datetime.now() - fecha
                h = delta.total_seconds() / 3600
                if h < 1:   return h, f"{int(delta.total_seconds()/60)}min"
                elif h < 24: return h, f"{int(h)}h {int((h%1)*60)}min"
                else:        return h, f"{int(h//24)}d {int(h%24)}h"
            except ValueError:
                continue
    except Exception:
        pass
    return None, "Desconocido"

def extraer_movimientos(texto):
    movimientos = []
    patron = re.compile(
        r'#\d+\s*\n([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ\s\.]+?)\s*\(([^)]+)\)\s*\n'
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})[^\n]*\n(.*?)(?=#\d+\s*\n|\Z)',
        re.DOTALL
    )
    for m in patron.finditer(texto):
        contenido = m.group(4)
        nota_m = re.search(r'Nota:\s*(.+?)(?:\nAsignado|\nDocumento|\nAnadido|\nEncargo|\Z)', contenido, re.DOTALL)
        nota = nota_m.group(1).strip()[:300] if nota_m else ""
        enviado_a = ""
        for pat in [r'Asignado ha cambiado de .+? a ([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\n|$)',
                    r'Documento Enviado a ([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\n|$)']:
            mm = re.search(pat, contenido)
            if mm: enviado_a = mm.group(1).strip(); break
        movimientos.append({
            "nombre": m.group(1).strip(), "cargo": m.group(2).strip(),
            "fecha": m.group(3).strip(), "nota": nota, "enviado_a": enviado_a
        })
    return movimientos

# ============================================================
async def login(page):
    """Login CAS con manejo correcto de redirecciones"""
    print("Navegando al login CAS...")
    await page.goto(f"{CAS_LOGIN}?service={SERVICE}", timeout=60000, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    print(f"URL login: {page.url}")

    # Llenar credenciales
    await page.fill('input[name="username"]', USUARIO)
    await page.fill('input[name="password"]', CONTRASENA)

    # Click y esperar redirección completa al e-DOC
    async with page.expect_navigation(url=f"*egobedoc*", timeout=30000):
        await page.click('input[type="submit"], button[type="submit"], button[name="submit"]')

    # Esperar que el e-DOC procese la sesión completamente
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(4000)

    url_final = page.url
    print(f"URL post-login: {url_final}")

    # Verificar que llegamos al e-DOC
    if "egobedoc" not in url_final:
        raise Exception(f"Login fallido - URL inesperada: {url_final}")

    # Navegar a la raíz para consolidar cookies
    await page.goto(BASE_URL + "/", timeout=30000, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    texto = await page.inner_text("body")
    print(f"Body en /: {texto[:200].replace(chr(10),' ')}")
    print("Login completado OK")

async def extraer_lista_seccion(page, seccion, url):
    numeros = []
    vistos  = set()
    pagina  = 1

    while True:
        sep  = '&' if '?' in url else '?'
        purl = f"{url}{sep}page={pagina}"
        print(f"  [{seccion}] pag {pagina} -> {purl}")

        await page.goto(purl, timeout=60000, wait_until="networkidle")
        await page.wait_for_timeout(4000)

        url_actual = page.url
        texto      = await page.inner_text("body")
        contenido  = await page.content()

        print(f"  URL actual: {url_actual}")
        print(f"  Body[0:200]: {texto[:200].replace(chr(10),' ')}")

        # Si nos redirigió al login, session expiró
        if "cas/login" in url_actual or "Enter Username" in texto:
            print(f"  SESION EXPIRADA - redirigido al login")
            break

        # Buscar números de trámite de 5-8 dígitos
        nums = re.findall(r'\b(\d{5,8})\b', texto)
        # Filtrar números que parecen años, IPs, etc.
        nums = [n for n in nums if not n.startswith('20') or len(n) > 4]
        nums_href = re.findall(r'/issues/(\d+)', contenido)
        nums_data = re.findall(r'data-id=["\'](\d{5,8})["\']', contenido)

        todos  = list(dict.fromkeys(nums_href + nums_data + nums))
        nuevos = [n for n in todos if n not in vistos]

        print(f"  Encontrados: href={len(nums_href)} data={len(nums_data)} texto={len(nums)} nuevos={len(nuevos)}")

        if not nuevos:
            break

        for n in nuevos:
            vistos.add(n)
            numeros.append({"numero": n, "seccion": seccion, "descripcion": ""})

        print(f"  [{seccion}] pag {pagina}: {len(nuevos)} nuevos (total {len(numeros)})")

        if f'page={pagina+1}' not in contenido and 'rel="next"' not in contenido:
            break
        pagina += 1
        await page.wait_for_timeout(1000)

    print(f"  [{seccion}] TOTAL: {len(numeros)}")
    return numeros

async def extraer_detalle(page, numero, seccion):
    r = {"numero": numero, "seccion": seccion, "descripcion": "",
         "estado_edoc": "", "movimientos": [], "horas_sin_movimiento": 0,
         "tiempo_texto": "—", "ultimo_movimiento": None, "error": None}
    try:
        await page.goto(f"{BASE_URL}/issues/{numero}", timeout=60000, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(600)
        texto = await page.inner_text("body")
        m = re.search(r"Asunto:\s*(.+?)(?:\n)", texto)
        if m: r["descripcion"] = m.group(1).strip()
        m = re.search(r"Estado:\s*(\w[\w\s]*?)(?:\n)", texto)
        if m: r["estado_edoc"] = m.group(1).strip()
        idx = max(texto.find("Historico"), texto.find("Hist"))
        if idx > 0:
            r["movimientos"] = extraer_movimientos(texto[idx:])
        if r["movimientos"]:
            u = r["movimientos"][-1]
            h, t = calcular_tiempo(u["fecha"])
            r["horas_sin_movimiento"] = round(h, 2) if h else 0
            r["tiempo_texto"] = t
            r["ultimo_movimiento"] = u
    except Exception as e:
        r["error"] = str(e)
    return r

# ============================================================
async def ejecutar():
    print(f"\n{'='*60}\nINICIO: {datetime.now()}\n{'='*60}\n")
    if not USUARIO or not CONTRASENA:
        print("ERROR: Credenciales no configuradas"); return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors","--no-sandbox",
                  "--disable-setuid-sandbox","--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        await login(page)

        print("\nFASE 1 — Descubriendo tramites...\n")
        todos  = []
        vistos = set()
        for sec, url in SECCIONES.items():
            items = await extraer_lista_seccion(page, sec, url)
            for item in items:
                if item["numero"] not in vistos:
                    vistos.add(item["numero"])
                    todos.append(item)

        print(f"\nTotal descubiertos: {len(todos)}\n")

        print("FASE 2 — Detalle de cada tramite...\n")
        detalles = []
        alertas  = []
        for i, item in enumerate(todos, 1):
            print(f"[{i}/{len(todos)}] #{item['numero']}...")
            d = await extraer_detalle(page, item["numero"], item["seccion"])
            if not d["error"] and d["movimientos"]:
                u = d["movimientos"][-1]
                print(f"  OK {len(d['movimientos'])} movs | {d['tiempo_texto']}")
                if d["horas_sin_movimiento"] >= HORAS_ALERTA:
                    alertas.append({
                        "tramite": item["numero"], "seccion": item["seccion"],
                        "descripcion": d["descripcion"],
                        "responsable_actual": u.get("enviado_a") or u["nombre"],
                        "cargo": u["cargo"], "desde_cuando": u["fecha"],
                        "nota": u["nota"], "horas": round(d["horas_sin_movimiento"],1)
                    })
            detalles.append(d)

        await browser.close()

    with open(ARCHIVO_JSON, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "total": len(detalles), "tramites": detalles}, f, ensure_ascii=False, indent=2)
    print(f"\nOK {ARCHIVO_JSON}: {len(detalles)} tramites")

    with open(ARCHIVO_ALERTAS, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "total_alertas": len(alertas), "alertas": alertas}, f, ensure_ascii=False, indent=2)
    print(f"OK {ARCHIVO_ALERTAS}: {len(alertas)} alertas")
    print(f"\nCOMPLETADO: {datetime.now()}\n")

if __name__ == "__main__":
    asyncio.run(ejecutar())
