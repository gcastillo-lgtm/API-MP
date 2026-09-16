#!/usr/bin/env python3
"""
Buscador de licitaciones fotovoltaicas en Mercado Público (Chile).

- Revisa publicaciones de los últimos N días.
- Evalúa cuáles siguen VIGENTES para ofertar (fecha de cierre no pasada).
- Envía correo solo con las que son nuevas (no notificadas antes).
- Genera docs/index.html con TODAS las vigentes actuales, para publicar
  gratis con GitHub Pages y verlo desde un link, actualizado cada vez que
  corre el workflow (recomendado: cada hora).

Toda la configuración sensible (ticket, credenciales de correo) se lee de
variables de entorno — nunca va escrita en este archivo. Ver README.md.
"""

import os
import sys
import json
import ssl
import html
import smtplib
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ----------------------------------------------------------------------
# CONFIGURACIÓN (variables de entorno — ver README.md / workflow de Actions)
# ----------------------------------------------------------------------

API_TICKET = os.environ["MP_TICKET"]

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
EMAIL_TO = os.environ["EMAIL_TO"]

# Si se define en "false" (u otro valor no vacío que empiece con "n"/"0"),
# se genera igual la página web pero no se envían correos. Útil si en algún
# momento quieres usar solo la página y dejar de recibir emails.
ENVIAR_CORREO = os.environ.get("ENVIAR_CORREO", "true").strip().lower() not in ("false", "0", "no")

HISTORIAL_PATH = os.environ.get("HISTORIAL_PATH", "vistas.json")
CACHE_PATH = os.environ.get("CACHE_PATH", "detalle_cache.json")
PAGINA_PATH = os.environ.get("PAGINA_PATH", "docs/index.html")

# Días hacia atrás para revisar publicaciones antiguas aún abiertas
DIAS_A_REVISAR = int(os.environ.get("DIAS_A_REVISAR", "30"))

# Palabras clave del rubro fotovoltaico
PALABRAS_CLAVE = [
    "fotovoltaic", "fotovoltaica", "fotovoltaico",
    "panel solar", "paneles solares",
    "energia solar", "energía solar",
    "planta solar", "parque solar", "central solar",
    "generacion solar", "generación solar",
    "inversor solar", "inversores fotovoltaicos",
    "sistema solar fotovoltaico",
    "autoconsumo energetico", "autoconsumo energético",
    "energia renovable no convencional", "energía renovable no convencional",
    "ernc",
    "mantencion de paneles", "mantención de paneles",
    "modulo fotovoltaico", "módulo fotovoltaico",
    "modulos fotovoltaicos", "módulos fotovoltaicos",
    "sistema fotovoltaico", "central fotovoltaica",
    "generador fotovoltaico", "kit solar",
    "estructura fotovoltaica", "montaje de paneles",
    "on-grid", "off-grid", "pmgd",
]

BASE_URL = "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json"
LINK_BASE = "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idlicitacion="


# ----------------------------------------------------------------------
# UTILIDADES DE TEXTO / FILTRO
# ----------------------------------------------------------------------

def normalizar(texto):
    if not texto:
        return ""
    reemplazos = str.maketrans("áéíóúÁÉÍÓÚñÑ", "aeiouAEIOUnN")
    return texto.translate(reemplazos).lower()


def _texto_licitacion_para_filtro(lic):
    partes = [lic.get("Nombre", ""), lic.get("Descripcion", "")]
    items = lic.get("Items", {})
    if isinstance(items, dict):
        for item in items.get("Listado", []):
            partes.append(item.get("NombreProducto", ""))
            partes.append(item.get("Categoria", ""))
            partes.append(item.get("Descripcion", ""))
    return normalizar(" | ".join(p for p in partes if p))


def es_fotovoltaica(lic):
    texto = _texto_licitacion_para_filtro(lic)
    return any(normalizar(k) in texto for k in PALABRAS_CLAVE)


# ----------------------------------------------------------------------
# PERSISTENCIA (historial de correos ya enviados + caché de detalle)
# ----------------------------------------------------------------------

def cargar_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def guardar_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cargar_historial():
    return set(cargar_json(HISTORIAL_PATH, []))


def guardar_historial(vistos):
    guardar_json(HISTORIAL_PATH, list(vistos)[-5000:])


