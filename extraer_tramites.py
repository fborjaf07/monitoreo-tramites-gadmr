import asyncio
import json
import os
import re
from datetime import datetime
from playwright.async_api import async_playwright
from openpyxl import load_workbook

USUARIO = os.environ.get("USUARIO", "borjafa")
CONTRASENA = os.environ.get("CONTRASENA", "tu_contrasena")
HORAS_ALERTA = 24
ARCHIVO_ENTRADA = "tramites_entrada.xlsx"
ARCHIVO_ALERTAS = "alertas.json"
ARCHIVO_TODOS = "tramites_todos.json"

LOGIN_URL = ("https://egob.gadmriobamba.gob.ec:8443/cas/login?service=https%3A%2F%2Fegobedoc.gadmriobamba.gob.ec%3A8081%2Fauth%2Fcas%2Fcallback%3Forigin%3Dhttps%253A%252F%252Fegobedoc.gadmriobamba.gob.ec%253A8081%252F")
BASE_URL = "https://egobedoc.gadmriobamba.gob.ec:8081/issues/"

def leer_tramites_entrada():
    if not os.path.exists(ARCHIVO_ENTRADA):
        print("ERROR: No se encuentra tramites_entrada.xlsx")
        return []
    wb = load_workbook(ARCHIVO_ENTRADA)
    ws = wb.active
    tramites = []
    for row in ws.iter_rows(min_row=4, values_only=True):
        numero = row[0]
        descripcion = row[1] if len(row) > 1 else ""
        activo = row[2] if len(row) > 2 else "SI"
        if numero and str(numero).strip() and str(activo).upper() == "SI":
            tramites.append({
                "numero": str(numero).strip(),
                "descripcion": str(descripcion).strip() if descripcion else ""
            })
    return tramites

