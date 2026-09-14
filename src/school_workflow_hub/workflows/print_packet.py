"""Self-contained printable HTML from the existing validated fixture report.

Pure rendering only: no browser, printer, file writer, network, or delivery.
"""
from dataclasses import dataclass
from html import escape

from school_workflow_hub.core.runs import content_hash, deterministic_id
from school_workflow_hub.workflows.reporting_pipeline import render_report_packet


@dataclass(frozen=True)
class PrintPacket:
    stable_id: str
    content_hash: str
    html: str
    media_type: str = "text/html; charset=utf-8"
    delivery_state: str = "not_delivered"
    external_writes: int = 0


def render_print_packet(preview):
    markdown = render_report_packet(preview)
    # The existing renderer validates fixture IDs/counters and reconstructs
    # discussion text. Escape its output too; no scripts or remote resources.
    html = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>School report preview</title><style>"
            "body{font-family:system-ui,sans-serif;max-width:68rem;margin:2rem auto;padding:0 1rem;color:#17202a}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12pt/1.45 system-ui,sans-serif}"
            "header{border-bottom:2px solid #334155;margin-bottom:1.5rem}"
            "@media print{body{margin:0;padding:0}pre{font-size:10pt}@page{margin:16mm}}"
            "</style></head><body><header><h1>School report preview</h1>"
            "<p>Fictional test evidence · Teacher review pending · Not delivered or printed</p>"
            "</header><main><pre>" + escape(markdown) + "</pre></main></body></html>\n")
    digest = content_hash(html)
    return PrintPacket(deterministic_id("print-packet", {"content_hash": digest}), digest, html)