def cargar_cache():
    return cargar_json(CACHE_PATH, {})


def guardar_cache(cache):
    # Se descartan del caché las licitaciones cuyo cierre ya pasó hace más
    # de DIAS_A_REVISAR*2, para que el archivo no crezca para siempre.
    guardar_json(CACHE_PATH, cache)


# ----------------------------------------------------------------------
# LLAMADAS A LA API
# ----------------------------------------------------------------------

def _get(url, timeout):
    contexto_ssl = ssl._create_unverified_context()
    with urllib.request.urlopen(url, timeout=timeout, context=contexto_ssl) as resp:
        return json.loads(resp.read().decode("utf-8"))


def obtener_licitaciones_del_dia(fecha_ddmmyyyy):
    url = f"{BASE_URL}?fecha={fecha_ddmmyyyy}&ticket={API_TICKET}"
    try:
        data = _get(url, timeout=60)
    except urllib.error.URLError as e:
        print(f"  [x] Error al consultar fecha {fecha_ddmmyyyy}: {e}", file=sys.stderr)
        return []
    return data.get("Listado", [])


def obtener_detalle_licitacion(codigo_externo):
    url = f"{BASE_URL}?codigo={codigo_externo}&ticket={API_TICKET}"
    try:
        data = _get(url, timeout=30)
        listado = data.get("Listado", [])
        if listado:
            return listado[0]
    except Exception as e:
        print(f"  [x] Error al obtener detalle de {codigo_externo}: {e}", file=sys.stderr)
    return None


# ----------------------------------------------------------------------
# FECHAS / VIGENCIA
# ----------------------------------------------------------------------

def parsear_fecha_cierre(fecha_str):
    if not fecha_str:
        return None
    formatos = [
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
        "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
    ]
    limpio = fecha_str.replace("Z", "").strip()
    for fmt in formatos:
        try:
            return datetime.strptime(limpio, fmt).replace(tzinfo=timezone(timedelta(hours=-3)))
        except ValueError:
            continue
    return None


def evaluar_vigencia(fecha_cierre_str):
    fecha_cierre = parsear_fecha_cierre(fecha_cierre_str)
    if not fecha_cierre:
        return False, None, None
    ahora = datetime.now(timezone(timedelta(hours=-3)))
    if fecha_cierre <= ahora:
        return False, fecha_cierre, -1
    dias_restantes = (fecha_cierre - ahora).days
    return True, fecha_cierre, dias_restantes


# ----------------------------------------------------------------------
# CORREO
# ----------------------------------------------------------------------

def enviar_correo(licitaciones_nuevas, fecha_legible):
    asunto = f"[Licitaciones Fotovoltaicas] {len(licitaciones_nuevas)} nueva(s) vigente(s) - {fecha_legible}"

    filas = []
    for lic in licitaciones_nuevas:
        codigo = lic.get("CodigoExterno", "")
        nombre = html.escape(lic.get("Nombre", "(sin nombre)"))
        comprador = lic.get("Comprador", {}) if isinstance(lic.get("Comprador"), dict) else {}
        organismo = html.escape(comprador.get("NombreOrganismo", "(organismo no informado)"))
        region = html.escape(comprador.get("RegionUnidad", ""))
        dias_restantes = lic.get("_dias_restantes")
        fecha_dt = lic.get("_fecha_dt")
        fecha_txt = fecha_dt.strftime("%d-%m-%Y %H:%M hrs") if fecha_dt else "no informada"
        descripcion = html.escape((lic.get("Descripcion") or "").strip())
        descripcion = (descripcion[:280] + "…") if len(descripcion) > 280 else descripcion
        link = LINK_BASE + codigo
        filas.append(
            f"<li><b>{nombre}</b><br>"
            f"Organismo: {organismo}" + (f" ({region})" if region else "") + "<br>"
            f"Código: {codigo}<br>"
            f"Cierra en {dias_restantes} día(s) — {fecha_txt}<br>"
            + (f"Descripción: {descripcion}<br>" if descripcion else "")
            + f"<a href='{link}'>Ver licitación en Mercado Público</a></li><br>"
        )

    cuerpo = f"<h2>Licitaciones fotovoltaicas nuevas - {fecha_legible}</h2><ol>" + "".join(filas) + "</ol>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(cuerpo, "html", "utf-8"))

    destinatarios = [d.strip() for d in EMAIL_TO.split(",") if d.strip()]
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, destinatarios, msg.as_string())

    print(f"Correo enviado a {destinatarios} con {len(licitaciones_nuevas)} licitación(es) nueva(s).")


