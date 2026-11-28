"""The node agent (profile ``fleet``): ``python -m fleet.agent``.

Once ``smoking-pi fleet join`` has enrolled the node (``fleet/join.py``),
the agent sends a heartbeat every ``next_heartbeat`` seconds over mutual
TLS, trying the lead URLs in order, applies the directives that come back,
and renews its certificate (with a new key) from two thirds of its life on.

It is never in the measurement path, and a lead that is away changes
nothing on the node: the agent backs off (30 s doubling to 30 min, +-20 %
jitter) and keeps trying, and the node measures exactly as a standalone
one. Nothing here exits on a bad state: ``state_corrupt``,
``identity_mismatch``, ``revoked``, ``cert_expired`` and ``stopped`` are
states that ``smoking-pi fleet status`` shows and a person resolves, and an
unexpected error is logged with an error id and retried later. Memory and
disk are bounded: a few small files, at most 64 pending directive results,
and log lines only when something changes (or hourly while failing).

What a heartbeat reports today is what the agent knows without any new
privilege: the node's names, the software version and edition, the enabled
profiles and the capabilities they imply. The doctor's counts, measurement
freshness and the uplink interface are left out (absent, never invented)
until the agent can read them (docs/fleet.md, "What a node reports").
"""

from __future__ import annotations

import logging
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from . import chain, nodekeys, nodestate, protocol, runner, settings, statefile, status
from .layout import NodePaths, node_paths
from .transport import BadAnswer, HttpsTransport, LeadError, Tls, Transport, TransportError
from .transport import call as lead_call

log = logging.getLogger("fleet")

#: The agent's states. Only ``not_enrolled`` may lead to enrolling: the
#: others wait for a person (``fleet leave`` clears every one of them).
STATES = ("no_identity", "not_enrolled", "enrolled", "state_corrupt", "identity_mismatch",
          "revoked", "cert_expired", "stopped")

#: After an error the protocol says to stop on (unauthenticated,
#: duplicate_node, ...), the agent sends nothing for this long, then tries
#: once more: the operator may have fixed it on the lead. A restart of the
#: agent also tries again at once. ``revoked`` is different: it is for good.
STOP_RETRY_SECONDS = 6 * 3600
#: A failed renewal is retried this often (the old certificate still has a
#: third of its life, 30 days, when renewal starts).
RENEW_RETRY_SECONDS = 900
#: Enrolled and no lead has answered for this long (or three heartbeat
#: intervals, if longer): health is ``failing``.
FAILING_AFTER_SECONDS = 600
#: Directive results waiting for the next successful heartbeat.
MAX_PENDING_RESULTS = 64
#: While failing, the same failure is logged again at most this often.
RELOG_SECONDS = 3600

#: The edition: the fleet profile exists only in Pro's compose file.
EDITION = "pro"
_VERSION = re.compile(r"[0-9A-Za-z.+~-]{1,40}", re.ASCII)


