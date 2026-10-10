"""
Descarga del último resultado generado en Google Flow.

Flujo: hover card → click more_vert → menuitem Descargar → sub-menú resolución

SELECTORES CRÍTICOS (validados 2026-04-15):
  - more_vert: el ícono es texto <i>more_vert</i>, NO atributo data-icon
  - menuitem: usa role="menuitem", NO el elemento custom <menuitem>
"""
import asyncio
from pathlib import Path
import re
import shutil
import time
from .browser import get_page
from . import settings

SEL_RESULT_CARD = 'flow-grid-tile-container, flow-tile-container, [aria-roledescription="draggable"]'
SEL_MORE_MENU   = 'button[aria-label="Más opciones"], button:has(:is(i, mat-icon):text-is("more_vert"))'
SEL_DL_ITEM     = '[role="menuitem"]:has-text("Descargar"), [role="menuitem"]:has-text("Transferir"), [role="menuitem"]:has-text("Descarregar"), [role="menuitem"]:has-text("Download"), button:has(:is(i, mat-icon):text-is("download"))'
SEL_DL_RES = {
    "1K": '[role="menuitem"]:has-text("1K")',
    "2K": '[role="menuitem"]:has-text("2K")',
    "720p": '[role="menuitem"]:has-text("720p")',
}


# Pipeline: só tiles COM imagem (exclui cards de erro e spinners). A ordem do
# DOM é da mais nova para a mais antiga.
SEL_READY_IMAGE_CARD = (
    'flow-tile-container:has(flow-image-tile img), '
    'flow-grid-tile-container:has(flow-image-tile img), '
    'flow-tile-container:has(img.image), '
    'flow-grid-tile-container:has(img.image)'
)


def _sel_card(is_video: bool = False) -> str:
    """Card del asset. El filtro `:has(...)` salta las cards de error de Flow,
    que no tienen miniatura."""
    if is_video:
        return (
            'flow-tile-container:has(flow-video-tile), '
            'flow-tile-container:has(video), '
            'flow-grid-tile-container:has(flow-video-tile), '
            'flow-grid-tile-container:has(video), '
            '[aria-roledescription="draggable"]:has(video), '
            'flow-tile-container, '
            'flow-grid-tile-container, '
            '[aria-roledescription="draggable"]'
        )
    return (
        'flow-tile-container:has(flow-image-tile), '
        'flow-tile-container:has(img.image), '
        'flow-grid-tile-container:has(flow-image-tile), '
        'flow-grid-tile-container:has(img.image), '
        'flow-tile-container, '
        'flow-grid-tile-container, '
        '[aria-roledescription="draggable"], mat-card, flow-media-card'
    )


def _sel_menu_item(icono: str) -> str:
    """Entrada del menú por su ligadura de ícono o texto."""
    if icono == "download":
        return ':is([role="menuitem"], button):has(:is(i, mat-icon):text-is("download")), [role="menuitem"]:has-text("Descargar"), [role="menuitem"]:has-text("Transferir"), [role="menuitem"]:has-text("Descarregar"), [role="menuitem"]:has-text("Download")'
    return f':is([role="menuitem"], button, [role="button"]):has(:is(i, mat-icon):text-is("{icono}"))'