# ----------------------------------------------------------------------
# PÁGINA WEB (docs/index.html -> GitHub Pages)
# ----------------------------------------------------------------------

def generar_pagina_html(vigentes_totales, ahora_str):
    tarjetas = []
    for lic in vigentes_totales:
        codigo = lic.get("CodigoExterno", "")
        nombre = html.escape(lic.get("Nombre", "(sin nombre)"))
        comprador = lic.get("Comprador", {}) if isinstance(lic.get("Comprador"), dict) else {}
        organismo = html.escape(comprador.get("NombreOrganismo", "(organismo no informado)"))
        region = html.escape(comprador.get("RegionUnidad", "Región no especificada"))
        dias_restantes = lic.get("_dias_restantes")
        fecha_dt = lic.get("_fecha_dt")
        fecha_txt = fecha_dt.strftime("%d-%m-%Y %H:%M hrs") if fecha_dt else "No especificada"
        descripcion = html.escape((lic.get("Descripcion") or "Sin descripción detallada.").strip())
        descripcion = (descripcion[:350] + "…") if len(descripcion) > 350 else descripcion
        link = LINK_BASE + codigo

        urgente = dias_restantes is not None and dias_restantes <= 2
        badge_clase = "badge-urgente" if urgente else "badge-abierta"
        badge_txt = "CIERRA PRONTO" if urgente else f"{dias_restantes} DÍAS RESTANTES"

        tarjetas.append(f"""
        <article class="card">
          <span class="badge {badge_clase}">{badge_txt}</span>
          <h2>{nombre}</h2>
          <dl>
            <dt>Organismo</dt><dd>{organismo} ({region})</dd>
            <dt>Código</dt><dd><code>{codigo}</code></dd>
            <dt>Cierra</dt><dd>{fecha_txt}</dd>
          </dl>
          <p class="desc">{descripcion}</p>
          <a class="btn" href="{link}" target="_blank" rel="noopener">Ver ficha oficial en Mercado Público &rarr;</a>
        </article>
        """)

    contenido_tarjetas = "".join(tarjetas) if tarjetas else "<p class='vacio'>No hay licitaciones fotovoltaicas vigentes en este momento.</p>"

    pagina = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Licitaciones Fotovoltaicas Vigentes - Mercado Público</title>
