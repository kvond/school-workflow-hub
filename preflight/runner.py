"""Run every check for one week and collect the results."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from preflight.baseline import Baseline
from preflight.checks import calendar, convert, flow, links, markers, printing, supplies, sync, vt
from preflight.context import WeekContext
from preflight.models import ConversionIdea, Finding, PrintItem, Severity, SupplyItem

log = logging.getLogger(__name__)


@dataclass
class Result:
    ctx: WeekContext
    findings: list[Finding]
    prints: list[PrintItem]
    supplies: list[SupplyItem]
    conversions: list[ConversionIdea]
    slide_specs: dict[str, str]
    not_checked: list[str] = field(default_factory=list)

    def by_severity(self, sev: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity == sev]


def run(ctx: WeekContext, baseline: Baseline | None = None) -> Result:
    findings: list[Finding] = []
    for mod in (calendar, links, sync, markers, vt, flow):
        try:
            findings.extend(mod.check(ctx))
        except Exception:  # one broken check must not hide the others
            log.exception("check %s failed", mod.__name__)
            findings.append(Finding(Severity.CHECK, "All", "setup",
                                    f"The {mod.__name__.rsplit('.', 1)[-1]} check crashed; see the job log."))

    prints, print_findings = printing.collect(ctx)
    findings.extend(print_findings)
    supply_list = supplies.collect(ctx)
    ideas, specs = convert.collect(ctx, prints)

    if baseline is not None:
        for cc in ctx.courses:
            for fid, deck in cc.decks.items():
                findings.extend(baseline.compare(cc.name, deck))

    if ctx.config.llm_review and ctx.config.vertex_project:
        from preflight import llm

        decks = [(cc.name, d) for cc in ctx.courses for fid, d in cc.decks.items()
                 if cc.deck_roles.get(fid) in ("teacher deck", "student copy")]
        findings.extend(llm.review(decks, ctx.config.vertex_project, ctx.config.vertex_region))

    not_checked = []
    for cc in ctx.courses:
        if not cc.cfg.sections:
            not_checked.append(f"{cc.name}: Schoology (no section ids in config)")
        elif cc.schoology_error:
            not_checked.append(f"{cc.name}: Schoology ({cc.schoology_error[:80]})")
    if not ctx.config.llm_review:
        not_checked.append("VT judgment review by Claude (llm_review is off)")

    return Result(ctx=ctx, findings=dedupe(findings), prints=prints, supplies=supply_list,
                  conversions=ideas, slide_specs=specs, not_checked=not_checked)


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen: dict[tuple, Finding] = {}
    for f in findings:
        k = f.key()
        if k not in seen or f.severity > seen[k].severity:
            seen[k] = f
    order = {"calendar": 0, "links": 1, "sync": 2, "vt": 3, "markers": 4, "flow": 5, "print": 6, "setup": 7}
    return sorted(seen.values(), key=lambda f: (-f.severity, f.course, order.get(f.area, 9)))
