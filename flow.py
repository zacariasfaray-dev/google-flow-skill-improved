#!/usr/bin/env python
"""
flow.py - CLI para manejar Google Flow (labs.google) con Playwright.

Esta es la UNICA puerta de entrada. Un agente de IA NO necesita escribir
codigo de Playwright: solo llama a estos comandos.

Comandos
--------
  python flow.py login
      Abre el navegador para loguearte en Google con tu cuenta. La sesion
      queda guardada de forma PERMANENTE en session/flowbot-profile.
      Solo se hace una vez (o cuando caduque la sesion).

  python flow.py status
      Dice si ya hay sesion guardada.

  python flow.py image --prompt "..." [--ratio 9:16] [--model "Nano Banana 2 Lite"]
                        [--image referencia.png] [--name escena1] [--out outputs]
      Genera UNA imagen (texto->imagen, o imagen+prompt->imagen si das --image).

  python flow.py video --prompt "..." [--ratio 9:16] [--model "Veo 3.1 - Lite"]
                        [--duration 8s]
                        [--start frame.png] [--end frame_final.png]
                        [--name escena1] [--out outputs]
      Genera UN video. Sin --start = texto->video. Con --start = anima esa
      imagen. Con --start y --end = interpola inicio->fin.

  python flow.py batch guion.json [--out outputs]
      Ejecuta una lista de trabajos EN ORDEN dentro de UN solo proyecto Flow.
      Ver examples/guion_ejemplo.json para el formato.

Modelos validos
---------------
  Imagen: "Nano Banana 2 Lite", "Nano Banana 2", "Nano Banana Pro", "Imagen 4"
  Video : "Veo 3.1 - Lite", "Veo 3.1 - Fast", "Veo 3.1 - Quality", "Omni 1.1 Flash"
  Ratios: "9:16", "16:9", "1:1", "4:3", "3:4"
"""
import argparse
import asyncio
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

import flow_provider as flow
from flow_provider import settings

# Consola UTF-8 en Windows (acentos/emojis sin romper la salida).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent.resolve()
DEFAULT_OUT = BASE_DIR / "outputs"
# URL base de Flow - se adapta según el locale
FLOW_BASE_URL = "https://labs.google/fx"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in ("_", "-", ".")) or _stamp()


def _out_path(out_dir: Path, name: str, ext: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir / f"{_safe(name)}.{ext}")


def _resolve_asset(project_dir: Path, ref: str | None) -> str | None:
    """Resuelve una referencia a un asset (start/end/image) dentro del batch.

    Si 'ref' existe tal cual, se usa. Si no, se busca por nombre dentro de la
    carpeta del proyecto (con o sin .png). Asi el guion puede encadenar solo
    con el 'name' de un job anterior, sin rutas largas.
    """
    if not ref:
        return ref
    p = Path(ref)
    if p.exists():
        return str(p)
    cand = project_dir / p.name
    if cand.exists():
        return str(cand)
    if not cand.suffix:
        cand_png = project_dir / f"{p.name}.png"
        if cand_png.exists():
            return str(cand_png)
    return ref  # se deja igual; fallara con error claro si no existe


def session_exists() -> bool:
    profile = Path(settings.FLOW_CHROME_PROFILE)
    return profile.exists() and any(profile.iterdir())


