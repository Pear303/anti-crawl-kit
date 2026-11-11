"""``ack-ui`` —— 本地 Web 控制台。

只做四件事：列配方、填参数、运行、看日志和报告。刻意不做拖拽编辑器、
选择器点选和账号管理——那些是另一个产品，而且会让这个包变重。

安全边界：

- 默认只监听 ``127.0.0.1``。
- 配方路径必须落在 ``--recipes`` 目录内，报告只能读 ``--runs`` 目录。
- 日志经 ``redact_mapping`` 脱敏，避免把 cookie / token 打到页面上。

用法::

    ack-ui                          # http://127.0.0.1:8766
    ack-ui --headed                 # 浏览器有头运行
    ack-ui --channel msedge         # 用 Edge
    ack-ui --recipes examples/recipes --runs runs
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import uuid
import webbrowser
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .console import redact_mapping
from .recipe import list_recipes, load_recipe, resume_recipe, run_recipe
from .report import render_html
from .ui_assets import INDEX_HTML

_FIRST_RUN = object()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class UiJob:
    id: str
    recipe_path: str
    recipe_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    with_browser: bool = False
    status: str = "queued"          # queued / running / needs_agent / done / failed
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str = ""
    _subscribers: list[queue.Queue] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class UiService:
    """控制台的业务层，和 HTTP 解耦，方便单独测试。"""

    def __init__(
        self,
        recipes_dir: str | Path,
        runs_dir: str | Path,
        *,
        headed: bool = False,
        channel: str = "",
        max_jobs: int = 30,
    ) -> None:
        self.recipes_dir = Path(recipes_dir).resolve()
        self.runs_dir = Path(runs_dir).resolve()
        self.headed = headed
        self.channel = channel
        self._jobs: dict[str, UiJob] = {}
        self._lock = threading.Lock()
        self._max_jobs = max(1, max_jobs)
        self._http_client: Any = None

    # ── 配方 ──────────────────────────────────────────
    def recipes(self) -> list[dict[str, Any]]:
        rows = list_recipes(self.recipes_dir)
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            path = Path(row.get("path") or "")
            try:
                item["path"] = path.resolve().relative_to(self.recipes_dir).as_posix()
            except (ValueError, OSError):
                item["path"] = path.name
            out.append(item)
        return out

    def recipe_detail(self, rel_path: str) -> dict[str, Any]:
        recipe = load_recipe(self._resolve_recipe(rel_path))
        data = recipe.to_dict()
        data["path"] = rel_path
        return data

    def _resolve_recipe(self, rel_path: str) -> Path:
        if not rel_path:
            raise FileNotFoundError("缺少 recipe 路径")
        target = (self.recipes_dir / rel_path).resolve()
        try:
            target.relative_to(self.recipes_dir)
        except ValueError as exc:
            raise PermissionError("配方路径越界") from exc
        if not target.is_file():
            raise FileNotFoundError(f"配方不存在: {rel_path}")
        return target

    # ── 报告 ──────────────────────────────────────────
    def runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.is_dir():
            return []
        files = [path for path in self.runs_dir.rglob("*.html") if path.is_file()]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        rows = []
        for path in files[:60]:
            try:
                rel = path.relative_to(self.runs_dir).as_posix()
            except ValueError:
                continue
            rows.append(
                {
                    "name": path.stem,
                    "kind": path.parent.name if path.parent != self.runs_dir else "runs",
                    "url": f"/runs/{rel}",
                }
            )
        return rows

    # ── 作业 ──────────────────────────────────────────
    def start(
        self,
        rel_path: str,
        inputs: dict[str, Any] | None = None,
        with_browser: bool = False,
    ) -> str:
        recipe = load_recipe(self._resolve_recipe(rel_path))
        job = UiJob(
            id=uuid.uuid4().hex[:10],
            recipe_path=rel_path,
            recipe_id=recipe.id,
            inputs=dict(inputs or {}),
            with_browser=bool(with_browser),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._prune_locked()
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return job.id

    def resume(self, job_id: str, value: Any) -> None:
        job = self.require(job_id)
        if job.status != "needs_agent":
            raise ValueError(f"当前状态不是 needs_agent: {job.status}")
        job.status = "running"
        threading.Thread(target=self._execute, args=(job, value), daemon=True).start()

    def _execute(self, job: UiJob, resume_value: Any = _FIRST_RUN) -> None:
        job.status = "running"
        job.started_at = _now()
        done_sent = {"value": False}

        def on_event(event: dict[str, Any]) -> None:
            if event.get("type") == "done":
                done_sent["value"] = True
            self._emit(job.id, event)

        try:
            recipe = load_recipe(self._resolve_recipe(job.recipe_path))
            need_browser = job.with_browser or any(
                step.uses.startswith("browser_") for step in recipe.steps
            )
            context = nullcontext(None)
            if need_browser:
                from .session import BrowserSession

                context = BrowserSession(
                    headless=not self.headed,
                    skip_version_check=True,
                    channel=self.channel or None,
                )
            with context as page:
                if resume_value is _FIRST_RUN:
                    run = run_recipe(
                        recipe,
                        inputs=job.inputs,
                        page=page,
                        http_client=self._client(),
                        on_event=on_event,
                    )
                else:
                    run = resume_recipe(
                        job.result,
                        resume_value,
                        recipe=recipe,
                        page=page,
                        http_client=self._client(),
                        on_event=on_event,
                    )
            job.result = redact_mapping(run.to_dict())
            job.status = run.status
            job.error = run.error
        except Exception as exc:  # 作业失败不拖垮控制台
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            if not done_sent["value"]:
                self._emit(job.id, {"type": "done", "status": "failed", "error": job.error})
        finally:
            job.finished_at = _now()

    def _client(self) -> Any:
        if self._http_client is None:
            from .http import AntiRiskHttpClient

            self._http_client = AntiRiskHttpClient("ui", timeout=30, max_retries=2)
        return self._http_client

    def _emit(self, job_id: str, event: dict[str, Any]) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        payload = redact_mapping(dict(event))
        payload["at"] = _now()
        with job._lock:
            job.events.append(payload)
            subscribers = list(job._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(payload)
            except queue.Full:
                pass

    def events(self, job_id: str, since: int = 0) -> list[dict[str, Any]]:
        job = self.require(job_id)
        with job._lock:
            return job.events[since:]

    def subscribe(self, job_id: str) -> queue.Queue:
        job = self.require(job_id)
        subscriber: queue.Queue = queue.Queue(maxsize=1000)
        with job._lock:
            job._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, job_id: str, subscriber: queue.Queue) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        with job._lock:
            if subscriber in job._subscribers:
                job._subscribers.remove(subscriber)

    def require(self, job_id: str) -> UiJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"job 不存在: {job_id}")
        return job

    def snapshot(self, job_id: str) -> dict[str, Any]:
        job = self.require(job_id)
        return {
            "job_id": job.id,
            "recipe_id": job.recipe_id,
            "recipe_path": job.recipe_path,
            "status": job.status,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "has_result": job.result is not None,
            "event_count": len(job.events),
        }

    def report_html(self, job_id: str) -> str:
        job = self.require(job_id)
        if job.result is None:
            raise ValueError("作业还没有结果")
        return render_html(job.result, kind="recipe", title=f"配方 · {job.recipe_id}")

    def _prune_locked(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        finished = [
            (job.finished_at, job_id)
            for job_id, job in self._jobs.items()
            if job.status in ("done", "failed")
        ]
        for _, job_id in sorted(finished)[: max(0, len(self._jobs) - self._max_jobs)]:
            self._jobs.pop(job_id, None)

    def close(self) -> None:
        client = self._http_client
        if client is not None and hasattr(client, "close"):
            client.close()


# ── HTTP 层 ──────────────────────────────────────────


def make_handler(service: UiService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ack-ui/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # 默认不刷访问日志；排查时用 --verbose 打开。
            if getattr(self.server, "verbose", False):
                super().log_message(fmt, *args)

        # ── 响应工具 ──
        def _send(self, body: bytes, status: int, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self._send(body, status, "application/json; charset=utf-8")

        def _text(self, text: str, status: int = 200) -> None:
            self._send(text.encode("utf-8"), status, "text/html; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        # ── GET ──
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path in ("/", "/index.html"):
                    self._text(INDEX_HTML)
                elif path == "/api/recipes":
                    self._json(service.recipes())
                elif path == "/api/recipes/detail":
                    self._json(service.recipe_detail((query.get("path") or [""])[0]))
                elif path == "/api/runs":
                    self._json(service.runs())
                elif path.startswith("/api/jobs/"):
                    self._handle_job(path)
                elif path.startswith("/runs/"):
                    self._serve_run(path)
                else:
                    self._error(404, "not found")
            except FileNotFoundError as exc:
                self._error(404, str(exc))
            except PermissionError as exc:
                self._error(403, str(exc))
            except KeyError as exc:
                self._error(404, str(exc))
            except Exception as exc:  # pragma: no cover - 兜底
                self._error(500, f"{type(exc).__name__}: {exc}")

        def _handle_job(self, path: str) -> None:
            parts = [item for item in path.strip("/").split("/") if item]
            job_id = parts[2] if len(parts) > 2 else ""
            action = parts[3] if len(parts) > 3 else ""
            if action == "events":
                self._sse(job_id)
            elif action == "report":
                self._text(service.report_html(job_id))
            else:
                self._json(service.snapshot(job_id))

        def _serve_run(self, path: str) -> None:
            rel = unquote(path[len("/runs/"):])
            target = (service.runs_dir / rel).resolve()
            try:
                target.relative_to(service.runs_dir)
            except ValueError as exc:
                raise PermissionError("报告路径越界") from exc
            if not target.is_file():
                raise FileNotFoundError(f"报告不存在: {rel}")
            content_type = (
                "text/html; charset=utf-8"
                if target.suffix.lower() == ".html"
                else "application/octet-stream"
            )
            self._send(target.read_bytes(), 200, content_type)

        # ── SSE ──
        def _sse(self, job_id: str) -> None:
            job = service.require(job_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            subscriber = service.subscribe(job_id)
            try:
                for event in service.events(job_id):
                    self._sse_write(event)
                if job.status in ("done", "failed"):
                    return
                while True:
                    try:
                        event = subscriber.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    self._sse_write(event)
                    if event.get("type") == "done":
                        break
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                service.unsubscribe(job_id, subscriber)

        def _sse_write(self, event: dict[str, Any]) -> None:
            payload = json.dumps(event, ensure_ascii=False, default=str)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        # ── POST ──
        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(data, dict):
                    raise ValueError("请求体必须是 JSON 对象")
                if path == "/api/run":
                    job_id = service.start(
                        str(data.get("recipe") or ""),
                        data.get("inputs") or {},
                        bool(data.get("with_browser")),
                    )
                    self._json({"job_id": job_id})
                elif path.startswith("/api/jobs/") and path.endswith("/resume"):
                    job_id = [item for item in path.strip("/").split("/") if item][2]
                    service.resume(job_id, data.get("value"))
                    self._json({"ok": True})
                else:
                    self._error(404, "not found")
            except FileNotFoundError as exc:
                self._error(404, str(exc))
            except PermissionError as exc:
                self._error(403, str(exc))
            except (ValueError, KeyError) as exc:
                self._error(400, str(exc))
            except Exception as exc:  # pragma: no cover - 兜底
                self._error(500, f"{type(exc).__name__}: {exc}")

    return Handler


def serve(service: UiService, host: str = "127.0.0.1", port: int = 8766) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    server.daemon_threads = True
    print(f"[OK] ack-ui 已启动: http://{host}:{port}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("[WARN] 当前监听的不是本机地址，局域网内其他人也能访问。")
    print("[INFO] Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        service.close()


def _default_recipes() -> str:
    for name in ("examples/recipes", "recipes"):
        if Path(name).is_dir():
            return name
    return "recipes"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ack-ui",
        description="本地 Web 控制台：列配方、填参数、跑、看日志和报告",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认只监听本机）")
    parser.add_argument("--port", type=int, default=8766, help="端口（默认 8766）")
    parser.add_argument("--recipes", default="", help="配方目录（默认 examples/recipes）")
    parser.add_argument("--runs", default="runs", help="报告目录（默认 runs）")
    parser.add_argument("--headed", action="store_true", help="浏览器有头运行")
    parser.add_argument(
        "--channel",
        default="",
        choices=["", "chrome", "msedge"],
        help="浏览器通道（默认自动：chrome → msedge）",
    )
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args(argv)

    recipes_dir = args.recipes or _default_recipes()
    if not Path(recipes_dir).is_dir():
        print(f"[FAIL] 配方目录不存在: {recipes_dir}")
        return 2

    service = UiService(
        recipes_dir,
        args.runs,
        headed=args.headed,
        channel=args.channel,
    )
    if args.open:
        threading.Timer(
            0.8, lambda: webbrowser.open(f"http://{args.host}:{args.port}")
        ).start()
    serve(service, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
