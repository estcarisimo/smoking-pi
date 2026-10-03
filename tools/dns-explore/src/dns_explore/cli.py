"""``dns-explore``: how diverse, concentrated and stable is what the house resolves?

Iteration 0 of the domain-selection design (Notion: "Selección de dominios:
de observaciones DNS a targets"). It changes nothing: it reads the DNS
observer's query log and reports, for several aggregation levels, scores
and depths (K), the numbers the design's thresholds should come from.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from dns_explore import logread, metrics, units

logger = logging.getLogger(__name__)

app = typer.Typer(add_completion=False, help=__doc__)

KEEP_QTYPES = {"A", "AAAA", "HTTPS", "SVCB"}


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _rules(value: str) -> list[tuple[int, int]]:
    """``"2:3,3:5"`` -> ``[(2, 3), (3, 5)]``; each day count at least 1."""
    out = []
    for pair in _csv(value):
        enter, sep, leave = pair.partition(":")
        if not (sep and enter.isdigit() and leave.isdigit() and int(enter) and int(leave)):
            raise typer.BadParameter(f"{pair}: expected E:L, two whole days >= 1",
                                     param_hint="--rules")
        out.append((int(enter), int(leave)))
    return out


def _window(value: str) -> timedelta | None:
    if value in ("", "all"):
        return None
    n, unit = int(value[:-1]), value[-1]
    return {"h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


# Where an install keeps SmokePing's generated Targets: a clone's Pro edition,
# then the .deb layout's default SMOKING_PI_OUTPUT_DIR. A relocated output
# directory comes first (_output_dir_targets).
TARGETS_CANDIDATES = (
    Path.home() / "smoking-pi/editions/pro/config-manager/output/Targets",
    Path("/var/lib/smoking-pi/output/Targets"),
)
DEB_DEFAULTS = Path("/etc/default/smoking-pi")


def _output_dir_targets() -> list[Path]:
    """Targets under SMOKING_PI_OUTPUT_DIR, from the environment or from the
    .deb's /etc/default/smoking-pi, where an operator may have moved it."""
    dirs = [os.environ.get("SMOKING_PI_OUTPUT_DIR", "")]
    try:
        for ln in DEB_DEFAULTS.read_text().splitlines():
            key, _, value = ln.strip().partition("=")
            if key == "SMOKING_PI_OUTPUT_DIR":
                dirs.append(value.strip().strip("'\""))
    except OSError:
        pass
    return [Path(d) / "Targets" for d in dirs if d]


def _targets(value: str) -> Path | None:
    if value == "none":
        return None
    if value != "auto":
        path = Path(value)
        if not path.is_file():
            raise typer.BadParameter(f"{value}: no such file", param_hint="--targets")
        return path
    candidates = [*_output_dir_targets(), *TARGETS_CANDIDATES]
    return next((p for p in candidates if p.is_file()), None)