# ---------------------------------------------------------------------------
# login / status
# ---------------------------------------------------------------------------
async def cmd_login(_args) -> int:
    profile = Path(settings.FLOW_CHROME_PROFILE)
    profile.mkdir(parents=True, exist_ok=True)
    
    # Limpeza preventiva de travas de perfil do Chrome
    for lock_name in ["SingletonLock", "lock", "Lock"]:
        lock_file = profile / lock_name
        if lock_file.exists():
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass

    print("=== LOGIN GOOGLE FLOW ===", flush=True)
    print(f"Perfil persistente: {profile}", flush=True)
    print("Iniciando janela do navegador Chrome...", flush=True)
    print()

    async with async_playwright() as p:
        launch_kwargs = {
            "user_data_dir": str(profile),
            "headless": False,
            "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            "viewport": {"width": 1280, "height": 900},
        }
        try:
            ctx = await p.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
        except Exception as err_chrome:
            print(f"  (Aviso Chrome nativo: {err_chrome}. Tentando Chromium padrão...)", flush=True)
            ctx = await p.chromium.launch_persistent_context(**launch_kwargs)

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        locale = _args.locale if hasattr(_args, 'locale') and _args.locale else getattr(settings, 'FLOW_LOCALE', 'es-419')
        flow_url = f"{FLOW_BASE_URL}/{locale}/tools/flow"
        print(f"  -> Abrindo URL de login: {flow_url}", flush=True)
        
        try:
            await page.goto(flow_url, wait_until="commit", timeout=60000)
        except Exception as e:
            print(f"  -> (Aviso navegação inicial: {e})", flush=True)

        try:
            await page.bring_to_front()
        except Exception:
            pass

        print("\n" + "="*65, flush=True)
        print("  NAVEGADOR ABERTO COM SUCESSO!", flush=True)
        print("  1. Olhe na sua barra de tarefas do Windows e acesse a janela do Chrome.")
        print("  2. Faça o login na sua conta do Google com calma.")
        print("  3. Quando terminar o login, pressione ENTER aqui neste terminal.")
        print("="*65 + "\n", flush=True)
        
        # O Chrome fica permanentemente aberto até o usuário decidir pressionar ENTER no terminal
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, input, "  [+] Pressione ENTER aqui no terminal APÓS concluir o login no Chrome: ")
        except (KeyboardInterrupt, EOFError):
            pass

        try:
            await page.wait_for_timeout(1000)
            print("  -> Salvando sessão e perfil...", flush=True)
            await ctx.close()
        except Exception:
            pass

    print("OK: sessão guardada com sucesso! Agora você já pode gerar imagens e vídeos.", flush=True)
    return 0




async def cmd_status(_args) -> int:
    if session_exists():
        print(f"OK: sesion presente en {settings.FLOW_CHROME_PROFILE}")
        return 0
    print("SIN SESION. Corre primero: python flow.py login")
    return 1


# ---------------------------------------------------------------------------
# Generadores atomicos (asumen proyecto YA abierto)
# ---------------------------------------------------------------------------
async def _gen_image(prompt, ratio, model, count, image, out_path) -> str:
    if count != 1:
        raise ValueError("Esta CLI descarga una imagen por trabajo. Para un lote, usa varios jobs image con count=1.")
    await flow.select_image_mode(aspect_ratio=ratio, count=count, model=model)
    if image:
        await flow.upload_standalone_image(image)
    pre = await flow.get_canvas_count()
    await flow.submit_prompt(prompt)
    await flow.wait_for_image(pre_submit_count=pre)
    return await flow.download_latest(out_path, resolution="1K", is_video=False, ratio=ratio)


async def _gen_video(prompt, ratio, model, count, start, end, out_path, duration=None) -> str:
    if count != 1:
        raise ValueError("Esta CLI descarga un video por trabajo. Para un lote, usa varios jobs video con count=1.")
    if start and end:
        await flow.select_video_mode(mode="fotogramas", model=model, aspect_ratio=ratio, count=count, duration=duration)
        await flow.upload_frame(start, slot="initial")
        await asyncio.sleep(2)  # Reducido de 3 a 2 segundos
        await flow.upload_frame(end, slot="final")
    elif start:
        await flow.select_video_mode(mode="fotogramas", model=model, aspect_ratio=ratio, count=count, duration=duration)
        await flow.upload_frame(start, slot="initial")
    else:
        await flow.select_video_mode(mode="texto", model=model, aspect_ratio=ratio, count=count, duration=duration)
    pre = await flow.get_canvas_count()
    await flow.submit_prompt(prompt)
    await flow.wait_for_video(pre_submit_count=pre)
    return await flow.download_latest(out_path, resolution="720p", is_video=True)


