"""Domain/mirror rotation with health checking and demotion (plan §1.1).

The user keeps an *ordered* list of domains (official + mirrors). We try them
in order; on failure/timeout/challenge we rotate to the next. Domains that
keep failing get demoted to the back and enter a cooldown, then become
eligible again. Health checks reset all state for a domain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class DomainState:
    domain: str
    consecutive_failures: int = 0
    demoted: bool = False
    demoted_at: float = 0.0
    last_failure_reason: str = ""
    last_used: float = 0.0
    last_success: float = 0.0

    def score(self, now: float, cooldown: float) -> tuple[int, float]:
        """Sort key: healthy domains first in user order, demoted ones last."""
        revived = self.demoted and (now - self.demoted_at) >= cooldown
        rank = 0 if (not self.demoted or revived) else 1
        # demoted domains: least-recently-tried first
        return (rank, self.last_used if rank else 0.0)


class DomainRotator:
    def __init__(
        self,
        domains: list[str],
        *,
        demote_after: int = 3,
        cooldown_seconds: float = 300.0,
        clock: object = None,
    ):
        self.domains = [d.strip().lower() for d in domains if d.strip()]
        self.demote_after = max(1, demote_after)
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self._states: dict[str, DomainState] = {
            d: DomainState(d) for d in self.domains
        }
        self.events: list[str] = []  # audit log for tests/UI

    def set_domains(self, domains: list[str]) -> None:
        old = self._states
        self.domains = [d.strip().lower() for d in domains if d.strip()]
        self._states = {}
        for d in self.domains:
            self._states[d] = old.get(d, DomainState(d))

    def _now(self) -> float:
        now = self._clock
        return now() if callable(now) else float(now)

    def order(self) -> list[str]:
        """Current try-order (healthy in user order, demoted after cooldown-sorted)."""
        now = self._now()
        ranked = sorted(self._states.values(), key=lambda s: s.score(now, self.cooldown_seconds))
        # stable within rank: preserve user order for rank 0
        return [s.domain for s in ranked]

    def next_domain(self) -> str | None:
        if not self.domains:
            return None
        return self.order()[0]

    def report_success(self, domain: str) -> None:
        st = self._states.setdefault(domain, DomainState(domain))
        st.consecutive_failures = 0
        st.demoted = False
        st.last_success = self._now()
        st.last_used = self._now()

    def report_failure(self, domain: str, reason: str) -> bool:
        """Returns True if the domain just got demoted."""
        st = self._states.setdefault(domain, DomainState(domain))
        st.consecutive_failures += 1
        st.last_failure_reason = reason
        st.last_used = self._now()
        demoted_now = False
        if not st.demoted and st.consecutive_failures >= self.demote_after:
            st.demoted = True
            st.demoted_at = self._now()
            demoted_now = True
            self.events.append(
                f"{self._now():.0f} demoted {domain} after {st.consecutive_failures} failures ({reason})"
            )
        return demoted_now

    def stats(self) -> list[dict]:
        now = self._now()
        return [
            {
                "domain": st.domain,
                "consecutive_failures": st.consecutive_failures,
                "demoted": st.demoted and (now - st.demoted_at) < self.cooldown_seconds,
                "last_failure_reason": st.last_failure_reason,
            }
            for st in (self._states[d] for d in self.domains)
        ]