async def _open_card_menu(page, card, is_video: bool = False, caller: str = "download") -> None:
    """Abre el menú de acciones de una card (Descargar, Reutilizar, Papelera…)."""
    dl_sel = _sel_menu_item("download")
    prefix = "[download 2/5]" if caller == "download" else "[cleanup]"

    # Fecha qualquer menu residual do download anterior: um menu aberto
    # pertence à card ANTERIOR e clicar nele não descarrega esta card
    # (foi a causa dos timeouts de 60s nos batches multi-imagem — o
    # "Menú ya desplegado" retornava cedo e o clique caía no menu velho).
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.4)
    except Exception:
        pass

    # Si hay algún menú residual abierto (ej. menú de "Borrar" de clic derecho previo), cerrarlo con Escape
    try:
        borrar_popup = page.locator(':is([role="menu"], [role="menuitem"]):has-text("Borrar")')
        if await borrar_popup.count() > 0 and await borrar_popup.first.is_visible():
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
    except Exception:
        pass

    await card.scroll_into_view_if_needed()

    # 1. Hover sobre la card para revelar la botonera flotante superior con los 3 puntos (more_vert)
    print(f"  {prefix} Hover sobre la card para revelar botón de 3 puntos (more_vert)...")
    more_btn = card.locator(
        'button[aria-label*="opciones" i], '
        'button[aria-label*="options" i], '
        'button[aria-label*="Más" i], '
        'button:has(:is(i, mat-icon):text-is("more_vert")), '
        'button:has(:is(i, mat-icon):text-is("more_horiz")), '
        'button[aria-haspopup="menu"]'
    ).first

    for hover_attempt in range(3):
        box = await card.bounding_box()
        if box:
            if hover_attempt == 0:
                await page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
            elif hover_attempt == 1:
                # Esquina superior derecha donde vive la píldora de acciones
                await page.mouse.move(box["x"] + box["width"] * 0.85, box["y"] + box["height"] * 0.15)
            else:
                await page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.2)
        else:
            await card.hover()

        await asyncio.sleep(0.4)
        if await more_btn.count() > 0 and await more_btn.is_visible():
            break

    if await more_btn.count() > 0 and await more_btn.is_visible():
        print(f"  {prefix} Haciendo clic en botón de 3 puntos (more_vert)...")
        await more_btn.click()
        await asyncio.sleep(0.5)
        if await page.locator(dl_sel).count() > 0 and await page.locator(dl_sel).first.is_visible():
            print(f"  {prefix} Menú desplegado con éxito vía botón 3 puntos.")
            return

    # Fallback solo para imágenes: el clic derecho en video solo tiene "Borrar"
    if not is_video:
        print(f"  {prefix} Fallback: probando clic derecho en la card de imagen...")
        await card.click(button="right", force=True)
        await asyncio.sleep(0.5)
        if await page.locator(dl_sel).count() > 0 and await page.locator(dl_sel).first.is_visible():
            print(f"  {prefix} Menú desplegado con éxito vía clic derecho.")
            return
        await page.keyboard.press("Escape")

    if caller == "download":
        raise RuntimeError("No pude abrir el menú con opción 'Descargar' en la card tras varios intentos.")