# ---------------------------------------------------------------------------
# PIPELINE de imagens — submits sobrepostos
# ---------------------------------------------------------------------------
# Em vez de enviar → esperar ficar pronta → descarregar → próximo, o pipeline
# dispara todos os prompts espaçados `gap` segundos (o Chrome fica aberto e o
# Flow gera em fundo), espera o conjunto e descarrega no fim. O mapeamento
# job<->card assume ordem de conclusão = ordem de envio — sempre certo com
# prompts iguais (o caso da geração em massa) e o comportamento normal do Flow
# com prompts diferentes.
async def _submit_image(prompt, ratio, model, image) -> None:
    """Só configura e ENVIA (sem esperar nem descarregar). Uma repetição se o
    campo ainda estiver preso da geração anterior."""
    await flow.select_image_mode(aspect_ratio=ratio, count=1, model=model)
    if image:
        await flow.upload_standalone_image(image)
    try:
        await flow.submit_prompt(prompt)
    except Exception:
        await asyncio.sleep(10)
        await flow.submit_prompt(prompt)


async def _wait_mount(pre, tries=15):
    """A card de loading monta-se logo ao enviar; sem card nova o submit foi
    engolido (Flow saturado). Devolve True se o canvas cresceu."""
    for _ in range(tries):
        await asyncio.sleep(2)
        try:
            if await flow.get_canvas_count() > pre:
                return True
        except Exception:
            pass
    return False


async def _run_image_pipeline(project_dir, jobs, total, d_ratio, d_img_model, gap):
    submitted = []
    results = []
    for i, job in enumerate(jobs, 1):
        name = job.get("name") or f"image_{i:02d}"
        prompt = job.get("prompt", "")
        ratio = job.get("ratio", d_ratio)
        model = job.get("model", d_img_model)
        print(f"\n--- [{i}/{total}] image :: {name} ---")
        if int(job.get("count", 1)) != 1:
            err = "Esta CLI descarga una imagen por trabajo. Para un lote, usa varios jobs image con count=1."
            print(f"  ERROR en '{name}': {err}")
            results.append({"name": name, "type": "image", "ok": False, "error": err})
            continue
        try:
            pre = await flow.get_canvas_count()
            await _submit_image(
                prompt, ratio, model,
                _resolve_asset(project_dir, job.get("image")),
            )
            if not await _wait_mount(pre):
                print("  [pipeline] sem card nova no canvas — a reenviar uma vez...")
                await _submit_image(
                    prompt, ratio, model,
                    _resolve_asset(project_dir, job.get("image")),
                )
                if not await _wait_mount(pre, tries=5):
                    raise Exception("o envio não montou card no canvas (Flow saturado?)")
            submitted.append({"name": name, "ratio": ratio})
            print(f"  enviado (pipeline {i}/{total})")
        except Exception as e:
            print(f"  ERROR en '{name}': {e}")
            results.append({"name": name, "type": "image", "ok": False, "error": str(e)})
        if i < len(jobs) and gap > 0:
            print(f"[pipeline] próximo envio em {gap}s (o Chrome fica aberto, a gerar em fundo)...")
            for remaining in range(gap, 0, -5):
                print(f"  ... {remaining}s")
                await asyncio.sleep(min(5, remaining))

    n = len(submitted)
    if n == 0:
        return results

    print(f"[pipeline] {n} prompts enviados. A esperar as {n} imagens...")
    try:
        ready = await flow.wait_for_n_images(n, timeout_ms=120000 + n * 90000)
    except Exception as e:
        print(f"  [pipeline] recolha parcial: {e}")
        try:
            debug_dir = project_dir / "_debug"
            debug_dir.mkdir(exist_ok=True)
            page = await flow.get_page()
            await page.screenshot(path=str(debug_dir / "_collect.png"))
            (debug_dir / "_collect.html").write_text(await page.content(), encoding="utf-8")
            print(f"  Diagnóstico guardado en {debug_dir}")
        except Exception as debug_error:
            print(f"  No se pudo guardar el diagnóstico: {debug_error}")
        try:
            ready = await flow.count_ready_images()
        except Exception:
            ready = 0
        if ready <= 0:
            for s in submitted:
                results.append({"name": s["name"], "type": "image", "ok": False, "error": str(e)})
            return results
        print(f"  [pipeline] {ready}/{n} prontas — a descarregar essas; as restantes ficam como falha.")

    # Descarrega da mais ANTIGA para a mais recente (o DOM ordena da mais nova
    # para a mais antiga, daí o índice invertido).
    for i, s in enumerate(submitted):
        if i >= ready:
            results.append({"name": s["name"], "type": "image", "ok": False,
                            "error": f"só {ready}/{n} imagens ficaram prontas no prazo"})
            continue
        out_path = _out_path(project_dir, s["name"], "png")
        try:
            saved = await flow.download_latest(
                out_path, resolution="1K", is_video=False, ratio=s["ratio"],
                card_index=(ready - 1 - i), strict=True,
            )
            print(f"  OK -> {saved}")
            results.append({"name": s["name"], "type": "image", "file": saved, "ok": True})
        except Exception as e:
            print(f"  ERROR en '{s['name']}': {e}")
            debug_dir = project_dir / "_debug"
            try:
                debug_dir.mkdir(exist_ok=True)
                page = await flow.get_page()
                debug_name = f"{i + 1:02d}_{_safe(s['name'])}"
                await page.screenshot(path=str(debug_dir / f"{debug_name}.png"))
                (debug_dir / f"{debug_name}.html").write_text(await page.content(), encoding="utf-8")
                print(f"  Diagnóstico guardado en {debug_dir}")
            except Exception as debug_error:
                print(f"  No se pudo guardar el diagnóstico: {debug_error}")
            results.append({"name": s["name"], "type": "image", "ok": False, "error": str(e)})
    return results


