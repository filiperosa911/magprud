"""Worker de thread única que detém a sessão Playwright (MagConnector).

O Playwright (API sync) não pode ser usado entre threads, e o FastAPI roda
endpoints ``def`` num threadpool. Por isso TODA ação na MAG é serializada por
este worker: uma thread dedicada cria o conector (lazy), mantém o Chrome aberto
e processa uma fila de ações. As requisições web enfileiram e aguardam o futuro.
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future
from typing import Callable

log = logging.getLogger("seguros.dashboard.worker")


class ConnectorWorker:
    def __init__(self, config, notifier=None) -> None:
        self.config = config
        self.notifier = notifier
        self._q: queue.Queue = queue.Queue()
        self._connector = None
        self._started = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="mag-worker", daemon=True)
        self._thread.start()

    # --- API pública ---------------------------------------------------------

    def submit(self, fn: Callable, *, ensure_auth: bool = True, timeout: float = 300.0):
        """Enfileira ``fn(connector)`` na thread do worker e aguarda o resultado."""
        fut: Future = Future()
        self._q.put((fn, ensure_auth, fut))
        return fut.result(timeout=timeout)

    def status(self) -> dict:
        return {"conector_ativo": self._connector is not None}

    def shutdown(self) -> None:
        self._q.put((None, False, None))

    # --- loop da thread ------------------------------------------------------

    def _run(self) -> None:
        while True:
            fn, ensure_auth, fut = self._q.get()
            if fn is None:
                self._close()
                return
            try:
                conn = self._ensure_connector(ensure_auth)
                fut.set_result(fn(conn))
            except Exception as err:  # noqa: BLE001 - propaga ao futuro
                log.exception("ação do worker falhou: %s", err)
                if fut is not None:
                    fut.set_exception(err)

    def _ensure_connector(self, ensure_auth: bool):
        if self._connector is None:
            from ..connectors.mag.connector import MagConnector

            log.info("iniciando MagConnector no worker")
            conn = MagConnector(self.config, notifier=self.notifier)
            conn.start()
            self._connector = conn
        if ensure_auth:
            self._connector.ensure_authenticated(interactive=False)
        return self._connector

    def _close(self) -> None:
        if self._connector is not None:
            try:
                self._connector.close()
            except Exception:  # noqa: BLE001
                pass
            self._connector = None


__all__ = ["ConnectorWorker"]