def _unwrap_zip(src: Path) -> Path | None:
    """Flow ahora entrega la descarga como ZIP (p. ej. 'transferência.zip').
    Extrae la primera imagen del archivo y devuelve su ruta temporal."""
    import os
    import tempfile
    import zipfile
    try:
        if not zipfile.is_zipfile(str(src)):
            return None
        with zipfile.ZipFile(str(src)) as zf:
            miembros = [n for n in zf.namelist()
                        if not n.endswith("/")
                        and Path(n).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov")]
            if not miembros:
                return None
            fd, tmp_name = tempfile.mkstemp(suffix=Path(miembros[0]).suffix.lower())
            os.close(fd)  # en Windows o ficheiro fica preso se o fd ficar aberto
            destino = Path(tmp_name)
            with zf.open(miembros[0]) as fh, open(destino, "wb") as out:
                shutil.copyfileobj(fh, out)
            print(f"  [download] ZIP desembrulhado: {miembros[0]} -> {destino}")
            return destino
    except Exception as err:
        print(f"  [download] ZIP ilegible ({err})")
        return None


def _save_to_dest(src_path: Path | str, dest_path: Path | str) -> None:
    """Copia o convierte a PNG si el destino es .png (desenvuelve ZIP si hace falta)."""
    src = Path(src_path)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Flow entrega ZIP: hay que sacar la imagen antes de nada.
    tmp = _unwrap_zip(src)
    try:
        real = tmp or src
        if dest.suffix.lower() == ".png":
            from PIL import Image
            try:
                with Image.open(real) as im:
                    im.save(str(dest), "PNG")
            except Exception as err:
                # Si ya venía en PNG (o PIL no entiende el formato), copia tal cual
                # antes de perder la generación entera.
                shutil.copy2(str(real), str(dest))
                print(f"  [download] PIL no pudo convertir ({err}); copiado tal cual")
            return
        shutil.copy2(str(real), str(dest))
    finally:
        if tmp:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


_JS_SRC_CARD = """el => Array.from(el.querySelectorAll('img'))
    .map(i => i.currentSrc || i.src)
    .filter(u => typeof u === 'string' && u.includes('/asb/'))"""
# Pipeline multi-card: o Flow serve as miniaturas via flow-content.google
# (assinadas, sem /asb/ no DOM) — qualquer src http da card serve.
_JS_SRC_CARD_ALL = """el => Array.from(el.querySelectorAll('img'))
    .map(i => i.currentSrc || i.src)
    .filter(u => typeof u === 'string' && u.startsWith('http'))"""
_JS_SRC_ALL = """() => Array.from(document.querySelectorAll('img'))
    .map(i => i.currentSrc || i.src)
    .filter(u => typeof u === 'string' && u.includes('/asb/'))"""


def _aspect(ratio) -> float | None:
    """'16:9' -> 1.777…"""
    if not ratio:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$", str(ratio))
    if not m:
        return None
    w, h = float(m.group(1)), float(m.group(2))
    return (w / h) if h else None


def _img_dims(data: bytes) -> tuple[int, int] | None:
    """Dimensiones (w, h) a partir de los bytes: PNG, JPEG o WebP."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = int.from_bytes(data[i + 2:i + 4], "big")
                if seg < 2:
                    return None
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    return (int.from_bytes(data[i + 7:i + 9], "big"),
                            int.from_bytes(data[i + 5:i + 7], "big"))
                i += 2 + seg
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
            chunk = data[12:16]
            if chunk == b"VP8X":
                return (1 + int.from_bytes(data[24:27], "little"),
                        1 + int.from_bytes(data[27:30], "little"))
            if chunk == b"VP8 ":
                return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                        int.from_bytes(data[28:30], "little") & 0x3FFF)
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except Exception:
        return None
    return None


def _src_variants(src: str) -> list[str]:
    """Variantes de tamaño: Flow expone miniaturas con sufijo '=sNNN'."""
    out = [src]
    m = re.search(r"=s(\d+)(?:-[a-z0-9]+)?$", src)
    if m:
        out.append(src[:m.start()] + "=s2048")
        out.append(src[:m.start()])
    elif "?" not in src:
        out.append(src + "=s2048")
    return out


async def _fetch_image_bytes(page, src: str) -> bytes | None:
    """Binario valido (>=50KB, magic bytes) de un src /asb/, probando variantes."""
    for url in _src_variants(src):
        try:
            resp = await page.context.request.get(url, timeout=60_000)
            if not resp.ok:
                continue
            data = await resp.body()
        except Exception:
            continue
        if len(data) < 50_000:
            continue
        if not (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"
                or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
            continue
        return data
    return None


async def _image_via_dom(page, card, output_path: str, ratio=None, timeout_s: int = 45, strict: bool = False) -> str | None:
    """Estrategia 0: pide el binario del <img> del resultado por HTTP (sin clics).

    - Busca en la card Y en toda la página (la lista de resultados es otra zona
      del DOM y su <img> pinta con retardo: por eso hay polling).
    - Con el ratio pedido (16:9…) elige la imagen que cuadra y DESCARTA la
      referencia subida (1:1), que si no se colaba como si fuera la escena.
    - `strict=True` (pipeline): mira SÓ a card dada, nunca o resto da página.
    Devuelve el path si guardo una imagen valida; None si hay que seguir con el
    flujo UI (nunca lanza).
    """
    target = _aspect(ratio)
    deadline = time.monotonic() + timeout_s
    cache: dict[str, bytes | None] = {}
    best = None  # (rank, bytes, data, src, dims)

    while True:
        srcs: list[str] = []
        try:
            card_srcs = await card.evaluate(_JS_SRC_CARD_ALL if strict else _JS_SRC_CARD)
        except Exception:
            card_srcs = []
        if strict:
            page_srcs = []
        else:
            try:
                page_srcs = await page.evaluate(_JS_SRC_ALL)
            except Exception:
                page_srcs = []
        for u in list(card_srcs or []) + list(page_srcs or []):
            if u and u not in srcs:
                srcs.append(u)

        for src in srcs[:6]:
            if src not in cache:
                print(f"  [download 0/1] Binario directo del DOM ({src.split('?')[0][:70]}...)")
                cache[src] = await _fetch_image_bytes(page, src)
            data = cache[src]
            if not data:
                continue
            dims = _img_dims(data)
            if target and dims and dims[1]:
                aspect = dims[0] / dims[1]
                tol = max(0.08, target * 0.06)
                rank = 0 if abs(aspect - target) <= tol else 1
            elif target:
                rank = 1  # sin dimensiones no se puede validar contra el ratio
            else:
                rank = 0
            cand = (rank, len(data), data, src, dims)
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand

        if best and best[0] == 0:
            break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(2)

    if best is None:
        print("  [download 0/1] Ningún <img src> de Flow disponible; flujo UI.")
        return None
    if best[0] != 0:
        print("  [download 0/1] Aviso: solo imágenes que no cuadran con el ratio pedido; flujo UI.")
        return None

    data = best[2]
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dims = best[4]
    dim_txt = f", {dims[0]}x{dims[1]}" if dims else ""
    print(f"  [download] OK directo desde DOM: {output_path} ({len(data)} bytes{dim_txt}).")
    await asyncio.sleep(0.4)
    return output_path


async def download_latest(output_path: str, resolution: str = "1K", is_video: bool = False, ratio=None, card_index: int = 0, strict: bool = False) -> str:
    """Descarga el último resultado. Retorna el path guardado. Debe llamarse con lock.

    `ratio` ('16:9'…) hace que Estrategia 0 elija la imagen que cuadra con lo
    pedido y descarte la referencia subida (que sigue en el DOM).
    `card_index`/`strict` (pipeline): em vez da card mais nova, descarrega a
    n-ésima card COM imagem (0 = mais nova); com `strict` a Estrategia 0 mira
    só essa card. O comportamento por omissão (0/False) é o de sempre."""
    page = await get_page()

    # O Flow (PT) abre o detalhe do asset em lightbox por cima do canvas (ver
    # _debug/01_referencia_1_a.png de 10/10/2026: painel de detalhe aberto na
    # hora do download). Com ele aberto o hover/more_vert da card não responde
    # e o download morre em "No se pudo guardar". Um Escape fecha-o; sem nada
    # aberto é inofensivo (o menu da card ainda não abriu a esta altura).
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    except Exception:
        pass

    print(f"  [download 1/5] Localizando card de {'video' if is_video else 'imagen'} (índice {card_index if isinstance(card_index, int) and card_index >= 0 else 0})...")
    _sel = SEL_READY_IMAGE_CARD if (strict and not is_video) else _sel_card(is_video)
    card = page.locator(_sel).nth(card_index if isinstance(card_index, int) and card_index >= 0 else 0)
    await card.wait_for(state="visible", timeout=30_000)

    # ────────────────────────────────────────────────────────────────────────
    # Estrategia 0 (Imagen): binario directo del <img> del resultado, SIN clics.
    # El flujo UI (hover -> Descargar) cerraba la pagina en Flow (PT) y la
    # descarga se perdia; el <img> apunta a flow.google.com/asb/... (mismo
    # origen, cookies de la sesion incluidas) y se puede pedir por HTTP.
    # ────────────────────────────────────────────────────────────────────────
    if not is_video:
        saved0 = await _image_via_dom(page, card, output_path, ratio=ratio, strict=strict)
        if saved0:
            return saved0

    # ────────────────────────────────────────────────────────────────────────
    # Estrategia 1 (Directa para Video): CDN directo en el tag <video src="...">
    # ────────────────────────────────────────────────────────────────────────
    if is_video:
        # Bucle de Paciencia: Si la card de video aún está en render (Fases 1, 2 o 3),
        # esperar pacientemente en vez de quemar reintentos en falso.
        max_patience = 240
        p_start = time.monotonic()
        while time.monotonic() - p_start < max_patience:
            text = ""
            try:
                text = await card.inner_text()
            except Exception:
                pass

            # Si hay porcentaje (ej. 9%, 20%, 26%), el video sigue procesando en Flow
            pct_match = re.search(r'\b\d+%\b', text)
            if pct_match:
                pct = pct_match.group(0)
                elapsed = int(time.monotonic() - p_start)
                print(f"  [download] Video en render en Flow ({pct}, {elapsed}s). Esperando...")
                await asyncio.sleep(5.0)
                continue

            # Comprobar si el tag <video> ya tiene su enlace CDN firmado
            video_el = card.locator("video").first
            if await video_el.count() > 0:
                video_url = await video_el.get_attribute("src") or ""
                if not video_url:
                    video_url = await page.evaluate("(v) => v.currentSrc || v.src", video_el) or ""
                if (video_url.startswith("http://") or video_url.startswith("https://")) and len(video_url) > 40:
                    print(f"  [download] ¡Video terminado y listo en el reproductor! Procediendo...")
                    break
                elif video_url.startswith("blob:") and len(video_url) > 50:
                    print(f"  [download] ¡Video terminado con buffer blob listo! Procediendo...")
                    break

            # Despertar el reproductor de video de Flow: Flow requiere hover sobre la card
            # para inyectar el <video src="https://..."> (lazy loading).
            try:
                await card.hover(force=True)
                await page.evaluate("""(el) => {
                    if (!el) return;
                    el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                    el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                    const v = el.querySelector('video') || el.querySelector('flow-video-tile');
                    if (v) {
                        v.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                        v.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                    }
                }""", card)
            except Exception:
                pass

            # Si no hay porcentaje ni src, Flow está en el desenfoque inicial o en el revelado final
            elapsed = int(time.monotonic() - p_start)
            if elapsed > 15:
                more_btn = card.locator('button[aria-label*="opciones" i], button:has(:is(i, mat-icon):text-is("more_vert"))').first
                if await more_btn.count() > 0 and await more_btn.is_visible():
                    break

            print(f"  [download] Asentando render de video en Flow ({elapsed}s)...")
            await asyncio.sleep(3.0)

        video_el = card.locator("video").first
        if await video_el.count() > 0:
            video_url = await video_el.get_attribute("src")
            if not video_url:
                video_url = await page.evaluate("(v) => v.currentSrc || v.src", video_el)

            if video_url and (video_url.startswith("http://") or video_url.startswith("https://")):
                print(f"  [download] Enlace CDN directo detectado en <video src>. Descargando MP4...")
                try:
                    resp = await page.request.get(video_url, timeout=60_000)
                    if resp.ok:
                        body = await resp.body()
                        if len(body) >= 50_000:
                            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                            with open(output_path, "wb") as f:
                                f.write(body)
                            sz = Path(output_path).stat().st_size
                            print(f"  [download] OK: Video descargado exitosamente vía CDN directo en {output_path} ({sz} bytes).")
                            await asyncio.sleep(1.0)
                            return output_path
                except Exception as cdn_err:
                    print(f"  [download] Aviso: Descarga CDN directa lanzó ({cdn_err}); continuando con flujo UI...")

            elif video_url and video_url.startswith("blob:"):
                print(f"  [download] Enlace blob detectado en <video src>. Extrayendo buffer...")
                try:
                    import base64
                    blob_b64 = await page.evaluate("""async (url) => {
                        const res = await fetch(url);
                        const blob = await res.blob();
                        return new Promise((resolve, reject) => {
                            const reader = new FileReader();
                            reader.onloadend = () => resolve(reader.result.split(',')[1]);
                            reader.onerror = reject;
                            reader.readAsDataURL(blob);
                        });
                    }""", video_url)
                    if blob_b64:
                        data = base64.b64decode(blob_b64)
                        if len(data) >= 50_000:
                            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                            with open(output_path, "wb") as f:
                                f.write(data)
                            sz = Path(output_path).stat().st_size
                            print(f"  [download] OK: Video descargado exitosamente vía blob buffer en {output_path} ({sz} bytes).")
                            await asyncio.sleep(1.0)
                            return output_path
                except Exception as blob_err:
                    print(f"  [download] Aviso: Extracción blob lanzó ({blob_err}); continuando con flujo UI...")

    # ────────────────────────────────────────────────────────────────────────
    # Estrategia 2: Flujo UI interactivo (Menú 3 puntos -> Descargar -> Submenú)
    # ────────────────────────────────────────────────────────────────────────
    await _open_card_menu(page, card, is_video=is_video)

    dl_item = page.locator(_sel_menu_item("download")).first
    if await dl_item.count() == 0:
        dl_item = page.locator(SEL_DL_ITEM).first
    if await dl_item.count() == 0:
        await page.keyboard.press("Escape")
        raise RuntimeError("No encontré la opción 'Descargar' en el menú de la card.")

    candidatos = [SEL_DL_RES.get(resolution)] + [
        SEL_DL_RES[k] for k in SEL_DL_RES if k != resolution
    ]
    if is_video:
        candidatos.append('[role="menuitem"]:has-text("Tamaño original")')
    candidatos = [c for c in candidatos if c]

    # Desplegar el submenú de resoluciones con hover o click ANTES de esperar la descarga
    print("  [download 3/5] Desplegando submenú sobre 'Descargar'...")
    sub_visible = False
    try:
        await dl_item.hover(timeout=2_000)
        await asyncio.sleep(0.4)
        for sel in candidatos:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                sub_visible = True
                break
    except Exception:
        pass

    if not sub_visible:
        try:
            print("  [download 3/5] Hover no abrió submenú; haciendo clic en 'Descargar'...")
            await dl_item.click()
            await asyncio.sleep(0.4)
        except Exception:
            pass

    # Localizar la opción de resolución deseada
    target_loc = None
    elegido = None
    for sel in candidatos:
        loc = page.locator(sel).first
        try:
            await loc.wait_for(state="visible", timeout=4_000)
            target_loc = loc
            elegido = sel
            print(f"  [download 4/5] Opción encontrada en submenú: {sel}")
            break
        except Exception:
            continue

    if not target_loc:
        print("  [download 4/5] Submenú no visible; se usará 'Descargar' directo.")
        target_loc = dl_item

    # Disparar la descarga dentro del listener oficial de Playwright
    print(f"  [download 5/5] Clic en la opción de descarga y esperando evento...")
    async with page.expect_download(timeout=60_000) as info:
        await target_loc.click()

    download = await info.value
    suggested = getattr(download, "suggested_filename", "archivo")
    print(f"  [download] Evento de descarga recibido ({suggested}). Guardando...")

    # Guardar en output_path
    saved = False

    # 1. Intentar download.path() directo si Playwright lo tiene en caché
    try:
        p = await download.path()
        if p and Path(p).exists() and Path(p).stat().st_size > 50_000:
            _save_to_dest(p, output_path)
            saved = True
            print(f"  [download] Guardado directo vía download.path() en {output_path}")
    except Exception:
        pass

    # 2. Intentar save_as de Playwright
    if not saved:
        temp_path = Path(f"{output_path}.download")
        try:
            await download.save_as(str(temp_path))
            _save_to_dest(temp_path, output_path)
            if Path(output_path).exists() and Path(output_path).stat().st_size > 50_000:
                saved = True
                print(f"  [download] OK: Guardado vía save_as en {output_path}")
        except Exception as err:
            print(f"  [download] Aviso: save_as lanzó ({err}); buscando archivo en disco...")
        finally:
            temp_path.unlink(missing_ok=True)

    # 3. Rescate por Filesystem (busca en carpeta de perfil y Downloads del usuario)
    if not saved:
        search_dirs = [
            Path(settings.FLOW_CHROME_PROFILE).parent / "downloads" if settings.FLOW_CHROME_PROFILE else Path("downloads"),
            Path.home() / "Downloads",
        ]
        for sec in range(25):
            for sdir in search_dirs:
                if not sdir.exists():
                    continue
                # Coincidencia exacta por nombre sugerido
                if suggested and (sdir / suggested).exists() and (sdir / suggested).stat().st_size > 50_000:
                    _save_to_dest(sdir / suggested, output_path)
                    saved = True
                    break
                # Búsqueda de archivos recién descargados (incluyendo .tmp de Playwright y .jpeg)
                recientes = sorted(
                    [f for f in sdir.iterdir() if f.is_file() and not f.name.endswith('.crdownload') and f.suffix.lower() in ('.tmp', '.jpeg', '.jpg', '.png', '.mp4', '.webp', '.zip')],
                    key=lambda x: x.stat().st_mtime,
                    reverse=True
                )
                for cand in recientes[:6]:
                    if (time.time() - cand.stat().st_mtime) < 120 and cand.stat().st_size > 50_000:
                        try:
                            with open(cand, "rb") as cf:
                                header = cf.read(8)
                            # Validar que sea imagen (JPEG, PNG) o video (MP4/WebM)
                            if header.startswith(b'\xff\xd8') or header.startswith(b'\x89PNG') or header.startswith(b'RIFF') or header[4:8] == b'ftyp':
                                _save_to_dest(cand, output_path)
                                saved = True
                                print(f"  [download] Rescatado desde archivo temporal: {cand.name}")
                                break
                        except Exception:
                            pass
                if saved:
                    break
            if saved:
                break
            await asyncio.sleep(0.5)

    if not saved:
        raise RuntimeError(f"No se pudo guardar la descarga en {output_path}")

    sz = Path(output_path).stat().st_size if Path(output_path).exists() else 0
    print(f"  [download] OK: Lámina guardada con éxito en {output_path} ({sz} bytes).")

    # Asegurar que el contexto apunte de nuevo a la pestaña del proyecto Flow y cerrar about:blank
    try:
        from . import browser
        if browser._context:
            flow_pages = [p for p in browser._context.pages if not p.is_closed() and "/project/" in p.url]
            blank_pages = [p for p in browser._context.pages if not p.is_closed() and "about:blank" in p.url]
            if flow_pages and blank_pages:
                for bp in blank_pages:
                    try:
                        await bp.close()
                    except Exception:
                        pass
                browser._page = flow_pages[0]
                try:
                    await browser._page.bring_to_front()
                except Exception:
                    pass
    except Exception:
        pass

    # Pausa limpia en Python para asentar el navegador antes de la siguiente imagen
    await asyncio.sleep(1.5)
    return output_path


async def delete_latest_card(is_video: bool = False) -> None:
    """Borra el último resultado generado en caso de error (fallback) para reintentar.

    No fatal: si el menú cambió y no se puede borrar, se sigue adelante — perder
    la limpieza es molesto, tumbar el run por eso sería peor.
    """
    page = await get_page()

    card = page.locator(_sel_card(is_video)).first
    if await card.count() == 0:
        return

    print(f"  [cleanup] Intento fallido previo: limpiando card incompleta ({'video' if is_video else 'imagen'})...")
    try:
        if is_video:
            # En video, el clic derecho abre directamente el menú con "Borrar"
            await card.click(button="right", force=True)
            await asyncio.sleep(0.5)
            borrar_btn = page.locator(':is([role="menu"], [role="menuitem"], button):has-text("Borrar")').first
            if await borrar_btn.count() > 0 and await borrar_btn.is_visible():
                await borrar_btn.click()
                await page.wait_for_timeout(1_000)
                print("  [cleanup] OK: Card de video eliminada con éxito vía 'Borrar'.")
                return

        await _open_card_menu(page, card, is_video=is_video, caller="cleanup")
        del_item = page.locator(_sel_menu_item("delete")).first
        if await del_item.count() == 0:
            del_item = page.locator('[role="menuitem"]:has-text("Eliminar"), [role="menuitem"]:has-text("Borrar")').first
        if await del_item.count() > 0:
            await del_item.click()
            await page.wait_for_timeout(1_000)
            print("  [cleanup] OK: Card eliminada con éxito.")
        else:
            await page.keyboard.press("Escape")
    except Exception as e:
        print(f"  [cleanup] Aviso: No se pudo eliminar la card ({e}). Continuando.")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