def calcular_tiempo(fecha_str):
    if not fecha_str:
        return None, "Desconocido"
    try:
        formatos = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"]
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
            return horas, f"{int(delta.total_seconds()/60)} min"
        elif horas < 24:
            return horas, f"{int(horas)}h {int((horas%1)*60)}min"
        else:
            dias = int(horas // 24)
            hrs = int(horas % 24)
            return horas, f"{dias}d {hrs}h"
    except Exception:
        return None, "Desconocido"

def color_semaforo(horas):
    if horas is None:
        return "D9D9D9"
    if horas >= HORAS_ALERTA:
        return "FF4C4C"
    elif horas >= HORAS_ALERTA * 0.75:
        return "FFC000"
    else:
        return "70AD47"

def extraer_movimientos(historico_text):
    """
    Extrae todos los movimientos del historico.
    El e-DOC ahora muestra el mas reciente PRIMERO.
    Esta funcion extrae en el orden que aparecen (reciente->antiguo)
    y al final se invierten para guardar cronologicamente (antiguo->reciente).

    Formato de cada bloque:
    Tipo de accion (opcional)
    (
    NUMERO_TRAMITE
    )
    #NUM_MOVIMIENTO
    NOMBRE APELLIDO (CARGO)
    2026-09-24 15:00 (21 dias)
    Nota: ...
    Asignado ha cambiado de X a Y
    """
    movimientos = []
    patron_bloque = re.compile(
        r'(?:\(\s*\n\d+\s*\n\)\s*\n)?#\d+\s*\n([^\n(]+)\(([^)]+)\)\s*\n(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})[^\n]*\n(.*?)(?=(?:\(\s*\n\d+\s*\n\)\s*\n)?#\d+\s*\n[^\n(]+\(|\Z)',
        re.DOTALL
    )
    for m in patron_bloque.finditer(historico_text):
        nombre = m.group(1).strip()
        cargo = m.group(2).strip()
        fecha = m.group(3).strip()
        contenido = m.group(4)
        nota_match = re.search(r'Nota:\s*(.+?)(?:\nAsignado|\nDocumento|\nAnadido|\nEncargo|\Z)', contenido, re.DOTALL)
        nota = nota_match.group(1).strip()[:300] if nota_match else ""
        enviado_a = ""
        asig_match = re.search(r'Asignado ha cambiado de .+? a ([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\n|$)', contenido)
        if asig_match:
            enviado_a = asig_match.group(1).strip()
        else:
            env_match = re.search(r'Documento Enviado a ([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?)(?:\n|$)', contenido)
            if env_match:
                enviado_a = env_match.group(1).strip()
        movimientos.append({
            "nombre": nombre,
            "cargo": cargo,
            "fecha": fecha,
            "nota": nota,
            "enviado_a": enviado_a
        })

    # IMPORTANTE: el e-DOC ahora muestra reciente->antiguo.
    # Invertimos para guardar cronologicamente (antiguo->reciente),
    # asi el ultimo elemento de la lista es siempre el movimiento mas reciente.
    movimientos = list(reversed(movimientos))
    return movimientos

async def extraer_datos(page, numero):
    resultado = {
        "numero": numero,
        "asunto": "",
        "estado": "",
        "movimientos": [],
        "hijos": [],
        "error": None
    }
    try:
        url = BASE_URL + numero
        # Carga la pagina y espera que este estable
        await page.goto(url, wait_until="networkidle")
        # Espera adicional para que cargue el contenido dinamico
        await page.wait_for_timeout(3000)
        # Ya NO se necesita scroll: el e-DOC ahora muestra los movimientos
        # mas recientes PRIMERO, por lo que estan visibles desde el inicio.
        texto = await page.inner_text("body")

        asunto_match = re.search(r"Asunto:\s*(.+?)(?:\n)", texto)
        if asunto_match:
            resultado["asunto"] = asunto_match.group(1).strip()

        estado_match = re.search(r"Estado:\s*(\w+)", texto)
        if estado_match:
            resultado["estado"] = estado_match.group(1).strip()

        idx_hijos = texto.find("Trámite/s hijos")
        if idx_hijos == -1:
            idx_hijos = texto.find("Tramite/s hijos")
        idx_historico = texto.find("Histórico")
        if idx_historico == -1:
            idx_historico = texto.find("Historico")

        if idx_hijos > 0 and idx_historico > idx_hijos:
            seccion_hijos = texto[idx_hijos:idx_historico]
            patron = re.compile(
                r'\(\s*\n(\d+)\s*\n\)\s*\n.*?(Nuevo|En proceso|Aprobado|Rechazado|Cerrado|Firmado|Enviado|Respuesta|Archivado)\s*\n(\d{4}-\d{2}-\d{2})',
                re.DOTALL
            )
            for match in patron.finditer(seccion_hijos):
                num_hijo = match.group(1).strip()
                estado_hijo = match.group(2).strip()
                fecha_hijo = match.group(3).strip()
                horas, tiempo_texto = calcular_tiempo(fecha_hijo)
                resultado["hijos"].append({
                    "numero_memorando": f"#{num_hijo}",
                    "estado": estado_hijo,
                    "fecha_movimiento": fecha_hijo,
                    "horas_transcurridas": horas,
                    "tiempo_texto": tiempo_texto,
                    "en_alerta": horas is not None and horas >= HORAS_ALERTA
                })

        if idx_historico > 0:
            historico_text = texto[idx_historico:]
            movimientos = extraer_movimientos(historico_text)
            resultado["movimientos"] = movimientos  # Todos, orden cronologico

    except Exception as e:
        resultado["error"] = str(e)
    return resultado

async def ejecutar():
    tramites = leer_tramites_entrada()
    if not tramites:
        return
    print(f"\n{'='*55}")
    print(f"INICIO: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Tramites a procesar: {len(tramites)}")
    print(f"{'='*55}\n")
    todos = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors", "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        print("Iniciando sesion...")
        await page.goto(LOGIN_URL)
        await page.wait_for_load_state("networkidle")
        await page.fill('input[name="username"]', USUARIO)
        await page.fill('input[name="password"]', CONTRASENA)
        await page.click('input[type="submit"], button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        print("Sesion iniciada\n")
        for i, t in enumerate(tramites, 1):
            print(f"[{i}/{len(tramites)}] Tramite #{t['numero']}...")
            datos = await extraer_datos(page, t["numero"])
            datos["descripcion_entrada"] = t["descripcion"]
            todos.append(datos)
            if datos["error"]:
                print(f"   AVISO: {datos['error']}")
            else:
                movs = datos["movimientos"]
                if movs:
                    # movs[-1] es el mas reciente (orden cronologico)
                    print(f"   OK | Movimientos: {len(movs)} | Ultimo: {movs[-1]['nombre']} -> {movs[-1]['enviado_a']}")
                else:
                    print(f"   SIN MOVIMIENTOS detectados")
        await browser.close()

    # Construir resumen de tramites para JSON
    tramites_json = []
    alertas_json = []
    for datos in todos:
        movs = datos.get("movimientos", [])
        # movs esta en orden cronologico: el ultimo es el mas reciente
        ultimo = movs[-1] if movs else None
        horas_ult, tiempo_txt = calcular_tiempo(ultimo["fecha"]) if ultimo else (None, "")
        en_alerta = horas_ult is not None and horas_ult >= HORAS_ALERTA

        # Detectar si esta archivado
        archivado = False
        quien_archivo = ""
        if movs:
            for mov in reversed(movs):
                if "Archivado" in mov.get("nota", "") or "archivado" in mov.get("nota", "").lower():
                    archivado = True
                    quien_archivo = mov["nombre"]
                    break

        t_info = {
            "numero": datos["numero"],
            "descripcion": datos.get("descripcion_entrada", ""),
            "estado_edoc": datos.get("estado", ""),
            "en_alerta": en_alerta,
            "archivado": archivado,
            "quien_archivo": quien_archivo,
            "responsable_previo": movs[-2]["nombre"] if len(movs) >= 2 else "",
            "horas_sin_movimiento": round(horas_ult, 1) if horas_ult else None,
            "tiempo_texto": tiempo_txt,
            "ultimo_movimiento": {
                "nombre": ultimo["nombre"],
                "cargo": ultimo["cargo"],
                "fecha": ultimo["fecha"],
                "nota": ultimo["nota"],
                "enviado_a": ultimo["enviado_a"]
            } if ultimo else None,
            "movimientos": movs
        }
        tramites_json.append(t_info)

        if en_alerta:
            alertas_json.append({
                "tramite": datos["numero"],
                "descripcion": datos.get("descripcion_entrada", ""),
                "quien_envio": ultimo["nombre"],
                "responsable_actual": ultimo["enviado_a"],
                "cargo": ultimo["cargo"],
                "desde_cuando": ultimo["fecha"],
                "nota": ultimo["nota"],
                "horas": round(horas_ult, 1)
            })

    # Guardar JSON completo de tramites
    payload_todos = {
        "timestamp": datetime.now().isoformat(),
        "total": len(tramites_json),
        "tramites": tramites_json
    }
    with open("tramites_todos.json", "w", encoding="utf-8") as f:
        json.dump(payload_todos, f, ensure_ascii=False, indent=2)
    print(f"tramites_todos.json guardado ({len(tramites_json)} tramites)")

    # Guardar JSON de alertas
    payload_alertas = {
        "timestamp": datetime.now().isoformat(),
        "total_alertas": len(alertas_json),
        "alertas": alertas_json,
        "mensaje_resumen": (
            f"ALERTA TRAMITES - {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            f"Se encontraron {len(alertas_json)} tramite(s) sin movimiento por mas de {HORAS_ALERTA}h."
        ) if alertas_json else "Sin alertas activas."
    }
    with open(ARCHIVO_ALERTAS, "w", encoding="utf-8") as f:
        json.dump(payload_alertas, f, ensure_ascii=False, indent=2)
    print(f"alertas.json guardado ({len(alertas_json)} alertas)")
    print(f"COMPLETADO: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Subir a GitHub automaticamente
    try:
        import subprocess
        carpeta = "C:\\MonitoreoTramites"
        msg = f"Actualizacion automatica {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(["git", "add", "tramites_todos.json", "alertas.json"], cwd=carpeta, check=True)
        subprocess.run(["git", "commit", "-m", msg], cwd=carpeta, check=True)
        subprocess.run(["git", "push"], cwd=carpeta, check=True)
        print("GitHub actualizado correctamente")
    except subprocess.CalledProcessError as e:
        print(f"Aviso GitHub: {e}")
    except Exception as e:
        print(f"Error al subir a GitHub: {e}")


if __name__ == "__main__":
    asyncio.run(ejecutar())