# ---------------------------------------------------------------------------
# Comandos de generacion
# ---------------------------------------------------------------------------
async def cmd_image(args) -> int:
    if not session_exists():
        print("SIN SESION. Corre primero: python flow.py login")
        return 1
    out_dir = Path(args.out)
    name = args.name or f"imagen_{_stamp()}"
    out_path = _out_path(out_dir, name, "png")
    await flow.startup()
    try:
        await flow.create_project()
        saved = await _gen_image(args.prompt, args.ratio, args.model, args.count, args.image, out_path)
        print(f"OK imagen -> {saved}")
        return 0
    finally:
        await flow.shutdown()


async def cmd_video(args) -> int:
    if not session_exists():
        print("SIN SESION. Corre primero: python flow.py login")
        return 1
    out_dir = Path(args.out)
    name = args.name or f"video_{_stamp()}"
    out_path = _out_path(out_dir, name, "mp4")
    await flow.startup()
    try:
        await flow.create_project()
        saved = await _gen_video(args.prompt, args.ratio, args.model, args.count, args.start, args.end, out_path, args.duration)
        print(f"OK video -> {saved}")
        return 0
    finally:
        await flow.shutdown()


async def cmd_batch(args) -> int:
    if not session_exists():
        print("SIN SESION. Corre primero: python flow.py login")
        return 1

    data = json.loads(Path(args.jobfile).read_text(encoding="utf-8"))
    defaults = data.get("defaults", {})
    d_ratio = defaults.get("ratio", "9:16")
    d_img_model = defaults.get("image_model", "Nano Banana 2 Lite")
    d_vid_model = defaults.get("video_model", "Veo 3.1 - Lite")
    d_delay = defaults.get("delay", getattr(args, "delay", 25))
    jobs = data.get("jobs", [])

    # Cada proyecto va a su propia subcarpeta: facil de revisar y de borrar.
    project = data.get("project") or f"proyecto_{_stamp()}"
    project_dir = Path(args.out) / _safe(project)
    project_dir.mkdir(parents=True, exist_ok=True)

    print(f"BATCH '{project}': {len(jobs)} trabajos. Salida -> {project_dir}")
    print(f"Intervalo de delay configurado entre trabalhos: {d_delay}s")
    results = []
    await flow.startup()
    try:
        await flow.create_project()
        # Pipeline: 2+ imagens → envios sobrepostos (sem re-indentação do
        # caminho sequencial abaixo: o `continue` salta-o por inteiro).
        only_images = bool(jobs) and len(jobs) > 1 and all(j.get("type", "image") == "image" for j in jobs)
        if only_images:
            print(f"[pipeline] {len(jobs)} imagens: envios espaçados {d_delay}s sem esperar por cada uma.")
            results = await _run_image_pipeline(project_dir, jobs, len(jobs), d_ratio, d_img_model, d_delay)
        for i, job in enumerate(jobs, 1):
            if only_images:
                continue
            if i > 1:
                delay_sec = job.get("delay", d_delay)
                trigger_pct = job.get("trigger_percent", defaults.get("trigger_percent", getattr(args, "trigger_percent", 25)))
                if trigger_pct > 0:
                    print(f"\n[batch-25%] Aguardando a geração anterior atingir {trigger_pct}% (ou max {delay_sec}s) para enviar o próximo prompt...")
                    await flow.wait_until_percent(trigger_pct, timeout_ms=delay_sec * 1000)
                elif delay_sec > 0:
                    print(f"\n[batch] Aguardando {delay_sec}s antes da próxima geração para evitar limites de taxa e erros de política...")
                    for remaining in range(delay_sec, 0, -5):
                        print(f"  ... {remaining}s restantes")
                        await asyncio.sleep(min(5, remaining))


            jtype = job.get("type", "image")
            name = job.get("name") or f"{jtype}_{i:02d}"
            prompt = job.get("prompt", "")
            ratio = job.get("ratio", d_ratio)
            max_job_retries = int(job.get("max_retries", defaults.get("max_retries", 5)))
            print(f"\n--- [{i}/{len(jobs)}] {jtype} :: {name} ---")
            
            job_ok = False
            for retry_num in range(max_job_retries + 1):
                if retry_num > 0:
                    retry_delay = min(15 * retry_num, 90)
                    print(f"  [retry] Tentativa {retry_num + 1}/{max_job_retries + 1} para '{name}' em {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                try:
                    if jtype == "image":
                        out_path = _out_path(project_dir, name, "png")
                        saved = await _gen_image(
                            prompt, ratio, job.get("model", d_img_model),
                            int(job.get("count", 1)),
                            _resolve_asset(project_dir, job.get("image")), out_path,
                        )
                    elif jtype == "video":
                        out_path = _out_path(project_dir, name, "mp4")
                        saved = await _gen_video(
                            prompt, ratio, job.get("model", d_vid_model),
                            int(job.get("count", 1)),
                            _resolve_asset(project_dir, job.get("start")),
                            _resolve_asset(project_dir, job.get("end")), out_path,
                            job.get("duration", d_vid_duration),
                        )
                    else:
                        print(f"  tipo desconocido '{jtype}', saltando.")
                        break
                    print(f"  OK -> {saved}")
                    results.append({"name": name, "type": jtype, "file": saved, "ok": True})
                    job_ok = True
                    break
                except Exception as e:
                    err_msg = str(e)
                    is_policy = "política" in err_msg.lower() or "policy" in err_msg.lower()
                    if retry_num < max_job_retries:
                        tipo_erro = "política" if is_policy else "geração"
                        print(f"  ⚠️ Erro de {tipo_erro} em '{name}': {err_msg[:150]}")
                        print(f"  [retry] Será retentado automaticamente...")
                        continue
                    # Última tentativa falhou
                    print(f"  ERROR FINAL em '{name}' após {max_job_retries + 1} tentativas: {e}")
                    debug_dir = project_dir / "_debug"
                    try:
                        debug_dir.mkdir(exist_ok=True)
                        page = await flow.get_page()
                        debug_name = f"{i:02d}_{_safe(name)}"
                        await page.screenshot(path=str(debug_dir / f"{debug_name}.png"))
                        (debug_dir / f"{debug_name}.html").write_text(await page.content(), encoding="utf-8")
                        print(f"  Diagnóstico guardado en {debug_dir}")
                    except Exception as debug_error:
                        print(f"  No se pudo guardar el diagnóstico: {debug_error}")
            
            if not job_ok and jtype in ("image", "video"):
                results.append({"name": name, "type": jtype, "ok": False, "error": err_msg})

    finally:
        await flow.shutdown()

    report = project_dir / "batch_report.json"
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for r in results if r.get("ok"))
    print(f"\nBATCH terminado: {ok}/{len(results)} OK. Carpeta: {project_dir}")
    return 0 if ok == len(results) else 1


