"""
FASE 1: Descubre todos los números de trámite por sección
Guarda: numeros_entrada.json, numeros_enviados.json, numeros_reasignados.json
"""
import asyncio
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright

USUARIO    = os.environ.get("EDOC_USUARIO", "")
CONTRASENA = os.environ.get("EDOC_CONTRASENA", "")
CAS_LOGIN  = "https://egob.gadmriobamba.gob.ec:8443/cas/login"
BASE_URL   = "https://egobedoc.gadmriobamba.gob.ec:8081"
SERVICE    = "https://egobedoc.gadmriobamba.gob.ec:8081/auth/cas/callback?origin=https%3A%2F%2Fegobedoc.gadmriobamba.gob.ec%3A8081%2F"

SECCIONES = {
    "entrada":     f"{BASE_URL}/my/passig",
    "enviados":    f"{BASE_URL}/my/pmy",
    "reasignados": f"{BASE_URL}/my/preasigned",
}

async def login(page):
    print("Login...")
    await page.goto(f"{CAS_LOGIN}?service={SERVICE}", timeout=60000, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.fill('input[name="username"]', USUARIO)
    await page.fill('input[name="password"]', CONTRASENA)
    await page.click('button[type="submit"], input[type="submit"]')
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(5000)
    if "egobedoc" not in page.url:
        raise Exception(f"Login fallido: {page.url}")
    print(f"Login OK - {page.url}")

async def descubrir_seccion(page, key, url):
    numeros = []
    vistos  = set()
    pagina  = 1
    while True:
        sep  = '&' if '?' in url else '?'
        purl = f"{url}{sep}page={pagina}"
        print(f"  [{key}] pag {pagina}...")
        await page.goto(purl, timeout=60000, wait_until="networkidle")
        await page.wait_for_timeout(3000)
        if "cas/login" in page.url:
            print(f"  Sesión expirada"); break
        contenido = await page.content()
        texto     = await page.inner_text("body")
        # Buscar números de trámite visibles en la tabla
        nums = list(dict.fromkeys(
            re.findall(r'Tr[áa]mite:\s*(\d+)', texto) +
            re.findall(r'/issues/(\d+)', contenido) +
            re.findall(r'data-id=["\'](\d+)["\']', contenido)
        ))
        nuevos = [n for n in nums if n not in vistos]
        if not nuevos: break
        for n in nuevos:
            vistos.add(n)
            numeros.append(n)
        print(f"  [{key}] pag {pagina}: {len(nuevos)} nuevos (total {len(numeros)})")
        if f'page={pagina+1}' not in contenido and 'rel="next"' not in contenido: break
        pagina += 1
        await page.wait_for_timeout(800)
    return numeros

async def main():
    print(f"DESCUBRIMIENTO - {datetime.now()}")
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

        resumen = {}
        for key, url in SECCIONES.items():
            nums = await descubrir_seccion(page, key, url)
            resumen[key] = nums
            with open(f"numeros_{key}.json", "w") as f:
                json.dump({"seccion": key, "total": len(nums), "numeros": nums}, f)
            print(f"  -> numeros_{key}.json ({len(nums)} trámites)")

        await browser.close()

    # Guardar resumen para que los otros workflows sepan cuántos hay
    with open("descubrimiento.json", "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "totales": {k: len(v) for k, v in resumen.items()}
        }, f)
    print(f"\nRESUMEN: { {k:len(v) for k,v in resumen.items()} }")
    print(f"COMPLETADO: {datetime.now()}")

if __name__ == "__main__":
    asyncio.run(main())