def iso(ts: datetime | float) -> str:
    if not isinstance(ts, datetime):
        ts = datetime.fromtimestamp(ts, timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def software_version(env: dict[str, str] | None = None) -> str:
    """The release this node runs (SMOKING_PI_VERSION, the image tag the
    command chose; ``dev`` for a source checkout)."""
    env = os.environ if env is None else env
    value = (env.get("SMOKING_PI_VERSION") or "").strip().lstrip("v") or "dev"
    return value if _VERSION.fullmatch(value) else "unknown"


def enabled_profiles(env: dict[str, str] | None = None) -> tuple[str, ...]:
    env = os.environ if env is None else env
    out: list[str] = []
    for p in (env.get("COMPOSE_PROFILES") or "").split(","):
        p = p.strip()
        if re.fullmatch(protocol.TOKEN_PATTERN, p, re.ASCII) and p not in out:
            out.append(p)
    return tuple(out[:64])


# ---------------------------------------------------------------------------
# What the files say
# ---------------------------------------------------------------------------


@dataclass
class Local:
    """The node's state as its files say, before anything is sent."""

    state: str
    line: str
    problems: list[str] = field(default_factory=list)
    enrollment: dict = field(default_factory=dict)
    endpoints: protocol.LeadEndpoints | None = None
    creds: nodestate.Credentials | None = None


def assess(cfg: settings.Settings, paths: NodePaths, now: datetime) -> Local:
    read = statefile.read_object(paths.enrollment)
    enrollment: dict = read if isinstance(read, dict) else {}
    problems = list(cfg.problems)
    present = read != statefile.ABSENT
    who = f"node {cfg.node_id} ({cfg.node_name}, site {cfg.site_name})"
    if read == statefile.CORRUPT or (
            present and not settings.valid_node_id(str(enrollment.get("node_id", "")))):
        problems.append("enrollment.json is unreadable, not a JSON object, or has no "
                        "valid node_id")
        return Local("state_corrupt",
                     "fleet agent: the enrollment file is corrupt; nothing is sent, and the "
                     "node will not enroll over it ('smoking-pi fleet leave --force' clears "
                     "it). Idle.", problems)
    if present and enrollment["node_id"] != cfg.node_id:
        problems.append("enrollment.json was made for another node id than FLEET_NODE_ID")
        return Local("identity_mismatch",
                     f"fleet agent: enrolled as node {enrollment['node_id']}, but "
                     f"FLEET_NODE_ID is {cfg.node_id or 'empty'} (an older env file restored "
                     "beside this agent's volume?); nothing is sent. Idle.", problems)
    if not cfg.has_identity:
        return Local("no_identity",
                     "fleet agent: no node id (FLEET_NODE_ID is empty or invalid); "
                     "run 'smoking-pi fleet status' on the host to create one. Idle.", problems)
    if not present:
        return Local("not_enrolled",
                     f"fleet agent: {who} is not enrolled; nothing is sent. "
                     "'smoking-pi fleet join' joins a fleet. Idle.", problems)
    fleet_id = str(enrollment.get("fleet_id", ""))
    revoked = enrollment.get("revoked")
    if isinstance(revoked, dict):
        return Local("revoked",
                     f"fleet agent: the lead revoked {who} from fleet {fleet_id} "
                     f"({revoked.get('at', '?')}); nothing is sent again, and the node keeps "
                     "measuring on its own. 'smoking-pi fleet leave' clears the enrollment.",
                     problems, enrollment)
    try:
        endpoints = nodestate.lead_endpoints(enrollment)
        if not protocol.valid_uuid(fleet_id):
            raise protocol.ValidationError(["fleet_id: malformed"])
        creds = nodestate.load_credentials(paths, cfg.node_id,
                                           str(enrollment.get("root_sha256", "")))
    except protocol.ValidationError:
        problems.append("enrollment.json has no valid fleet_id or lead list")
        creds, endpoints = None, None
    except nodestate.CredentialError as err:
        problems.append(f"credentials: {err.reason}")
        creds, endpoints = None, None
    if creds is None:
        return Local("state_corrupt",
                     f"fleet agent: enrolled in fleet {fleet_id}, but the enrollment or its "
                     "credentials cannot be used; nothing is sent ('smoking-pi fleet leave "
                     "--force', then a new join). Idle.", problems, enrollment)
    if now >= creds.not_after:
        return Local("cert_expired",
                     f"fleet agent: this node's certificate expired on {iso(creds.not_after)} "
                     "(no lead answered while it could renew); nothing is sent. Re-join: "
                     "'smoking-pi fleet leave --force', then 'smoking-pi fleet join' with a "
                     "new join string.", problems, enrollment, endpoints, creds)
    return Local("enrolled",
                 f"fleet agent: {who} is enrolled in fleet {fleet_id}; heartbeats go to "
                 f"{endpoints.current}.", problems, enrollment, endpoints, creds)


def describe(cfg: settings.Settings, paths=None, now: datetime | None = None
             ) -> tuple[dict, str]:
    """The status report and the one log line, from settings and the state
    dir alone (no runtime: see :meth:`Agent.report`)."""
    local = assess(cfg, paths or node_paths(), now or datetime.now(timezone.utc))
    return _report(cfg, local), local.line


def _report(cfg: settings.Settings, local: Local, **runtime: Any) -> dict:
    enrolled = local.state in ("enrolled", "stopped", "cert_expired", "revoked")
    e = local.enrollment if enrolled else {}
    endpoints = local.endpoints
    store = e.get("store") if isinstance(e.get("store"), dict) else None
    revoked = e.get("revoked") if isinstance(e.get("revoked"), dict) else None
    values = dict(
        role="agent", state=local.state, node_id=cfg.node_id or None,
        node_name=cfg.node_name, site_name=cfg.site_name,
        lead_urls=list(endpoints.urls) if endpoints else list(cfg.lead_urls),
        enrolled=enrolled, fleet_id=e.get("fleet_id") if enrolled else None,
        site_id=e.get("site_id") if enrolled else None,
        problems=local.problems,
        cert_not_after=iso(local.creds.not_after) if local.creds else None,
        renew_after=iso(local.creds.renew_after()) if local.creds else None,
        revoked_at=revoked.get("at") if revoked else None,
        store_received=bool(store),
        health=("idle" if local.state in ("no_identity", "not_enrolled")
                else "ok" if local.state == "enrolled" else "failing"),
    )
    values.update(runtime)
    return status.snapshot(**values)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """How one exchange with the leads ended."""

    kind: str  # ok, unreachable, backoff, stop, revoked
    url: str | None = None
    response: Any = None
    code: str | None = None
    retry_after: int | None = None


class Agent:
    def __init__(self, cfg: settings.Settings, paths: NodePaths | None = None,
                 transport: Transport | None = None, clock: Callable[[], float] = time.time,
                 rng: random.Random | None = None, env: dict[str, str] | None = None):
        self.cfg = cfg
        self.paths = paths or node_paths()
        self.transport = transport or HttpsTransport()
        self.clock = clock
        self.rng = rng or random.Random()
        self.version = software_version(env)
        self.profiles = enabled_profiles(env)
        self.instance_id = protocol.new_instance_id()
        self.started = clock()
        self.local: Local | None = None
        self.in_use: str | None = None
        self.last_ok: float | None = None
        self.next_attempt: float | None = None
        self.interval: int | None = None
        self.backoff = 0.0
        self.failures = 0
        self.last_error: str | None = None
        self.pending: list[protocol.DirectiveResult] = []
        self.stopped_until: float | None = None
        self.stop_code: str | None = None
        self.renew_retry_at = 0.0
        self._said: dict[str, float] = {}
        self._state_said: str | None = None
        self._skew_said = False

    # -- logging, once per change ------------------------------------------------

    def _say(self, kind: str, fmt: str, *args: Any, level: int = logging.WARNING) -> None:
        now = self.clock()
        if kind in self._said and now - self._said[kind] < RELOG_SECONDS:
            return
        if len(self._said) >= 64:  # bounded, whatever the leads do
            self._said.clear()
        self._said[kind] = now
        log.log(level, fmt, *args)

    def _note_state(self, local: Local) -> None:
        if local.state != self._state_said:
            self._state_said = local.state
            # The settings' own problems were logged once at start.
            for problem in local.problems[len(self.cfg.problems):]:
                log.warning("problem: %s", problem)
            log.info("%s", local.line)

    # -- the loop ----------------------------------------------------------------

    def tick(self) -> tuple[dict, float]:
        """One turn for runner.loop: never raises."""
        try:
            delay = self.step()
        except Exception as err:  # the agent must never crash-loop
            error_id = protocol.new_error_id()
            tb = err.__traceback__
            while tb is not None and tb.tb_next is not None:
                tb = tb.tb_next
            where = (f"{os.path.basename(tb.tb_frame.f_code.co_filename)}:{tb.tb_lineno}"
                     if tb is not None else "?")
            log.error("unexpected %s at %s (error id %s); trying again later",
                      type(err).__name__, where, error_id)
            self.last_error = f"unexpected error (error id {error_id})"
            delay = self._next_backoff()
            self.next_attempt = self.clock() + delay
        return self.report(), delay

    def step(self) -> float:
        """Do what is due; return the seconds until something is due again."""
        now = self.clock()
        try:
            done = nodestate.finish_renewal(self.paths)
            if done:
                log.info("an interrupted certificate renewal was %s", done)
        except OSError as err:
            log.warning("cannot finish an interrupted renewal (%s)", type(err).__name__)
        local = assess(self.cfg, self.paths, self._dt(now))
        self.local = local
        self._note_state(local)
        if local.state != "enrolled":
            return status.TICK_SECONDS
        if self.stopped_until is not None:
            if now < self.stopped_until:
                return min(status.TICK_SECONDS, self.stopped_until - now)
            self.stopped_until = self.stop_code = None
        if self.next_attempt is None:
            self.next_attempt = self._first_heartbeat(local, now)
        if now >= self.next_attempt:
            self._heartbeat(local, now)
        local = self.local
        if (local.state == "enrolled" and self.stopped_until is None
                and self.last_ok is not None and now >= self.renew_retry_at
                and local.creds is not None and self._dt(now) >= local.creds.renew_after()):
            log.info("the certificate is two thirds through its life (expires %s): renewing",
                     iso(local.creds.not_after))
            self._renew(local, now)
        due = self.stopped_until if self.stopped_until is not None else self.next_attempt
        return max(1.0, min(status.TICK_SECONDS, due - self.clock()))

    def report(self) -> dict:
        local = self.local or assess(self.cfg, self.paths, self._dt(self.clock()))
        state = "stopped" if (local.state == "enrolled" and self.stopped_until) else local.state
        shown = Local(state, local.line, local.problems, local.enrollment, local.endpoints,
                      local.creds)
        runtime: dict[str, Any] = {}
        if local.state == "enrolled":
            now = self.clock()
            ref = self.last_ok if self.last_ok is not None else self.started
            grace = max(FAILING_AFTER_SECONDS,
                        3 * (self.interval or protocol.HEARTBEAT_DEFAULT))
            healthy = self.stopped_until is None and now - ref <= grace
            runtime = dict(
                health="ok" if healthy else "failing",
                lead_in_use=self.in_use,
                last_heartbeat=iso(self.last_ok) if self.last_ok is not None else None,
                next_heartbeat=(iso(self.stopped_until or self.next_attempt)
                                if (self.stopped_until or self.next_attempt) else None),
                failures=self.failures,
                last_error=self.last_error,
                detail=(f"stopped by the lead's '{self.stop_code}' answer; tries again at "
                        f"{iso(self.stopped_until)} or when the agent restarts"
                        if self.stopped_until else None),
            )
        else:
            runtime = dict(detail=local.line)
        return _report(self.cfg, shown, **runtime)

    def _dt(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, timezone.utc)

    def _first_heartbeat(self, local: Local, now: float) -> float:
        interval = local.enrollment.get("heartbeat_interval")
        if (isinstance(interval, int)
                and protocol.HEARTBEAT_MIN <= interval <= protocol.HEARTBEAT_MAX):
            self.interval = interval
            try:
                enrolled = protocol.parse_time(
                    str(local.enrollment.get("enrolled_at"))).timestamp()
                return max(now, min(enrolled + interval, now + interval))
            except ValueError:
                pass
        return now

    def _next_backoff(self) -> float:
        self.backoff = (protocol.BACKOFF_MIN if not self.backoff
                        else min(self.backoff * 2, protocol.BACKOFF_MAX))
        return self.backoff * self.rng.uniform(0.8, 1.2)

    # -- talking to the leads ----------------------------------------------------

    def lead_order(self, endpoints: protocol.LeadEndpoints) -> list[str]:
        """The lead that answered last (if still listed), the list's
        ``current``, the rest of the list, then FLEET_LEAD_URLS entries the
        list lacks (an operator's way in when every lead moved)."""
        order: list[str] = []
        first = [self.in_use] if self.in_use in endpoints.urls else []
        for url in [*first, endpoints.current, *endpoints.urls, *self.cfg.lead_urls]:
            if url and url not in order:
                order.append(url)
        return order

    def _tls(self) -> Tls:
        return Tls(root=self.paths.root_cert, chain=self.paths.chain, key=self.paths.key)

    def exchange(self, route: str, message: Any, response_cls: type) -> Outcome:
        """Send to each lead in order until one answers what the protocol
        says to act on. Never raises for a lead's behavior."""
        assert self.local is not None and self.local.endpoints is not None
        leads = self.lead_order(self.local.endpoints)
        renegotiated = False
        restarts = 0
        i = 0
        while i < len(leads):
            url = leads[i]
            try:
                resp = lead_call(self.transport, url, "POST", route, message, response_cls,
                                 self._tls())
                return Outcome("ok", url=url, response=resp)
            except TransportError as err:
                self._say(f"unreachable:{url}:{err.reason}",
                          "lead %s did not answer (%s)", url, err.reason)
            except BadAnswer as err:
                self._say(f"bad:{url}", "lead %s answered outside the protocol: %s",
                          url, "; ".join(err.reasons[:5]))
            except LeadError as err:
                code, body = err.code, err.body
                action = protocol.agent_action(code)
                if code == "revoked":
                    return Outcome("revoked", url=url, code=code)
                if action == "next_lead":
                    if (body is not None and body.lead_endpoints is not None and restarts < 3
                            and self._take_endpoints(body.lead_endpoints)):
                        restarts += 1
                        leads, i = self.lead_order(self.local.endpoints), 0
                        continue
                elif action == "backoff":
                    return Outcome("backoff", url=url, code=code,
                                   retry_after=body.retry_after if body else None)
                elif (action == "renegotiate" and not renegotiated and body is not None
                      and protocol.negotiate(body.supported_min or 0, body.supported_max or 0)
                      is not None):
                    renegotiated = True
                    continue
                else:
                    return Outcome("stop", url=url, code=code)
            i += 1
        return Outcome("unreachable")

    def _take_endpoints(self, new: protocol.LeadEndpoints) -> bool:
        """Replace the lead list durably if ``new`` has a higher epoch."""
        assert self.local is not None and self.local.endpoints is not None
        if new.epoch <= self.local.endpoints.epoch:
            return False
        enrollment = dict(self.local.enrollment, lead_endpoints=nodestate.endpoints_dict(new))
        nodestate.write_enrollment(self.paths, enrollment)
        self.local.enrollment, self.local.endpoints = enrollment, new
        self.in_use = None
        log.info("lead list replaced (epoch %d): current %s, %d endpoint(s)",
                 new.epoch, new.current, len(new.urls))
        return True

    def _heartbeat(self, local: Local, now: float) -> None:
        assert local.endpoints is not None
        message = protocol.HeartbeatRequest(
            protocol=protocol.PROTOCOL_MAX, protocol_min=protocol.PROTOCOL_MIN,
            node_id=self.cfg.node_id, instance_id=self.instance_id, sent_at=iso(now),
            lead_epoch=local.endpoints.epoch, inventory=self.inventory(),
            directive_results=tuple(self.pending))
        out = self.exchange("/fleet/heartbeat", message, protocol.HeartbeatResponse)
        if out.kind == "ok":
            resp: protocol.HeartbeatResponse = out.response
            if self.failures:
                log.info("heartbeat to %s answered again, after %d failed attempt(s)",
                         out.url, self.failures)
            elif self.last_ok is None:
                log.info("first heartbeat answered by %s", out.url)
            self._said.clear()
            self.in_use, self.last_ok, self.failures = out.url, self.clock(), 0
            self.backoff, self.last_error = 0.0, None
            self.interval = resp.next_heartbeat
            self.next_attempt = self.clock() + resp.next_heartbeat
            self.pending = []
            self._check_skew(resp.server_time)
            self.apply(resp)
            return
        self._failed(out, "heartbeat")

    def _failed(self, out: Outcome, what: str) -> None:
        now = self.clock()
        if out.kind == "revoked":
            self.revoke("revoked", now)
            return
        if out.kind == "stop":
            self.stopped_until = now + STOP_RETRY_SECONDS
            self.stop_code = out.code
            self.last_error = f"{what}: the lead answered '{out.code}'"
            log.error("%s: the lead at %s answered '%s'; nothing is sent until %s (or an "
                      "agent restart). See 'smoking-pi fleet status'.", what, out.url, out.code,
                      iso(self.stopped_until))
            return
        self.failures += 1
        delay = self._next_backoff()
        if out.kind == "backoff":
            delay = max(delay, float(out.retry_after or 0))
            self.in_use = out.url  # the protocol says: retry the same lead
            self.last_error = f"{what}: the lead answered '{out.code}'"
        else:
            self.last_error = f"{what}: no lead answered"
        self.next_attempt = now + delay
        self._say(f"failed:{what}:{out.kind}",
                  "%s failed (%s); next attempt in %d s. The node measures as usual.",
                  what, self.last_error, int(delay))

    def _check_skew(self, server_time: str) -> None:
        try:
            skew = protocol.parse_time(server_time).timestamp() - self.clock()
        except ValueError:
            return
        if abs(skew) > 60 and not self._skew_said:
            self._skew_said = True
            log.warning("this node's clock differs from the lead's by %d s; certificates "
                        "and timestamps depend on it", int(skew))

    def inventory(self) -> protocol.Inventory:
        caps = tuple(c for c in ("influxdb", "clickhouse") if c in self.profiles)
        return protocol.Inventory(node_name=self.cfg.node_name, site_name=self.cfg.site_name,
                                  version=self.version, edition=EDITION,
                                  profiles=self.profiles, capabilities=caps)

    # -- directives --------------------------------------------------------------

    def _result(self, directive_id: str, outcome: str, error_id: str | None = None) -> None:
        self.pending.append(protocol.DirectiveResult(id=directive_id, status=outcome,
                                                     error_id=error_id))
        del self.pending[:-MAX_PENDING_RESULTS]

    def apply(self, resp: protocol.HeartbeatResponse) -> None:
        """The response's directives, in order; ``revoked`` overrides the rest."""
        for bad in resp.malformed:
            log.warning("directive #%d dropped (no usable id; a lead bug): %s",
                        bad.index, "; ".join(bad.reasons))
        for d in resp.directives:
            if isinstance(d, protocol.Revoked):
                self.revoke(d.reason, self.clock())
                return
        for d in resp.directives:
            try:
                self._apply_one(d)
            except OSError as err:
                error_id = protocol.new_error_id()
                log.error("directive %s could not be kept (%s, error id %s)",
                          d.id, type(err).__name__, error_id)
                self._result(d.id, "failed", error_id)

    def _apply_one(self, d: Any) -> None:
        local = self.local
        assert local is not None
        if isinstance(d, protocol.UnknownDirective):
            log.info("directive %s of unknown type '%s' ignored (this agent is older than "
                     "the lead)", d.id, d.type)
            self._result(d.id, "ignored")
        elif isinstance(d, protocol.InvalidDirective):
            log.warning("directive %s ('%s') is invalid: %s", d.id, d.type or "?",
                        "; ".join(d.reasons[:5]))
            self._result(d.id, "invalid")
        elif isinstance(d, protocol.SetLeadEndpoints):
            taken = self._take_endpoints(d.lead_endpoints)
            if not taken:
                log.info("directive %s: lead list epoch %d not above the one held; ignored",
                         d.id, d.lead_endpoints.epoch)
            self._result(d.id, "done" if taken else "ignored")
        elif isinstance(d, protocol.RenewNow):
            log.info("directive %s: renewing the certificate now", d.id)
            ok, error_id = self._renew(local, self.clock())
            self._result(d.id, "done" if ok else "failed", error_id)
        elif isinstance(d, protocol.RepointStore):
            # The data plane is a later release: keep what was sent, say
            # it was not applied ("ignored": the lead decides).
            local.enrollment = nodestate.save_store(self.paths, local.enrollment, d.store,
                                                    iso(self.clock()))
            nodestate.write_enrollment(self.paths, local.enrollment)
            log.info("directive %s: store %s kept, not applied (this release does not "
                     "replicate)", d.id, d.store.store_id)
            self._result(d.id, "ignored")
        elif isinstance(d, protocol.StartSeed):
            local.enrollment = dict(local.enrollment, seed_requested=dict(
                protocol.encode(d), received_at=iso(self.clock())))
            nodestate.write_enrollment(self.paths, local.enrollment)
            log.info("directive %s: history seed kept, not applied (this release does not "
                     "replicate)", d.id)
            self._result(d.id, "ignored")
        else:  # a type DIRECTIVES knows but this agent does not handle
            self._result(d.id, "ignored")

    def revoke(self, reason: str, now: float) -> None:
        """Stop for good. The enrollment says so (only a new join undoes
        it); the node's measurements are untouched."""
        local = self.local
        assert local is not None
        enrollment = dict(local.enrollment, revoked={"at": iso(now), "reason": reason})
        try:
            nodestate.write_enrollment(self.paths, enrollment)
        except OSError as err:
            log.error("cannot record the revocation (%s); stopping anyway",
                      type(err).__name__)
        log.error("the lead revoked this node (%s): nothing is sent again. The node keeps "
                  "measuring on its own; 'smoking-pi fleet leave' clears the enrollment.",
                  reason)
        self.local = assess(self.cfg, self.paths, self._dt(now))
        if self.local.state != "revoked":  # the file could not be written
            self.local = Local("revoked", "fleet agent: revoked by the lead; nothing is sent "
                               "again. 'smoking-pi fleet leave' clears the enrollment.",
                               local.problems, enrollment, local.endpoints, local.creds)
        self._state_said = "revoked"
        self.pending = []

    # -- renewal -----------------------------------------------------------------

    def _renew(self, local: Local, now: float) -> tuple[bool, str | None]:
        """A new key, a CSR, ``/fleet/renew`` with the current certificate;
        the new pair replaces the old only once checked and stored."""
        assert local.creds is not None
        key = nodekeys.new_key()
        message = protocol.RenewRequest(
            protocol=protocol.PROTOCOL_MAX, protocol_min=protocol.PROTOCOL_MIN,
            node_id=self.cfg.node_id, instance_id=self.instance_id,
            csr_pem=nodekeys.csr_pem(key, self.cfg.node_id))
        out = self.exchange("/fleet/renew", message, protocol.RenewResponse)
        if out.kind != "ok":
            self.renew_retry_at = now + RENEW_RETRY_SECONDS
            if out.kind in ("revoked", "stop"):
                self._failed(out, "renewal")
            else:
                self._say("renew-failed", "renewal failed (%s); the current certificate "
                          "stays in use (expires %s), retrying in %d min", out.kind,
                          iso(local.creds.not_after), RENEW_RETRY_SECONDS // 60)
            return False, None
        resp: protocol.RenewResponse = out.response
        error_id = protocol.new_error_id()
        try:
            issued = chain.verify(resp.cert_chain, local.creds.root, self._dt(self.clock()))
            if not nodekeys.matches(issued[0], key):
                raise chain.ChainError("the renewed certificate is not for the new key")
            if not chain.node_identity_ok(issued[0], self.cfg.node_id):
                raise chain.ChainError("the renewed certificate names another node")
        except chain.ChainError as err:
            self.renew_retry_at = now + RENEW_RETRY_SECONDS
            log.error("renewal refused: %s (error id %s); the current certificate stays",
                      err.reason, error_id)
            return False, error_id
        try:
            nodestate.store_renewal(self.paths, nodekeys.key_pem(key),
                                    nodestate.chain_text(chain.pem(c) for c in issued))
            enrollment = dict(local.enrollment, not_after=iso(issued[0].not_valid_after_utc))
            nodestate.write_enrollment(self.paths, enrollment)
        except OSError as err:
            self.renew_retry_at = now + RENEW_RETRY_SECONDS
            log.error("cannot store the renewed certificate (%s, error id %s); the current "
                      "one stays", type(err).__name__, error_id)
            return False, error_id
        self.local = assess(self.cfg, self.paths, self._dt(self.clock()))
        self.renew_retry_at = 0.0
        log.info("certificate renewed; it expires %s", iso(issued[0].not_valid_after_utc))
        return True, None


def main(argv: list[str] | None = None, *, stop: threading.Event | None = None,
         transport: Transport | None = None) -> int:
    runner.configure_logging()
    cfg = settings.load()
    for problem in cfg.problems:
        if not problem.startswith("FLEET_NODE_ID"):
            log.warning("setting ignored: %s", problem)
    agent = Agent(cfg, transport=transport)
    runner.loop(agent.tick, stop=stop)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
