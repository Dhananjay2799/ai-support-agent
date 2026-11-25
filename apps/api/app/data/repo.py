"""SQL for the mock CRM: customers, charges, refunds, tickets. No business rules here."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.policy.config import ContractStatus, Tier

Conn = AsyncConnection[DictRow]

_CUSTOMER_COLUMNS = "id, public_id, full_name, email, tier, contract_status, renews_on"


@dataclass(frozen=True)
class CustomerRow:
    id: int
    public_id: str
    full_name: str
    email: str
    tier: Tier
    contract_status: ContractStatus
    renews_on: date | None


@dataclass(frozen=True)
class ChargeRow:
    id: int
    public_id: str
    amount: Decimal
    description: str
    status: str
    created_at: datetime
    refunded: Decimal


@dataclass(frozen=True)
class RefundRow:
    public_id: str
    amount: Decimal
    created_at: datetime


@dataclass(frozen=True)
class TicketRow:
    public_id: str
    category: str
    priority: str
    created_at: datetime


def _customer(row: DictRow) -> CustomerRow:
    return CustomerRow(**{k: row[k] for k in CustomerRow.__dataclass_fields__})


async def find_customer(conn: Conn, *, email: str, zip_code: str) -> CustomerRow | None:
    cur = await conn.execute(
        f"select {_CUSTOMER_COLUMNS} from public.customers where lower(email) = lower(%s) and zip_code = %s",
        (email.strip(), zip_code.strip()),
    )
    row = await cur.fetchone()
    return _customer(row) if row else None


async def customer_for_persona(conn: Conn, persona_key: str) -> CustomerRow | None:
    cur = await conn.execute(
        f"select {_CUSTOMER_COLUMNS} from public.customers where persona_key = %s", (persona_key,)
    )
    row = await cur.fetchone()
    return _customer(row) if row else None


_CHARGE_SELECT = """
select ch.id, ch.public_id, ch.amount, ch.description, ch.status, ch.created_at,
       coalesce((select sum(r.amount) from public.refunds r
                 where r.charge_id = ch.id and r.status = 'succeeded'), 0) as refunded
from public.charges ch
"""


async def recent_charges(conn: Conn, customer_id: int, limit: int = 10) -> list[ChargeRow]:
    cur = await conn.execute(
        _CHARGE_SELECT + "where ch.customer_id = %s order by ch.created_at desc limit %s",
        (customer_id, limit),
    )
    return [ChargeRow(**row) for row in await cur.fetchall()]


async def get_charge(conn: Conn, customer_id: int, public_id: str) -> ChargeRow | None:
    """Scoped to the customer: a charge id from someone else's account is not found."""
    cur = await conn.execute(
        _CHARGE_SELECT + "where ch.customer_id = %s and ch.public_id = %s", (customer_id, public_id)
    )
    row = await cur.fetchone()
    return ChargeRow(**row) if row else None


async def duplicate_refunded(conn: Conn, customer_id: int, charge: ChargeRow, window_days: int) -> bool:
    """Was another charge of the same amount and description, within `window_days` of this
    one, already refunded? Then the two are a duplicate pair and this is the one to keep."""
    cur = await conn.execute(
        """
        select exists (
          select 1 from public.charges ch
          join public.refunds r on r.charge_id = ch.id and r.status = 'succeeded'
          where ch.customer_id = %(customer)s and ch.id <> %(charge)s
            and ch.amount = %(amount)s and ch.description = %(description)s
            and ch.created_at between %(created)s - make_interval(days => %(days)s)
                                  and %(created)s + make_interval(days => %(days)s)
        ) as refunded
        """,
        {
            "customer": customer_id,
            "charge": charge.id,
            "amount": charge.amount,
            "description": charge.description,
            "created": charge.created_at,
            "days": window_days,
        },
    )
    row = await cur.fetchone()
    return bool(row and row["refunded"])


async def refund_by_key(conn: Conn, idempotency_key: str) -> RefundRow | None:
    cur = await conn.execute(
        "select public_id, amount, created_at from public.refunds where idempotency_key = %s",
        (idempotency_key,),
    )
    row = await cur.fetchone()
    return RefundRow(**row) if row else None


async def create_refund(
    conn: Conn,
    *,
    charge: ChargeRow,
    customer_id: int,
    amount: Decimal,
    reason: str,
    idempotency_key: str,
    public_id: str,
) -> tuple[RefundRow, bool]:
    """Insert once per idempotency key. Returns (refund, created): a retried call gets
    the original refund back instead of paying out twice."""
    async with conn.transaction():
        cur = await conn.execute(
            """
            insert into public.refunds (public_id, charge_id, customer_id, amount, reason, idempotency_key)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (idempotency_key) do nothing
            returning public_id, amount, created_at
            """,
            (public_id, charge.id, customer_id, amount, reason, idempotency_key),
        )
        row = await cur.fetchone()
        if row is None:
            cur = await conn.execute(
                "select public_id, amount, created_at from public.refunds where idempotency_key = %s",
                (idempotency_key,),
            )
            existing = await cur.fetchone()
            assert existing is not None
            return RefundRow(**existing), False
        status = "refunded" if charge.refunded + amount >= charge.amount else "partially_refunded"
        await conn.execute("update public.charges set status = %s where id = %s", (status, charge.id))
        return RefundRow(**row), True


async def create_ticket(
    conn: Conn,
    *,
    customer_id: int | None,
    session_id: str,
    category: str,
    priority: str,
    summary: str,
) -> TicketRow:
    cur = await conn.execute(
        """
        insert into public.tickets (customer_id, session_id, category, priority, summary)
        values (%s, %s, %s, %s, %s)
        returning public_id, category, priority, created_at
        """,
        (customer_id, session_id, category, priority, summary),
    )
    row = await cur.fetchone()
    assert row is not None
    return TicketRow(**row)


async def add_to_ticket(conn: Conn, public_id: str, note: str) -> None:
    """Append a follow-up to an open ticket, so a second hand-off isn't lost."""
    await conn.execute(
        "update public.tickets set summary = summary || %s where public_id = %s",
        (f"\n\nFollow-up: {note}", public_id),
    )