async def cmd_clean(args) -> int:
    out_dir = Path(args.out)
    if args.project:
        target = out_dir / _safe(args.project)
        if not target.exists():
            print(f"No existe: {target}")
            return 0
        shutil.rmtree(target)
        print(f"Borrado proyecto: {target}")
        return 0
    if not out_dir.exists():
        print(f"Nada que limpiar en {out_dir}")
        return 0
    borrados = 0
    for child in out_dir.iterdir():
        if child.name == "LEEME.txt":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        borrados += 1
    print(f"Limpiado {out_dir}: {borrados} elementos borrados.")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CLI Google Flow (Playwright).")
    # Agregar argumento de locale al parser principal para que esté disponible en todos los subcomandos
    p.add_argument("--locale", default=None, help="Locale para la interfaz de Flow (ej: en-US, es-419)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="Loguearse en Google Flow (una vez).")
    sub.add_parser("status", help="Ver si hay sesion guardada.")

    pi = sub.add_parser("image", help="Generar una imagen.")
    pi.add_argument("--prompt", required=True)
    pi.add_argument("--ratio", default="9:16")
    pi.add_argument("--model", default="Nano Banana 2 Lite")
    pi.add_argument("--count", type=int, default=1)
    pi.add_argument("--image", default=None, help="Imagen de referencia para editar.")
    pi.add_argument("--name", default=None)
    pi.add_argument("--out", default=str(DEFAULT_OUT))

    pv = sub.add_parser("video", help="Generar un video.")
    pv.add_argument("--prompt", required=True)
    pv.add_argument("--ratio", default="9:16")
    pv.add_argument("--model", default="Veo 3.1 - Lite")
    pv.add_argument("--count", type=int, default=1)
    pv.add_argument("--duration", default=None, help="Duracion del clip, por ejemplo 8s (si el modelo la admite).")
    pv.add_argument("--start", default=None, help="Fotograma inicial (imagen).")
    pv.add_argument("--end", default=None, help="Fotograma final (imagen).")
    pv.add_argument("--name", default=None)
    pv.add_argument("--out", default=str(DEFAULT_OUT))

    pb = sub.add_parser("batch", help="Ejecutar guion JSON de varios trabajos.")
    pb.add_argument("jobfile")
    pb.add_argument("--out", default=str(DEFAULT_OUT))
    pb.add_argument("--delay", type=int, default=25, help="Segundos de espera entre cada generacion (defecto: 25s).")
    pb.add_argument("--trigger-percent", type=int, default=25, help="Porcentaje de progreso para disparar el siguiente prompt (defecto: 25).")




    pc = sub.add_parser("clean", help="Borrar resultados de outputs.")
    pc.add_argument("project", nargs="?", default=None,
                    help="Nombre del proyecto a borrar. Vacio = limpiar todo outputs.")
    pc.add_argument("--out", default=str(DEFAULT_OUT))

    return p


HANDLERS = {
    "login": cmd_login,
    "status": cmd_status,
    "image": cmd_image,
    "video": cmd_video,
    "batch": cmd_batch,
    "clean": cmd_clean,
}


# ---------------------------------------------------------------------------
# :)
# ---------------------------------------------------------------------------
EASTER_EGG = r"""
   _   _ _   _ ____   ___
  | \ | | | | |  _ \ / _ \
  |  \| | | | | |_) | | | |
  | |\  | |_| |  <| |_| |
  |_| \_|\___/|_| \_\\___/     x   B R P L

  Google Flow Skill -- forjado por NURO para BRPL.
  El contexto es el verdadero superpoder. Disfruta y crea.
"""


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] in ("nuro", "brpl", "--credits"):
        print(EASTER_EGG)
        return 0
    args = build_parser().parse_args()
    # Si se pasa --locale en la linea de comandos, actualizar settings.FLOW_LOCALE para las funciones del provider
    if hasattr(args, "locale") and args.locale:
        settings.FLOW_LOCALE = args.locale
    return asyncio.run(HANDLERS[args.cmd](args))


if __name__ == "__main__":
    sys.exit(main())