def load(
    files: list[Path] | None,
    container: str,
    window: timedelta | None,
    exclude: tuple[str, ...],
    measured: frozenset[str] = frozenset(),
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Queries as a DataFrame, and counts of what was filtered and why."""
    queries = list(logread.read_files(files) if files else logread.read_container(container))
    counts = {"read": len(queries)}
    if window is not None and queries:
        newest = max(q.ts for q in queries)
        queries = [q for q in queries if q.ts >= newest - window]
    counts["in_window"] = len(queries)
    kept, own, smokeping, other = [], 0, 0, 0
    for q in queries:
        if q.qname in measured:
            smokeping += 1
        elif units.excluded(q, exclude):
            own += 1
        elif q.qtype not in KEEP_QTYPES or q.rcode != "NOERROR":
            other += 1
        else:
            kept.append(q)
    counts.update(excluded_own=own, excluded_measured=smokeping,
                  dropped_type_or_rcode=other, kept=len(kept))
    df = pd.DataFrame(
        {
            "ts": pd.to_datetime([q.ts for q in kept], utc=True),
            # The calendar day as the log wrote it (AdGuard logs the host's
            # local time with its offset); UTC days would move the hour
            # after local midnight to the day before.
            "day": [q.ts.date() for q in kept],
            # Minutes after that local midnight: tells a whole day from the
            # partial ones at the ends of the log.
            "clock": [q.ts.hour * 60 + q.ts.minute for q in kept],
            "cached": [q.cached for q in kept],
            "q": kept,
        }
    )
    return df, counts


def add_levels(df: pd.DataFrame, levels: list[str], asn: units.AsnLookup | None) -> pd.DataFrame:
    """One column per aggregation level."""
    if asn is not None and {"asn", "org"} & set(levels):
        asn.resolve_all(a for q in df["q"] for a in q.addrs[:1])
    for level in levels:
        fn = units.unit_fn(level, asn)
        df[level] = [fn(q) for q in df["q"]]
    return df


@app.command()
def report(
    file: Annotated[
        list[Path] | None,
        typer.Option(
            "--file", "-f", help="Local copies of querylog.json; default: read the container."
        ),
    ] = None,
    container: Annotated[
        str, typer.Option(help="The DNS observer's container.")
    ] = logread.CONTAINER,
    levels: Annotated[
        str, typer.Option(help=f"Comma list of {', '.join(units.LEVELS)}.")
    ] = ",".join(units.LEVELS),
    scores: Annotated[
        str, typer.Option(help=f"Comma list of {', '.join(metrics.SCORES)}.")
    ] = ",".join(metrics.SCORES),
    k: Annotated[str, typer.Option(help="Depths (top-K), comma list.")] = "5,10,20,50",
    rules: Annotated[
        str,
        typer.Option(help="Enter/leave rules to replay, as E:L pairs: added after E days "
                     "in the daily top-K, removed after L days out of it."),
    ] = "1:1,2:3,3:3,3:5",
    whole_days: Annotated[
        bool,
        typer.Option("--whole-days/--all-days",
                     help="Compare whole days only: leave out a first or last day the "
                     "log covers in part."),
    ] = True,
    window: Annotated[
        str, typer.Option(help="Only the last Nh / Nd of the log, or 'all'.")
    ] = "all",
    exclude_own: Annotated[bool, typer.Option(help="Drop the Pi's own and local traffic.")] = True,
    exclude_file: Annotated[
        Path | None, typer.Option(help="Extra exclusion patterns, one per line.")
    ] = None,
    targets: Annotated[
        str,
        typer.Option(help="SmokePing's generated Targets: the names it measures are the "
                     "Pi's own lookups. 'auto' finds the install's; 'none' keeps them."),
    ] = "auto",
    show: Annotated[int, typer.Option(help="Top units to list per level.")] = 15,
    asn: Annotated[bool, typer.Option(help="Look up origin ASes (Team Cymru via 1.1.1.1).")] = True,
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Also write the results here.")
    ] = None,
    verbose: Annotated[bool, typer.Option("-v")] = False,
) -> None:
    """Diversity, concentration, coverage and churn of the observed DNS."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    level_list, score_list = _csv(levels), _csv(scores)
    ks = tuple(int(x) for x in _csv(k))
    rule_list = _rules(rules)
    lookup = units.AsnLookup() if asn else None
    if not asn:
        level_list = [lv for lv in level_list if lv not in ("asn", "org")]
    patterns: tuple[str, ...] = units.DEFAULT_EXCLUDE if exclude_own else ()
    if exclude_file:
        patterns += tuple(
            ln.strip()
            for ln in exclude_file.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        )

    targets_path = _targets(targets)
    measured = (units.measured_hosts(targets_path.read_text())
                if targets_path and exclude_own else frozenset())
    if targets == "auto" and targets_path is None and exclude_own:
        typer.echo("No SmokePing Targets found: its own lookups stay in (--targets PATH).")

    df, counts = load(file, container, _window(window), patterns, measured)
    if df.empty:
        typer.echo(f"No queries left after filtering: {counts}")
        raise typer.Exit(1)
    df = add_levels(df, level_list, lookup)
    span = (df["ts"].min(), df["ts"].max())
    results: dict = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
        "span": [str(span[0]), str(span[1])],
        "levels": level_list,
        "scores": score_list,
        "k": list(ks),
    }

    total_read = max(counts["in_window"], 1)
    typer.echo(
        f"Log span {span[0]:%Y-%m-%d %H:%M} to {span[1]:%Y-%m-%d %H:%M} UTC "
        f"({(span[1] - span[0]).total_seconds() / 3600:.1f} h)"
    )
    typer.echo(
        f"Queries: {counts['in_window']} in window; kept {counts['kept']}; "
        f"the Pi's own/local {counts['excluded_own']} "
        f"({100 * counts['excluded_own'] / total_read:.1f}%); "
        f"SmokePing's lookups of {len(measured)} measured names {counts['excluded_measured']} "
        f"({100 * counts['excluded_measured'] / total_read:.1f}%); "
        f"other types or failed {counts['dropped_type_or_rcode']}"
    )
    if "fqdn" in level_list:
        rand = df["fqdn"].drop_duplicates().map(units.looks_random).mean()
        typer.echo(f"Distinct names that look generated (hash/pod ids): {100 * rand:.1f}%")

    # Diversity and concentration, per level and score.
    rows = []
    for level in level_list:
        for score in score_list:
            d = metrics.diversity(metrics.scores(df, level, score), ks)
            rows.append({"level": level, "score": score, **d.as_row()})
    div = pd.DataFrame(rows)
    results["diversity"] = div.to_dict(orient="records")
    typer.echo("\n== Diversity and concentration ==")
    typer.echo(div.round(3).to_string(index=False))

    # What coalescing hides: behind the top-K services, how many CDNs/ASes/orgs.
    if "service" in level_list:
        base = metrics.scores(
            df, "service", "presence" if "presence" in score_list else score_list[0]
        )
        ret_rows = []
        for kk in ks:
            top = base.index[:kk]
            row = {"top_k_services": kk}
            for other in ("fqdn", "cdn", "asn", "org"):
                if other in level_list:
                    row[f"distinct_{other}"] = metrics.retained(df, "service", other, top)
            ret_rows.append(row)
        ret = pd.DataFrame(ret_rows)
        results["retained"] = ret.to_dict(orient="records")
        typer.echo("\n== Behind the top-K services (by presence) ==")
        typer.echo(ret.to_string(index=False))

    # Top units per level.
    results["top"] = {}
    for level in level_list:
        pres = metrics.scores(df, level, "presence")
        vol = metrics.scores(df, level, "queries")
        top = pd.DataFrame({"presence_h": pres, "queries": vol}).fillna(0)
        top = top.sort_values(["presence_h", "queries"], ascending=False).head(show)
        results["top"][level] = top.reset_index(names=level).to_dict(orient="records")
        typer.echo(f"\n== Top {show} by presence: {level} ==")
        typer.echo(top.astype(int).to_string())

    # Churn and stability compare days, so only whole ones: a day the log
    # starts or ends in ranks fewer hours and looks like churn.
    daily, partial = metrics.whole_days(df) if whole_days else (df, [])
    results["partial_days_left_out"] = [str(d) for d in partial]
    days = daily["day"].nunique() if not daily.empty else 0
    results["churn"] = []
    left_out = f"; left out, partial: {', '.join(map(str, partial))}" if partial else ""
    typer.echo(f"\n== Day-over-day churn of the top-K ({days} whole day(s) in the log"
               f"{left_out}) ==")
    if days < 2:
        typer.echo("Needs at least two whole days of log; run again later (or --all-days).")
    else:
        churn_rows = []
        for level in level_list:
            for score in score_list:
                for kk in ks:
                    c = metrics.churn(daily, level, score, kk)
                    churn_rows.append(
                        {
                            "level": level,
                            "score": score,
                            "k": kk,
                            "mean_jaccard": c["jaccard"].mean(),
                            "mean_swaps": (c["entered"] + c["left"]).mean() / 2,
                            "max_gap_days": c["gap_days"].max(),
                        }
                    )
        ch = pd.DataFrame(churn_rows)
        results["churn"] = ch.to_dict(orient="records")
        typer.echo(ch.round(3).to_string(index=False))

        # Who comes and goes, and what an enter/leave rule would have done.
        stab_rows = []
        for level in level_list:
            for score in score_list:
                for kk in ks:
                    row = {"level": level, "score": score, "k": kk,
                           **metrics.stability(daily, level, score, kk)}
                    m = metrics.membership(daily, level, score, kk)
                    for enter, leave in rule_list:
                        h = metrics.hysteresis(m, enter, leave)
                        row[f"E{enter}L{leave}"] = f"+{h['adds']}/-{h['drops']}"
                    stab_rows.append(row)
        st = pd.DataFrame(stab_rows)
        results["stability"] = st.to_dict(orient="records")
        typer.echo(
            "\n== Stability of the top-K: units ever in it, in it every day, once; "
            "re-entries; longest absence before a return (days); units tied at the "
            "cut; changes under enter-after-E-days / leave-after-L-days rules =="
        )
        typer.echo(st.round(2).to_string(index=False))

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(results, indent=1, default=str))
        typer.echo(f"\nWrote {json_out}")
