#!/usr/bin/env python3
"""
Monitor de arabicparfums.com (Shopify)
Detecta nuevos productos y cambios de stock/precio
Notifica por Telegram sin límites y con protección anti-crash
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time
from datetime import datetime

# ─── CONFIGURACIÓN ────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")

BASE_URL = "https://arabicparfums.com"

# Puedes añadir más colecciones de la web si lo deseas
CATEGORIAS = [
    {
        "nombre": "📦 Testers",
        "url": f"{BASE_URL}/collections/tester"
    },
]

STATE_FILE = "estado_productos_arabic.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
# ──────────────────────────────────────────────────────────────────

session = requests.Session()
session.headers.update(HEADERS)


def cargar_estado():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"  ⚠️ Archivo de estado corrupto ignorado ({e}). Empezando de cero.")
            return {}
    return {}


def guardar_estado(estado):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def parsear_productos_html(html):
    """Extrae productos de la cuadrícula de Shopify de Arabic Parfums."""
    soup = BeautifulSoup(html, "html.parser")
    productos = {}

    # Cada producto está dentro de un div con esta clase
    articulos = soup.select('div.productgrid--item')

    for art in articulos:
        # Extraer nombre y link
        title_elem = art.select_one('h2.productitem--title a')
        if not title_elem:
            continue
            
        nombre = title_elem.get_text(strip=True)
        href = title_elem.get('href', '')
        full_url = f"{BASE_URL}{href}" if href.startswith('/') else href
        
        # El ID del producto será su "slug" (la última parte del link)
        producto_id = href.rstrip('/').split('/')[-1]
        if not producto_id:
            continue

        # Extraer precio (Buscamos dentro del div del precio actual)
        precio_elem = art.select_one('.price__current span.money')
        precio = precio_elem.get_text(strip=True) if precio_elem else "Sin precio"

        # Extraer stock (Buscamos si tiene la etiqueta de "Proximamente" o "soldout")
        etiqueta_agotado = art.select_one('.productitem__badge--soldout')
        boton_agotado = art.select_one('button.disabled')
        
        en_stock = True
        if etiqueta_agotado or boton_agotado:
            en_stock = False

        productos[producto_id] = {
            "nombre": nombre,
            "precio": precio,
            "url": full_url,
            "en_stock": en_stock,
        }

    return productos


def scrape_categoria(url):
    """Descarga todos los productos de una categoría paginada de Shopify."""
    productos = {}
    pagina = 1
    base = url.split("?")[0]

    while True:
        timestamp = int(time.time())
        # En Shopify la paginación es con ?page=X
        url_pag = f"{base}?page={pagina}&t={timestamp}"

        try:
            r = session.get(url_pag, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"  ⚠️  Error en {url_pag}: {e}")
            break

        nuevos = parsear_productos_html(r.text)

        if not nuevos:
            print(f"  ✅ Sin productos en la página {pagina}, fin de categoría")
            break

        # Si Shopify nos empieza a devolver IDs que ya hemos guardado, cortamos
        ids_realmente_nuevos = set(nuevos.keys()) - set(productos.keys())
        if not ids_realmente_nuevos:
            print(f"  ⚠️  Shopify repite productos en la página {pagina}, fin de categoría")
            break

        productos.update(nuevos)
        print(f"    Página {pagina}: {len(ids_realmente_nuevos)} nuevos (Total: {len(productos)})")

        pagina += 1
        time.sleep(1)

        # Límite de seguridad de 100 páginas (suelen tener entre 5 y 20 páginas como mucho)
        if pagina > 100:
            print("  ⚠️  Límite de seguridad de 100 páginas alcanzado")
            break

    return productos


def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Sin credenciales Telegram — volcando por consola:")
        print("─" * 60)
        print(mensaje)
        print("─" * 60)
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    limite_caracteres = 4000
    mensajes_cortados = []
    
    if len(mensaje) <= limite_caracteres:
        mensajes_cortados.append(mensaje)
    else:
        lineas = mensaje.split('\n')
        bloque_actual = ""
        for linea in lineas:
            if len(bloque_actual) + len(linea) + 1 > limite_caracteres:
                mensajes_cortados.append(bloque_actual.strip())
                bloque_actual = linea + "\n"
            else:
                bloque_actual += linea + "\n"
        if bloque_actual:
            mensajes_cortados.append(bloque_actual.strip())

    for i, msg in enumerate(mensajes_cortados):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
            
        print(f"  📤 Enviando bloque {i+1}/{len(mensajes_cortados)} a Telegram...")
        
        max_reintentos = 3
        for intento in range(max_reintentos):
            try:
                r = requests.post(url, json=payload, timeout=15)
                
                # Sistema Anti-Ban de Telegram
                if r.status_code == 429:
                    espera = r.json().get("parameters", {}).get("retry_after", 5)
                    print(f"  ⏳ Telegram pide frenar. Esperando {espera} segundos...")
                    time.sleep(espera + 1)
                    continue
                    
                r.raise_for_status()
                print("  ✅ Enviado")
                break
                
            except Exception as e:
                print(f"  ❌ Error Telegram: {e}")
                break
        
        # Pausa de 3.5 segundos entre bloques para no superar los límites de la API de Telegram
        time.sleep(3.5)


def comparar_y_notificar(nombre_cat, productos_nuevos, productos_anteriores, ya_notificados=None):
    mensajes = []
    if ya_notificados is None:
        ya_notificados = set()

    # 1. Productos NUEVOS (filtrando los que ya se notificaron en otra categoría)
    nuevos = {k: v for k, v in productos_nuevos.items()
              if k not in productos_anteriores and v['nombre'] not in ya_notificados}
    if nuevos:
        # Registrar como ya notificados para las siguientes categorías
        for p in nuevos.values():
            ya_notificados.add(p['nombre'])

        lista = "\n".join(
            f"  • <a href='{p['url']}'>{p['nombre']}</a> — {p['precio']}"
            for p in nuevos.values()
        )
        mensajes.append(f"🆕 <b>Nuevos productos en {nombre_cat}</b>\n{lista}")



    # 3. Cambios de PRECIO y STOCK
    cambios = []
    for k, prod_nuevo in productos_nuevos.items():
        if k in productos_anteriores:
            prod_ant = productos_anteriores[k]

            # Stock
            if not prod_ant.get("en_stock", True) and prod_nuevo["en_stock"]:
                cambios.append(f"  🟢 <b>¡VUELVE A HABER STOCK!</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")


            # Precio
            p_ant = prod_ant.get("precio", "")
            p_nue = prod_nuevo.get("precio", "")
            if p_ant and p_nue and p_ant != p_nue:
                cambios.append(
                    f"  💸 <b>CAMBIO PRECIO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>\n  {p_ant} → <b>{p_nue}</b>"
                )
                
    if cambios:
        lista = "\n\n".join(cambios)
        mensajes.append(f"⚡ <b>Actualizaciones en {nombre_cat}</b>\n\n{lista}")

    return mensajes


def main():
    print(f"\n🕐 Monitor arabicparfums.com — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    estado_anterior = cargar_estado()
    estado_nuevo    = {}
    todos_mensajes  = []
    ya_notificados  = set()  # Evita notificar el mismo producto en varias categorías

    for cat in CATEGORIAS:
        nombre = cat["nombre"]
        url    = cat["url"]
        print(f"\n📦 Scrapeando {nombre}...")

        productos  = scrape_categoria(url)
        anteriores = estado_anterior.get(url, {})
        print(f"  → {len(productos)} productos encontrados")

        estado_nuevo[url] = productos

        if anteriores:
            msgs = comparar_y_notificar(nombre, productos, anteriores, ya_notificados)
            todos_mensajes.extend(msgs)
        else:
            print("  ℹ️  Primera ejecución, guardando estado inicial")

    if todos_mensajes:
        print(f"\n📣 {len(todos_mensajes)} notificaciones")
        for msg in todos_mensajes:
            enviar_telegram(msg)
    else:
        print("\n✅ Sin cambios detectados")

    guardar_estado(estado_nuevo)
    print("\n💾 Estado guardado\n")


if __name__ == "__main__":
    main()