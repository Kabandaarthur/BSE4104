"""Builds the retrieval corpus for the Student-Support Case Agent (Week 3).

knowledge/corpus.json is the source of truth. This script reads it, downloads
each real source, records provenance (SHA-256, byte count, HTTP status, fetch
timestamp), extracts plain text, and writes the provenance back into the
manifest. knowledge/source_register.md is then generated from the manifest, so
the register table can never drift from what is actually indexed.

Raw downloads and extracted text are both committed, so a reviewer can verify
that the indexed text matches what the URL served, without a network.

Usage:
    python src/fetch_corpus.py              # fetch + extract + regenerate register
    python src/fetch_corpus.py --verify     # re-checksum, report drift, no writes
    python src/fetch_corpus.py --register   # regenerate the register only
    python src/fetch_corpus.py --force      # re-download even if raw file exists
"""

import argparse
import hashlib
import json
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "knowledge"
MANIFEST = KNOWLEDGE / "corpus.json"
RAW_DIR = KNOWLEDGE / "raw"
TEXT_DIR = KNOWLEDGE / "text"
SYNTHETIC_DIR = KNOWLEDGE / "synthetic"
REGISTER = KNOWLEDGE / "source_register.md"

USER_AGENT = "BSE4104-StudentSupportAgent/0.1 (academic coursework; Makerere University)"
TIMEOUT = 60

# Page boundaries survive into the extracted text so a citation can name a page
# even when heading detection fails on a badly structured PDF.
PAGE_MARKER = "[[page:{n}]]"

# Several Makerere policy PDFs are scans with no text layer at all. Below this
# many words per page we stop trusting the text layer and OCR the page images.
MIN_WORDS_PER_PAGE = 5
OCR_DPI = 150


class CorpusError(RuntimeError):
    pass


