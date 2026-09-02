"""In-memory seed data for the mock legacy bank portal.

Deliberately not a database — this app exists only to give the automation
something real (and repeatable) to drive. A handful of member IDs are
reserved to deterministically trigger the runtime conditions replay has to
handle per brief §3.3: not-found, permission-denied, slow-load, and
session-timeout are all *seed-driven*, not timing-/flake-driven, so the
same replay reproduces the same outcome every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

PERMISSION_DENIED_ID = "20001"
SLOW_LOAD_ID = "30001"
SESSION_TIMEOUT_ID = "40001"
SLOW_LOAD_SECONDS = 4


@dataclass
class SubAccount:
    account_number: str
    account_type: str
    balance: Decimal


@dataclass
class Member:
    member_id: str
    first_name: str
    last_name: str
    savings_balance: Decimal
    checking_balance: Decimal
    status: str  # "active" | "restricted"
    sub_accounts: list[SubAccount] = field(default_factory=list)


def _seed() -> dict[str, Member]:
    return {
        "10001": Member("10001", "Ava", "Nguyen", Decimal("4210.55"), Decimal("1023.10"), "active"),
        "10002": Member("10002", "Marcus", "Ibe", Decimal("980.00"), Decimal("120.44"), "active"),
        "10003": Member("10003", "Priya", "Shah", Decimal("15230.02"), Decimal("3300.00"), "active"),
        PERMISSION_DENIED_ID: Member(
            PERMISSION_DENIED_ID, "Restricted", "Account", Decimal("0.00"), Decimal("0.00"), "restricted"
        ),
        SLOW_LOAD_ID: Member(SLOW_LOAD_ID, "Slow", "Loader", Decimal("500.00"), Decimal("0.00"), "active"),
        SESSION_TIMEOUT_ID: Member(SESSION_TIMEOUT_ID, "Session", "Expiry", Decimal("100.00"), Decimal("0.00"), "active"),
    }


class MembersStore:
    def __init__(self) -> None:
        self._members: dict[str, Member] = _seed()
        self._next_account_seq = 1

    def reset(self) -> None:
        """Used by tests / between demo runs so replays are reproducible."""
        self._members = _seed()
        self._next_account_seq = 1

    def get(self, member_id: str) -> Member | None:
        return self._members.get(member_id)

    def search(self, query: str) -> list[Member]:
        q = query.strip().lower()
        if not q:
            return []
        return [
            m
            for m in self._members.values()
            if q in m.member_id or q in m.last_name.lower() or q in m.first_name.lower()
        ]

    def open_sub_account(self, member_id: str, account_type: str, initial_deposit: Decimal) -> SubAccount:
        member = self._members[member_id]
        account_number = f"SA-{member_id}-{self._next_account_seq:04d}"
        self._next_account_seq += 1
        sub = SubAccount(account_number, account_type, initial_deposit)
        member.sub_accounts.append(sub)
        return sub


store = MembersStore()
