"""
Analizador de imágenes de daños vehiculares (§4.5 – Módulo IA)
Usa Pillow + numpy para extracción de características y reglas heurísticas.
"""
import io
import logging
import numpy as np

logger = logging.getLogger(__name__)

_CATEGORIAS = {
    "dano_carroceria":   "Daño en carrocería",
    "llanta_dano":       "Daño en neumático o rueda",
    "motor_humo":        "Humo o derrame en compartimento motor",
    "vidrio_roto":       "Vidrio o parabrisas con daño",
    "multiple_dano":     "Daños en múltiples zonas",
    "sin_dano_visible":  "Sin daño visible identificado",
}

_SEVERIDAD_ES = {"leve": "Leve", "moderado": "Moderado", "grave": "Grave"}


def _extraer_features(img) -> dict:
    """Extrae estadísticas globales y zonales de imagen usando PIL y numpy."""
    from PIL import ImageFilter
    img_rgb = img.convert("RGB").resize((224, 224), resample=1)  # LANCZOS
    arr = np.array(img_rgb, dtype=np.float32)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # ── Estadísticas globales ────────────────────────────────────────────────
    brightness   = float(np.mean(arr)) / 255.0
    variance     = float(np.std(arr))  / 255.0
    red_ratio    = float(np.mean(r)) / (float(np.mean(g) + np.mean(b)) / 2.0 + 1e-6)

    max_ch = np.maximum(np.maximum(r, g), b)
    min_ch = np.minimum(np.minimum(r, g), b)
    saturation   = float(np.mean((max_ch - min_ch) / (max_ch + 1e-6)))
    dark_ratio   = float(np.mean(arr < 50))

    gray   = img_rgb.convert("L")
    edges  = gray.filter(ImageFilter.FIND_EDGES)
    e_arr  = np.array(edges, dtype=np.float32)
    edge_density = float(np.mean(e_arr)) / 255.0

    # ── Análisis por zonas ───────────────────────────────────────────────────
    # Las llantas suelen aparecer en la mitad inferior de la foto;
    # el compartimento del motor suele ocupar el centro/superior.
    h = arr.shape[0]
    mid = h // 2
    bot = arr[mid:, :, :]
    top = arr[:mid,  :, :]

    bottom_dark  = float(np.mean(bot < 50))   # fracción muy oscura mitad inferior
    top_dark     = float(np.mean(top < 50))   # fracción muy oscura mitad superior
    bottom_sat   = float(np.mean(                           # saturación mitad inferior
        (np.maximum(np.maximum(bot[:,:,0], bot[:,:,1]), bot[:,:,2]) -
         np.minimum(np.minimum(bot[:,:,0], bot[:,:,1]), bot[:,:,2])) /
        (np.maximum(np.maximum(bot[:,:,0], bot[:,:,1]), bot[:,:,2]) + 1e-6)
    ))
    dark_var     = float(np.std(bot[bot < 80])) / 255.0 if np.any(bot < 80) else 0.0

    return {
        "brightness":    brightness,
        "variance":      variance,
        "red_ratio":     red_ratio,
        "saturation":    saturation,
        "dark_ratio":    dark_ratio,
        "edge_density":  edge_density,
        "bottom_dark":   bottom_dark,
        "top_dark":      top_dark,
        "bottom_sat":    bottom_sat,
        "dark_var":      dark_var,
    }