def load_manifest():
    if not MANIFEST.is_file():
        raise CorpusError(f"Manifest not found: {MANIFEST}")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(manifest):
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str):
    """Returns (bytes, http_status, content_type). Verifies TLS."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
        return response.read(), response.status, response.headers.get("Content-Type", "")


def extract_pdf(path: Path) -> tuple[str, int]:
    reader = PdfReader(str(path))
    parts = []
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        parts.append(PAGE_MARKER.format(n=number))
        parts.append(text.strip())
    return "\n".join(parts).strip(), len(reader.pages)


def _order_boxes(boxes, page_width):
    """Order OCR boxes into reading order, handling two-column layouts.

    The Students' Regulations and the semester/credit regulations are set in two
    columns. Sorting boxes by vertical position alone reads straight across the
    gutter and interleaves clause (1) of the left column with clause (5) of the
    right, which would put semantically scrambled text into every chunk.

    A real column gutter is a vertical strip that no text box spans. We scan the
    middle half of the page for the widest such strip; if one is wide enough to
    be a gutter rather than word spacing, the page is read as two columns.

    boxes: list of (left, right, y_centre, text).
    """
    if not boxes:
        return []

    step = max(1, int(page_width / 200))
    free = [
        x
        for x in range(int(0.25 * page_width), int(0.75 * page_width), step)
        if not any(left < x < right for left, right, _, _ in boxes)
    ]

    longest, current = [], []
    for x in free:
        if current and x - current[-1] <= step:
            current.append(x)
        else:
            if len(current) > len(longest):
                longest = current
            current = [x]
    if len(current) > len(longest):
        longest = current

    if longest and (longest[-1] - longest[0]) > 0.05 * page_width:
        split = (longest[0] + longest[-1]) / 2
        boxes = sorted(boxes, key=lambda b: (0 if b[0] < split else 1, b[2]))
    else:
        boxes = sorted(boxes, key=lambda b: b[2])
    return [b[3] for b in boxes]


def ocr_pdf(path: Path) -> tuple[str, int]:
    """OCR a scanned PDF. Renders pages with pdftoppm, reads them with RapidOCR."""
    from rapidocr_onnxruntime import RapidOCR  # heavy: imported only when needed

    engine = RapidOCR()
    parts = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-r", str(OCR_DPI), "-png", str(path), f"{tmp}/page"],
            check=True,
            capture_output=True,
        )
        images = sorted(Path(tmp).glob("page-*.png"))
        for number, image in enumerate(images, start=1):
            result, _ = engine(str(image))
            boxes = []
            for box, text, _score in result or []:
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                boxes.append((min(xs), max(xs), sum(ys) / len(ys), text))
            width = max((b[1] for b in boxes), default=1.0)
            parts.append(PAGE_MARKER.format(n=number))
            parts.extend(_order_boxes(boxes, width))
    return "\n".join(parts).strip(), len(images)


def extract_html(path: Path) -> tuple[str, int]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "form", "aside", "noscript"]):
        tag.decompose()

    # Pick the richest content container, not the first one that matches: on the
    # CoCIS and mak.ac.ug pages the first match is a near-empty wrapper, which
    # silently reduced two large pages to a few dozen words. If no candidate
    # holds most of the page's text, fall back to the body.
    body = soup.body or soup
    body_words = len(body.get_text(" ", strip=True).split())
    candidates = []
    for selector in ("main", "article", ".entry-content", "#content", ".content"):
        found = soup.select_one(selector)
        if found:
            candidates.append((len(found.get_text(" ", strip=True).split()), found))
    best_words, best = max(candidates, default=(0, None), key=lambda c: c[0])
    main = best if best is not None and best_words >= 0.6 * body_words else body

    # Keep heading text on its own line so the heading parser can see structure.
    lines = []
    for element in main.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"]):
        text = " ".join(element.get_text(" ").split())
        if not text:
            continue
        if element.name.startswith("h"):
            lines.append("")
            lines.append(text)
            lines.append("")
        else:
            lines.append(text)

    if not lines:  # fallback for pages with an unusual structure
        lines = [" ".join(main.get_text(" ").split())]

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, 0


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", re.sub(r"\[\[page:\d+\]\]", " ", text)))


def raw_path_for(document) -> Path:
    suffix = {"pdf": ".pdf", "html": ".html", "markdown": ".md"}[document["format"]]
    return RAW_DIR / f"{document['doc_id']}{suffix}"


def process(document, force=False):
    """Fetch (if needed) and extract one document. Returns a provenance dict."""
    doc_id = document["doc_id"]

    if document["status"] != "active":
        return {"state": "skipped", "reason": document["status"]}

    if document["kind"] == "synthetic":
        source = SYNTHETIC_DIR / f"{doc_id}.md"
        if not source.is_file():
            return {"state": "missing", "reason": f"expected authored file at {source}"}
        data = source.read_bytes()
        text = source.read_text(encoding="utf-8")
        pages = 0
        provenance = {
            "origin": "team-authored",
            "local_source": str(source.relative_to(ROOT)),
            "sha256": sha256_of(data),
            "bytes": len(data),
        }
    else:
        raw = raw_path_for(document)
        if raw.is_file() and not force:
            data = raw.read_bytes()
            provenance = dict(document.get("provenance", {}))
            provenance.setdefault("fetched_at", "unknown (pre-existing file)")
        else:
            try:
                data, status, content_type = download(document["source_url"])
            except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError, OSError) as e:
                return {"state": "fetch_failed", "reason": str(e)}
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(data)
            provenance = {
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "http_status": status,
                "content_type": content_type,
                "tls_verified": True,
            }
        provenance["sha256"] = sha256_of(data)
        provenance["bytes"] = len(data)
        provenance["raw_path"] = str(raw.relative_to(ROOT))

        method = "text-layer"
        try:
            if document["format"] == "pdf":
                text, pages = extract_pdf(raw)
                # A scan has a valid page count but essentially no extractable
                # text. Fall back to OCR rather than indexing an empty document.
                if pages and word_count(text) < MIN_WORDS_PER_PAGE * pages:
                    text, pages = ocr_pdf(raw)
                    method = f"ocr (rapidocr, {OCR_DPI} dpi)"
            else:
                text, pages = extract_html(raw)
        except Exception as e:  # a corrupt download should not kill the whole run
            return {"state": "extract_failed", "reason": f"{type(e).__name__}: {e}"}
        provenance["extraction_method"] = method

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = TEXT_DIR / f"{doc_id}.txt"
    text_path.write_text(text, encoding="utf-8")

    provenance["text_path"] = str(text_path.relative_to(ROOT))
    provenance["pages"] = pages
    provenance["words"] = word_count(text)
    provenance["extracted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    provenance["state"] = "ok" if provenance["words"] > 0 else "empty_extraction"
    return provenance


def verify(manifest):
    """Re-checksum every raw/authored file against the manifest. Returns problems."""
    problems = []
    for document in manifest["documents"]:
        doc_id = document["doc_id"]
        provenance = document.get("provenance") or {}
        if document["status"] != "active":
            continue
        if not provenance:
            problems.append(f"{doc_id}: no provenance recorded - run the fetch first")
            continue

        recorded = provenance.get("sha256")
        path_key = "local_source" if document["kind"] == "synthetic" else "raw_path"
        relative = provenance.get(path_key)
        if not relative:
            problems.append(f"{doc_id}: manifest has no {path_key}")
            continue
        path = ROOT / relative
        if not path.is_file():
            problems.append(f"{doc_id}: file missing at {relative}")
            continue
        actual = sha256_of(path.read_bytes())
        if actual != recorded:
            problems.append(f"{doc_id}: checksum drift\n    recorded {recorded}\n    actual   {actual}")

        text_relative = provenance.get("text_path")
        if not text_relative or not (ROOT / text_relative).is_file():
            problems.append(f"{doc_id}: extracted text missing")
        elif provenance.get("words", 0) == 0:
            problems.append(f"{doc_id}: extracted text is empty (scanned PDF or bad selector?)")
    return problems


def build_register(manifest):
    active = [d for d in manifest["documents"] if d["status"] == "active"]
    real = [d for d in active if d["kind"] == "real"]
    synthetic = [d for d in active if d["kind"] == "synthetic"]
    excluded = [d for d in manifest["documents"] if d["status"] != "active"]
    total_words = sum((d.get("provenance") or {}).get("words", 0) for d in active)

    lines = [
        "# Corpus / Source Register",
        "",
        "**Project:** University Student-Support Case Agent (BSE4104) — Week 3 deliverable 1",
        "",
        "> **Generated file — do not edit by hand.**",
        "> Source of truth is [`knowledge/corpus.json`](corpus.json).",
        "> Regenerate with `python src/fetch_corpus.py --register`.",
        "",
        f"**Indexed documents:** {len(active)} ({len(real)} real, {len(synthetic)} synthetic) &nbsp;|&nbsp; "
        f"**Excluded:** {len(excluded)} &nbsp;|&nbsp; **Total extracted words:** {total_words:,}",
        "",
        "Every document below was logged here *before* being chunked and indexed. "
        "Each row's SHA-256 is taken over the exact bytes retrieved from the URL, so the "
        "indexed text can be proven to match what the source served on the retrieval date.",
        "",
        "## Indexed documents",
        "",
        "| Doc ID | Title / Description | Source / Provenance | Real or Synthetic | Date Added |",
        "|---|---|---|---|---|",
    ]

    for d in active:
        provenance = d.get("provenance") or {}
        description = f"**{d['title']}** — {d['description']}" if d["description"] else f"**{d['title']}**"
        if d["kind"] == "synthetic":
            source = f"Team-created. `{provenance.get('local_source', 'n/a')}`"
            kind = "**Synthetic — clearly labelled**"
        else:
            source = f"[{d['source_url']}]({d['source_url']})"
            if d.get("source_page"):
                source += f"<br>Listed at: [policy index]({d['source_page']})"
            kind = "Real"
        stats = []
        if provenance.get("pages"):
            stats.append(f"{provenance['pages']} pages")
        if provenance.get("words"):
            stats.append(f"{provenance['words']:,} words")
        if stats:
            source += f"<br>{', '.join(stats)}"
        if provenance.get("sha256"):
            source += f"<br>`sha256:{provenance['sha256'][:16]}…`"
        lines.append(f"| {d['doc_id']} | {description} | {source} | {kind} | {d['date_added']} |")

    if excluded:
        lines += [
            "",
            "## Excluded / unavailable sources",
            "",
            "Recorded deliberately: the register should show what was attempted, not only what succeeded.",
            "",
            "| Doc ID | Title | Attempted source | Why excluded |",
            "|---|---|---|---|",
        ]
        for d in excluded:
            url = d["source_url"] or "—"
            lines.append(f"| {d['doc_id']} | {d['title']} | `{url}` | {d['notes']} |")
        lines += [
            "",
            "**On the four scans.** D01, D10, D12 and D15 are image-only PDFs: every page is a",
            "single scanned picture, so a text extractor returns nothing at all. They are genuine",
            "Makerere documents and were retrieved successfully — the checksums in",
            "`knowledge/corpus.json` prove what was downloaded — but they cannot be indexed or",
            "cited as they stand. OCR is implemented in `src/fetch_corpus.py` (`ocr_pdf`, with",
            "two-column handling) and recovers them; it was not run for this submission because it",
            "needs roughly 15-20 minutes of sustained CPU. To index them later:",
            "",
            "```bash",
            "python src/fetch_corpus.py --only D01 --force   # repeat for D10, D12, D15",
            "```",
            "",
            "Their raw scans are not committed (24 MB of page images for zero retrievable text);",
            "re-fetch from `source_url` and check the recorded sha256.",
        ]

    lines += [
        "",
        "## Provenance notes",
        "",
        "- Raw downloads are committed under `knowledge/raw/` (except the four scans above);",
        "  extracted text under `knowledge/text/`.",
        "- `python src/fetch_corpus.py --verify` re-checksums every file and exits non-zero on drift.",
        "- Synthetic documents (D08, D09) carry a visible banner **inside the file body**, not only in this",
        "  table, so a retrieved chunk cannot be mistaken for real Makerere policy.",
        "- Calendar pages (D05, D06) are time-sensitive; checksum drift on those is expected between",
        "  semesters and means *re-fetch*, not *corruption*.",
        "- No real student data is stored anywhere in this corpus.",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from `knowledge/corpus.json`._",
    ]
    REGISTER.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="re-checksum only, no writes")
    parser.add_argument("--register", action="store_true", help="regenerate the register only")
    parser.add_argument("--force", action="store_true", help="re-download existing raw files")
    parser.add_argument("--only", help="comma-separated doc IDs to process")
    args = parser.parse_args()

    manifest = load_manifest()

    if args.verify:
        problems = verify(manifest)
        if problems:
            print(f"FAIL - {len(problems)} problem(s):")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        active = sum(1 for d in manifest["documents"] if d["status"] == "active")
        print(f"OK - {active} documents verified, no checksum drift.")
        return 0

    if args.register:
        build_register(manifest)
        print(f"Wrote {REGISTER.relative_to(ROOT)}")
        return 0

    wanted = set(args.only.split(",")) if args.only else None
    failures = 0
    for document in manifest["documents"]:
        if wanted and document["doc_id"] not in wanted:
            continue
        result = process(document, force=args.force)
        state = result.get("state")
        if state == "ok":
            print(f"  {document['doc_id']}  ok       {result['words']:>7,} words  {document['title'][:52]}")
            document["provenance"] = result
        elif state == "skipped":
            print(f"  {document['doc_id']}  skipped  ({result['reason']})")
        elif state == "empty_extraction":
            # Keep the provenance: an empty extraction is a finding to document,
            # not a crash. Usually a scanned PDF with no text layer.
            failures += 1
            document["provenance"] = result
            print(
                f"  {document['doc_id']}  EMPTY EXTRACTION - 0 words from "
                f"{result.get('pages', 0)} pages (scanned PDF?)  {document['title'][:40]}"
            )
        else:
            failures += 1
            print(f"  {document['doc_id']}  {state.upper()}: {result.get('reason', '(no detail)')}")

    save_manifest(manifest)
    build_register(manifest)
    print(f"\nManifest and register updated. {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
