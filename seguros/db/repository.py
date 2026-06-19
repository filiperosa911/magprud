"""DAO — único módulo que importa ``sqlite3`` fora de ``connection``.

Cada método mutante faz seu próprio ``commit`` para que um crash no meio do loop
deixe o banco consistente e o próximo run retome de onde parou (idempotência).
Todas as consultas são escopadas por ``corretor_id``.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from ..clock import iso_utc, now_utc
from ..domain.models import Canal, ClienteRegua, Modo, ReguaStatus, Resultado


def _row_to_cliente(row: sqlite3.Row) -> ClienteRegua:
    return ClienteRegua(
        cpf=row["cpf"],
        corretor_id=row["corretor_id"],
        nome=row["nome"],
        telefone=row["telefone"],
        email=row["email"],
        valor_inadimplente_cents=row["valor_inadimplente_cents"],
        valor_texto=row["valor_texto"],
        vencimento_mais_antigo=row["vencimento_mais_antigo"],
        competencia=row["competencia"],
        work_status=row["work_status"],
        link_pagamento=row["link_pagamento"],
        link_gerado_em=row["link_gerado_em"],
        autoriza_whatsapp=bool(row["autoriza_whatsapp"]),
        autoriza_email=bool(row["autoriza_email"]),
        whatsapp_enviado_em=row["whatsapp_enviado_em"],
        email_enviado_em=row["email_enviado_em"],
        enrolled_em=row["enrolled_em"],
        status=ReguaStatus(row["status"]),
        atualizado_em=row["atualizado_em"],
    )


class ReguaRepository:
    def __init__(self, conn: sqlite3.Connection, corretor_id: str = "local") -> None:
        self.conn = conn
        self.corretor_id = corretor_id

    def exists(self, cpf: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM clientes_regua WHERE corretor_id = ? AND cpf = ?",
            (self.corretor_id, cpf),
        )
        return cur.fetchone() is not None

    def get(self, cpf: str) -> ClienteRegua | None:
        cur = self.conn.execute(
            "SELECT * FROM clientes_regua WHERE corretor_id = ? AND cpf = ?",
            (self.corretor_id, cpf),
        )
        row = cur.fetchone()
        return _row_to_cliente(row) if row else None

    def all_clientes(self) -> list[ClienteRegua]:
        cur = self.conn.execute(
            "SELECT * FROM clientes_regua WHERE corretor_id = ? ORDER BY enrolled_em",
            (self.corretor_id,),
        )
        return [_row_to_cliente(r) for r in cur.fetchall()]

    def insert_enrollment(self, c: ClienteRegua) -> None:
        now = iso_utc()
        self.conn.execute(
            """
            INSERT INTO clientes_regua (
                cpf, corretor_id, nome, telefone, email,
                valor_inadimplente_cents, valor_texto, vencimento_mais_antigo,
                competencia, work_status, link_pagamento, link_gerado_em,
                autoriza_whatsapp, autoriza_email,
                whatsapp_enviado_em, email_enviado_em,
                enrolled_em, status, atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                c.cpf,
                self.corretor_id,
                c.nome,
                c.telefone,
                c.email,
                c.valor_inadimplente_cents,
                c.valor_texto,
                c.vencimento_mais_antigo,
                c.competencia,
                c.work_status,
                c.link_pagamento,
                c.link_gerado_em,
                int(c.autoriza_whatsapp),
                int(c.autoriza_email),
                c.whatsapp_enviado_em,
                c.email_enviado_em,
                c.enrolled_em or now,
                c.status.value,
                now,
            ),
        )
        self.conn.commit()

    def mark_whatsapp_sent(self, cpf: str, when_iso: str | None = None) -> None:
        ts = when_iso or iso_utc()
        self.conn.execute(
            "UPDATE clientes_regua SET whatsapp_enviado_em = ?, atualizado_em = ? "
            "WHERE corretor_id = ? AND cpf = ?",
            (ts, iso_utc(), self.corretor_id, cpf),
        )
        self.conn.commit()

    def mark_email_sent(self, cpf: str, when_iso: str | None = None) -> None:
        ts = when_iso or iso_utc()
        self.conn.execute(
            "UPDATE clientes_regua SET email_enviado_em = ?, atualizado_em = ? "
            "WHERE corretor_id = ? AND cpf = ?",
            (ts, iso_utc(), self.corretor_id, cpf),
        )
        self.conn.commit()

    def set_status(self, cpf: str, status: ReguaStatus) -> None:
        self.conn.execute(
            "UPDATE clientes_regua SET status = ?, atualizado_em = ? "
            "WHERE corretor_id = ? AND cpf = ?",
            (status.value, iso_utc(), self.corretor_id, cpf),
        )
        self.conn.commit()

    def update_link(self, cpf: str, link: str, gerado_em_iso: str | None = None) -> None:
        self.conn.execute(
            "UPDATE clientes_regua SET link_pagamento = ?, link_gerado_em = ?, "
            "atualizado_em = ? WHERE corretor_id = ? AND cpf = ?",
            (link, gerado_em_iso or iso_utc(), iso_utc(), self.corretor_id, cpf),
        )
        self.conn.commit()

    def update_work_status(self, cpf: str, work_status: str | None) -> None:
        self.conn.execute(
            "UPDATE clientes_regua SET work_status = ?, atualizado_em = ? "
            "WHERE corretor_id = ? AND cpf = ?",
            (work_status, iso_utc(), self.corretor_id, cpf),
        )
        self.conn.commit()

    def update_contact(self, cpf: str, *, telefone, email, autoriza_whatsapp,
                       autoriza_email) -> None:
        self.conn.execute(
            "UPDATE clientes_regua SET telefone = ?, email = ?, autoriza_whatsapp = ?, "
            "autoriza_email = ?, atualizado_em = ? WHERE corretor_id = ? AND cpf = ?",
            (telefone, email, int(bool(autoriza_whatsapp)), int(bool(autoriza_email)),
             iso_utc(), self.corretor_id, cpf),
        )
        self.conn.commit()

    def due_for_followup(self, offset_days: int, reference=None) -> list[ClienteRegua]:
        """Clientes em régua, sem e-mail enviado, com ``enrolled_em`` há >= offset dias."""
        ref = reference or now_utc()
        cutoff = (ref.date() - timedelta(days=offset_days)).isoformat()
        cur = self.conn.execute(
            """
            SELECT * FROM clientes_regua
            WHERE corretor_id = ?
              AND status = 'em_regua'
              AND email_enviado_em IS NULL
              AND substr(enrolled_em, 1, 10) <= ?
            ORDER BY enrolled_em
            """,
            (self.corretor_id, cutoff),
        )
        return [_row_to_cliente(r) for r in cur.fetchall()]

    def pending_whatsapp(self) -> list[ClienteRegua]:
        """Clientes em régua com WhatsApp autorizado, link gerado e ainda não enviado.

        Cobre o WhatsApp do dia 0 que foi adiado (fora da janela) ou falhou num run
        anterior — caso contrário ele se perderia (o cliente sai do filtro de
        descoberta após o "Cobrar").
        """
        cur = self.conn.execute(
            "SELECT * FROM clientes_regua WHERE corretor_id = ? AND status = 'em_regua' "
            "AND whatsapp_enviado_em IS NULL AND autoriza_whatsapp = 1 "
            "AND link_pagamento IS NOT NULL AND TRIM(link_pagamento) <> '' "
            "ORDER BY enrolled_em",
            (self.corretor_id,),
        )
        return [_row_to_cliente(r) for r in cur.fetchall()]

    def active_cpfs(self) -> set[str]:
        cur = self.conn.execute(
            "SELECT cpf FROM clientes_regua WHERE corretor_id = ? AND status = 'em_regua'",
            (self.corretor_id,),
        )
        return {r["cpf"] for r in cur.fetchall()}