def _clasificar(feat: dict) -> tuple[str, str, float]:
    """Clasificación por reglas heurísticas sobre estadísticas de imagen.

    Orden de prioridad: llanta → motor → vidrio → carrocería → sin daño.
    El orden importa: las condiciones más específicas van primero para
    evitar que cualquier foto nítida caiga en 'dano_carroceria'.
    """
    b  = feat["brightness"]
    v  = feat["variance"]
    r  = feat["red_ratio"]
    d  = feat["dark_ratio"]
    e  = feat["edge_density"]
    s  = feat["saturation"]
    bd = feat.get("bottom_dark", d)   # fracción oscura mitad inferior
    bs = feat.get("bottom_sat",  s)   # saturación mitad inferior

    # ── 1. LLANTA PONCHADA ───────────────────────────────────────────────────
    # Caucho negro: zona inferior muy oscura y poco saturada (llanta en la parte baja),
    # O imagen globalmente oscura con poca varianza de color.
    # Se usan OR amplios para capturar fotos de ángulos y distancias variadas.
    if (bd > 0.28 and bs < 0.45 and d > 0.18) or \
       (d > 0.35 and v < 0.30 and s < 0.35) or \
       (d > 0.50 and v < 0.32) or \
       (v < 0.09 and b < 0.42 and s < 0.30):
        # Descartar si hay mucho rojo/naranja (sería motor/fuego)
        if r < 1.50:
            cat, conf = "llanta_dano", 0.70

        else:
            cat, conf = "motor_humo", 0.62

    # ── 2. MOTOR / HUMO ─────────────────────────────────────────────────────
    # Compartimento motor: oscuro pero con varianza moderada (partes metálicas,
    # cables, etc.). Baja saturación (metales grises/negros). Sin rojo dominante.
    elif b < 0.38 and d > 0.20 and v > 0.07 and r < 1.15:
        cat, conf = "motor_humo", 0.67
    elif b < 0.30 and d > 0.16:
        cat, conf = "motor_humo", 0.62
    elif r > 1.50 and s > 0.35:            # llamas / naranja intenso
        cat, conf = "motor_humo", 0.61

    # ── 3. VIDRIO ROTO ──────────────────────────────────────────────────────
    # Patrón de fractura: muchos bordes en zona clara/brillante.
    elif e > 0.14 and b > 0.48 and v > 0.14:
        cat, conf = "vidrio_roto", 0.60

    # ── 4. DAÑO EN CARROCERÍA / MÚLTIPLE ────────────────────────────────────
    # Solo se activa con umbrales altos para no absorber fotos genéricas.
    elif e > 0.17 and v > 0.26:
        if d > 0.20 or (e > 0.19 and v > 0.30):
            cat, conf = "multiple_dano", 0.66
        elif r > 1.20:
            cat, conf = "dano_carroceria", 0.68
        else:
            cat, conf = "dano_carroceria", 0.62

    # ── 5. SIN DAÑO IDENTIFICABLE ───────────────────────────────────────────
    else:
        cat, conf = "sin_dano_visible", 0.48

    # ── Severidad ────────────────────────────────────────────────────────────
    if d > 0.40 or (e > 0.18 and v > 0.30):
        sev = "grave"
    elif d > 0.20 or e > 0.12:
        sev = "moderado"
    else:
        sev = "leve"

    return cat, sev, conf


_DESCRIPCIONES: dict[str, str] = {
    "dano_carroceria":  "Se detectaron deformaciones o marcas de impacto en la carrocería.",
    "llanta_dano":      "Se identificó posible daño o desgaste en los neumáticos o ruedas.",
    "motor_humo":       "Se detectaron indicios de humo, aceite o problemas en el compartimento del motor.",
    "vidrio_roto":      "Se identificó rotura o daño en los vidrios o parabrisas del vehículo.",
    "multiple_dano":    "El análisis detecta daños en múltiples zonas del vehículo.",
    "sin_dano_visible": "No se identificaron daños visibles significativos en la imagen.",
}


def analizar(imagen_bytes: bytes) -> dict:
    """Analiza una foto de incidente vial.

    Returns:
        {categoria, etiqueta_es, severidad, confianza, descripcion_auto, advertencia}
    """
    try:
        from PIL import Image
        img  = Image.open(io.BytesIO(imagen_bytes))
        feat = _extraer_features(img)
        cat, sev, conf = _clasificar(feat)
        return {
            "categoria":       cat,
            "etiqueta_es":     _CATEGORIAS.get(cat, cat),
            "severidad":       sev,
            "severidad_es":    _SEVERIDAD_ES.get(sev, sev),
            "confianza":       round(conf, 3),
            "descripcion_auto": _DESCRIPCIONES.get(cat, ""),
            "advertencia":     "Análisis preliminar automático — requiere verificación del técnico.",
        }
    except Exception as exc:
        logger.error("Error analizando imagen: %s", exc)
        return {
            "categoria":       "sin_clasificar",
            "etiqueta_es":     "Sin clasificar",
            "severidad":       "desconocido",
            "severidad_es":    "Desconocido",
            "confianza":       0.0,
            "descripcion_auto": "No se pudo procesar la imagen correctamente.",
            "advertencia":     str(exc),
        }