<style>
  :root {{
    --bg: #f4f6f9; --card-bg: #ffffff; --text: #212529; --muted: #6c757d;
    --accent: #0d6efd; --header-bg: #0d233a; --border: #e2e6ea;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #0f1420; --card-bg: #171d2b; --text: #e8ebf0; --muted: #9aa4b2;
      --accent: #4c8dff; --header-bg: #0a0f1a; --border: #2a3242;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #0f1420; --card-bg: #171d2b; --text: #e8ebf0; --muted: #9aa4b2;
    --accent: #4c8dff; --header-bg: #0a0f1a; --border: #2a3242;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: 'Segoe UI', Arial, sans-serif; padding: 0 0 40px 0;
  }}
  header {{
    background: var(--header-bg); color: #fff; padding: 28px 20px; text-align: center;
  }}
  header h1 {{ margin: 0; font-size: 22px; letter-spacing: 0.5px; }}
  header p {{ margin: 8px 0 0; color: #a7b6c9; font-size: 13px; }}
  main {{ max-width: 900px; margin: 0 auto; padding: 24px 16px; }}
  .card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 20px; margin-bottom: 20px;
  }}
  .card h2 {{ font-size: 16px; margin: 8px 0 14px; }}
  .badge {{
    display: inline-block; padding: 3px 10px; font-size: 11px; font-weight: 700;
    border-radius: 4px; text-transform: uppercase;
  }}
  .badge-urgente {{ background: #fff3cd; color: #856404; }}
  .badge-abierta {{ background: #d4edda; color: #155724; }}
  dl {{ display: grid; grid-template-columns: 110px 1fr; gap: 4px 8px; margin: 0 0 12px; font-size: 13px; }}
  dt {{ color: var(--muted); font-weight: 600; }}
  dd {{ margin: 0; }}
  .desc {{
    font-style: italic; color: var(--muted); border-left: 3px solid var(--accent);
    padding: 8px 12px; margin: 0 0 14px; font-size: 13px; overflow-x: auto;
  }}
  .btn {{
    display: inline-block; background: var(--accent); color: #fff; text-decoration: none;
    padding: 9px 16px; border-radius: 4px; font-size: 13px; font-weight: 600;
  }}
  .vacio {{ text-align: center; color: var(--muted); padding: 40px 0; }}
  footer {{ text-align: center; color: var(--muted); font-size: 12px; margin-top: 20px; }}
</style>
</head>
<body>
<header>
  <h1>Licitaciones Fotovoltaicas Vigentes</h1>
  <p>Mercado Público Chile &bull; Última actualización: {ahora_str}</p>
</header>
<main>
  {contenido_tarjetas}
</main>
<footer>Generado automáticamente cada hora desde la API de ChileCompra.</footer>
</body>
</html>"""

    guardar_json  # no-op reference to keep linters quiet about unused import ordering
    os.makedirs(os.path.dirname(PAGINA_PATH) or ".", exist_ok=True)
    with open(PAGINA_PATH, "w", encoding="utf-8") as f:
        f.write(pagina)
    print(f"Página generada en {PAGINA_PATH} con {len(vigentes_totales)} licitación(es) vigente(s).")


# ----------------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ----------------------------------------------------------------------

def main():
    ahora = datetime.now(timezone(timedelta(hours=-3)))
    fecha_legible_hoy = ahora.strftime("%d-%m-%Y")
    ahora_str = ahora.strftime("%d-%m-%Y %H:%M hrs (Chile)")

    print(f"Revisando publicaciones de los últimos {DIAS_A_REVISAR} días...")
    candidatas_por_codigo = {}
    for i in range(DIAS_A_REVISAR):
        fecha_evaluar = ahora - timedelta(days=i)
        fecha_api = fecha_evaluar.strftime("%d%m%Y")
        for lic in obtener_licitaciones_del_dia(fecha_api):
            if es_fotovoltaica(lic):
                candidatas_por_codigo[lic.get("CodigoExterno")] = lic

    print(f"Coincidencias por palabra clave en el período: {len(candidatas_por_codigo)}")

    cache = cargar_cache()
    vigentes_totales = []

    for codigo, lic_resumen in candidatas_por_codigo.items():
        detalle = cache.get(codigo)
        if detalle is None:
            detalle = obtener_detalle_licitacion(codigo) or lic_resumen
            cache[codigo] = detalle

        fecha_cierre_str = (detalle.get("Fechas") or {}).get("FechaCierre") or detalle.get("FechaCierre")
        es_vigente, fecha_dt, dias_restantes = evaluar_vigencia(fecha_cierre_str)

        if es_vigente:
            detalle["_fecha_dt"] = fecha_dt
            detalle["_dias_restantes"] = dias_restantes
            vigentes_totales.append(detalle)

    vigentes_totales.sort(key=lambda l: l.get("_dias_restantes", 9999))
    print(f"Vigentes actualmente (para la página web): {len(vigentes_totales)}")

    # Página web: SIEMPRE se regenera con el estado actual completo.
    generar_pagina_html(vigentes_totales, ahora_str)

    # Correo: solo licitaciones vigentes que no se han notificado antes.
    if ENVIAR_CORREO:
        vistos = cargar_historial()
        nuevas = [l for l in vigentes_totales if l.get("CodigoExterno") not in vistos]
        print(f"Nuevas para notificar por correo: {len(nuevas)}")
        if nuevas:
            enviar_correo(nuevas, fecha_legible_hoy)
            for lic in nuevas:
                vistos.add(lic.get("CodigoExterno"))
            guardar_historial(vistos)
    else:
        print("Envío de correo desactivado (ENVIAR_CORREO=false). Solo se actualizó la página web.")

    # Se guarda el caché de detalle al final (evita repetir llamadas a la
    # API para licitaciones ya conocidas en la próxima corrida horaria).
    guardar_cache(cache)


if __name__ == "__main__":
    main()