class OptOutRepository:
    def __init__(self, conn: sqlite3.Connection, corretor_id: str = "local") -> None:
        self.conn = conn
        self.corretor_id = corretor_id

    def is_opted_out(self, *, cpf: str | None = None, telefone: str | None = None) -> bool:
        clauses, params = [], [self.corretor_id]
        if cpf:
            clauses.append("cpf = ?")
            params.append(cpf)
        if telefone:
            clauses.append("telefone = ?")
            params.append(telefone)
        if not clauses:
            return False
        cur = self.conn.execute(
            f"SELECT 1 FROM opt_out WHERE corretor_id = ? AND ({' OR '.join(clauses)}) LIMIT 1",
            params,
        )
        return cur.fetchone() is not None

    def add(self, *, cpf: str | None = None, telefone: str | None = None,
            origem: str = "manual") -> None:
        self.conn.execute(
            "INSERT INTO opt_out (corretor_id, cpf, telefone, origem, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.corretor_id, cpf, telefone, origem, iso_utc()),
        )
        self.conn.commit()


class LogRepository:
    def __init__(self, conn: sqlite3.Connection, corretor_id: str = "local") -> None:
        self.conn = conn
        self.corretor_id = corretor_id

    def record(
        self,
        *,
        cpf: str,
        canal: Canal,
        resultado: Resultado | str,
        modo: Modo,
        link: str | None = None,
        payload_resumo: str | None = None,
    ) -> None:
        res = resultado.value if isinstance(resultado, Resultado) else str(resultado)
        self.conn.execute(
            "INSERT INTO log_disparos "
            "(corretor_id, cpf, canal, link, resultado, payload_resumo, modo, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.corretor_id,
                cpf,
                canal.value,
                link,
                res,
                payload_resumo,
                modo.value,
                iso_utc(),
            ),
        )
        self.conn.commit()


__all__ = ["ReguaRepository", "OptOutRepository", "LogRepository"]
